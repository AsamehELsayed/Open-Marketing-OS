"""DEV-005 W4 — LoRA adapter packaging RECORD (prep only, NO TRAINING).

Per docs/v1/lora-training-plan.md §Packaging: the adapter is stored
separately with a manifest (base hash, adapter hash/name, compatible
quantization/runtime, eval report, license). Rollback disables the
adapter in config without touching base model or data.

W4 builds the manifest SHAPE only. No adapter weights are produced,
written, or shipped here. Any attempt to train or publish without a
recorded benchmark winner raises the closed-gate error.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.evals.models.lora_config import (
    LORA_GATE_MESSAGE,
    require_benchmark_winner,
)

ADAPTER_STATUS_NOT_TRAINED = "NOT_TRAINED"
ADAPTER_STATUS_NOT_SHIPPED = "NOT_SHIPPED"
ADAPTER_NAME_PREFIX = "OMOS-LoRA-"

REQUIRED_MANIFEST_FIELDS = (
    "adapter_name",
    "base_model",
    "base_hash",
    "adapter_hash",
    "quantization",
    "runtime",
    "eval_report",
    "license",
    "status",
)


@dataclass
class AdapterManifest:
    adapter_name: str = ""
    base_model: str = ""
    base_hash: str = ""
    adapter_hash: str = ""
    quantization: str = ""
    runtime: str = "llama.cpp"
    eval_report: str = ""
    license: str = ""
    status: str = ADAPTER_STATUS_NOT_TRAINED

    def to_dict(self) -> dict:
        return asdict(self)


def _placeholder(value: str, label: str) -> str:
    return value or f"UNASSIGNED ({label})"


def missing_manifest_fields(manifest: dict) -> list[str]:
    if not isinstance(manifest, dict):
        return list(REQUIRED_MANIFEST_FIELDS)
    return [f for f in REQUIRED_MANIFEST_FIELDS if not manifest.get(f)]


def build_packaging_record(
    *,
    base_model: str,
    base_hash: str,
    quantization: str = "",
    runtime: str = "llama.cpp",
    license: str = "",
) -> AdapterManifest:
    """Describe where the accepted adapter WOULD be recorded (no weights).

    The OMOS-LoRA-v1 name is assigned only after acceptance; until then
    the record is explicitly NOT_TRAINED / NOT_SHIPPED.
    """
    return AdapterManifest(
        adapter_name="UNASSIGNED (OMOS-LoRA-v1 only after acceptance)",
        base_model=base_model,
        base_hash=base_hash,
        adapter_hash="UNASSIGNED (no training executed)",
        quantization=_placeholder(quantization, "compatible quantization at acceptance"),
        runtime=runtime,
        eval_report="UNASSIGNED (base-vs-adapter eval not run)",
        license=_placeholder(license, "record base + adapter license at acceptance"),
        status=f"{ADAPTER_STATUS_NOT_TRAINED}/{ADAPTER_STATUS_NOT_SHIPPED}",
    )


def train_adapter(*args, **kwargs) -> None:
    """Refuse training: gate is closed in W4 (no winner, no weights)."""
    require_benchmark_winner(kwargs.get("winner_manifest"))
    raise RuntimeError(
        "LoRA training is NOT implemented in W4 (prep only). "
        + LORA_GATE_MESSAGE
    )


def publish_adapter(*args, **kwargs) -> None:
    """Refuse publishing: no adapter passed its gain gate in W4."""
    require_benchmark_winner(kwargs.get("winner_manifest"))
    raise RuntimeError(
        "LoRA publish refused: no adapter cleared the base-vs-adapter gain gate "
        "(lora-training-plan.md). Status stays NOT_SHIPPED; keep the base model. "
        + LORA_GATE_MESSAGE
    )
