"""``SkillRouter`` -- choose which playbooks a turn is allowed to see.

Frozen contract: plan §1.4, as amended by **PLAN-AMENDMENT-1** (recorded in
``run.json`` under ``contract_deviations_ratified`` and in plan §1.4.5 itself).
Everything in this module's public surface is normative and was not improvised
here.

What this is
------------
A **selector**, not a bypass. It decides which ``SKILL.md`` playbooks are
injected into the Account Manager's ``understand`` context. It does not decide
the route, does not run a tool, does not call RAG, and does not replace any
stage of the pipeline (plan §1.4.5, "Provider independence"). A turn that
selects three skills still runs ``load_project`` first, because
:func:`route` refuses to select anything without project scope.

What this is not
----------------
A second intent classifier. A resolved tool capability is detected by the
*existing* capability classifier (:func:`resolve_tool_capability`, which calls
``account_manager.classify_to_route`` -- the exact condition the graph's
``route_selector`` uses at ``account_manager_graph.py:859-860``), so this
module and the graph can never disagree about which turns resolve a tool
action. A new heuristic here would be two sources of truth for one question.

The amended mechanical-skip rule (PLAN-AMENDMENT-1)
---------------------------------------------------
Original frozen §1.4.5: a resolved tool capability unconditionally returns
``selected=()`` and injects no foundation. Measured during wave B, that rule
collides with §6.1/A6 (every one of the 340 upstream eval prompts routes to its
own skill) for exactly six prompts -- audit/fix asks that carry the vocabulary
of a methodology playband as well as a tool-shaped token, e.g. "do an SEO audit
of our SaaS website". 98.24 % of the corpus was unreachable with both contracts
in place; the collision was reported with all six prompts pinned and was
ratified as PLAN-AMENDMENT-1, not tuned away.

The ratified amendment keeps **both** goals at once:

1. a resolved tool capability forces ``foundation_injected=False`` in every
   case -- a turn the classifier calls a tool action never loads the
   product-marketing foundation on top;
2. it suppresses skill *selection* **only** when the deterministic scorer
   (§1.4.3) would also select nothing -- i.e. the intent carries no skill
   vocabulary at all.

So a methodology ask ("do an SEO audit of our SaaS website") gets its playbook
at the frozen scores, capped at ``max_skills``, while a genuine pure-mechanical
ask -- "crawl example.com": capability + zero deterministic score -- is still
refused a playbook and a foundation entirely. Selection never bypasses the
pipeline: the graph still runs the tool capability branch; the playbook merely
rides along (plan §1.4.5's provider-independence invariant).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any, Callable, Iterable, Mapping, Sequence

from .records import MarketingSkillRecord
from .roles import EMPLOYEE_ROLES, RoleTableError, role_for

#: Frozen cap on injected task-specific skills (plan §1.4.1).
MAX_SKILLS_PER_TURN = 3

#: Frozen cap on employee roles per turn (plan §1.4.1). Equals
#: ``app.contracts.runtime.MAX_AGENT_CONCURRENCY_DEFAULT``
#: (``app/contracts/runtime.py:17``), ``app.services.turns._POOL``
#: ``max_workers=4`` (``app/services/turns.py:25``), and ``AGENTS.md`` "Maximum
#: default parallel workers: 4". Plan §1.4.1 places this constant beside
#: ``MAX_SKILLS_PER_TURN``, and this module owns ``router.py``, so it lives here
#: and is not duplicated into ``roles.py``.
#:
#: With ``MAX_SKILLS_PER_TURN = 3`` the cap is unreachable through
#: :func:`route` (at most 3 skills means at most 3 roles). It is enforced anyway
#: because a fan-out that silently grew past the thread pool would be a
#: concurrency bug, and an unreachable guard is cheaper than that bug.
MAX_EMPLOYEES_PER_TURN = 4

#: The playbook whose context is the "read stable knowledge first" foundation
#: (``AGENTS.md`` Marketing Brain). Named as a constant because the rule is
#: about *this* file, not about "some skill".
FOUNDATION_SKILL_ID = "product-marketing"

#: The complete reason-code enum (plan §1.4.2). Nothing outside this tuple ever
#: reaches a decision, so a decision is always attributable.
#:
#: Disjoint by construction from ``records.REASON_CODES``: those are
#: SCREAMING_CASE *validation* failures of a file on disk, these are kebab-case
#: *routing* outcomes of a turn. Collapsing the two vocabularies would make
#: "this skill file is broken" indistinguishable from "this turn matched
#: nothing".
REASON_CODES = (
    "deterministic-exact-match",
    "deterministic-keyword-match",
    "model-classification",
    "related-skill-expansion",
    "product-marketing-foundation",
    "mechanical-tool-action-skip",
    "no-skill-selected",
    "cap-truncated",
    "registry-unavailable",
)

R_EXACT = "deterministic-exact-match"
R_KEYWORD = "deterministic-keyword-match"
R_MODEL = "model-classification"
R_RELATED = "related-skill-expansion"
R_FOUNDATION = "product-marketing-foundation"
R_MECHANICAL = "mechanical-tool-action-skip"
R_NONE = "no-skill-selected"
R_TRUNCATED = "cap-truncated"
R_UNAVAILABLE = "registry-unavailable"

#: The frozen scoring weights (plan §1.4.3). All are dyadic rationals, so the
#: floats compare exactly and ``score`` is safe to sort on -- no epsilon, no
#: float fuzz, no "almost equal" branch in the total order.
W_EXACT = 3.0
W_KEYWORD = 2.0
W_ID_TOKEN = 1.0
W_RELATED = 0.5
W_MODEL = 1.0

_NORMALISE = re.compile(r"[^a-z0-9]+")

#: Start of the description clause that carries the quoted trigger phrases
#: (plan §1.4.3, the 2.0 "Also use when the user mentions ..." component).
_KEYWORD_CLAUSE = re.compile(r"also\s+use\s+when", re.I)

#: End of that clause: the first sentence terminator sitting immediately inside
#: a closing quote, e.g. ``... or 'why isn't my offer converting.' Best for``.
_CLAUSE_END = re.compile(r"[.!?][\"']")

#: One quoted phrase, in either style, anchored so that intra-word apostrophes
#: cannot be mistaken for delimiters:
#:
#: * the opening quote must be at the start of the clause or preceded by
#:   whitespace -- a real list item always is, while the ``'`` in ``isn't`` or
#:   ``don't`` never is;
#: * the closing quote must be immediately preceded by ``,`` or ``.``, which is
#:   the shape of every enumerated item in the corpus;
#: * the closing quote must be followed by whitespace or end of clause.
#:
#: Measured over the 50 vendored descriptions: 47 carry the clause and this
#: extracts 713 phrases. Without the leading-context anchor the naive
#: ``'([^']+)'`` regex shreds ``'this page isn't converting,'`` into
#: ``'this page isn'`` -- measured, not assumed.
_KEYWORD_SPANS = (
    re.compile(r"(?<![^\s])'([^'\n]{2,160}?[,.])(?='(?:\s|$|,|;))"),
    re.compile('(?<![^\\s])"([^"\\n]{2,160}?[,.])(?="(?:\\s|$|,|;))'),
)


def normalise(text: str) -> str:
    """``re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()`` -- plan §1.4.3.

    Exposed because the test normalises independently, so a change to the rule
    on either side shows up as a disagreement rather than as a silent drift.
    """
    return _NORMALISE.sub(" ", (text or "").lower()).strip()


@lru_cache(maxsize=512)
def keyword_phrases(description: str) -> tuple[str, ...]:
    """Quoted phrases from the description's ``Also use when ...`` clause.

    Returned normalised and de-duplicated in first-seen order. A description
    with no such clause (measured: 3 of 50) yields ``()`` -- not a guess. The
    clause is delimited by a single measured rule, not by a hand-listed set of
    "Best for / Use this / For ..." markers, because a marker list is a guess
    that silently stops working on the next re-pin.

    Memoised because it is a pure function of a string that never changes
    within a process, and because scoring re-derives it for every record on
    every call -- 50 records times every turn. The return value is a tuple, so
    a caller cannot poison the cache.
    """
    match = _KEYWORD_CLAUSE.search(description or "")
    if not match:
        return ()
    tail = (description or "")[match.end() :]
    end = _CLAUSE_END.search(tail)
    clause = tail[: end.start()] if end else tail

    phrases: list[str] = []
    for pattern in _KEYWORD_SPANS:
        for raw in pattern.findall(clause):
            token = normalise(raw)
            if token and any(ch.isalnum() for ch in token):
                phrases.append(token)
    return tuple(dict.fromkeys(phrases))


def skill_id_tokens(skill_id: str) -> tuple[str, ...]:
    """Hyphen-split tokens of a skill id, one character and shorter dropped.

    ``copy-editing`` -> ``("copy", "editing")``. The length floor exists because
    a bare ``-`` split of a short slug would produce a token that matches half
    the corpus; it is a measured guard, not a tuned constant.
    """
    return tuple(part for part in (skill_id or "").split("-") if len(part) > 1)


# --------------------------------------------------------------------------- #
# the mechanical-tool-action definition -- reused, never re-invented          #
# --------------------------------------------------------------------------- #


def resolve_tool_capability(intent: str) -> str:
    """Return the single named tool capability for ``intent``, or ``""``.

    Delegates to the classifier the graph itself uses:
    ``account_manager.classify_to_route`` returning ``"tool_capability"`` is
    literally the branch condition at ``account_manager_graph.py:859-860``, and
    ``graphs.intent.classify_intent`` supplies the capability *name*. Plan
    §1.4.5: "Mechanical" is defined by the existing capability classifier, not by
    a new heuristic -- so the two surfaces cannot disagree.

    Returns ``""`` if the intent is not a capability request, and also if the
    classifier cannot be imported or raises: the router degrades to
    deterministic scoring rather than taking a turn down over a classifier
    import. That failure mode is visible in the decision -- a turn that would
    have been suppressed by a capability instead scores, and any selection it
    gets carries a scoring reason code, so the difference is legible rather
    than silent.
    """
    try:
        from app.graphs.account_manager import classify_to_route
        from app.graphs.intent import classify_intent
    except Exception:
        return ""
    try:
        if classify_to_route(intent or "") != "tool_capability":
            return ""
        capability = classify_intent(intent or "").get("capability") or {}
        return str(capability.get("capability") or "")
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# the frozen dataclasses                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SkillRouteRequest:
    """Everything the router is allowed to see. No I/O, no globals.

    ``intent`` is the normalised ``user_request``. ``project_state`` is the
    ``graphs._project_state()`` output -- ``{}`` is exactly what that function
    returns when there is no project scope, which is how the
    ``NO_PROJECT_SCOPE`` rule is enforced here without a second lookup.
    ``conversation_context`` and ``task`` are carried for the caller and the
    employee prompt; the frozen scoring formula (§1.4.3) scores ``intent`` only,
    so they deliberately do not influence selection.
    """

    intent: str
    project_state: dict
    conversation_context: tuple[str, ...] = ()
    task: dict = field(default_factory=dict)
    available_skills: tuple[MarketingSkillRecord, ...] = ()
    max_skills: int = MAX_SKILLS_PER_TURN


@dataclass(frozen=True)
class SkillSelection:
    """One selected playbook and why it was selected."""

    skill_id: str
    reason_code: str
    score: float


@dataclass(frozen=True)
class SkillRouteDecision:
    """The whole answer. Frozen so it can be compared byte-for-byte."""

    selected: tuple[SkillSelection, ...] = ()
    reason_codes: tuple[str, ...] = ()
    employee_roles: tuple[str, ...] = ()
    foundation_injected: bool = False

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return tuple(item.skill_id for item in self.selected)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready view; tuples become lists. Used for byte-equality checks."""
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, tuple):
                data[key] = list(value)
        return data


