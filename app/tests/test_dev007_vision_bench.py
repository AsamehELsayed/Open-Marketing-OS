"""DEV-007 W3-bench stub tests: interface shape, TOON builder output, fake markers.

Phase A only. No model weights, no network, no Marketing LoRA imports.
Spec authority: frozen stub in the DEV-007 W3-bench packet (interface I-6).
"""
import dataclasses
import inspect

import pytest

from app.services.vision import VisionObservation, observe, vision_to_toon
from app.services.vision import interface as vision_interface


# ---------- interface shape (I-6.1 / I-6.2 / I-6.3) ----------

def test_vision_observation_is_frozen_dataclass():
    assert dataclasses.is_dataclass(VisionObservation)
    assert VisionObservation.__dataclass_params__.frozen is True
    assert VisionObservation.__module__ == "app.services.vision.interface"


def test_vision_observation_field_names_and_order():
    names = [f.name for f in dataclasses.fields(VisionObservation)]
    assert names == ["summary", "elements", "text_seen",
                     "confidence", "provider", "model_id"]


def test_vision_observation_field_types():
    types = {f.name: f.type for f in dataclasses.fields(VisionObservation)}
    assert types["summary"] is str
    assert types["elements"] == list[str]
    assert types["text_seen"] == list[str]
    assert types["confidence"] is str
    assert types["provider"] is str
    assert types["model_id"] is str


def test_vision_observation_rejects_mutation():
    obs = VisionObservation(summary="s", elements=[], text_seen=[],
                            confidence="low", provider="local", model_id="m")
    with pytest.raises(dataclasses.FrozenInstanceError):
        obs.summary = "changed"  # type: ignore[misc]


def test_vision_observation_validates_confidence_literals():
    base = dict(summary="s", elements=[], text_seen=[],
                provider="local", model_id="m")
    for level in ("high", "medium", "low"):
        assert VisionObservation(confidence=level, **base).confidence == level
    with pytest.raises(ValueError):
        VisionObservation(confidence="very_high", **base)


def test_vision_observation_validates_provider_literals():
    base = dict(summary="s", elements=[], text_seen=[],
                confidence="medium", model_id="m")
    for prov in ("local", "openai"):
        assert VisionObservation(provider=prov, **base).provider == prov
    with pytest.raises(ValueError):
        VisionObservation(provider="anthropic", **base)


def test_vision_observation_rejects_non_string_list_members():
    with pytest.raises(TypeError):
        VisionObservation(summary="s", elements=["ok", 7], text_seen=[],
                          confidence="low", provider="local", model_id="m")
    with pytest.raises(TypeError):
        VisionObservation(summary="s", elements=[], text_seen="nope",
                          confidence="low", provider="local", model_id="m")


