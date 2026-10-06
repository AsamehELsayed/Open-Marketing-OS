"""DEV-005 W4 — LoRA config RECORD (prep only, NO TRAINING).

Per docs/v1/lora-training-plan.md + model-benchmark-plan.md §4
(winner-before-LoRA rule): no training starts until a benchmark winner
is declared with reviewer APPROVE. This module stores the intended
hyperparameters as a record and REFUSES to build a trainable config
without a recorded winner manifest.

Winner manifest (created only after real benchmarks + reviewer APPROVE):
  development/runs/DEV-005/benchmarks/winner.json
  { "winner_declared": true, "reviewer_approval": "APPROVE",
    "model": "<name>", "weight_hash": "sha256:..." , ... }

Stdlib only. Never imports torch / transformers / peft. Never writes weights.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

LORA_GATE_MESSAGE = (
    "LoRA GATE CLOSED: no benchmark winner declared. "
    "Run docs/v1/model-benchmark-plan.md benchmarks on founder hardware, "
    "commit development/runs/DEV-005/benchmark-matrix.csv + raw logs, obtain "
    "reviewer APPROVE, and record development/runs/DEV-005/benchmarks/winner.json "
    "before any LoRA training. TRAINING: NOT_RUN, winner: UNDECLARED."
)

WINNER_MANIFEST_REL = Path("development/runs/DEV-005/benchmarks/winner.json")

DEFAULT_RANK = 8
DEFAULT_DROPOUT = 0.05


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "development" / "runs" / "DEV-005" / "plan.md").exists():
            return parent
    return here.parents[3] if len(here.parents) >= 4 else here.parent


def default_winner_path(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    return _repo_root() / WINNER_MANIFEST_REL


def load_winner_manifest(path: str | Path | None = None) -> dict | None:
    p = default_winner_path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_winner_declared(manifest: dict | None) -> bool:
    if not isinstance(manifest, dict):
        return False
    return bool(
        manifest.get("winner_declared") is True
        and manifest.get("reviewer_approval") == "APPROVE"
        and manifest.get("model")
        and manifest.get("weight_hash")
    )


def require_benchmark_winner(path: str | Path | None = None) -> dict:
    """Return the winner manifest or raise the closed-gate error."""
    manifest = load_winner_manifest(path)
    if not is_winner_declared(manifest):
        raise RuntimeError(LORA_GATE_MESSAGE)
    return manifest


@dataclass
class LoRAConfig:
    """Intended-training record. trainable only after the winner gate."""

    base_model: str = ""
    base_revision: str = ""
    tokenizer: str = ""
    dataset_hash: str = ""
    rank: int = DEFAULT_RANK
    alpha: int = DEFAULT_RANK * 2
    dropout: float = DEFAULT_DROPOUT
    target_modules: tuple = ("q_proj", "v_proj")
    use_qlora: bool = False
    seed: int = 42
    training_permitted: bool = False
    status: str = "NOT_RUN"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["target_modules"] = list(self.target_modules)
        return d


def describe_default_config() -> dict:
    """Inspectable record of intended defaults (does NOT permit training)."""
    return LoRAConfig().to_dict()


def build_training_config(
    *,
    base_model: str,
    base_revision: str,
    dataset_hash: str,
    rank: int = DEFAULT_RANK,
    dropout: float = DEFAULT_DROPOUT,
    target_modules: tuple = ("q_proj", "v_proj"),
    use_qlora: bool = False,
    seed: int = 42,
    winner_manifest: str | Path | None = None,
) -> LoRAConfig:
    """Build a trainable config ONLY with a recorded benchmark winner.

    Raises RuntimeError with the closed-gate message when no valid
    winner manifest exists. Never starts training itself.
    """
    winner = require_benchmark_winner(winner_manifest)
    if base_model != winner.get("model"):
        raise RuntimeError(
            "LoRA GATE CLOSED: requested base_model does not match the recorded "
            f"benchmark winner ({winner.get('model')!r}). LoRA trains only on the "
            "winner base (model-benchmark-plan §4)."
        )
    if rank not in (8, 16):
        raise ValueError("rank must be 8 or 16 per lora-training-plan.md")
    return LoRAConfig(
        base_model=base_model,
        base_revision=base_revision,
        tokenizer=winner.get("tokenizer", base_model),
        dataset_hash=dataset_hash,
        rank=rank,
        alpha=2 * rank,
        dropout=dropout,
        target_modules=tuple(target_modules),
        use_qlora=use_qlora,
        seed=seed,
        training_permitted=True,
        status="GATED_READY_NOT_RUN",
    )