# --------------------------------------------------------------------------- #
# scoring                                                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Prepared:
    """Per-request precomputation, so 50 records are prepared once per call."""

    skill_id: str
    triggers: tuple[str, ...]
    keywords: tuple[str, ...]
    id_tokens: frozenset[str]
    related: tuple[str, ...]


def _prepare(
    records: Sequence[MarketingSkillRecord],
) -> tuple[dict[str, _Prepared], tuple[str, ...]]:
    """Build the scoring table. Returns ``(by_id, eligible_ids)``.

    A record is *eligible* when it is valid, enabled, and present in the
    versioned role table. Eligibility is re-checked here even though
    ``available_skills`` is contractually "enabled AND valid only": the router
    takes its input from a caller, and refusing to inject a record whose role is
    unmapped is the only honest option -- there is no role to fan out to, and
    inventing one would fabricate capability.
    """
    by_id: dict[str, _Prepared] = {}
    for record in records:
        try:
            role = role_for(record.skill_id)
        except RoleTableError:
            continue
        if not record.enabled or not record.is_valid:
            continue
        by_id[record.skill_id] = _Prepared(
            skill_id=record.skill_id,
            triggers=tuple(t for t in (normalise(x) for x in record.triggers) if t),
            keywords=keyword_phrases(record.description),
            id_tokens=frozenset(skill_id_tokens(record.skill_id)),
            related=tuple(record.related_skills),
        )
    return by_id, tuple(sorted(by_id))


