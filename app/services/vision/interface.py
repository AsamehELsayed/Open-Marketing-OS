"""DEV-007 frozen vision provider interface (Phase A stub + Phase B, I-6).

Pipeline split (perception vs reasoning): image -> vision observations ->
TOON evidence -> Marketing LoRA -> marketing reasoning. This module owns
perception and evidence encoding ONLY; it never imports, loads, or modifies
the Marketing LoRA or its weights.

Phase A status:
- observe() is a FAKE stub while OBSERVE_IS_FAKE is True, clearly marked FAKE
  (docstring, OBSERVE_IS_FAKE flag, and FAKE markers in every produced field).
  It performs no inference, loads no weights, and makes no network calls.
- vision_to_toon() is REAL: it builds a fenced TOON evidence block per
  docs/v1/toon-contract.md (schema version reuses the W0 contract constant,
  stable field order, explicit [N] row counts, bounded fields) through one
  maintained encoder (_quote/_escape) following the TOON quoting rules
  (toonformat.dev syntax cheatsheet v4.1).

Phase B (W3-impl): real local + optional OpenAI backends live behind the same
frozen signature. OBSERVE_IS_FAKE stays True until a real provider is wired
and smoke-verified on this host; flip only after that smoke passes.
"""
import hashlib
import re
from dataclasses import dataclass

from app.contracts.toon_boundary import TOON_SCHEMA_VERSION

CONFIDENCE_LEVELS = ("high", "medium", "low")
PROVIDERS = ("local", "openai")

# Phase-A marker. Phase B (W3-impl) flips this to False only after a real
# provider is wired and smoke-verified; the packet signature stays frozen.
OBSERVE_IS_FAKE = True

_KIND = "vision_observation"
_MAX_ROWS = 64
_MAX_FIELD_CHARS = 500
_MAX_TOTAL_CHARS = 8000
_FORBIDDEN_OUTPUT_KEYS = frozenset({
    "api_key", "secret", "token", "password",
    "chain_of_thought", "reasoning_content",
})

_NUMBER_LIKE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_LITERAL_STRINGS = frozenset({"true", "false", "null", "NaN", "Infinity", "-Infinity"})
# TOON must-quote special characters (plus document/active delimiter ','
# and any control character — handled below).
_SPECIAL_CHARS = frozenset('":\\[]{}')


@dataclass(frozen=True)
class VisionObservation:
    """Immutable perception result produced by observe() (frozen per I-6)."""

    summary: str
    elements: list[str]
    text_seen: list[str]
    confidence: str  # one of CONFIDENCE_LEVELS: high | medium | low
    provider: str    # one of PROVIDERS: local | openai
    model_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.summary, str):
            raise TypeError("summary must be str")
        if not isinstance(self.model_id, str):
            raise TypeError("model_id must be str")
        for name in ("elements", "text_seen"):
            value = getattr(self, name)
            if not isinstance(value, list):
                raise TypeError(f"{name} must be list[str]")
            if not all(isinstance(item, str) for item in value):
                raise TypeError(f"{name} must be list[str]")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(
                f"confidence must be one of {CONFIDENCE_LEVELS}, got {self.confidence!r}")
        if self.provider not in PROVIDERS:
            raise ValueError(
                f"provider must be one of {PROVIDERS}, got {self.provider!r}")


def _fake_observe(image_bytes: bytes, provider: str) -> VisionObservation:
    """Phase-A deterministic stub (no model, no weights, no network)."""
    digest = hashlib.sha256(image_bytes).hexdigest()
    return VisionObservation(
        summary=f"FAKE observation: no inference performed (sha256:{digest[:12]}).",
        elements=[
            f"FAKE element: image sha256:{digest[:12]}",
            f"FAKE element: byte_length={len(image_bytes)}",
        ],
        text_seen=[],
        confidence="low",
        provider=provider,
        model_id=f"FAKE/phase-a/{provider}",
    )