def test_observe_signature_matches_frozen_stub():
    sig = inspect.signature(observe)
    assert list(sig.parameters) == ["image_bytes", "provider"]
    assert sig.parameters["image_bytes"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert sig.parameters["image_bytes"].default is inspect.Parameter.empty
    assert sig.parameters["provider"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["provider"].default == "local"
    assert sig.return_annotation is VisionObservation


def test_observe_rejects_unknown_provider_and_non_bytes():
    with pytest.raises(ValueError):
        observe(b"\x00", provider="anthropic")
    with pytest.raises(TypeError):
        observe("not-bytes")  # type: ignore[arg-type]


# ---------- fake-marked (I-6.4) ----------

def test_observe_is_explicitly_marked_fake():
    assert vision_interface.OBSERVE_IS_FAKE is True
    assert "FAKE" in (observe.__doc__ or "")
    assert "FAKE" in (vision_interface.__doc__ or "")
    assert "Phase A" in (vision_interface.__doc__ or "")


def test_fake_observation_carries_fake_markers_and_honest_confidence():
    obs = observe(b"\x89PNG-fake")
    assert "FAKE" in obs.summary
    assert "FAKE" in obs.model_id
    assert obs.confidence == "low"          # honest: stub performs no inference
    assert obs.provider == "local"
    assert obs.text_seen == []              # honest: stub performs no OCR
    assert all("FAKE" in e for e in obs.elements)


def test_fake_observe_is_deterministic_per_input():
    a1 = observe(b"same-bytes")
    a2 = observe(b"same-bytes")
    b = observe(b"other-bytes")
    assert a1 == a2
    assert a1.summary != b.summary
    openai_obs = observe(b"same-bytes", provider="openai")
    assert openai_obs.provider == "openai"
    assert openai_obs != a1


# ---------- TOON builder output (I-6.5) ----------

def _golden() -> VisionObservation:
    return VisionObservation(
        summary="Dark UI dashboard with a bright CTA button",
        elements=["header nav", "hero image of a red sneaker",
                  "price tag 49,99 EUR"],
        text_seen=["Buy now", "مرحبا"],
        confidence="high",
        provider="local",
        model_id="qwen2-vl-2b-instruct",
    )


def test_vision_to_toon_wraps_single_fenced_toon_block():
    out = vision_to_toon(_golden())
    assert out.startswith("```toon\n")
    assert out.endswith("\n```")
    assert out.count("```toon") == 1


def test_vision_to_toon_stable_field_order_and_counts():
    from app.contracts.toon_boundary import TOON_SCHEMA_VERSION
    obs = _golden()
    body = vision_to_toon(obs)[len("```toon\n"):-len("\n```")]
    lines = body.split("\n")
    assert lines[0] == f'schema_version: "{TOON_SCHEMA_VERSION}"'
    assert lines[1] == "kind: vision_observation"
    assert lines[2] == "provider: local"
    assert lines[3] == "model_id: qwen2-vl-2b-instruct"
    assert lines[4] == "confidence: high"
    assert lines[5] == "summary: Dark UI dashboard with a bright CTA button"
    # explicit row counts match list lengths (TOON [N] guardrail);
    # internal spaces stay bare, only the comma-containing value quotes
    assert lines[6] == ('elements[3]: header nav,'
                        'hero image of a red sneaker,"price tag 49,99 EUR"')
    # Arabic preserved raw (UTF-8); comma-free value stays unquoted
    assert lines[7] == "text_seen[2]: Buy now,مرحبا"
    assert len(lines) == 8
    # deterministic
    assert vision_to_toon(obs) == vision_to_toon(obs)


def test_vision_to_toon_quotes_and_escapes_special_values():
    obs = VisionObservation(
        summary='summary: with colon',          # ":" forces quoting
        elements=["line1\nline2", "- bullet", "# tag",
                  'say "hi"', "42", "plain"],
        text_seen=[],
        confidence="low",
        provider="local",
        model_id="FAKE/phase-a/local",
    )
    body = vision_to_toon(obs)
    assert 'summary: "summary: with colon"' in body
    assert '"line1\\nline2"' in body          # newline escaped inside quotes
    assert '"- bullet"' in body               # leading "-" quoted
    assert '"# tag"' in body                  # leading "#" quoted
    assert '"say \\"hi\\""' in body           # quote escaped
    assert '"42"' in body                     # number-like string quoted
    assert ",plain" in body                   # plain value unquoted
    assert "text_seen: []" in body            # empty array encoding


def test_vision_to_toon_emits_only_allowed_keys():
    body = vision_to_toon(_golden())[len("```toon\n"):-len("\n```")]
    allowed = {"schema_version", "kind", "provider", "model_id",
               "confidence", "summary", "elements", "text_seen"}
    keys = {line.split(":", 1)[0].split("[", 1)[0].strip()
            for line in body.split("\n")}
    assert keys <= allowed
    for forbidden in ("api_key", "secret", "token", "password",
                      "chain_of_thought", "reasoning_content"):
        assert forbidden not in keys


def test_vision_to_toon_enforces_bounds_fail_closed():
    base = dict(text_seen=[], confidence="low",
                provider="local", model_id="m")
    too_many = VisionObservation(summary="s",
                                 elements=[f"e{i}" for i in range(65)], **base)
    with pytest.raises(ValueError):
        vision_to_toon(too_many)
    too_long = VisionObservation(elements=[], summary="x" * 501, **base)
    with pytest.raises(ValueError):
        vision_to_toon(too_long)
    under = VisionObservation(summary="s",
                              elements=[f"e{i}" for i in range(64)], **base)
    assert vision_to_toon(under).startswith("```toon\n")


def test_vision_to_toon_rejects_non_observation_input():
    with pytest.raises(TypeError):
        vision_to_toon({"summary": "dict"})  # type: ignore[arg-type]