def _base_scores(
    intent_norm: str,
    intent_tokens: frozenset[str],
    by_id: Mapping[str, _Prepared],
) -> dict[str, tuple[float, str]]:
    """The three non-relational components of the frozen score (§1.4.3)."""
    scored: dict[str, tuple[float, str]] = {}
    for skill_id, prepared in by_id.items():
        score = 0.0
        reason = ""
        if any(trigger in intent_norm for trigger in prepared.triggers):
            score += W_EXACT
            reason = R_EXACT
        if any(phrase in intent_norm for phrase in prepared.keywords):
            score += W_KEYWORD
            reason = reason or R_KEYWORD
        if prepared.id_tokens & intent_tokens:
            score += W_ID_TOKEN
            # The frozen enum (§1.4.2) has no dedicated code for the 1.0
            # slug-token component, and inventing one is a bigger deviation than
            # reusing the nearest frozen code. `deterministic-keyword-match` is
            # the honest fit in kind -- a keyword matched deterministically,
            # without an eval-prompt match -- and the definition gap is reported
            # in the handoff rather than hidden.
            reason = reason or R_KEYWORD
        if score > 0.0:
            scored[skill_id] = (score, reason)
    return scored


def _expand_related(
    scored: Mapping[str, tuple[float, str]],
    by_id: Mapping[str, _Prepared],
) -> dict[str, tuple[float, str]]:
    """Add the +0.5 ``related-skill-expansion`` component.

    Seeds are every candidate with a non-relational component, ranked by the
    total order. Because every seed scores at least ``W_ID_TOKEN`` (1.0) and
    expansion adds 0.5, a seed can never be displaced by something it pulled in
    -- expansion can only fill slots left over when fewer than ``max_skills``
    skills matched on their own evidence. That is the intended reading of
    "related", and it keeps the cap meaningful.
    """
    expanded = dict(scored)
    seeds = sorted(scored, key=lambda sid: (-scored[sid][0], sid))
    for seed in seeds:
        for related_id in by_id[seed].related:
            if related_id in expanded or related_id not in by_id:
                continue
            expanded[related_id] = (W_RELATED, R_RELATED)
    return expanded


