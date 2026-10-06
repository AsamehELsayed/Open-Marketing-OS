"""W2 context-compression boundary: budget + UNKNOWN separation.

Wraps the context_router policy (MAX_CHUNKS/MAX_CHARS, stale/unknown split)
as a framework-compatible compressor so a future LangChain compressor
(e.g. LLMLingua / embeddings-filter) can replace the body without changing
callers. Default behavior is byte-compatible with context_router.
"""
from __future__ import annotations

from app.services.rag import context_router


class EvidenceCompressor:
    """Truncate hits to a char budget, preserving order. Pure function."""

    def __init__(self, max_chars: int = context_router.MAX_CHARS,
                 max_chunks: int = context_router.MAX_CHUNKS):
        self.max_chars = max_chars
        self.max_chunks = max_chunks

    def compress(self, hits: list[dict], *, max_chars: int | None = None) -> list[dict]:
        budget = self.max_chars if max_chars is None else max_chars
        out, used = [], 0
        for h in hits[: self.max_chunks]:
            text = h.get("text", "")
            if used + len(text) > budget:
                break
            out.append(h)
            used += len(text)
        return out
