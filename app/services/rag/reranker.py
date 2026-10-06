"""W2 reranker boundary: optional multilingual rerank, off by default.

- PassThroughReranker: returns hits unchanged (default; zero cost).
- BG Reranker candidate (bge-reranker-v2-m3) ships ONLY if a measured
  quality gain exceeds its latency/memory cost (rag-plan gate). Until then
  CrossEncoderReranker raises with guidance and never downloads weights.
"""
from __future__ import annotations


class PassThroughReranker:
    """No-op reranker: preserves fused order. Default for all paths."""

    id = "passthrough"
    version = "1"

    def rerank(self, query: str, hits: list[dict], *, top_n: int = 6) -> list[dict]:
        return list(hits[:max(0, top_n)])


class CrossEncoderReranker:
    """Candidate boundary for bge-reranker-v2-m3. Not shipped (no weights).

    Constructing is allowed (config record); rerank() refuses until weights
    are locally present AND the gain gate has been measured. Never downloads.
    """

    id = "bge-reranker-v2-m3"
    version = "candidate"

    def __init__(self, weights_dir: str = "models/bge-reranker-v2-m3"):
        self.weights_dir = weights_dir

    def is_available(self) -> bool:
        from pathlib import Path

        p = Path(self.weights_dir)
        return p.is_dir() and any(p.iterdir())

    def rerank(self, query: str, hits: list[dict], *, top_n: int = 6) -> list[dict]:
        raise RuntimeError(
            "bge-reranker-v2-m3 weights are not vendored and the reranker gain gate "
            "has not been measured; refusing to download. See docs/v1/rag-plan.md. "
            "Using PassThroughReranker instead."
        )