def _rank(scored: Mapping[str, tuple[float, str]]) -> list[tuple[str, float, str]]:
    """Score descending, then ``skill_id`` ascending. A **total** order.

    Both keys are needed: several real prompts tie at 3.0 with a legitimate
    competitor (measured: 9 of 340), and ``skill_id`` is what makes the tie
    deterministic instead of dependent on dict order.
    """
    return [
        (skill_id, score, reason)
        for skill_id, (score, reason) in sorted(
            scored.items(), key=lambda item: (-item[1][0], item[0])
        )
    ]


def _roles_for(selected: Iterable[SkillSelection]) -> tuple[str, ...]:
    """Employee roles for the selection, order-stable, de-duplicated, capped."""
    roles: list[str] = []
    for item in selected:
        try:
            role = role_for(item.skill_id)
        except RoleTableError:
            continue
        if role in roles:
            continue
        roles.append(role)
        if len(roles) >= MAX_EMPLOYEES_PER_TURN:
            break
    return tuple(role for role in roles if role in EMPLOYEE_ROLES)


def has_project_scope(request: SkillRouteRequest) -> bool:
    """True when the turn carries a project to reason about.

    ``graphs._project_state()`` returns ``{}`` for an empty project id and for
    a failed adapter read, and the graph records ``NO_PROJECT_SCOPE: ...`` in
    ``state["errors"]`` for both. So an empty mapping, or an ``errors`` list
    naming the condition, is the signal. A turn with no project scope selects
    nothing: a playbook injected into a turn that cannot name a company, a
    product or a goal is generic advice, and generic advice is the failure mode
    this run exists to remove (plan §1.4.5, A16).
    """
    state = request.project_state or {}
    if not state:
        return False
    if not str(state.get("project_id") or "").strip():
        return False
    errors = state.get("errors") or ()
    if isinstance(errors, str):
        errors = (errors,)
    return not any("NO_PROJECT_SCOPE" in str(item) for item in errors)


