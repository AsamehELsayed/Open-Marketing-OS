"""DEV-007 W6: MCP policy helpers (pure, no I/O).

MCP is an EXTENSION mechanism: it never replaces internal tools. Every
discovered MCP tool is classified into exactly one side-effect category,
and each category maps to a frozen approval level:

    READ            -> GREEN  (autonomous)
    WRITE           -> YELLOW (approval-gated)
    EXTERNAL_ACTION -> YELLOW (approval-gated)
    DESTRUCTIVE     -> RED    (approval-gated)

This module is deliberately dependency-free so the integrator, QA, and
UI layers can reuse the matrix without importing transport code.
"""

from __future__ import annotations

import re

# Frozen approval-mapping matrix: category -> approval level.
APPROVAL_MATRIX: dict[str, str] = {
    "READ": "GREEN",
    "WRITE": "YELLOW",
    "EXTERNAL_ACTION": "YELLOW",
    "DESTRUCTIVE": "RED",
}

CATEGORIES = tuple(APPROVAL_MATRIX)

# Verb-prefix heuristics applied to snake_case tool names, checked in order.
# DESTRUCTIVE first so `delete_then_notify` classifies as DESTRUCTIVE, and
# EXTERNAL_ACTION before WRITE so `send_email` is external, not a plain write.
_DESTRUCTIVE_RE = re.compile(
    r"^(delete|destroy|drop|remove|purge|wipe|terminate|kill|revoke|reset)",
    re.IGNORECASE,
)
_EXTERNAL_RE = re.compile(
    r"^(send|publish|post|share|notify|email|message|sms|tweet|broadcast|"
    r"deploy|trigger|webhook|invite|charge|pay|refund|order|ship)",
    re.IGNORECASE,
)
_WRITE_RE = re.compile(
    r"^(create|update|write|put|set|add|insert|edit|patch|append|save|"
    r"upsert|register|configure|approve|merge)",
    re.IGNORECASE,
)
_READ_RE = re.compile(
    r"^(get|list|read|fetch|search|describe|query|find|lookup|view|show|"
    r"check|validate|inspect|retrieve|status|health|ping)",
    re.IGNORECASE,
)

# Description fallback: when the name is ambiguous, scan the description for
# destructive / external / mutating language before defaulting to READ.
_DESC_DESTRUCTIVE_RE = re.compile(
    r"\b(delet|destruct|destroy|drop\b|purge|wipe|irreversible|permanent)\w*",
    re.IGNORECASE,
)
_DESC_EXTERNAL_RE = re.compile(
    r"\b(send|publish|notify|email|sms|webhook|external|deploy|charge|pay)\w*",
    re.IGNORECASE,
)
_DESC_WRITE_RE = re.compile(
    r"\b(creat|updat|writ|modif|mutat|persist|stor|save)\w*",
    re.IGNORECASE,
)


def classify_mcp_tool(name: str, description: str = "") -> str:
    """Classify a discovered MCP tool into READ|WRITE|EXTERNAL_ACTION|DESTRUCTIVE.

    Fail-closed default: unknown/ambiguous tools classify as WRITE (YELLOW),
    never as autonomous READ, unless the name clearly reads.
    """
    tool = (name or "").strip().lstrip("_")
    desc = description or ""
    if _DESTRUCTIVE_RE.match(tool):
        return "DESTRUCTIVE"
    if _EXTERNAL_RE.match(tool):
        return "EXTERNAL_ACTION"
    if _WRITE_RE.match(tool):
        return "WRITE"
    if _READ_RE.match(tool):
        return "READ"
    # Name was ambiguous: consult the description, most dangerous first.
    if _DESC_DESTRUCTIVE_RE.search(desc):
        return "DESTRUCTIVE"
    if _DESC_EXTERNAL_RE.search(desc):
        return "EXTERNAL_ACTION"
    if _DESC_WRITE_RE.search(desc):
        return "WRITE"
    # Fail closed: ambiguous tools are WRITE (approval-gated), never READ.
    return "WRITE"


def approval_for_category(category: str) -> str:
    """Return the frozen approval level (GREEN|YELLOW|RED) for a category."""
    try:
        return APPROVAL_MATRIX[category]
    except KeyError:
        raise ValueError(f"unknown MCP tool category: {category!r}") from None


def approval_for_tool(name: str, description: str = "") -> tuple[str, str]:
    """Classify a tool and return (category, approval_level)."""
    category = classify_mcp_tool(name, description)
    return category, approval_for_category(category)


def is_autonomous(category: str) -> bool:
    """True only for READ (GREEN) tools that may run without approval."""
    return approval_for_category(category) == "GREEN"


# ---------------------------------------------------------------------------
# Audit scrubbing: audit rows must NEVER contain secrets.
# ---------------------------------------------------------------------------

_SECRET_KEY_RE = re.compile(
    r"(secret|token|passwd|password|api[_-]?key|auth|bearer|credential|"
    r"private[_-]?key|session[_-]?key|client[_-]?secret)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(r"sk-[A-Za-z0-9_-]{4,}|gh[pousr]_[A-Za-z0-9]{4,}")
REDACTED = "[REDACTED]"


def scrub_value(key: str, value: object) -> object:
    """Redact a single audit value if its key or content looks secret-bearing."""
    if isinstance(value, str):
        if _SECRET_KEY_RE.search(key or "") or _SECRET_VALUE_RE.search(value):
            return REDACTED
        return value
    if isinstance(value, dict):
        return {str(k): scrub_value(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_value(key, v) for v in value]
    return value


def scrub_mapping(mapping: dict) -> dict:
    """Return a secret-scrubbed copy of a mapping for audit rows."""
    if not isinstance(mapping, dict):
        return {}
    return {str(k): scrub_value(str(k), v) for k, v in mapping.items()}


def audit_arg_keys(args: dict) -> list[str]:
    """Audit rows store argument KEY names only, never values (zero secrets)."""
    if not isinstance(args, dict):
        return []
    return sorted(str(k) for k in args)
