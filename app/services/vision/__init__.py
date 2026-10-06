"""Vision perception service (DEV-007 Phase A stub + Phase B backends, I-6).

Perception only: image -> VisionObservation -> TOON evidence for the model
boundary. While OBSERVE_IS_FAKE is True, observe() is the FAKE stub (marked
FAKE in interface.py) and vision_to_toon() is the REAL builder. Phase B adds
local/OpenAI backends behind the same signature without importing the
Marketing LoRA; downstream reasoning consumes the TOON evidence only.
"""
from .errors import (
    VisionIngressError,
    VisionNotConfiguredError,
    VisionProviderError,
)
from .interface import VisionObservation, observe, vision_to_toon
from .ingress import observe_image_file, image_bytes_from_record, image_disk_path
from .local_provider import LocalVisionProvider
from .openai_provider import OpenAIVisionProvider
from .scrub import scrub_list, scrub_text

__all__ = [
    "VisionObservation",
    "observe",
    "vision_to_toon",
    "VisionProviderError",
    "VisionNotConfiguredError",
    "VisionIngressError",
    "LocalVisionProvider",
    "OpenAIVisionProvider",
    "observe_image_file",
    "image_bytes_from_record",
    "image_disk_path",
    "scrub_text",
    "scrub_list",
]