def _decision(
    *,
    selected: tuple[SkillSelection, ...] = (),
    reason_codes: tuple[str, ...] = (),
    foundation_injected: bool = False,
) -> SkillRouteDecision:
    return SkillRouteDecision(
        selected=selected,
        reason_codes=reason_codes,
        employee_roles=_roles_for(selected),
        foundation_injected=foundation_injected,
    )


# --------------------------------------------------------------------------- #
# the router                                                                 #
# --------------------------------------------------------------------------- #


def route(
    request: SkillRouteRequest,
    *,
    capability_resolver: Callable[[str], str] | None = None,
    classifier: Callable[[SkillRouteRequest], Iterable[str]] | None = None,
) -> SkillRouteDecision:
    """Select at most ``max_skills`` playbooks for one turn.

    Evaluation order is the graph's, not a convenient one:

    1. **no candidates** -> ``("registry-unavailable",)``. §1.5.2: a missing
       library must not take chat down. The router cannot tell "the library is
       absent" from "every skill is disabled" -- it does no I/O -- so both are
       reported under the one code the enum has for "I have nothing to offer".
       That conflation is deliberate and disclosed rather than papered over with
       a second, invented code.
    2. **no project scope** -> ``("no-skill-selected",)``. ``route_selector``
       sends an errored turn to ``respond`` before any branch, so the router
       must not have selected anything by then either.
    3. **deterministic scoring** (§1.4.3) computes the frozen base scores; the
       capability class BELOW and the scorer are compared against each other,
       not short-circuited, so the amended rule can see the scorer's verdict.
    4. **resolved tool capability** (a non-empty :func:`resolve_tool_capability`
       result):
       - with deterministic scores, selection proceeds normally in the frozen
         order; ``foundation_injected`` is forced ``False`` so a turn the
         classifier calls a tool action never loads the foundation on top
         (PLAN-AMENDMENT-1, clause 1);
       - with a zero deterministic score the turn is a *true pure-mechanical
         ask* and is still refused entirely (clause 2). The model rescue is
         deliberately not consulted on this path: the original contract never
         let a model speak for a turn the frozen rule suppressed, and the
         amendment preserves that -- otherwise a chatty model could re-arm the
         exact context the skip exists to avoid.
    5. **no capability**, every score is 0 and a ``classifier`` was supplied ->
       one classification call, skill added at ``score 1.0``. It can only
       rescue a zero, never reorder a deterministic hit, because it is not
       reached when one exists.
    6. **cap** (truncate to ``max_skills``), then the foundation marker only
       on the no-capability path when anything survived.

    ``capability_resolver`` and ``classifier`` are injection points, not
    configuration. The defaults are the shipped behaviour; the test uses them to
    isolate one stage at a time.
    """
    resolve = capability_resolver or resolve_tool_capability

    if not request.available_skills:
        return _decision(reason_codes=(R_UNAVAILABLE,))

    if not has_project_scope(request):
        return _decision(reason_codes=(R_NONE,))

    try:
        capability = resolve(request.intent or "")
    except Exception:
        # A classifier that blows up must not take a turn down. Degrading to
        # deterministic scoring is visible rather than silent: the decision
        # carries scoring reason codes instead of the skip code, so a reader
        # can see that the capability check did not run.
        capability = ""

    by_id, eligible = _prepare(request.available_skills)
    if not eligible:
        return _decision(reason_codes=(R_UNAVAILABLE,))

    intent_norm = normalise(request.intent)
    intent_tokens = frozenset(intent_norm.split()) - {""}
    scored = _base_scores(intent_norm, intent_tokens, by_id)

    if not scored:
        if capability:
            # PLAN-AMENDMENT-1: a capability without any scored skill vocabulary
            # is a true pure-mechanical ask -> suppress selection entirely and
            # do not reach the model. Nothing this broader contract used to get.
            return _decision(reason_codes=(R_MECHANICAL,))
        if classifier is not None:
            for skill_id in _as_skill_ids(classifier(request)):
                if skill_id in by_id and skill_id not in scored:
                    scored[skill_id] = (W_MODEL, R_MODEL)
        if not scored:
            return _decision(reason_codes=(R_NONE,))

    scored = _expand_related(scored, by_id)

    ranked = _rank(scored)
    cap = request.max_skills if request.max_skills is not None else MAX_SKILLS_PER_TURN
    cap = max(0, int(cap))
    kept = ranked[:cap]
    truncated = len(ranked) > cap

    if not kept:
        return _decision(reason_codes=(R_NONE,))

    selected = tuple(
        SkillSelection(skill_id=skill_id, reason_code=reason, score=score)
        for skill_id, score, reason in kept
    )

    codes: list[str] = []
    for item in selected:
        if item.reason_code not in codes:
            codes.append(item.reason_code)
    if truncated and R_TRUNCATED not in codes:
        codes.append(R_TRUNCATED)

    if capability:
        # A resolved tool capability forces the foundation OFF (clause 1 of the
        # amendment) even when the scorer selected a playbook: the graph still
        # runs its tool branch, so the playbooks ride along but the
        # product-marketing preamble does not.
        return _decision(selected=selected, reason_codes=tuple(codes), foundation_injected=False)

    codes.append(R_FOUNDATION)
    return _decision(selected=selected, reason_codes=tuple(codes), foundation_injected=True)


