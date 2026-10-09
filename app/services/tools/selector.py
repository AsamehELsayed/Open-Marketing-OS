"""Provider-independent, registry-backed selection for read-only tools.

This is a small deterministic fallback for requests that the Account
Manager's existing explicit capability routes do not recognize.  It reads
tool names, descriptions, parameter schemas, and safety metadata from the
registered catalog; it does not ask the model to emit a native function call
and it never auto-selects a write or external-action tool.
"""
from __future__ import annotations

import re


_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "for", "from", "i",
    "in", "is", "it", "me", "my", "of", "on", "or", "our", "please",
    "the", "this", "to", "we", "with", "you", "your", "can", "could",
    "would", "tell", "help", "about", "do", "does", "did", "have",
    "has", "had",
})

_CANONICAL = {
    "analyze": "audit", "analyse": "audit", "review": "audit",
    "inspect": "audit", "check": "audit", "evaluate": "audit",
    "look": "search", "lookup": "search", "research": "search",
    "find": "search", "search": "search", "browse": "fetch",
    "visit": "fetch", "open": "fetch", "fetch": "fetch",
    "scan": "crawl", "crawl": "crawl", "show": "list", "view": "list",
    "list": "list", "get": "get", "read": "get", "site": "website",
    "sites": "website", "websites": "website", "campaigns": "campaign",
    "accounts": "account", "current": "current", "latest": "current",
    "which": "get", "who": "get", "where": "get",
    "when": "get", "how": "get",
}

_ACTION_WORDS = frozenset({
    "audit", "search", "fetch", "crawl", "list", "get", "current",
    "compare", "summarize", "report", "weather", "forecast",
})
_ACTION_VERBS = _ACTION_WORDS - {"weather", "forecast"}
_URL_RE = re.compile(r"(?:https?://)?(?:www\.)?[a-z0-9-]+\.[a-z]{2,}(?:/[^\s]*)?", re.I)
_HANDLE_RE = re.compile(r"@([A-Za-z0-9_.]{2,40})")


def _tokens(value: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+|[\u0600-\u06ff]+", str(value or "").lower())
    normalized: set[str] = set()
    for word in words:
        if word in _STOP_WORDS:
            continue
        token = _CANONICAL.get(word, word)
        if token.endswith("s") and len(token) > 4:
            token = token[:-1]
        normalized.add(token)
    return normalized


def _score(request_terms: set[str], record: dict) -> float:
    name_terms = _tokens(str(record.get("name") or "").replace("_", " "))
    description_terms = _tokens(str(record.get("description") or ""))
    parameter_terms = _tokens(" ".join(
        str(key) for key in (record.get("parameters") or {}).get("properties", {})
    ))
    name_hits = request_terms & name_terms
    description_hits = request_terms & description_terms
    parameter_hits = request_terms & parameter_terms
    return (len(name_hits) * 2.5 + len(description_hits) +
            len(parameter_hits) * 0.5)


def _arguments(record: dict, text: str, project_state: dict | None) -> tuple[dict, list[str]]:
    parameters = record.get("parameters") or {}
    properties = parameters.get("properties") or {}
    required = [str(name) for name in parameters.get("required", [])]
    args: dict = {}
    project = project_state if isinstance(project_state, dict) else {}
    url_match = _URL_RE.search(text or "")
    handle_match = _HANDLE_RE.search(text or "")

    for name, definition in properties.items():
        key = str(name)
        if key in ("query", "goal"):
            args[key] = (text or "").strip()
        elif key == "url":
            value = url_match.group(0).rstrip(".,!?;:") if url_match else ""
            if not value:
                value = str(project.get("website") or "").strip()
            if value:
                args[key] = value
        elif key in ("handle", "username") and handle_match:
            args[key] = handle_match.group(1)
        elif key == "status":
            match = re.search(
                r"\b(draft(?:ed)?|proposed|approved|active|executing|measuring|rejected|blocked)\b",
                text or "", re.I,
            )
            if match:
                value = match.group(1).lower()
                args[key] = "drafted" if value.startswith("draft") else value
        elif key == "limit":
            match = re.search(r"\b(?:first|last|top)\s+(\d{1,2})\b", text or "", re.I)
            if match:
                args[key] = max(1, min(50, int(match.group(1))))

    missing = [name for name in required if args.get(name) in (None, "")]
    return args, missing


def select_registered_tool(text: str, *, catalog: list[dict],
                           project_state: dict | None = None) -> dict | None:
    """Select a high-confidence read tool from its registered metadata.

    Ties and weak matches stay on the normal conversational route.  A matched
    write/external tool is deliberately excluded; explicit draft writes are
    handled by the Account Manager's existing guarded proposal path.
    """
    request_terms = _tokens(text)
    if not request_terms or not (
        request_terms & _ACTION_WORDS or "?" in str(text or "")
    ):
        return None

    candidates: list[tuple[float, dict]] = []
    for record in catalog or []:
        if not isinstance(record, dict):
            continue
        if record.get("side_effect") != "read":
            continue
        name_terms = _tokens(str(record.get("name") or "").replace("_", " "))
        description_terms = _tokens(str(record.get("description") or ""))
        parameter_terms = _tokens(" ".join(
            str(key) for key in (record.get("parameters") or {}).get("properties", {})
        ))
        subject_terms = request_terms - _ACTION_VERBS
        if not subject_terms.intersection(name_terms | description_terms | parameter_terms):
            continue
        score = _score(request_terms, record)
        if score >= 2.5:
            candidates.append((score, record))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: (-pair[0], str(pair[1].get("name") or "")))
    best_score, best = candidates[0]
    if len(candidates) > 1 and best_score - candidates[1][0] < 1.0:
        return None

    args, missing = _arguments(best, str(text or ""), project_state)
    return {
        "intent_type": "TOOL_CAPABILITY",
        "capability": str(best.get("name") or ""),
        "arguments": args,
        "missing": missing,
        "confidence": min(0.95, 0.65 + best_score / 20),
        "source_text": str(text or "")[:300],
        "selected_tool": str(best.get("name") or ""),
    }
