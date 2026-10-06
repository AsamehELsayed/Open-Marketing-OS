"""Optional OpenAI vision provider (DEV-007 Phase B).

API key is resolved server-side from a vault ref only (frozen I-3). Paid
network calls are off by default (allow_paid=False) and never run while
OBSERVE_IS_FAKE is True (dispatch enforces that). Keys are never logged or
placed on VisionObservation fields.
"""
from __future__ import annotations

import base64
import json
from typing import Callable

from app.services.vision.errors import VisionNotConfiguredError, VisionProviderError
from app.services.vision.interface import VisionObservation
from app.services.vision.scrub import scrub_list, scrub_text

DEFAULT_MODEL_ID = "gpt-4o-mini"
VAULT_SCOPE = "installation"
VAULT_LABEL = "integration_openai"

# post: (api_key, model_id, image_b64, prompt) -> raw model text/JSON (test seam)
HttpPost = Callable[[str, str, str, str], "str | dict"]


def resolve_openai_key() -> str:
    """Resolve the OpenAI key through the shared ConfigService authority."""
    try:
        from app.services.config_service import ConfigService
        value = ConfigService.get_openai_api_key()
    except Exception as exc:
        raise VisionNotConfiguredError("OpenAI vision is not configured") from exc
    if not value:
        raise VisionNotConfiguredError("OpenAI vision is not configured")
    return value


def _parse_output(raw: object) -> tuple[str, list[str], list[str]]:
    if isinstance(raw, dict):
        summary = str(raw.get("summary") or "").strip() or "OpenAI vision observation."
        elements = [str(x) for x in (raw.get("elements") or [])]
        text_seen = [str(x) for x in (raw.get("text_seen") or [])]
        return summary, elements, text_seen
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            summary = lines[0] if lines else "OpenAI vision observation."
            return summary, [], lines[1:]
        if isinstance(data, dict):
            return _parse_output(data)
    raise VisionProviderError("OpenAI vision response was not parseable")


class OpenAIVisionProvider:
    """OpenAI backend behind frozen observe(); gated, vault-ref only."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        allow_paid: bool = False,
        http_post: HttpPost | None = None,
        key_resolver: Callable[[], str] | None = None,
    ) -> None:
        self.model_id = model_id
        self.allow_paid = bool(allow_paid)
        self._http_post = http_post
        self._key_resolver = key_resolver or resolve_openai_key

    def can_run(self) -> tuple[bool, str]:
        if not self.allow_paid:
            return False, "paid calls disabled (allow_paid=False)"
        if self._http_post is None:
            return False, "no http transport configured"
        try:
            self._key_resolver()
        except VisionNotConfiguredError as exc:
            return False, str(exc)
        return True, "ok"

    def observe(self, image_bytes: bytes) -> VisionObservation:
        if not isinstance(image_bytes, bytes):
            raise TypeError("image_bytes must be bytes")
        if not image_bytes:
            raise VisionProviderError("empty image_bytes")
        if not self.allow_paid:
            raise VisionNotConfiguredError(
                "OpenAI vision paid call disabled (allow_paid=False)"
            )
        api_key = self._key_resolver()
        if self._http_post is None:
            raise VisionProviderError("OpenAI vision http transport not configured")
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        prompt = (
            "Describe this image for marketing evidence. "
            "Return JSON with keys summary (string), elements (array of strings), "
            "text_seen (array of strings). No secrets."
        )
        raw = self._http_post(api_key, self.model_id, image_b64, prompt)
        summary, elements, text_seen = _parse_output(raw)
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
            provider="openai",
            model_id=self.model_id,
        )
