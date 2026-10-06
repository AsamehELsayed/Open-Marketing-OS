"""The 340-prompt routing acceptance matrix (plan §6.1, W5).

The measured headline, from real decisions over the real corpus
---------------------------------------------------------------
Run this file with ``-s`` to see the matrix report printed by
``test_routing_matrix_coverage_is_printed_and_measured``::

    skills in library      : 50
    prompts from evals.json: 340
    SELECTED-OWN           : 340
    COVERAGE               : 100.00 %
    -- of which with a resolved tool capability: 6 (playbook kept, foundation off)
    pure-mechanical skips in corpus: 0
    own-first (rank 1)     : 331 (97.35 %)

**Why the number changed from the first attempt.** The first W5 wave shipped
the originally-frozen §1.4.5 skip *unconditionally* and measured 334/340 =
98.24 %, because 6 of the 340 upstream prompts resolve to a tool capability.
That measurement reached the orchestrator as a blocker and was ratified as
**PLAN-AMENDMENT-1** (``run.json``: ``contract_deviations_ratified``; the
amendment is also recorded verbatim in plan §1.4.5): a resolved tool capability
forces ``foundation_injected=False`` but suppresses skill selection **only**
when the deterministic scorer would also select nothing. Under that rule every
prompt reaches its playbook (coverage 100.00 %, measured above) and a true
pure-mechanical ask -- one that names a tool AND carries no skill vocabulary at
all -- still skips entirely, foundation included.

Nothing here papers over the number. The suite does not assert "there are no
misses" as a wish: the coverage is computed from real decisions over real files,
printed, and then compared against the amended target; the count 340 is a
property of the ``evals.json`` files, so deleting or skipping a prompt is
visible. If a future re-pin or classifier change drops coverage below 100 %,
the matrix tests fail with the prompt text in the message -- a miss is reported
with the prompt, never quietly removed.

The rest of the suite pins the router's other frozen behaviour: the amended
mechanical skip, the cap and its reason code, the foundation rule, project
scope, byte-level determinism, the model rescue being consultable only for a
zero on the knowledge path, and skills-are-knowledge / tools-are-action (§1.4.6).
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.services.skills import (
    EMPLOYEE_ROLES,
    SKILL_ROLE_MAP,
    MarketingSkillRecord,
    load_registry,
    records as records_module,
)
from app.services.skills import router as router_module
from app.services.skills.router import (
    FOUNDATION_SKILL_ID,
    MAX_EMPLOYEES_PER_TURN,
    MAX_SKILLS_PER_TURN,
    R_EXACT,
    R_FOUNDATION,
    R_KEYWORD,
    R_MECHANICAL,
    R_MODEL,
    R_NONE,
    R_RELATED,
    R_TRUNCATED,
    R_UNAVAILABLE,
    REASON_CODES,
    SkillRouteDecision,
    SkillRouteRequest,
    has_project_scope,
    keyword_phrases,
    normalise,
    resolve_tool_capability,
    route,
)

#: The corpus size this run pins. Asserted here, never used in product logic --
#: and ``test_no_hardcoded_corpus_size_in_the_skills_package`` proves the
#: shipped package does not contain it either.
EXPECTED_EVAL_COUNT = 340

#: A realistic, non-empty project scope. Empty scope is the NO_PROJECT_SCOPE
#: case and is exercised separately.
PROJECT_STATE = {
    "project_id": "acceptance-fixture",
    "name": "Acceptance Fixture Co",
    "website": "https://fixture.example",
    "goal": "prove the router",
    "instagram_handle": "",
    "social_accounts": [],
    "aliases": [],
    "memories": [],
}

#: One real library load, shared by every test. ``disabled=""`` keeps the
#: registry off the database: this suite must be runnable with no DB at all.
REGISTRY = load_registry(disabled="")
SKILLS: tuple[MarketingSkillRecord, ...] = REGISTRY.records

_UNSET = object()


# --------------------------------------------------------------------------- #
# the corpus, read from the files                                             #
# --------------------------------------------------------------------------- #


def read_eval_prompts() -> list[tuple[str, int, str]]:
    """``(skill_id, eval_id, prompt)`` for every prompt in every evals.json.

    Read straight off disk with ``json`` -- deliberately *not* through
    ``MarketingSkillRecord.triggers``.  If the loader ever stopped mining
    ``evals.json``, or mined it wrongly, the file count would stop matching
    ``sum(eval_count)`` and the equality assertions below would fire.  That is
    the point of asserting 340 from the files rather than from the records.
    """
    rows: list[tuple[str, int, str]] = []
    for record in sorted(SKILLS, key=lambda item: item.skill_id):
        path = Path(REGISTRY.root) / record.skill_id / "evals" / "evals.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        entries = document["evals"]
        assert isinstance(entries, list), path
        for entry in entries:
            assert isinstance(entry, dict), path
            prompt = entry.get("prompt")
            assert isinstance(prompt, str) and prompt.strip(), (path, entry.get("id"))
            rows.append((record.skill_id, int(entry["id"]), prompt.strip()))
    return rows


EVAL_PROMPTS = read_eval_prompts()


def case_id(skill_id: str, eval_id: int) -> str:
    return f"{skill_id}#{eval_id:02d}"


PROMPT_BY_CASE: dict[str, str] = {
    case_id(skill_id, eval_id): prompt for skill_id, eval_id, prompt in EVAL_PROMPTS
}
MATRIX_IDS = list(PROMPT_BY_CASE)

#: Which of those prompts the *frozen* classifier resolves to a tool
#: capability. Under PLAN-AMENDMENT-1 these are no longer skipped: they are
#: scored, so their own playbook is selected, but the foundation is forced off.
CAPABILITY_INTENTS: dict[str, str] = {
    key: capability
    for key, prompt in PROMPT_BY_CASE.items()
    if (capability := resolve_tool_capability(prompt))
}

#: The capability-classified prompts, by name, as measured at amendment time
#: (run.json ``contract_deviations_ratified`` PLAN-AMENDMENT-1). Same set the
#: first wave pinned as misses; the amendment converts them to selections.
EXPECTED_CAPABILITY_PROMPTS = {
    "ai-seo#02",
    "ai-seo#05",
    "aso#01",
    "schema#06",
    "seo-audit#01",
    "site-architecture#06",
}


def make_request(
    intent: str,
    *,
    project_state: dict | object = _UNSET,
    available_skills: tuple[MarketingSkillRecord, ...] = SKILLS,
    max_skills: int = MAX_SKILLS_PER_TURN,
) -> SkillRouteRequest:
    return SkillRouteRequest(
        intent=intent,
        project_state=PROJECT_STATE if project_state is _UNSET else project_state,  # type: ignore[arg-type]
        conversation_context=(),
        task={"id": "q0", "query": intent},
        available_skills=available_skills,
        max_skills=max_skills,
    )


def fingerprint(decision: SkillRouteDecision) -> bytes:
    """Canonical bytes for a decision, for byte-for-byte equality assertions."""
    return json.dumps(
        decision.to_dict(), sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")


def no_capability(_text: str) -> str:
    """A resolver that reports "not a tool action".

    Used wherever a test is about scoring rather than about §1.4.5, so the
    measurement under test is the one being asserted.
    """
    return ""


_MATRIX: list[tuple[str, SkillRouteDecision]] | None = None


def matrix() -> list[tuple[str, SkillRouteDecision]]:
    """One real decision per prompt, computed once.

    Cached because the suite scores the corpus six times over and the router is
    deterministic -- which
    ``test_determinism_is_byte_identical_for_every_prompt`` proves rather than
    assumes, so the cache cannot hide a non-determinism bug.
    """
    global _MATRIX
    if _MATRIX is None:
        _MATRIX = [
            (key, route(make_request(prompt))) for key, prompt in PROMPT_BY_CASE.items()
        ]
    return _MATRIX


def first_prompt(skill_id: str) -> str:
    for owner, _, prompt in EVAL_PROMPTS:
        if owner == skill_id:
            return prompt
    raise AssertionError(skill_id)


def first_knowledge_prompt(skill_id: str) -> str:
    """First prompt of ``skill_id`` that does **not** resolve a tool capability.

    Used by tests that are about scoring, the cap and project scope, so the
    capability branch of the amended rule never quietly decides their outcome.
    """
    for owner, eval_id, prompt in EVAL_PROMPTS:
        if owner == skill_id and case_id(owner, eval_id) not in CAPABILITY_INTENTS:
            return prompt
    raise AssertionError(skill_id)


# --------------------------------------------------------------------------- #
# the corpus itself                                                           #
# --------------------------------------------------------------------------- #


def test_library_loads_with_zero_missing_and_zero_invalid():
    """``MISSING: 0`` / ``INVALID: 0`` are measured by the loader, not asserted."""
    report = REGISTRY.report
    assert report.missing_count == 0, report.summary()
    assert report.invalid_count == 0, (report.summary(), report.invalid)
    assert report.valid_count == report.total, report.summary()
    assert report.file_count > 0
    assert SKILLS, "no skills loaded; the matrix would be vacuous"


def test_eval_corpus_is_340_prompts_read_from_the_files():
    """The denominator is a property of the files, so deletion is visible."""
    assert len(EVAL_PROMPTS) == EXPECTED_EVAL_COUNT, (
        f"expected {EXPECTED_EVAL_COUNT} upstream eval prompts read from "
        f"{len(SKILLS)} evals.json files, found {len(EVAL_PROMPTS)}"
    )
    assert sum(record.eval_count for record in SKILLS) == len(EVAL_PROMPTS)
    assert all(record.eval_count > 0 for record in SKILLS), [
        record.skill_id for record in SKILLS if record.eval_count == 0
    ]
    assert sum(len(record.triggers) for record in SKILLS) == len(EVAL_PROMPTS)


def test_every_trigger_is_a_substring_of_its_own_evals_file():
    """A4, checked against the file: triggers are mined, never authored.

    The contract is ``tuple(dict.fromkeys(p[:TRIGGER_MAX_CHARS] for p in
    prompts))``, so each trigger is either a whole prompt from the file or the
    512-character prefix of one. Anything else would be an authored string.
    """
    for record in SKILLS:
        path = Path(REGISTRY.root) / record.skill_id / "evals" / "evals.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        prompts = {
            str(entry["prompt"]).strip()
            for entry in document["evals"]
            if isinstance(entry, dict) and str(entry.get("prompt") or "").strip()
        }
        assert prompts, path
        for trigger in record.triggers:
            assert trigger in prompts or any(
                prompt[: len(trigger)] == trigger for prompt in prompts
            ), (record.skill_id, trigger[:80])


def test_role_table_covers_every_registered_skill():
    assert set(SKILL_ROLE_MAP) == {record.skill_id for record in SKILLS}
    assert set(SKILL_ROLE_MAP.values()) <= set(EMPLOYEE_ROLES)


# --------------------------------------------------------------------------- #
# the matrix                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", MATRIX_IDS)
def test_routing_matrix(key):
    """One assertion per upstream prompt: the router reaches its own playbook.

    Under PLAN-AMENDMENT-1 there is no escape hatch left. A prompt that
    resolves a tool capability is *scored* now, so its own playbook must still
    appear in a real ``route`` decision; a prompt that scores nothing is
    refused and IS a coverage miss, counted and printed below -- never waived.
    """
    prompt = PROMPT_BY_CASE[key]
    skill_id = key.partition("#")[0]
    decision = route(make_request(prompt))
    assert skill_id in decision.skill_ids, (
        f"{key}: router did not reach {skill_id!r}. "
        f"selected={decision.skill_ids} reason_codes={decision.reason_codes}. "
        f"prompt={prompt!r}"
    )


def test_routing_matrix_coverage_is_printed_and_measured():
    """The headline number, printed, from real decisions over the real corpus.

    The acceptance threshold is 100 % (plan A6, as amended by
    PLAN-AMENDMENT-1). The printed number is *computed* from decisions reached
    here -- at no point does this module print a wished-for constant.
    """
    measured = matrix()
    total = len(measured)
    selected_own = 0
    own_first = 0
    foundation_off = 0
    skips = 0
    misses: list[tuple[str, str, tuple[str, ...]]] = []
    for (key, decision), (_, _, prompt) in zip(measured, EVAL_PROMPTS):
        skill_id = key.partition("#")[0]
        if skill_id in decision.skill_ids:
            selected_own += 1
        else:
            misses.append((key, prompt, decision.reason_codes))
        if decision.skill_ids and decision.skill_ids[0] == skill_id:
            own_first += 1
        if key in CAPABILITY_INTENTS:
            assert decision.foundation_injected is False, (key, decision)
            assert R_FOUNDATION not in decision.reason_codes, (key, decision)
            foundation_off += 1
        if decision.reason_codes == (R_MECHANICAL,):
            skips += 1

    coverage = 100.0 * selected_own / total
    lines = [
        "",
        "=== DEV-008-SKILLS-OPS W5 routing acceptance matrix ===",
        f"  skills in library      : {len(SKILLS)}",
        f"  prompts from evals.json: {total}",
        f"  SELECTED-OWN           : {selected_own}",
        f"  COVERAGE               : {coverage:.2f} %",
        f"  -- capability-forced (foundation off): {foundation_off}",
        f"  pure-mechanical skips  : {skips}",
        f"  OWN-FIRST (rank 1)     : {own_first} ({100.0 * own_first / total:.2f} %)",
        f"  max len(selected)      : {max(len(d.selected) for _, d in measured)}",
        f"  related-expansion used : "
        f"{sum(1 for _, d in measured if any(i.reason_code == R_RELATED for i in d.selected))}",
        "",
        "  misses (prompt text in full, and the reason):",
    ]
    for key, prompt, codes in misses:
        lines.append(f"    - {key}  reasons={codes}")
        lines.append(f"        {prompt}")
    lines.append("=== end matrix report ===")
    print("\n".join(lines))

    # The corpus is the denominator, read from the files; nothing may be
    # deleted or skipped to make the number look better.
    assert total == EXPECTED_EVAL_COUNT
    assert len(misses) == total - selected_own
    assert skips == 0, "an eval prompt still hits the pure-mechanical skip"
    assert set(CAPABILITY_INTENTS) == EXPECTED_CAPABILITY_PROMPTS, (
        "the set of prompts the frozen classifier resolves to a capability "
        "changed; the capability-forced count and the pinned amendment test "
        "must be re-measured, not silently updated"
    )
    assert foundation_off == len(CAPABILITY_INTENTS)
    # The amended acceptance: every prompt routes to its own playbook.
    assert selected_own == total, (
        f"coverage {coverage:.2f}% < 100% -- the misses above name the prompts "
        "and must be reported and fixed, never removed or weaker-asserted"
    )


def test_the_six_capability_prompts_select_their_own_playbook_without_foundation():
    """PLAN-AMENDMENT-1, pinned end to end on the six formerly-lost prompts.

    Each of the six still resolves a tool capability (that fact was the blocker
    in the first wave), yet the deterministic scorer reaches its own playbook,
    so selection happens -- and the foundation is forced off. If the scorer
    ever loses any of the six, the matrix fails above; this test also fails
    individually so the regression is namespaced.
    """
    assert set(CAPABILITY_INTENTS) == EXPECTED_CAPABILITY_PROMPTS, sorted(CAPABILITY_INTENTS)
    for key in sorted(CAPABILITY_INTENTS):
        prompt = PROMPT_BY_CASE[key]
        skill_id = key.partition("#")[0]
        assert CAPABILITY_INTENTS[key], key
        decision = route(make_request(prompt))
        assert skill_id in decision.skill_ids, (key, decision.skill_ids)
        assert decision.selected, (key, decision)
        assert decision.foundation_injected is False, (key, decision)
        assert R_FOUNDATION not in decision.reason_codes, (key, decision.reason_codes)
        assert R_MECHANICAL not in decision.reason_codes, (key, decision.reason_codes)
        assert any(
            item.reason_code == R_EXACT for item in decision.selected if item.skill_id == skill_id
        ) or skill_id == decision.skill_ids[0], (key, decision)


def test_matcher_alone_reaches_340_of_340():
    """Isolation proof: the scorer was never the limitation, before or after.

    With a resolver that reports no capability at all -- i.e. the scoring path
    with no capability branch at all -- the same 340 prompts must still all
    reach their own skill. Selection equality against the real decision is
    asserted separately per prompt by
    :func:`test_capability_changes_only_the_foundation_not_the_selection`.
    """
    misses: list[str] = []
    for skill_id, _, prompt in EVAL_PROMPTS:
        bypassed = route(make_request(prompt), capability_resolver=no_capability)
        if skill_id not in bypassed.skill_ids:
            misses.append(
                f"{skill_id}: {prompt!r} bypassed-selection={bypassed.skill_ids}"
            )
    assert not misses, misses


def test_capability_changes_only_the_foundation_not_the_selection():
    """For the six capability-classified prompts: identical `selected` with and
    without the capability branch. The amendment's whole effect is the
    foundation flag plus the zero-vocabulary skip; scoring is untouched."""
    for key in sorted(CAPABILITY_INTENTS):
        prompt = PROMPT_BY_CASE[key]
        real = route(make_request(prompt))
        bypassed = route(make_request(prompt), capability_resolver=no_capability)
        assert CAPABILITY_INTENTS[key], key
        assert real.selected == bypassed.selected, (key, asdict(real), asdict(bypassed))
        assert real.foundation_injected is False
        assert bypassed.foundation_injected is True


def test_selected_never_exceeds_the_cap_across_the_matrix():
    for key, decision in matrix():
        assert len(decision.selected) <= MAX_SKILLS_PER_TURN, key
        assert set(decision.reason_codes) <= set(REASON_CODES), (key, decision.reason_codes)


def test_every_reason_code_emitted_is_in_the_frozen_enum():
    for key, decision in matrix():
        for code in decision.reason_codes:
            assert code in REASON_CODES, (key, code)
        for item in decision.selected:
            assert item.reason_code in REASON_CODES, (key, item)


def test_determinism_is_byte_identical_for_every_prompt():
    """The same request twice -> the same bytes, over the whole corpus."""
    for key, first in matrix():
        second = route(make_request(PROMPT_BY_CASE[key]))
        assert fingerprint(first) == fingerprint(second), key
    for skill_id, _, prompt in EVAL_PROMPTS:
        left = route(make_request(prompt))
        right = route(make_request(prompt))
        assert fingerprint(left) == fingerprint(right), skill_id
        assert left == right


# --------------------------------------------------------------------------- #
# the cap                                                                     #
# --------------------------------------------------------------------------- #


def test_cap_truncates_to_three_and_reports_it():
    """6+ matching skills -> exactly 3 selected, and ``cap-truncated`` present."""
    owners = ("seo-audit", "cro", "copywriting", "ads", "emails", "pricing")
    intent = " ".join(first_prompt(owner) for owner in owners)
    decision = route(make_request(intent), capability_resolver=no_capability)

    assert len(decision.selected) == MAX_SKILLS_PER_TURN, decision
    assert R_TRUNCATED in decision.reason_codes, decision.reason_codes
    assert decision.foundation_injected is True
    assert set(decision.skill_ids) <= set(owners), decision.skill_ids
    assert len(set(decision.skill_ids)) == MAX_SKILLS_PER_TURN


def test_no_cap_code_when_fewer_than_the_cap_match():
    """One record in the roster means one candidate, so nothing is dropped.

    A single real prompt is not enough for this assertion: related-skill
    expansion legitimately adds candidates, so a one-prompt turn can still
    truncate. Pinning the roster keeps the test about the cap arithmetic.
    """
    only = REGISTRY.get("seo-audit")
    assert only is not None
    decision = route(make_request(first_prompt("seo-audit"), available_skills=(only,)),
                     capability_resolver=no_capability)
    assert decision.skill_ids == ("seo-audit",), decision
    assert R_TRUNCATED not in decision.reason_codes, decision.reason_codes


def test_max_skills_is_honoured_at_one_and_at_zero():
    intent = " ".join(
        first_prompt(o) for o in ("seo-audit", "cro", "copywriting")
    )
    one = route(make_request(intent, max_skills=1), capability_resolver=no_capability)
    assert len(one.selected) == 1
    assert R_TRUNCATED in one.reason_codes
    zero = route(make_request(intent, max_skills=0), capability_resolver=no_capability)
    assert zero.selected == ()
    assert zero.reason_codes == (R_NONE,)
    assert zero.foundation_injected is False


def test_keyword_only_match_uses_the_frozen_keyword_code():
    """A description-clause phrase with no eval prompt behind it: 2.0 only."""
    decision = route(
        make_request("conversion rate optimization"), capability_resolver=no_capability
    )
    assert decision.skill_ids, decision
    assert decision.selected[0].skill_id == "cro"
    assert decision.selected[0].reason_code == R_KEYWORD
    assert decision.selected[0].score == 2.0
    assert R_EXACT not in decision.reason_codes


def test_related_expansion_fills_spare_slots_and_never_outranks_a_seed():
    """Expansion is real: measured over the whole matrix, not just in theory."""
    used = 0
    for key, decision in matrix():
        expansions = [i for i in decision.selected if i.reason_code == R_RELATED]
        if not expansions:
            continue
        used += 1
        seeds = [i for i in decision.selected if i.reason_code != R_RELATED]
        assert seeds, (key, decision)
        for item in expansions:
            assert item.score == 0.5, (key, item)
        assert min(i.score for i in expansions) < min(i.score for i in seeds), key
    assert used > 50, f"related-skill expansion fired on only {used} prompts"
    # Cross-references come from the record, never from a table in this module.
    cro = REGISTRY.get("cro")
    assert cro is not None and "signup" in cro.related_skills


# --------------------------------------------------------------------------- #
# the mechanical skip (PLAN-AMENDMENT-1)                                      #
# --------------------------------------------------------------------------- #


def test_pure_tool_action_selects_nothing_and_injects_no_foundation():
    """§1.4.5 as amended, through the real classifier -- not a stub.

    These probes are pure in the *amended* sense: the classifier resolves a
    capability AND the frozen scorer scores nothing, so the intent carries no
    skill vocabulary at all. A probe that names a methodology word
    ("audit", "pricing") is deliberately excluded from this block -- it would
    not be pure, and the amendment routes it to the playbook instead.
    """
    for text, capability in (
        ("scrape the instagram account @somebrand", "instagram_public_profile"),
        ("check instagram @acme", "instagram_public_profile"),
        ("/crawl https://example.com", "website_crawl"),
        ("/fetch https://example.com", "website_fetch"),
    ):
        assert resolve_tool_capability(text) == capability, text
        decision = route(make_request(text))
        assert decision.selected == (), (text, decision)
        assert decision.employee_roles == (), (text, decision)
        assert decision.foundation_injected is False, (text, decision)
        assert decision.reason_codes == (R_MECHANICAL,), (text, decision)


def test_methodology_worded_capability_still_gets_its_playbook():
    """The other half of the amendment, directly: a capability-shaped ask that
    names a methodology keeps its playbook -- selected normally, foundation
    forced off. Contrast with the pure probes in the test above."""
    for text, capability in (
        ("/audit https://example.com", "website_marketing_audit"),
        ("crawl https://example.com/pricing", "website_crawl"),
    ):
        assert resolve_tool_capability(text) == capability, text
        decision = route(make_request(text))
        assert decision.selected, (text, decision)
        assert decision.foundation_injected is False, (text, decision)
        assert R_FOUNDATION not in decision.reason_codes, (text, decision)
        assert decision.reason_codes != (R_MECHANICAL,), (text, decision)


def test_capability_model_rescue_is_not_consulted_for_a_pure_mechanical_ask():
    """A zero-vocabulary capability turn must not reach the model rescue.

    The unconditional skip never consulted the model, and the amendment keeps
    that: a resolved capability plus a zero scorer suppresses outright, rather
    than being rescued into a playbook. A chatty model must not re-arm the
    foundation context the skip exists to avoid."""
    calls: list[str] = []

    def classifier(request: SkillRouteRequest):
        calls.append(request.intent)
        return ["copywriting"]

    pure = "scrape the instagram account @somebrand"
    decision = route(make_request(pure, max_skills=MAX_SKILLS_PER_TURN), classifier=classifier)
    assert resolve_tool_capability(pure), pure
    assert decision.reason_codes == (R_MECHANICAL,), decision
    assert decision.selected == ()
    assert decision.foundation_injected is False
    assert calls == [], "the model must not rescue a resolved capability ask"


def test_mechanical_skip_uses_the_graph_condition_not_a_local_heuristic():
    """The capability detection must agree with the branch the graph takes."""
    from app.graphs.account_manager import classify_to_route

    probes = [
        "scrape the instagram account @somebrand",
        "/audit https://example.com",
        "crawl https://example.com/pricing",
        "/crawl https://example.com",
        first_prompt("seo-audit"),
        first_prompt("copywriting"),
        "write a LinkedIn post about our pricing change",
    ]
    for text in probes:
        assert bool(resolve_tool_capability(text)) is (
            classify_to_route(text) == "tool_capability"
        ), text


def test_a_conversational_turn_is_not_treated_as_mechanical():
    decision = route(make_request(first_prompt("copywriting")))
    assert decision.reason_codes != (R_MECHANICAL,)
    assert decision.selected
    assert decision.foundation_injected is True


def test_mechanical_resolver_failure_degrades_to_scoring_rather_than_raising():
    """A broken classifier must not take a turn down."""

    def explode(_text: str) -> str:
        raise RuntimeError("capability resolver blew up")

    decision = route(make_request(first_prompt("copywriting")), capability_resolver=explode)
    assert decision.selected
    assert R_MECHANICAL not in decision.reason_codes


def test_pure_mechanical_with_foundation_forcing_only_changes_zero_vocabulary_turns():
    """The boundary of the amended rule, straight through the real router:
    scrape-nothing → skip; scrape + pricing word → playbook, no foundation."""
    pure = "scrape the instagram account @somebrand"
    d_pure = route(make_request(pure))
    assert d_pure.reason_codes == (R_MECHANICAL,) and d_pure.selected == ()
    assert d_pure.foundation_injected is False

    noisy = "scrape the instagram account @somebrand and tell me about pricing"
    d_noisy = route(make_request(noisy))
    assert resolve_tool_capability(noisy), noisy
    assert d_noisy.selected, d_noisy
    assert "pricing" in d_noisy.skill_ids
    assert d_noisy.foundation_injected is False
    assert R_MECHANICAL not in d_noisy.reason_codes


# --------------------------------------------------------------------------- #
# project scope                                                               #
# --------------------------------------------------------------------------- #


def test_no_project_scope_selects_nothing():
    """A16 / §1.4.5. Identical intent; only the scope differs."""
    intent = first_prompt("seo-audit")
    with_scope = route(make_request(intent), capability_resolver=no_capability)
    assert with_scope.selected, "fixture is wrong: this prompt must match"

    for state in ({}, {"project_id": ""}, dict(PROJECT_STATE, project_id="  ")):
        decision = route(make_request(intent, project_state=state))
        assert decision.selected == (), (state, decision)
        assert decision.employee_roles == (), (state, decision)
        assert decision.foundation_injected is False, (state, decision)
        assert decision.reason_codes == (R_NONE,), (state, decision)

    errored = dict(PROJECT_STATE, errors=["NO_PROJECT_SCOPE: project_id is required"])
    decision = route(make_request(intent, project_state=errored))
    assert decision.selected == ()
    assert decision.reason_codes == (R_NONE,)


def test_has_project_scope_reads_the_graphs_project_state_shape():
    assert has_project_scope(make_request("x")) is True
    assert has_project_scope(make_request("x", project_state={})) is False
    assert has_project_scope(
        make_request("x", project_state=dict(PROJECT_STATE, errors=["NO_PROJECT_SCOPE: x"]))
    ) is False
    assert has_project_scope(
        make_request("x", project_state=dict(PROJECT_STATE, errors=["something else"]))
    ) is True


def test_selection_never_bypasses_the_pipeline():
    """The decision carries playbooks only -- no route, no tool, no capability."""
    intent = " ".join(
        first_knowledge_prompt(o) for o in ("seo-audit", "cro", "copywriting")
    )
    decision = route(make_request(intent), capability_resolver=no_capability)
    assert len(decision.selected) == MAX_SKILLS_PER_TURN
    assert decision.foundation_injected is True
    assert set(decision.to_dict()) == {
        "selected",
        "reason_codes",
        "employee_roles",
        "foundation_injected",
    }
    blob = json.dumps(decision.to_dict()).lower()
    for forbidden in ("capability", "tool_id", "provider", "handler", "side_effect"):
        assert forbidden not in blob, forbidden


# --------------------------------------------------------------------------- #
# the foundation rule                                                         #
# --------------------------------------------------------------------------- #


def test_foundation_marker_appears_exactly_when_something_is_selected_and_no_capability():
    """The amended foundation rule, measured across the whole matrix:
    - a capability-classified prompt never injects the foundation, selected or
      not;
    - otherwise the marker appears exactly when something is selected, always
      last in ``reason_codes``."""
    from app.services.skills.router import R_FOUNDATION

    for key, decision in matrix():
        if key in CAPABILITY_INTENTS:
            assert decision.foundation_injected is False, key
            assert R_FOUNDATION not in decision.reason_codes, key
            continue
        if decision.selected:
            assert decision.foundation_injected is True, key
            assert decision.reason_codes[-1] == R_FOUNDATION, key
        else:
            assert decision.foundation_injected is False, key
            assert R_FOUNDATION not in decision.reason_codes, key
    assert set(CAPABILITY_INTENTS) == EXPECTED_CAPABILITY_PROMPTS


def test_foundation_skill_is_named_and_real():
    record = REGISTRY.get(FOUNDATION_SKILL_ID)
    assert record is not None and record.is_valid
    assert FOUNDATION_SKILL_ID in SKILL_ROLE_MAP


# --------------------------------------------------------------------------- #
# registry unavailable, disabled, invalid                                     #
# --------------------------------------------------------------------------- #


def test_no_candidates_is_registry_unavailable_and_the_turn_proceeds():
    decision = route(make_request(first_knowledge_prompt("seo-audit"), available_skills=()),
                     capability_resolver=no_capability)
    assert decision.reason_codes == (R_UNAVAILABLE,)
    assert decision.selected == ()
    assert decision.employee_roles == ()
    assert decision.foundation_injected is False


def test_disabled_skills_are_not_candidates():
    disabled = REGISTRY.with_disabled("seo-audit")
    record = disabled.get("seo-audit")
    assert record is not None and record.enabled is False
    decision = route(
        make_request(first_knowledge_prompt("seo-audit"), available_skills=disabled.records),
        capability_resolver=no_capability,
    )
    assert "seo-audit" not in decision.skill_ids, decision.skill_ids


def test_invalid_records_are_never_injected():
    """An ``invalid`` record is not a candidate, so the same slug cannot match."""
    cro = REGISTRY.get("cro")
    assert cro is not None
    broken = replace(cro, validation_status="invalid", enabled=True)
    others = tuple(r for r in SKILLS if r.skill_id != "cro")
    decision = route(make_request(first_knowledge_prompt("cro"),
                                  available_skills=(broken,) + others),
                     capability_resolver=no_capability)
    assert "cro" not in decision.skill_ids, decision.skill_ids
    # And the same intent with the valid record does reach it, so the assertion
    # above is about validation_status and not about a dead prompt.
    assert "cro" in route(make_request(first_knowledge_prompt("cro"), available_skills=SKILLS),
                          capability_resolver=no_capability).skill_ids


# --------------------------------------------------------------------------- #
# the model rescue                                                            #
# --------------------------------------------------------------------------- #


ZERO_INTENT = "qwerty zxcvb plugh"


def test_model_rescues_a_zero_but_never_reorders_a_hit():
    calls: list[str] = []

    def classifier(request: SkillRouteRequest):
        calls.append(request.intent)
        return ["pricing"]

    assert route(make_request(ZERO_INTENT)).reason_codes == (R_NONE,)

    rescued = route(make_request(ZERO_INTENT), classifier=classifier)
    assert calls == [ZERO_INTENT], "exactly one classification call"
    # Expansion may fill the spare slots behind the model's pick; what matters
    # is that the named skill leads and carries the model's reason code.
    assert rescued.skill_ids[0] == "pricing", rescued.skill_ids
    assert rescued.selected[0].reason_code == R_MODEL
    assert rescued.selected[0].score == 1.0
    assert R_FOUNDATION in rescued.reason_codes

    # A deterministic hit: the model is never consulted, so it cannot reorder.
    calls.clear()
    hit = route(make_request(first_knowledge_prompt("cro")), classifier=classifier)
    assert calls == [], "the model must not be consulted when a score is non-zero"
    assert "cro" in hit.skill_ids, hit.skill_ids
    own = [item for item in hit.selected if item.skill_id == "cro"]
    assert own and own[0].reason_code == R_EXACT, hit.selected


def test_model_cannot_inject_a_disabled_or_unknown_skill():
    disabled = REGISTRY.with_disabled("pricing").records
    decision = route(
        make_request(ZERO_INTENT, available_skills=disabled),
        classifier=lambda _r: ["pricing", "not-a-skill", ""],
    )
    assert decision.selected == (), decision


def test_model_return_value_shapes_are_all_tolerated():
    """A model boundary is untrusted input; every shape is handled, none raises."""
    for value, expected_first in (
        (None, None),
        ("seo-audit", "seo-audit"),
        ({"skills": ["seo-audit"]}, "seo-audit"),
        ([], None),
        (("seo-audit",), "seo-audit"),
        (7, None),
    ):
        decision = route(make_request(ZERO_INTENT), classifier=lambda _r, v=value: v)
        assert decision.skill_ids[:1] == ((expected_first,) if expected_first else ()), (
            value,
            decision,
        )


# --------------------------------------------------------------------------- #
# employee roles                                                              #
# --------------------------------------------------------------------------- #


def test_employee_roles_are_a_stable_subset_of_the_frozen_enum():
    for key, decision in matrix():
        assert set(decision.employee_roles) <= set(EMPLOYEE_ROLES), key
        assert len(decision.employee_roles) <= MAX_SKILLS_PER_TURN, key
        assert len(decision.employee_roles) <= MAX_EMPLOYEES_PER_TURN, key
        assert len(set(decision.employee_roles)) == len(decision.employee_roles), key
        again = route(make_request(PROMPT_BY_CASE[key]))
        assert again.employee_roles == decision.employee_roles, key
        for role in decision.employee_roles:
            assert any(
                SKILL_ROLE_MAP[item.skill_id] == role for item in decision.selected
            ), (role, decision)


# --------------------------------------------------------------------------- #
# §1.4.6 -- skills are knowledge, tools are action                             #
# --------------------------------------------------------------------------- #


def test_records_carry_no_tool_vocabulary():
    """§1.4.6 is about *fields*, not about prose.

    ``analytics`` legitimately contains the words "UTM parameters" in its
    description, so scanning values would be a false positive. The claim is that
    the record has no tool-shaped field, and that is a claim about key names.
    """
    forbidden = {"parameters", "handler", "permission_level", "side_effect", "tool_id"}
    fields = set(MarketingSkillRecord.__dataclass_fields__)
    assert not (fields & forbidden), fields & forbidden
    for record in SKILLS:
        keys = set(record.to_dict())
        assert not (keys & forbidden), (record.skill_id, keys & forbidden)
        assert keys == fields, (record.skill_id, keys ^ fields)


def test_routing_and_validation_reason_codes_are_disjoint_vocabularies():
    assert not (set(REASON_CODES) & set(records_module.REASON_CODES))
    for code in REASON_CODES:
        assert code == code.lower(), code
    for code in records_module.REASON_CODES:
        assert code == code.upper(), code


# --------------------------------------------------------------------------- #
# no hardcoded counts in product logic                                        #
# --------------------------------------------------------------------------- #


def test_no_hardcoded_corpus_size_in_the_skills_package():
    """A1, checked on the AST so prose may discuss a number without branching.

    No integer literal equal to the skill count or the prompt count may appear
    anywhere in ``app/services/skills/``.  A ``== 50`` branch is a library that
    silently stops covering every skill after the next re-pin; the manifest
    count is data and is compared, never hardcoded.
    """
    banned = {50, EXPECTED_EVAL_COUNT}
    package = Path(router_module.__file__).parent
    offenders: list[str] = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, int)
                and not isinstance(node.value, bool)
                and node.value in banned
            ):
                offenders.append(f"{path.name}:{node.lineno} literal {node.value}")
    assert not offenders, offenders


def test_router_source_has_no_equality_against_a_literal_count():
    source = Path(router_module.__file__).read_text(encoding="utf-8")
    assert "== 50" not in source
    assert "== 340" not in source
    assert "!= 50" not in source


# --------------------------------------------------------------------------- #
# the matcher itself                                                          #
# --------------------------------------------------------------------------- #


def test_normalise_implements_the_frozen_rule_independently():
    def reference(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()

    for text in ("Hello, World!", "A/B test", "--", "", "Ünïcodé 42", "  spaced  out  "):
        assert normalise(text) == reference(text), text


def test_keyword_extraction_survives_intra_word_apostrophes():
    """Regression pin for a real bug in the obvious regex.

    ``'([^']+)'`` shreds ``'users aren't activating,'`` and
    ``'this page isn't converting,'``.  The anchored extractor must keep them
    whole, because losing them loses real trigger phrases.
    """
    onboarding = REGISTRY.get("onboarding")
    cro = REGISTRY.get("cro")
    assert onboarding is not None and cro is not None
    assert "users aren t activating" in keyword_phrases(onboarding.description)
    assert "improve conversions" in keyword_phrases(cro.description)
    for phrases in (
        keyword_phrases(onboarding.description),
        keyword_phrases(cro.description),
    ):
        assert phrases, "extraction produced nothing"
        assert all(len(phrase) >= 2 for phrase in phrases), phrases
        assert not {"t", "isn", "don", "aren"} & set(phrases), phrases


def test_keyword_extraction_is_measured_against_the_real_descriptions():
    clause = re.compile(r"also\s+use\s+when", re.I)
    with_clause = [r for r in SKILLS if clause.search(r.description)]
    without = sorted(
        r.skill_id for r in SKILLS if not clause.search(r.description)
    )
    assert len(with_clause) == 47, len(with_clause)
    assert without == ["co-marketing", "community-marketing", "customer-research"]
    for record in with_clause:
        assert keyword_phrases(record.description), record.skill_id
    for skill_id in without:
        record = REGISTRY.get(skill_id)
        assert record is not None
        assert keyword_phrases(record.description) == ()
    total = sum(len(keyword_phrases(r.description)) for r in SKILLS)
    assert total > 300, total


def test_slug_token_split_drops_single_characters():
    assert router_module.skill_id_tokens("copy-editing") == ("copy", "editing")
    assert router_module.skill_id_tokens("seo-audit") == ("seo", "audit")
    assert router_module.skill_id_tokens("cro") == ("cro",)
    assert router_module.skill_id_tokens("a-b") == ()


def test_exact_component_uses_the_frozen_code_and_floor():
    for skill_id, eval_id, prompt in EVAL_PROMPTS:
        decision = route(make_request(prompt), capability_resolver=no_capability)
        own = [item for item in decision.selected if item.skill_id == skill_id]
        assert own, (skill_id, eval_id, prompt)
        assert own[0].reason_code == R_EXACT, (skill_id, eval_id, own[0])
        assert own[0].score >= 3.0, (skill_id, eval_id, own[0])


def test_explain_reports_scores_without_selecting():
    report = router_module.explain(
        make_request(first_prompt("cro")), capability_resolver=no_capability
    )
    assert report["eligible"] == sorted(r.skill_id for r in SKILLS)
    assert report["capability"] == ""
    assert "cro" in report["scores"]
    assert report["scores"]["cro"]["score"] >= 3.0


# --------------------------------------------------------------------------- #
# the model rescue on the knowledge path (unchanged frozen contract)          #
# --------------------------------------------------------------------------- #


def test_model_rescue_still_fires_for_a_zero_knowledge_turn():
    """The knowledge path (no resolved capability) keeps the frozen rescue.

    The rescue is suppressed only on the capability path (see
    :func:`test_capability_model_rescue_is_not_consulted_for_a_pure_mechanical_ask`);
    a plain zero vocabulary turn with no capability behind it must still be
    rescuable, exactly as §1.4.3 froze it."""
    calls: list[str] = []

    def classifier(request: SkillRouteRequest):
        calls.append(request.intent)
        return ["copywriting"]

    decision = route(make_request(ZERO_INTENT), classifier=classifier)
    assert calls == [ZERO_INTENT]
    assert decision.skill_ids[0] == "copywriting"
    assert decision.selected[0].reason_code == R_MODEL
    assert decision.selected[0].score == 1.0
    assert decision.foundation_injected is True
    assert decision.reason_codes[-1] == R_FOUNDATION
