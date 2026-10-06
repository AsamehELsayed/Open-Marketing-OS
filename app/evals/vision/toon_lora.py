"""TOON → Marketing LoRA evidence sample path (no LoRA import, no training).

Vision emits VisionObservation → vision_to_toon fenced block → this module
strips the fence and packages the TOON body as LoRA-dataset evidence metadata.
Marketing LoRA weights/logic remain untouched (I-6 / packet rule).
"""
from __future__ import annotations

from app.services.vision.interface import VisionObservation, vision_to_toon

_FENCE_PREFIX = "```toon\n"
_FENCE_SUFFIX = "\n```"


def toon_body_from_block(block: str) -> str:
    if not isinstance(block, str):
        raise TypeError("block must be str")
    if not block.startswith(_FENCE_PREFIX) or not block.endswith(_FENCE_SUFFIX):
        raise ValueError("expected a single fenced ```toon block")
    return block[len(_FENCE_PREFIX):-len(_FENCE_SUFFIX)]


def observation_to_lora_evidence(obs: VisionObservation) -> dict:
    """Package a vision observation as LoRA dataset evidence metadata.

    Returns a mapping suitable for evidence_labels / project_context attachment
    without importing or invoking the Marketing LoRA.
    """
    if not isinstance(obs, VisionObservation):
        raise TypeError("obs must be a VisionObservation")
    block = vision_to_toon(obs)
    body = toon_body_from_block(block)
    return {
        "status": "VERIFIED",
        "source": f"vision:{obs.provider}/{obs.model_id}",
        "confidence": obs.confidence,
        "toon_block": block,
        "toon_body": body,
        "schema_kind": "vision_observation",
        "text_seen_count": len(obs.text_seen),
        "element_count": len(obs.elements),
    }
