"""OCR / observation scrub: credentials must never appear in VisionObservation.

Extends the RAG deny-list patterns with vault refs and common auth forms.
Pattern matches are replaced or dropped; matched secret VALUES are never
echoed back to callers or logs.
"""
from __future__ import annotations

import re

from app.services.rag.secrets import PATTERNS as _RAG_PATTERNS

_REDACTED = "[REDACTED]"

_EXTRA_VALUE_PATTERNS = (
    re.compile(r"vault://[^\s\"']+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"),
    re.compile(
        r"(?i)\b(?:password|passwd|secret|token|api[_-]?key|authorization)"
        r"\s*[:=]\s*[^\s\"']+"
    ),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}"),
)


def _redact(text: str) -> str:
    out = text
    for pat in _EXTRA_VALUE_PATTERNS:
        out = pat.sub(_REDACTED, out)
    for pat in _RAG_PATTERNS:
        out = pat.sub(_REDACTED, out)
    return out


def contains_credential_like(text: str) -> bool:
    """True if text still matches a secret pattern after redaction attempt."""
    if not isinstance(text, str):
        return True
    redacted = _redact(text)
    for pat in list(_EXTRA_VALUE_PATTERNS) + list(_RAG_PATTERNS):
        if pat.search(redacted):
            return True
    return "vault://" in redacted


def scrub_text(text: str) -> str:
    """Return text with credential-like spans replaced by [REDACTED]."""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    return _redact(text)


def scrub_list(values: list[str]) -> list[str]:
    """Scrub each string; drop entries that remain credential-like or empty."""
    if not isinstance(values, list):
        raise TypeError("values must be list[str]")
    out: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise TypeError("values must be list[str]")
        cleaned = scrub_text(item).strip()
        if not cleaned:
            continue
        if contains_credential_like(cleaned):
            continue
        out.append(cleaned)
    return out
