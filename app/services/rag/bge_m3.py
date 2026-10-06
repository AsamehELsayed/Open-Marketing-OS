"""W2 BGE-M3 embedding candidate: config record only. No download, no spend.

BGE-M3 (BAAI/bge-m3, multilingual, 1024-dim) is the rag-plan embedding
candidate alongside the shipped local-hash baseline. This module records
the candidate identity and refuses to embed until weights are vendored
locally AND the benchmark gain gate passes. It never touches the network.

Upgrade path (needs W3 hardware winner + founder approval, out of W2):
vendored weights dir → implement embed() via the local runtime → rerun
benchmark.compare() → ship only on measured Recall@K gain within budget.
"""
from __future__ import annotations

from pathlib import Path

MODEL_ID = "BAAI/bge-m3"
DIM = 1024
WEIGHTS_DIR = "models/bge-m3"


class BGEM3Candidate:
    """Candidate embedding config. embed() always refuses in W2."""

    id = "bge-m3"
    version = "candidate"
    dim = DIM

    def __init__(self, weights_dir: str = WEIGHTS_DIR):
        self.weights_dir = weights_dir

    def is_available(self) -> bool:
        p = Path(self.weights_dir)
        try:
            return p.is_dir() and any(p.iterdir())
        except OSError:
            return False

    def embed(self, text: str) -> list[float]:
        raise RuntimeError(
            "BGE-M3 weights are not vendored (no download in W2 scope); "
            "local-hash remains the baseline. See docs/v1/rag-plan.md benchmarks."
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("BGE-M3 weights are not vendored (no download in W2 scope).")

    def embed_query(self, text: str) -> list[float]:
        return self.embed(text)
