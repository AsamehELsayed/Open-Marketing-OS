"""Local vision provider (DEV-007 Phase B): Qwen2-VL-2B-Instruct path.

Never downloads weights (>500 MB blocker). Loads only from a local weights
directory (local_files_only). Without torch/transformers/weights, observe()
fails closed with VisionProviderError — it never fabricates inference.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from app.services.vision.errors import VisionProviderError
from app.services.vision.interface import CONFIDENCE_LEVELS, VisionObservation
from app.services.vision.scrub import scrub_list, scrub_text

DEFAULT_MODEL_ID = "qwen2-vl-2b-instruct"
DEFAULT_WEIGHTS_ENV = "VISION_LOCAL_WEIGHTS"
_DEFAULT_MAX_PIXELS = 1024 * 1024

# runner: optional test/smoke seam — (image_bytes) -> raw dict/str from model
Runner = Callable[[bytes], "dict[str, object] | str"]


def local_weights_path(env: dict[str, str] | None = None) -> Path | None:
    source = os.environ if env is None else env
    raw = source.get(DEFAULT_WEIGHTS_ENV, "").strip()
    if not raw:
        return None
    return Path(raw)


def local_deps_status() -> tuple[bool, str]:
    missing: list[str] = []
    for mod in ("torch", "transformers"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return False, f"missing packages: {', '.join(missing)}"
    return True, "ok"


def _coerce_confidence(value: object) -> str:
    if isinstance(value, str) and value in CONFIDENCE_LEVELS:
        return value
    return "medium"


def _parse_runner_output(raw: object) -> tuple[str, list[str], list[str]]:
    if isinstance(raw, dict):
        summary = str(raw.get("summary") or raw.get("text") or "").strip()
        elements_raw = raw.get("elements") or []
        text_raw = raw.get("text_seen") or raw.get("ocr") or []
        if not isinstance(elements_raw, (list, tuple)):
            elements_raw = [str(elements_raw)]
        if not isinstance(text_raw, (list, tuple)):
            text_raw = [str(text_raw)]
        elements = [str(x) for x in elements_raw]
        text_seen = [str(x) for x in text_raw]
        if not summary:
            summary = "; ".join(text_seen)[:400] or "Local vision observation."
        return summary, elements, text_seen
    if isinstance(raw, str):
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        summary = lines[0] if lines else "Local vision observation."
        text_seen = lines[1:] if len(lines) > 1 else []
        return summary, [], text_seen
    raise VisionProviderError("local runner returned unsupported output type")


class LocalVisionProvider:
    """Local backend behind frozen observe(); real inference when deps exist."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        max_pixels: int = _DEFAULT_MAX_PIXELS,
        runner: Runner | None = None,
        weights_dir: str | Path | None = None,
    ) -> None:
        self.model_id = model_id
        self.max_pixels = int(max_pixels)
        self._runner = runner
        self._weights_dir = (
            Path(weights_dir) if weights_dir is not None else local_weights_path()
        )

    def can_run(self) -> tuple[bool, str]:
        if self._runner is not None:
            return True, "runner injected"
        ok, detail = local_deps_status()
        if not ok:
            return False, detail
        if self._weights_dir is None or not self._weights_dir.exists():
            return False, "local vision weights are not installed"
        return True, "ok"

    def observe(self, image_bytes: bytes) -> VisionObservation:
        if not isinstance(image_bytes, bytes):
            raise TypeError("image_bytes must be bytes")
        if not image_bytes:
            raise VisionProviderError("empty image_bytes")
        ok, detail = self.can_run()
        if not ok:
            raise VisionProviderError(f"local provider unavailable: {detail}")
        raw = (
            self._runner(image_bytes)
            if self._runner is not None
            else self._run_hf(image_bytes)
        )
        summary, elements, text_seen = _parse_runner_output(raw)
        summary = scrub_text(summary)
        elements = scrub_list(elements)
        text_seen = scrub_list(text_seen)
        if len(summary) > 500:
            summary = summary[:500]
        return VisionObservation(
            summary=summary,
            elements=elements[:64],
            text_seen=text_seen[:64],
            confidence="medium",
            provider="local",
            model_id=self.model_id,
        )

    def _run_hf(self, image_bytes: bytes) -> dict[str, object]:
        try:
            import torch
            from transformers import (
                AutoProcessor,
                Qwen2VLForConditionalGeneration,
            )
        except ImportError:
            raise VisionProviderError(
                "local vision runtime packages are unavailable"
            ) from None
        if self._weights_dir is None or not self._weights_dir.exists():
            raise VisionProviderError("local vision weights are not installed")
        try:
            from PIL import Image
            import io

            import qwen_vl_utils  # type: ignore
        except ImportError:
            raise VisionProviderError(
                "local vision runtime helpers are unavailable"
            ) from None

        model = Qwen2VLForConditionalGeneration.from_pretrained(
            str(self._weights_dir),
            local_files_only=True,
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        )
        processor = AutoProcessor.from_pretrained(
            str(self._weights_dir), local_files_only=True
        )
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "image": image,
                        "min_pixels": 256 * 256,
                        "max_pixels": self.max_pixels,
                    },
                    {
                        "type": "text",
                        "text": (
                            "Describe this image for marketing evidence. "
                            "List UI regions/objects, extract all visible text "
                            "line by line, then a one-sentence summary."
                        ),
                    },
                ],
            }
        ]
        text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = processor(
            text=[text],
            images=[image],
            padding=True,
            return_tensors="pt",
        ).to(model.device)
        with torch.no_grad():
            generated = model.generate(**inputs, max_new_tokens=512, do_sample=False)
        generated = generated[:, inputs.input_ids.shape[1]:]
        output = processor.batch_decode(
            generated, skip_special_tokens=True, clean_up_tokenization_spaces=False
        )[0]
        lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
        summary = lines[0] if lines else "Local vision observation."
        return {
            "summary": summary,
            "elements": lines[:32],
            "text_seen": lines[1:64],
        }
