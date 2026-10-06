"""Secret deny-list for the index pipeline (acceptance R7)."""
import re

PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{8,}"),
    re.compile(r"xox[bap]-[A-Za-z0-9-]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|client_secret)\s*[:=]\s*\S+"),
    re.compile(r"vault://[^\s\"']+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*[^\s\"']+"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{10,}"),
)


def find_secrets(text: str) -> list[str]:
    """Return pattern descriptions matched (values never returned — only kinds)."""
    return [p.pattern[:24] for p in PATTERNS if p.search(text)]


def contains_secrets(text: str) -> bool:
    return any(p.search(text) for p in PATTERNS)