def _dispatch_real(image_bytes: bytes, provider: str) -> VisionObservation:
    """Phase-B dispatch to real backends; used only when OBSERVE_IS_FAKE is False."""
    from app.services.vision.local_provider import LocalVisionProvider
    from app.services.vision.openai_provider import OpenAIVisionProvider

    if provider == "local":
        backend = LocalVisionProvider()
    elif provider == "openai":
        backend = OpenAIVisionProvider()
    else:
        raise ValueError(f"provider must be one of {PROVIDERS}, got {provider!r}")
    return backend.observe(image_bytes)


def observe(image_bytes: bytes, *, provider: str = "local") -> VisionObservation:
    """Frozen I-6 entrypoint: FAKE while OBSERVE_IS_FAKE=True, else real dispatch.

    Signature is frozen. While OBSERVE_IS_FAKE is True this returns the FAKE
    Phase-A stub (no model, no weights, no network): deterministic sha256-derived
    fields, confidence "low", empty text_seen. When the flag is False, dispatches
    to the Phase-B local or OpenAI backend (fail-closed if that backend cannot run).
    """
    if provider not in PROVIDERS:
        raise ValueError(
            f"provider must be one of {PROVIDERS}, got {provider!r}")
    if not isinstance(image_bytes, bytes):
        raise TypeError("image_bytes must be bytes")
    if OBSERVE_IS_FAKE:
        return _fake_observe(image_bytes, provider)
    return _dispatch_real(image_bytes, provider)


def _escape(value: str) -> str:
    """Single-pass TOON string escape (the one maintained encoder path)."""
    out: list[str] = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _quote(value: str) -> str:
    """TOON quoting rules: quote only when required; otherwise pass through."""
    must_quote = (
        value == ""
        or value != value.strip()
        or value in _LITERAL_STRINGS
        or bool(_NUMBER_LIKE.match(value))
        or value.startswith("-")   # "-" or "-..." reads as comment/number-ish
        or value.startswith("#")   # would read as a comment line
        or any(ch in _SPECIAL_CHARS or ch == "," or ord(ch) < 32 for ch in value)
    )
    if not must_quote:
        return value
    return '"' + _escape(value) + '"'


def _array_line(name: str, values: list[str]) -> str:
    if not values:
        return f"{name}: []"
    return f"{name}[{len(values)}]: " + ",".join(_quote(v) for v in values)


def _enforce_bounds(obs: VisionObservation) -> None:
    """Bound rows, field lengths, and total payload before encoding
    (toon-contract §Encoding rules: oversized packs fail closed)."""
    if len(obs.elements) > _MAX_ROWS or len(obs.text_seen) > _MAX_ROWS:
        raise ValueError(
            f"row budget exceeded (max {_MAX_ROWS} per list)")
    strings = [obs.summary, obs.model_id, *obs.elements, *obs.text_seen]
    for s in strings:
        if len(s) > _MAX_FIELD_CHARS:
            raise ValueError(
                f"field budget exceeded (max {_MAX_FIELD_CHARS} chars)")
    if sum(len(s) for s in strings) > _MAX_TOTAL_CHARS:
        raise ValueError(
            f"total payload budget exceeded (max {_MAX_TOTAL_CHARS} chars)")


def vision_to_toon(obs: VisionObservation) -> str:
    """Build a fenced ```toon evidence block from a VisionObservation (REAL).

    Stable field order: schema_version, kind, provider, model_id, confidence,
    summary, elements, text_seen. Values pass through the single _quote
    encoder; the output carries no secrets or hidden-reasoning keys.
    """
    if not isinstance(obs, VisionObservation):
        raise TypeError("obs must be a VisionObservation")
    _enforce_bounds(obs)
    lines = [
        f"schema_version: {_quote(TOON_SCHEMA_VERSION)}",
        f"kind: {_quote(_KIND)}",
        f"provider: {_quote(obs.provider)}",
        f"model_id: {_quote(obs.model_id)}",
        f"confidence: {_quote(obs.confidence)}",
        f"summary: {_quote(obs.summary)}",
        _array_line("elements", obs.elements),
        _array_line("text_seen", obs.text_seen),
    ]
    for line in lines:
        key = line.split(":", 1)[0].split("[", 1)[0].strip()
        if key in _FORBIDDEN_OUTPUT_KEYS:
            raise ValueError(f"forbidden key in TOON block: {key}")
    return "```toon\n" + "\n".join(lines) + "\n```"