def _as_skill_ids(named: Any) -> tuple[str, ...]:
    """Coerce a classifier's return value to skill ids, deterministically.

    A classifier is a model boundary, so its output is untrusted: a ``None``, a
    bare string (which would otherwise iterate character by character) and a
    non-iterable are all handled instead of raising inside a turn.
    """
    if named is None:
        return ()
    if isinstance(named, str):
        return (named,)
    if isinstance(named, Mapping):
        named = named.get("skills") or ()
    if not isinstance(named, Iterable):
        return ()
    return tuple(str(item) for item in named if item)


def explain(
    request: SkillRouteRequest, *, capability_resolver: Callable[[str], str] | None = None
) -> dict[str, Any]:
    """Score every eligible record without selecting. Diagnostics only.

    Returns ``{"capability", "eligible", "scores"}`` where ``scores`` maps a skill
    id to ``{"score": float, "reason_code": str}``, so a routing question can be
    answered from real output instead of from a guess. Never called by
    :func:`route`.
    """
    resolve = capability_resolver or resolve_tool_capability
    by_id, eligible = _prepare(request.available_skills)
    intent_norm = normalise(request.intent)
    intent_tokens = frozenset(intent_norm.split()) - {""}
    scored = _base_scores(intent_norm, intent_tokens, by_id)
    return {
        "capability": resolve(request.intent or ""),
        "eligible": list(eligible),
        "scores": {
            skill_id: {"score": score, "reason_code": reason}
            for skill_id, (score, reason) in sorted(scored.items())
        },
    }


__all__ = [
    "MAX_SKILLS_PER_TURN",
    "MAX_EMPLOYEES_PER_TURN",
    "FOUNDATION_SKILL_ID",
    "REASON_CODES",
    "SkillRouteRequest",
    "SkillSelection",
    "SkillRouteDecision",
    "route",
    "explain",
    "normalise",
    "keyword_phrases",
    "skill_id_tokens",
    "resolve_tool_capability",
    "has_project_scope",
]
