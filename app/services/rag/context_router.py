"""ContextRouter: direct-state block + RAG enrichment + UNKNOWN block (plan §10).

NEVER-RAG rule by construction: the state block is passed in by the caller
(services read SQLite directly); the router never answers state questions
from hits. UNKNOWN-tagged chunks are listed separately and never asserted.
Staleness: cited files whose on-disk sha differs from documents.file_sha are
marked STALE (reindex itself is a Settings/W5 action, not done here).
"""
import hashlib
from pathlib import Path

MAX_CHUNKS = 8
MAX_CHARS = 6000

STATE_KEYS = ("company", "campaigns", "approvals", "measurements")


def _file_sha(root: Path, rel: str) -> str | None:
    try:
        return hashlib.sha256((root / rel).read_bytes()).hexdigest()
    except OSError:
        return None


def build_context(conn, root: str | Path, question: str, state: dict, hits: list[dict]) -> dict:
    """Return {state_block, enrichment, unknown, stale, provenance}."""
    root = Path(root)
    state_block = {k: state.get(k) for k in STATE_KEYS if k in state}
    enrichment, unknown, stale, provenance = [], [], [], []
    budget = MAX_CHARS
    for h in hits[:MAX_CHUNKS]:
        current = _file_sha(root, h["path"])
        is_stale = current is not None and h.get("file_sha") and current != h["file_sha"]
        entry = {
            "path": h["path"], "chunk_id": h["chunk_id"], "header": h.get("header", ""),
            "status_tag": h.get("status_tag", "UNKNOWN"),
            "sources": h.get("sources", []), "stale": bool(is_stale),
            "text": h.get("text", "")[:1500],
        }
        provenance.append({"path": h["path"], "chunk_id": h["chunk_id"],
                           "file_sha": h.get("file_sha", ""), "status_tag": entry["status_tag"]})
        if is_stale:
            stale.append(entry)
            continue
        if entry["status_tag"] == "UNKNOWN":
            unknown.append(entry)
        else:
            if budget <= 0:
                continue
            enrichment.append(entry)
            budget -= len(entry["text"])
    return {
        "question": question,
        "state_block": state_block,
        "enrichment": enrichment,
        "unknown": unknown,
        "stale": stale,
        "provenance": provenance,
    }
