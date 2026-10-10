"""DEV-032 W2: natural-language campaign deliverable production and revision.

Graph-facing. This module decides *what* to generate, builds the scoped evidence
context, calls the existing ModelRouter with the turn's selected provider and
model, validates the result, and hands the batch to W1.

Every database write belongs to ``app.services.campaign_deliverables`` (W1):
``save_generated_batch`` for generated batches and ``revise`` with an expected
version for chat revisions. This module never writes a deliverable row itself.

Project isolation is fail-closed. ``project_id`` is required and is the only
scope used for the Business Profile, the optional Brief, and ``retrieve_scoped``
alike, so a turn can never read another client's facts.

Output checks are deterministic safeguards, not semantic truth proof. A
deliverable must separate persisted profile facts, retrieved evidence,
recommendations, and unknown or unsupported information; persisted-fact values
must match this turn's request or scoped project sources, and evidence claims
must cite identifiers returned by this project's scoped retrieval. Unsupported
high-risk values fail validation before persistence.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

MAX_CONTEXT_EVIDENCE = 8
MAX_EVIDENCE_CHARS = 1200
MAX_CONTENT_CHARS = 40_000
MAX_ITEMS = 5
MAX_TITLE_CHARS = 200
MAX_PLATFORM_CHARS = 80

#: The five campaign deliverable types W1 persists. ``type`` is a stable
#: editorial kind that must never change once a row exists; ``platform`` is a
#: free-text label (Instagram, newsletter, Google Ads, ...) or ``None``.
DELIVERABLE_TYPES: tuple[str, ...] = (
    "strategy_brief",
    "social_post",
    "ad_copy",
    "creative_brief",
    "content_calendar",
)

_TYPE_ALIASES: tuple[tuple[str, str], ...] = (
    ("strategy brief", "strategy_brief"),
    ("campaign strategy", "strategy_brief"),
    ("campaign brief", "strategy_brief"),
    ("strategy document", "strategy_brief"),
    ("marketing strategy", "strategy_brief"),
    ("strategy", "strategy_brief"),
    ("social media post", "social_post"),
    ("social post", "social_post"),
    ("social posts", "social_post"),
    ("social caption", "social_post"),
    ("instagram post", "social_post"),
    ("linkedin post", "social_post"),
    ("tiktok post", "social_post"),
    ("caption", "social_post"),
    ("post", "social_post"),
    ("posts", "social_post"),
    ("ad copy", "ad_copy"),
    ("ad creative", "ad_copy"),
    ("advertisement", "ad_copy"),
    ("advertising copy", "ad_copy"),
    ("ads", "ad_copy"),
    ("ad", "ad_copy"),
    ("creative brief", "creative_brief"),
    ("creative concept", "creative_brief"),
    ("creative direction", "creative_brief"),
    ("creative guidelines", "creative_brief"),
    ("visual brief", "creative_brief"),
    ("design brief", "creative_brief"),
    ("content calendar", "content_calendar"),
    ("editorial calendar", "content_calendar"),
    ("posting calendar", "content_calendar"),
    ("posting schedule", "content_calendar"),
    ("content schedule", "content_calendar"),
    ("calendar", "content_calendar"),
)

#: Words that mean "the user is asking for campaign content", used only to
#: decide whether a turn requests deliverables at all.
_DELIVERABLE_NOUNS = (
    "deliverable", "deliverables", "campaign content", "campaign copy",
    "campaign assets", "campaign materials", "content",
)

#: Nouns that name one existing asset for a targeted revision.
_CREATE_VERBS = (
    "create", "build", "prepare", "propose", "start", "generate", "write",
    "draft", "make", "produce", "give me", "set up",
)

_REVISION_VERBS = (
    "revise", "rewrite", "rework", "update", "edit", "change", "shorten",
    "expand", "tighten", "regenerate", "tweak", "adjust", "improve",
    "rephrase", "reword", "fix", "make it", "make the",
)

# A revision names an existing deliverable: it either opens with the verb
# ("Rewrite the ad copy") or points at one with a definite reference
# ("I want to update the ad copy"). An incidental verb elsewhere in the ask
# ("a post about our update policy") is a creation request, not a revision.
_REVISION_START_RE = re.compile(
    r"^\s*(?:please\s+|can\s+you\s+|could\s+you\s+|i\s+(?:want|need)\s+to\s+)?"
    r"(?:" + "|".join(re.escape(verb) for verb in _REVISION_VERBS) + r")\b",
    re.IGNORECASE)
_DEFINITE_DELIVERABLE_RE = re.compile(
    r"\b(?:the|that|this|our|its|existing)\s+(?:\w+\s+){0,2}"
    r"(?:posts?|copy|captions?|drafts?|assets?|deliverables?|briefs?|ads?)\b",
    re.IGNORECASE)

#: Channels a deliverable can actually go to. Deliberately excludes generic
#: words like "website" or "landing page", which usually appear inside an
#: objective ("improve website conversion") rather than naming a channel.
_CHANNEL_TERMS: tuple[str, ...] = (
    "instagram", "facebook", "tiktok", "linkedin", "twitter", "youtube",
    "pinterest", "reddit", "email", "newsletter", "google ads", "meta ads",
    "paid social", "organic social", "search ads", "blog",
)

_BLOCK_RE = re.compile(
    r"^[ \t]*#{1,6}[ \t]+DELIVERABLE\b[ \t]*(?P<meta>[^\r\n]*)\r?\n"
    r"(?P<body>.*?)(?=^[ \t]*#{1,6}[ \t]+DELIVERABLE\b|\Z)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)
_META_KV_RE = re.compile(r'(?P<key>[A-Za-z_]+)\s*=\s*"(?P<value>[^"]*)"')

#: The four honest sections, checked exactly like the business brief checks its
#: own. A missing section is a validation failure, never silently filled in.
REQUIRED_SECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("persisted user facts", r"(?im)^\s*#{1,6}\s*Persisted user facts\b"),
    ("retrieved evidence", r"(?im)^\s*#{1,6}\s*Retrieved evidence\b"),
    ("recommendations", r"(?im)^\s*#{1,6}\s*(?:AI suggestions|Recommendations)\b"),
    ("missing information", r"(?im)^\s*#{1,6}\s*"
                            r"(?:Unknown or unsupported information|Missing information)\b"),
)


class DeliverableValidationError(ValueError):
    """Model output failed the honesty contract; nothing may be persisted."""


class DeliverableGenerationError(RuntimeError):
    """The provider could not produce usable output; nothing was persisted."""

    def __init__(self, message: str, *, provider: str = "", model: str = "",
                 failure_code: str = ""):
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.failure_code = failure_code


class AmbiguousRevisionError(ValueError):
    """A revision request did not resolve to exactly one scoped deliverable."""

    def __init__(self, message: str, *, candidates: list | None = None):
        super().__init__(message)
        self.candidates = list(candidates or [])


# ---------------------------------------------------------------------------
# request parsing
# ---------------------------------------------------------------------------

def _mentions(text: str, phrase: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text))


def requested_types(text: str) -> list[str]:
    """Deliverable types named in a request, in canonical deliverable order."""
    lowered = f" {str(text or '').lower()} "
    found = {canonical for alias, canonical in _TYPE_ALIASES
             if _mentions(lowered, alias)}
    return [kind for kind in DELIVERABLE_TYPES if kind in found]


def resolved_types(text: str) -> list[str]:
    """Requested types, or the full standard set when none were named."""
    return requested_types(text) or list(DELIVERABLE_TYPES)


def requests_deliverables(text: str) -> bool:
    """True when the turn explicitly asks for campaign content, not a campaign."""
    lowered = f" {str(text or '').lower()} "
    return bool(requested_types(lowered)
                or any(_mentions(lowered, noun) for noun in _DELIVERABLE_NOUNS))


def creates_deliverables(text: str) -> bool:
    """True when the turn both asks for campaign content and asks to make it."""
    lowered = f" {str(text or '').lower()} "
    return (requests_deliverables(lowered)
            and any(verb in lowered for verb in _CREATE_VERBS))


def looks_like_revision(text: str) -> bool:
    """True when the turn asks to change content that already exists."""
    value = str(text or "")
    if not any(verb in f" {value.lower()} " for verb in _REVISION_VERBS):
        return False
    return bool(_REVISION_START_RE.match(value)
                or _DEFINITE_DELIVERABLE_RE.search(value))


def requested_channels(text: str) -> list[str]:
    lowered = f" {str(text or '').lower()} "
    return [term for term in _CHANNEL_TERMS if _mentions(lowered, term)]


def requested_duration(text: str) -> str:
    match = re.search(r"(?<!\w)(\d{1,3})\s*[- ]?(day|days|week|weeks|month|months)"
                      r"(?!\w)", str(text or "").lower())
    return f"{match.group(1)} {match.group(2)}" if match else ""


def _requested_audience(text: str) -> str:
    """Only an explicitly named audience counts.

    A loose "for ..." fallback was removed: it captured approval phrases and
    unrelated clauses, and a wrong audience is worse than an absent one.
    """
    match = re.search(
        r"(?i)\b(?:audience|target(?:ing)?|aimed at)\s*(?:is|of|at)?\s*[:\-]?\s*"
        r"(?:our\s+|the\s+)?([^,.;!?\r\n]{2,120})", str(text or ""))
    if not match:
        return ""
    candidate = match.group(1).strip(" .:-")
    return candidate[:400] if candidate else ""


def campaign_intake(text: str, *, objective: str = "", turn_id: str = "") -> dict:
    """Extended campaign metadata taken only from what the user actually said.

    No invented budget, metric, price, date, or audience. Absent facts stay
    absent rather than being filled with plausible defaults.
    """
    return {
        "objective": trim_objective(objective),
        "target_audience": _requested_audience(text),
        "channels": requested_channels(text),
        "duration": requested_duration(text),
        "request_facts": {
            "user_request": str(text or "")[:2000],
            "requested_types": requested_types(text),
            "turn_id": str(turn_id or ""),
        },
    }


def trim_objective(objective: str) -> str:
    """Drop the request details that trail the objective.

    The graph's goal capture runs to the next sentence boundary, so it can
    swallow the deliverable list, the duration, and the audience clause. Those
    live in their own fields; the objective keeps only what it is.
    """
    value = str(objective or "").strip()
    if not value:
        return ""
    value = re.split(r"[,;]", value, maxsplit=1)[0]
    value = re.split(r"(?i)\s+(?:targeting|target|audience|for\s+our)\b",
                     value, maxsplit=1)[0]
    for pattern in (
        r"(?i)\s+with\s+(?:an?|the)\s+(?:ad copy|social post|social media post|"
        r"content calendar|creative brief|strategy brief|calendar)\b.*$",
        r"(?i)\s+for\s+\d{1,3}\s*[- ]?(?:day|days|week|weeks|month|months)\b.*$",
        r"(?i)\s+on\s+(?:instagram|facebook|tiktok|linkedin|twitter|youtube|"
        r"email|newsletter|blog)\b.*$",
    ):
        value = re.sub(pattern, "", value)
    return value.strip(" .:-")[:500]


# ---------------------------------------------------------------------------
# existing campaign metadata (campaigns.workflow_json)
# ---------------------------------------------------------------------------

def apply_campaign_metadata(conn, project_id: str, campaign_id: str,
                            intake: dict) -> dict:
    """Merge intake facts into the campaign's existing workflow metadata.

    ``campaigns`` keeps its metadata in ``workflow_json``; W1 adds no campaign
    columns, so that stays the single home for objective, target audience,
    channels, duration, and request facts. Ownership is re-checked before the
    write so a foreign campaign id can never be annotated. Never raises.
    """
    pid = str(project_id or "").strip()
    cid = str(campaign_id or "").strip()
    if not (pid and cid) or not isinstance(intake, dict) or not intake:
        return {}
    row = _campaign_row(conn, pid, cid)
    if row is None:
        return {}
    meta, existing = _campaign_metadata(row)
    if existing == intake:
        return existing
    meta["campaign_intake"] = intake
    if not _write_campaign_metadata(conn, pid, cid, meta):
        return {}
    return intake


def reseal_campaign_metadata(conn, project_id: str, campaign_id: str) -> bool:
    """Restore the shape ``propose_campaign`` rebuilds from its turn key.

    ``t_propose_campaign`` reconstructs ``workflow_json`` from a fixed set of
    workflow keys and treats any difference as an idempotency-key reuse. Facts
    added after the proposal would make a retried turn fail, so they are lifted
    out before the tool is re-run and re-applied once it succeeds.
    """
    pid = str(project_id or "").strip()
    cid = str(campaign_id or "").strip()
    if not (pid and cid):
        return False
    row = _campaign_row(conn, pid, cid)
    if row is None:
        return False
    meta, existing = _campaign_metadata(row)
    if not isinstance(existing, dict):
        return False
    meta.pop("campaign_intake", None)
    return _write_campaign_metadata(conn, pid, cid, meta)


def _campaign_row(conn, project_id: str, campaign_id: str):
    try:
        return conn.execute(
            "SELECT workflow_json FROM campaigns WHERE id=? AND project_id=?",
            (campaign_id, project_id)).fetchone()
    except Exception:
        return None


def _campaign_metadata(row) -> tuple:
    try:
        meta = json.loads(row["workflow_json"] or "{}")
    except (TypeError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    existing = meta.get("campaign_intake")
    return meta, (existing if isinstance(existing, dict) else None)


def _write_campaign_metadata(conn, project_id: str, campaign_id: str, meta) -> bool:
    try:
        conn.execute(
            "UPDATE campaigns SET workflow_json=? WHERE id=? AND project_id=?",
            (json.dumps(meta, sort_keys=True), campaign_id, project_id))
        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            conn.rollback()
            return False
        conn.commit()
    except Exception:
        return False
    return True


def campaign_proposal_id(project_id: str, turn_id: str) -> str:
    """The campaign id ``propose_campaign`` derives from this turn's key."""
    return hashlib.sha256(
        f"{str(project_id or '')}:propose_campaign:{str(turn_id or '')}:campaign"
        .encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# scoped context
# ---------------------------------------------------------------------------

def build_generation_context(conn, *, project_id: str, campaign_title: str = "",
                             user_request: str = "") -> dict:
    """Profile, optional brief, and scoped evidence for one immutable turn project.

    ``project_id`` is required; retrieval fails closed without it and no
    fallback ever reaches another project's rows.
    """
    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required before generation (fail closed)")

    profile: dict = {}
    brief_md = ""
    try:
        from app.services import business_workspace as workspace

        row = workspace.get_profile(conn, pid)
        if row:
            profile = {field: str(row.get(field, "") or "") for field in workspace.FIELDS}
            profile["name"] = str(row.get("name", "") or "")
            profile["website"] = str(row.get("website", "") or "")
            brief_md = str(row.get("brief_md", "") or "")
    except Exception:
        profile, brief_md = {}, ""

    query = " ".join(part for part in (
        str(campaign_title or "").strip(),
        str(user_request or "").strip(),
        " ".join(f"{key} {value}" for key, value in profile.items() if value),
    ) if part).strip() or "campaign messaging offer audience"

    evidence: list[dict] = []
    retrieval_error = ""
    try:
        from app.services.rag.scoped_retrieval import retrieve_scoped

        result = retrieve_scoped(conn, query, project_id=pid, mode="lexical",
                                 k_fts=MAX_CONTEXT_EVIDENCE, k_sem=0)
        for hit in (result or {}).get("hits") or []:
            source_id = f"{hit.get('document_id') or ''}:{hit.get('chunk_id') or ''}"
            if source_id == ":":
                continue
            evidence.append({
                "source_id": source_id,
                "path": str(hit.get("path", "") or ""),
                "text": str(hit.get("text", "") or "").strip()[:MAX_EVIDENCE_CHARS],
            })
            if len(evidence) >= MAX_CONTEXT_EVIDENCE:
                break
    except Exception as exc:
        retrieval_error = f"{type(exc).__name__}: {exc}"[:200]

    return {
        "project_id": pid,
        "profile": profile,
        "brief_md": brief_md[:8000],
        "evidence": evidence,
        "evidence_ids": [item["source_id"] for item in evidence],
        "query": query,
        "retrieval_error": retrieval_error,
    }


# ---------------------------------------------------------------------------
# prompts, parsing, and validation
# ---------------------------------------------------------------------------

_SCHEMA_RULES = (
    "Return Markdown only. For every deliverable emit exactly one block that "
    'begins with a heading of the form \'## DELIVERABLE type="<type>" '
    'title="<title>" platform="<platform>"\', where <type> is one of: {types}. '
    "The block body must contain these four headings in this order: "
    "## Persisted user facts, ## Retrieved evidence, ## AI suggestions, and "
    "## Unknown or unsupported information.\n"
    "Persisted user facts: only values supplied in the business profile. Never "
    "invent a price, discount, claim, statistic, budget, metric, testimonial, "
    "date, or research finding.\n"
    "Retrieved evidence: cite supplied evidence only as [document_id:chunk_id] "
    "copied exactly from the supplied list. If no evidence was supplied, write "
    "'No project evidence available' and cite nothing.\n"
    "AI suggestions: clearly labelled suggestions, not facts.\n"
    "Unknown or unsupported information: anything you could not verify from "
    "the profile, brief, or evidence, including what is still missing."
)


def build_generation_prompt(context: dict, *, types: list[str],
                            user_request: str = "") -> tuple[str, str]:
    """System and user prompts for one deliverable batch."""
    wanted = [kind for kind in types if kind in DELIVERABLE_TYPES] or list(DELIVERABLE_TYPES)
    system = ("You write campaign deliverables for a marketing workspace. "
              + _SCHEMA_RULES.format(types=", ".join(wanted)))
    user = json.dumps({
        "user_request": str(user_request or "")[:2000],
        "requested_types": wanted,
        "campaign_title": str(context.get("campaign_title") or "")[:200],
        "business_profile": context.get("profile") or {},
        "business_brief": context.get("brief_md") or "",
        "evidence": context.get("evidence") or [],
    }, sort_keys=True, indent=2)
    return system, user


def build_revision_prompt(context: dict, *, current: dict,
                          instruction: str) -> tuple[str, str]:
    """System and user prompts for revising exactly one existing deliverable."""
    kind = str(current.get("type", "") or "")
    system = ("You revise one existing campaign deliverable. "
              + _SCHEMA_RULES.format(types=", ".join(
                  [kind] if kind in DELIVERABLE_TYPES else DELIVERABLE_TYPES))
              + "\nEmit exactly one block. Apply only the requested change.")
    user = json.dumps({
        "revision_request": str(instruction or "")[:2000],
        "current_deliverable": {
            "type": kind,
            "title": str(current.get("title", "") or ""),
            "platform": current.get("platform"),
            "content_md": str(current.get("content_md", "") or "")[:MAX_CONTENT_CHARS],
            "status": str(current.get("status", "") or ""),
            "current_version": current.get("current_version"),
        },
        "business_profile": context.get("profile") or {},
        "business_brief": context.get("brief_md") or "",
        "evidence": context.get("evidence") or [],
    }, sort_keys=True, indent=2)
    return system, user


def parse_generated_items(text: str) -> list[dict]:
    """Parse ``## DELIVERABLE`` blocks. Unparseable output yields ``[]``."""
    items: list[dict] = []
    for match in _BLOCK_RE.finditer(str(text or "")):
        meta = {found.group("key").lower(): found.group("value")
                for found in _META_KV_RE.finditer(match.group("meta"))}
        platform = str(meta.get("platform", "") or "").strip()[:MAX_PLATFORM_CHARS]
        items.append({
            "type": str(meta.get("type", "") or "").strip().lower(),
            "title": str(meta.get("title", "") or "").strip()[:MAX_TITLE_CHARS],
            "platform": platform or None,
            "content_md": match.group("body").strip(),
        })
    return items


def evidence_ids_in(markdown: str, allowed_ids) -> list[str]:
    allowed = set(allowed_ids or ())
    return [source_id
            for source_id in re.findall(r"\[([^\]\r\n:]+:[^\]\r\n]*)\]", markdown)
            if source_id in allowed]


def _has_unsupported_citations(markdown: str, allowed_ids) -> bool:
    """Reuse the business brief's canonical citation check (single source)."""
    try:
        from app.routes.business_workspace import has_unsupported_citations
        return bool(has_unsupported_citations(markdown, set(allowed_ids or ())))
    except Exception as exc:
        raise DeliverableValidationError(
            "The citation validator is unavailable; nothing was saved.") from exc


def _normalized_claim(text: str) -> str:
    return " ".join(re.findall(r"[\w]+", str(text or "").casefold()))


def _source_texts(context: dict | None, user_request: str) -> list[str]:
    """Collect only sources explicitly allowed for this generation turn."""
    scoped = context if isinstance(context, dict) else {}
    sources = [str(user_request or "")]
    profile = scoped.get("profile")
    if isinstance(profile, dict):
        sources.extend(str(value) for value in profile.values()
                       if isinstance(value, str) and value.strip())
    brief = scoped.get("brief_md")
    if isinstance(brief, str) and brief.strip():
        sources.append(brief)
    evidence = scoped.get("evidence")
    if isinstance(evidence, list):
        sources.extend(str(row.get("text") or "") for row in evidence
                       if isinstance(row, dict) and str(row.get("text") or "").strip())
    return [source for source in sources if source.strip()]


def _section_body(markdown: str, heading: str) -> str:
    start = re.search(
        rf"(?im)^\s*#{{1,6}}\s*{re.escape(heading)}\b[^\r\n]*\r?\n",
        markdown)
    if start is None:
        return ""
    end = re.search(r"(?im)^\s*#{1,6}\s+", markdown[start.end():])
    stop = start.end() + end.start() if end is not None else len(markdown)
    return markdown[start.end():stop].strip()


def _claim_fragments(text: str) -> list[str]:
    return [part.strip() for part in re.split(
        r"[\r\n;]+|(?<=[.!?])\s+", str(text or ""))
            if part.strip()]


def _validate_persisted_facts(markdown: str, source_texts: list[str], *, label: str) -> None:
    facts = _section_body(markdown, "Persisted user facts")
    sources = _normalized_claim(" ".join(source_texts))
    for fragment in _claim_fragments(facts):
        claim = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", fragment)
        claim = re.sub(r"^\*\*(.+?)\*\*:\s*", r"\1: ", claim).strip()
        if _normalized_claim(claim) == "no persisted facts supplied":
            continue
        if ":" in claim:
            label_part, value_part = claim.split(":", 1)
            if len(label_part.strip()) <= 40 and re.fullmatch(
                    r"[\w /-]+", label_part.strip()):
                claim = value_part.strip()
        normalized = _normalized_claim(claim)
        if not normalized or normalized not in sources:
            raise DeliverableValidationError(
                f"{label} contains a persisted fact that is not an exact value "
                "from this turn's request, project profile, brief, or scoped "
                "evidence. Nothing was saved.")


def _validate_evidence_claims(markdown: str, allowed_ids, *, label: str) -> None:
    evidence = _section_body(markdown, "Retrieved evidence")
    for fragment in _claim_fragments(evidence):
        if _normalized_claim(fragment) == "no project evidence available":
            continue
        if not evidence_ids_in(fragment, allowed_ids):
            raise DeliverableValidationError(
                f"{label} makes a retrieved-evidence statement without a valid "
                "scoped citation. Nothing was saved.")


_HIGH_RISK_TERMS = re.compile(
    r"\b(?:prices?|pricing|costs?|budgets?|discounts?|coupons?|free(?:\s+trial)?|"
    r"guarantees?|guaranteed|testimonials?|reviews?|ratings?|rated|statistics?|"
    r"metrics?|percent(?:age)?s?|kpis?|ctr|roas|roi|conversion(?:\s+rate)?|"
    r"revenue|growth|increases?|decreases?|sales|followers|impressions|clicks|"
    r"dates?|dollars?|euros?|pounds?|january|february|march|april|may|june|"
    r"july|august|september|october|november|december|monday|tuesday|"
    r"wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_NEGATION_PREFIX = re.compile(
    r"\b(?:no|not|none|without|unknown|unsupported|unconfirmed|unverified)\b"
    r"(?:\W+\w+){0,4}\W*$", re.IGNORECASE)
_NEGATION_SUFFIX = re.compile(
    r"^\W*(?:(?:is|are|was|were|has|have|had)\W+)?"
    r"(?:not\b|unknown\b|unconfirmed\b|unverified\b)", re.IGNORECASE)


def _validate_high_risk_claims(markdown: str, source_texts: list[str], *, label: str) -> None:
    """Reject unsupported numeric and high-risk terms by exact source match."""
    # Remove citation identifiers first so chunk numbers are not treated as claims.
    text = re.sub(r"\[[^\]\r\n]+:[^\]\r\n]+\]", "", markdown)
    source = _normalized_claim(" ".join(source_texts))
    for match in re.finditer(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)", text):
        value = _normalized_claim(match.group(0))
        if value and value not in source:
            raise DeliverableValidationError(
                f"{label} contains an unsupported numeric value; only values "
                "present in this turn's request or scoped project sources are "
                "allowed. Nothing was saved.")

    for match in _HIGH_RISK_TERMS.finditer(text):
        start = max(0, match.start() - 64)
        end = min(len(text), match.end() + 48)
        before = text[start:match.start()]
        after = text[match.end():end]
        if _NEGATION_PREFIX.search(before) or _NEGATION_SUFFIX.search(after):
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        if line_end < 0:
            line_end = len(text)
        statement = _normalized_claim(text[line_start:line_end])
        if not statement or statement not in source:
            raise DeliverableValidationError(
                f"{label} contains an unsupported high-risk factual claim "
                f"({match.group(0)}). Nothing was saved.")


def validate_generated_items(text: str, *, types: list[str], allowed_ids,
                             single: bool = False, context: dict | None = None,
                             user_request: str = "") -> list[dict]:
    """Validate a whole batch before anything is persisted.

    Raises :class:`DeliverableValidationError` on the first problem so a bad
    batch never lands half-written. These phrase and value checks are
    conservative heuristics, not semantic verification of arbitrary prose.
    """
    expected = list(dict.fromkeys(
        kind for kind in (types or []) if kind in DELIVERABLE_TYPES))
    if not expected:
        raise DeliverableValidationError(
            "No supported deliverable types were requested, so nothing was saved.")
    items = parse_generated_items(text)
    if not items:
        raise DeliverableValidationError(
            "The model did not return any deliverables in the required "
            "'## DELIVERABLE' block format, so nothing was saved.")
    if single and len(items) != 1:
        raise DeliverableValidationError(
            f"The model returned {len(items)} blocks for a revision that must "
            "change exactly one deliverable, so nothing was saved.")
    if len(items) > MAX_ITEMS:
        raise DeliverableValidationError(
            f"The model returned {len(items)} deliverables; at most {MAX_ITEMS} "
            "are supported, so nothing was saved.")

    validated: list[dict] = []
    counts: dict[str, int] = {}
    for ordinal, item in enumerate(items):
        kind = str(item.get("type", "") or "").strip().lower()
        label = f"Deliverable {ordinal + 1}"
        if kind not in DELIVERABLE_TYPES:
            raise DeliverableValidationError(
                f"{label} has unsupported type {kind!r}. Allowed types: "
                f"{', '.join(DELIVERABLE_TYPES)}. Nothing was saved.")
        if expected and kind not in expected:
            raise DeliverableValidationError(
                f"{label} is {kind!r} but this request asked for "
                f"{', '.join(expected)}. Nothing was saved.")
        counts[kind] = counts.get(kind, 0) + 1
        if counts[kind] > 1:
            raise DeliverableValidationError(
                f"The model returned more than one block for requested type "
                f"{kind!r}; each requested type needs exactly one block, so "
                "nothing was saved.")
        title = str(item.get("title", "") or "").strip()
        if not title:
            raise DeliverableValidationError(
                f"{label} ({kind}) has no title, so nothing was saved.")
        content = str(item.get("content_md", "") or "").strip()
        if not content:
            raise DeliverableValidationError(
                f"{label} ({kind}) has no content, so nothing was saved.")
        if len(content) > MAX_CONTENT_CHARS:
            raise DeliverableValidationError(
                f"{label} ({kind}) exceeds {MAX_CONTENT_CHARS} characters, so "
                "nothing was saved.")
        missing = [name for name, pattern in REQUIRED_SECTION_PATTERNS
                   if not re.search(pattern, content)]
        if missing:
            raise DeliverableValidationError(
                f"{label} ({kind}) did not separate "
                f"{', '.join(missing)}, so nothing was saved.")
        if _has_unsupported_citations(content, allowed_ids):
            raise DeliverableValidationError(
                f"{label} ({kind}) cited a source that is not part of this "
                "project's scoped knowledge, so nothing was saved.")
        sources = _source_texts(context, user_request)
        _validate_persisted_facts(content, sources, label=f"{label} ({kind})")
        _validate_evidence_claims(content, allowed_ids, label=f"{label} ({kind})")
        _validate_high_risk_claims(
            f"{title}\n{content}", sources, label=f"{label} ({kind})")
        validated.append({
            "type": kind,
            "title": title[:MAX_TITLE_CHARS],
            "platform": item.get("platform"),
            "content_md": content,
            "ordinal": ordinal,
            "evidence_ids": evidence_ids_in(content, allowed_ids),
        })
    missing = [kind for kind in expected if counts.get(kind, 0) == 0]
    if missing:
        raise DeliverableValidationError(
            "The model omitted requested deliverable type(s): "
            f"{', '.join(missing)}. Each requested type needs exactly one "
            "block, so nothing was saved.")
    return validated


# ---------------------------------------------------------------------------
# revision resolution
# ---------------------------------------------------------------------------

def resolve_revision_target(candidates: list, *, user_request: str) -> dict:
    """Resolve a chat revision to exactly one scoped deliverable.

    Callers scope ``candidates`` to the turn's campaign first, so this only
    has to disambiguate within an already-narrow set. Raises
    :class:`AmbiguousRevisionError` carrying the concrete candidates so the
    caller can ask one focused clarification instead of guessing.
    """
    scoped = [row for row in (candidates or [])
              if isinstance(row, dict) and str(row.get("id", "") or "").strip()]
    if not scoped:
        raise AmbiguousRevisionError(
            "I could not find a saved deliverable to revise in this campaign.")
    if len(scoped) == 1:
        return scoped[0]

    lowered = str(user_request or "").lower()

    by_title = [row for row in scoped
                if str(row.get("title", "") or "").strip()
                and str(row["title"]).strip().lower() in lowered]
    if len(by_title) == 1:
        return by_title[0]

    by_platform = [row for row in scoped
                   if str(row.get("platform", "") or "").strip()
                   and str(row["platform"]).strip().lower() in lowered]
    if len(by_platform) == 1:
        return by_platform[0]

    by_ordinal = [row for row in scoped
                  if _ordinal_matches(lowered, scoped.index(row), len(scoped))]
    if len(by_ordinal) == 1:
        return by_ordinal[0]

    raise AmbiguousRevisionError(
        f"This campaign has {len(scoped)} deliverables, so I did not guess "
        "which one to change. Tell me which one to revise.",
        candidates=[{"id": str(row.get("id", "")),
                     "type": str(row.get("type", "")),
                     "title": str(row.get("title", "")),
                     "platform": str(row.get("platform", "") or "")}
                    for row in scoped])


def _ordinal_matches(lowered: str, index: int, total: int) -> bool:
    position = index + 1
    for phrase, wanted in (("first|1st", 1), ("second|2nd", 2), ("third|3rd", 3),
                           ("fourth|4th", 4), ("fifth|5th", 5)):
        if re.search(r"(?<!\w)(?:" + phrase + r")(?!\w)", lowered):
            return position == wanted
    if re.search(r"(?<!\w)last(?!\w)", lowered):
        return position == total
    return False


# ---------------------------------------------------------------------------
# model invocation
# ---------------------------------------------------------------------------

def resolve_mode(provider: str) -> str:
    """Map the turn's provider selection onto a ModelRouter route mode."""
    selected = str(provider or "AUTO").strip().upper()
    if selected in ("LOCAL", "OPENAI", "OPENROUTER", "BASE", "MARKETING_LORA"):
        return selected
    return "AUTO"


def _router():
    """The process ModelRouter, resolved exactly like brief generation does."""
    from app.routes.graph_runtime import _get_model_router

    return _get_model_router()


def complete(conn, *, turn_id: str, project_id: str, system: str, user: str,
             provider: str = "AUTO", model_id: str = "",
             call_prefix: str = "", max_tokens: int = 2400) -> tuple[str, dict]:
    """Run one ModelRouter completion with the turn's selected provider/model.

    Returns ``(text, provenance identity)``. Raises
    :class:`DeliverableGenerationError` on provider failure so callers report
    the exact reason without claiming anything was persisted.
    """
    pid = str(project_id or "").strip()
    if not pid:
        raise DeliverableGenerationError(
            "project_id is required before generation (fail closed)",
            provider=str(provider or ""), model=str(model_id or ""),
            failure_code="missing_project_id")
    try:
        router = _router()
    except Exception as exc:
        raise DeliverableGenerationError(
            f"The AI provider could not be reached: {exc}"[:300],
            provider=str(provider or ""), model=str(model_id or ""),
            failure_code="router_unavailable") from exc
    if router is None:
        raise DeliverableGenerationError(
            "No AI provider is configured, so no deliverables were generated. "
            "You can still create and edit deliverables manually.",
            provider=str(provider or ""), model=str(model_id or ""),
            failure_code="router_unavailable")

    mode = resolve_mode(provider)
    # AUTO deliberately refuses a specific model; honour that instead of
    # silently substituting a different one than the user selected.
    override = str(model_id or "").strip()
    model_override = override if (override and mode != "AUTO") else None
    call_id = f"mc-{call_prefix or uuid.uuid4().hex[:12]}"
    try:
        response, call = router.complete(
            conn,
            turn_id=str(turn_id or "turn"),
            project_id=pid,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[],
            mode=mode,
            opts={"max_tokens": int(max_tokens)},
            model_override=model_override,
            call_id=call_id,
            adapter="campaign_production",
        )
    except Exception as exc:
        raise DeliverableGenerationError(
            f"The AI provider could not generate deliverables: {exc}"[:300],
            provider=str(getattr(exc, "provider", "") or provider or ""),
            model=str(getattr(exc, "model", "") or model_id or ""),
            failure_code=str(getattr(exc, "failure_code", "") or "")
            or type(exc).__name__) from exc

    text = str(getattr(response, "text", "") or "")
    if not text.strip():
        raise DeliverableGenerationError(
            "The AI provider returned no content, so nothing was saved.",
            provider=str(getattr(call, "provider", "") or ""),
            model=str(getattr(call, "model", "") or ""),
            failure_code="empty_completion")
    return text, {
        "provider": str(getattr(call, "provider", "") or ""),
        "model": str(getattr(call, "model", "") or ""),
        "requested_model": str(getattr(call, "requested_model", "") or override),
        "route_mode": str(getattr(call, "route_mode", "") or ""),
        "route_reason": str(getattr(call, "route_reason", "") or ""),
        "call_id": str(getattr(call, "call_id", "") or call_id),
        "total_tokens": getattr(call, "total_tokens", None),
        "estimated_cost_usd": getattr(call, "estimated_cost_usd", None),
    }


def batch_idempotency_key(*, project_id: str, campaign_id: str, turn_id: str) -> str:
    """Deterministic key so a retried turn never duplicates a deliverable."""
    seed = f"{project_id}\0{campaign_id}\0{turn_id}\0deliverables"
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# W1 storage (every deliverable write lives there)
# ---------------------------------------------------------------------------

def _storage():
    """W1's deliverables service. Imported lazily so the graph degrades."""
    from app.services import campaign_deliverables

    return campaign_deliverables


def list_deliverables(conn, *, project_id: str, campaign_id: str) -> list:
    """Campaign-scoped deliverable list. Raises when storage is unavailable."""
    storage = _storage()
    return list(storage.list_deliverables(conn, project_id, campaign_id) or [])


def _rows(value) -> list:
    """Normalize a W1 return value into a list of row dicts.

    ``save_generated_batch`` returns an ordered list; ``revise`` returns one
    public row.
    """
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("deliverables", "items", "saved", "records"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [dict(row) for row in nested if isinstance(row, dict)]
        if value.get("id") or value.get("type"):
            return [dict(value)]
    return []


def _w1_items(items: list) -> list:
    """Project validated items onto exactly W1's item contract.

    Validation metadata (batch position, evidence ids) stays here; the batch
    payload carries only ``type``, ``title``, ``platform``, and ``content_md``.
    """
    return [{"type": item["type"], "title": item["title"],
             "platform": item.get("platform"), "content_md": item["content_md"]}
            for item in items]


def _failure(error: str, *, status: str = "failed", **extra) -> dict:
    return {"ok": False, "status": status, "error": str(error).strip()[:400], **extra}


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------

def generate_batch(conn, *, project_id: str, campaign_id: str, turn_id: str,
                   user_request: str, types=None, campaign_title: str = "",
                   provider: str = "AUTO", model_id: str = "") -> dict:
    """Generate one deliverable batch and persist it atomically through W1.

    Returns an honest result: persisted ids and types, or a campaign-only
    partial failure with the exact reason. Nothing is written when the provider
    or the validation fails, so existing deliverables always survive.
    """
    pid = str(project_id or "").strip()
    cid = str(campaign_id or "").strip()
    if not pid or not cid:
        return _failure("A campaign deliverable needs both a project and a campaign.")

    wanted = list(dict.fromkeys(
        kind for kind in (list(types) if types else resolved_types(user_request))
        if kind in DELIVERABLE_TYPES))
    if not wanted:
        return _failure("No deliverable types were requested, so nothing was generated.",
                        status="skipped")

    batch_key = batch_idempotency_key(
        project_id=pid, campaign_id=cid, turn_id=turn_id)
    try:
        replayed = _storage().get_generated_batch(
            conn, pid, cid, batch_key)
    except Exception as exc:
        # If W1 cannot confirm whether this key already exists, a provider call
        # could create a duplicate or replace a user-visible retry result.
        return _failure(
            f"The saved generation batch could not be checked before retry: {exc}",
            campaign_id=cid, failure_code="idempotency_check_failed",
            preserved_existing=True)
    if replayed is not None:
        records = _rows(replayed)
        if not records:
            return _failure(
                "The saved generation batch has no readable deliverables.",
                campaign_id=cid, failure_code="idempotency_check_failed",
                preserved_existing=True)
        return {
            "ok": True,
            "status": "saved",
            "campaign_id": cid,
            "deliverables": records,
            "created": [str(row.get("id", "")) for row in records],
            "types": [str(row.get("type", "")) for row in records],
            "titles": [str(row.get("title", "")) for row in records],
            "idempotent_replay": True,
        }

    context = build_generation_context(conn, project_id=pid,
                                       campaign_title=campaign_title,
                                       user_request=user_request)
    system, user = build_generation_prompt(context, types=wanted,
                                           user_request=user_request)
    try:
        text, identity = complete(
            conn, turn_id=turn_id, project_id=pid, system=system, user=user,
            provider=provider, model_id=model_id,
            call_prefix=hashlib.sha256(
                f"{turn_id}:{cid}:batch".encode()).hexdigest()[:12],
        )
    except DeliverableGenerationError as exc:
        return _failure(exc, provider=exc.provider, model=exc.model,
                        failure_code=exc.failure_code, campaign_id=cid,
                        preserved_existing=True)

    try:
        items = validate_generated_items(
            text, types=wanted, allowed_ids=context["evidence_ids"],
            context=context, user_request=user_request)
    except DeliverableValidationError as exc:
        return _failure(exc, provider=identity["provider"], model=identity["model"],
                        failure_code="output_validation", campaign_id=cid,
                        preserved_existing=True)

    before = _existing_ids(conn, project_id=pid, campaign_id=cid)
    provenance = {
        "source": "campaign_production",
        "turn_id": str(turn_id or ""),
        "requested_types": wanted,
        "context_sources_used": (
            ["user_turn"]
            + (["business_profile"] if context.get("profile") else [])
            + (["business_brief"] if context.get("brief_md") else [])
            + (["project_rag"] if context.get("evidence") else [])
        ),
        "evidence": context["evidence"],
        "retrieval_error": context.get("retrieval_error", ""),
        **identity,
    }
    try:
        saved = _storage().save_generated_batch(
            conn, pid, cid, batch_key, _w1_items(items), provenance)
    except Exception as exc:
        return _failure(f"The generated deliverables could not be saved: {exc}",
                        provider=identity["provider"], model=identity["model"],
                        failure_code="persistence_failed", campaign_id=cid)

    records = _rows(saved)
    return {
        "ok": True,
        "status": "saved",
        "campaign_id": cid,
        "deliverables": records,
        "created": [str(row.get("id", "")) for row in records],
        "types": [str(row.get("type", "")) for row in records],
        "titles": [str(row.get("title", "")) for row in records],
        "provider": identity["provider"],
        "model": identity["model"],
        "evidence_count": len(context["evidence"]),
        # Honest replay signal: every returned row already existed before the
        # save, so the turn reused W1's original rows rather than writing new ones.
        "idempotent_replay": bool(records) and all(
            str(row.get("id", "")) in before for row in records),
    }


def _existing_ids(conn, *, project_id: str, campaign_id: str) -> set:
    try:
        return {str(row.get("id", "")) for row in list_deliverables(
            conn, project_id=project_id, campaign_id=campaign_id)}
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# revision
# ---------------------------------------------------------------------------

def revise_deliverable(conn, *, project_id: str, campaign_id: str, turn_id: str,
                       deliverable_id: str, instruction: str,
                       provider: str = "AUTO", model_id: str = "") -> dict:
    """Revise exactly one scoped deliverable and save it as a new version.

    Uses W1's expected-version revise function, so a stale read is reported as
    a conflict rather than overwriting a newer edit. On any provider or
    validation failure the existing version is left exactly as it was.
    """
    pid = str(project_id or "").strip()
    cid = str(campaign_id or "").strip()
    did = str(deliverable_id or "").strip()
    if not (pid and cid and did):
        return _failure("A revision needs a project, a campaign, and one deliverable.")

    storage = _storage()
    try:
        current = storage.get_deliverable(conn, pid, cid, did)
    except Exception as exc:
        return _failure(f"That deliverable is not available in this campaign: {exc}")
    if not isinstance(current, dict) or not current:
        return _failure("That deliverable no longer exists in this campaign.")

    expected_version = current.get("current_version")
    if not isinstance(expected_version, int) or isinstance(expected_version, bool):
        return _failure("The current deliverable version could not be read.")

    context = build_generation_context(conn, project_id=pid,
                                       campaign_title=str(current.get("title", "") or ""),
                                       user_request=instruction)
    system, user = build_revision_prompt(context, current=current,
                                         instruction=instruction)
    try:
        text, identity = complete(
            conn, turn_id=turn_id, project_id=pid, system=system, user=user,
            provider=provider, model_id=model_id,
            call_prefix=hashlib.sha256(
                f"{turn_id}:{did}:revise".encode()).hexdigest()[:12],
        )
    except DeliverableGenerationError as exc:
        return _failure(exc, provider=exc.provider, model=exc.model,
                        failure_code=exc.failure_code, campaign_id=cid,
                        deliverable_id=did, preserved_existing=True)

    try:
        items = validate_generated_items(
            text, types=[str(current.get("type", ""))],
            allowed_ids=context["evidence_ids"], single=True,
            context=context, user_request=instruction)
    except DeliverableValidationError as exc:
        return _failure(exc, provider=identity["provider"], model=identity["model"],
                        failure_code="output_validation", campaign_id=cid,
                        deliverable_id=did, preserved_existing=True)

    revised = items[0]
    # ``type`` is deliberately never sent: W1 stores the editorial kind on the
    # row and changing it would repurpose an existing deliverable.
    changes: dict = {"content_md": revised["content_md"]}
    if revised["title"] != str(current.get("title", "") or ""):
        changes["title"] = revised["title"]
    if revised["platform"] != current.get("platform"):
        changes["platform"] = revised["platform"]
    try:
        saved = storage.revise(conn, pid, cid, did, expected_version, changes)
    except Exception as exc:
        return _failure(f"The revised version could not be saved: {exc}",
                        provider=identity["provider"], model=identity["model"],
                        failure_code="revision_conflict", campaign_id=cid,
                        deliverable_id=did, preserved_existing=True)

    records = _rows(saved)
    row = records[0] if records else {}
    return {
        "ok": True,
        "status": "saved",
        "campaign_id": cid,
        "deliverable_id": str(row.get("id", "") or did),
        "deliverables": records or [dict(current)],
        "types": [str(row.get("type", current.get("type", "")) or "")],
        "titles": [str(row.get("title", current.get("title", "")) or "")],
        "version": row.get("current_version"),
        "provider": identity["provider"],
        "model": identity["model"],
    }
