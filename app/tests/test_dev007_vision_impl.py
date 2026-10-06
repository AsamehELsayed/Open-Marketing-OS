"""DEV-007 W3-impl tests: real backends, ingress, OCR-leak, TOON→LoRA seam.

OBSERVE_IS_FAKE stays True (no weights download, no paid calls); real paths
are exercised via injected runners / fake dispatch seams.
"""
from __future__ import annotations

import dataclasses
import inspect
import sqlite3
from pathlib import Path

import pytest

from app.contracts.toon_boundary import safety_check, validate_response_fields
from app.evals.vision.toon_lora import (
    observation_to_lora_evidence,
    toon_body_from_block,
)
from app.services.vision import (
    LocalVisionProvider,
    OpenAIVisionProvider,
    VisionIngressError,
    VisionNotConfiguredError,
    VisionProviderError,
    VisionObservation,
    image_bytes_from_record,
    image_disk_path,
    observe,
    observe_image_file,
    scrub_list,
    scrub_text,
    vision_to_toon,
)
from app.services.vision import interface as vision_interface
from app.services.vision.ingress import load_file_row, read_image_bytes
from app.services.vision.local_provider import local_deps_status, local_weights_path
from app.services.vision.openai_provider import DEFAULT_MODEL_ID as OA_MODEL


# ---------- fake → real swap seam ----------

def test_fake_flag_stays_true_and_signature_frozen():
    assert vision_interface.OBSERVE_IS_FAKE is True
    sig = inspect.signature(observe)
    assert list(sig.parameters) == ["image_bytes", "provider"]
    assert sig.parameters["provider"].default == "local"


def test_fake_observe_used_while_flag_true(monkeypatch):
    calls: list[str] = []

    def _real(image_bytes: bytes, provider: str) -> VisionObservation:
        calls.append(provider)
        return VisionObservation(
            summary="real",
            elements=[],
            text_seen=[],
            confidence="high",
            provider=provider,
            model_id="real-model",
        )

    monkeypatch.setattr(vision_interface, "OBSERVE_IS_FAKE", True)
    monkeypatch.setattr(vision_interface, "_dispatch_real", _real)
    obs = observe(b"png")
    assert "FAKE" in obs.summary
    assert calls == []


def test_fake_to_real_dispatch_seam(monkeypatch):
    def _real(image_bytes: bytes, provider: str) -> VisionObservation:
        assert image_bytes == b"png"
        return VisionObservation(
            summary="real observation from backend",
            elements=["hero"],
            text_seen=["Buy now"],
            confidence="high",
            provider=provider,
            model_id="qwen2-vl-2b-instruct",
        )

    monkeypatch.setattr(vision_interface, "OBSERVE_IS_FAKE", False)
    monkeypatch.setattr(vision_interface, "_dispatch_real", _real)
    obs = observe(b"png", provider="local")
    assert "FAKE" not in obs.summary
    assert obs.model_id == "qwen2-vl-2b-instruct"
    assert obs.confidence == "high"
    assert obs.text_seen == ["Buy now"]


def test_dispatch_unknown_provider_fails_even_when_real(monkeypatch):
    monkeypatch.setattr(vision_interface, "OBSERVE_IS_FAKE", False)
    with pytest.raises(ValueError):
        observe(b"x", provider="anthropic")


# ---------- local provider (fail-closed without deps/weights) ----------

def test_local_provider_requires_deps_or_weights():
    provider = LocalVisionProvider()
    ok, detail = provider.can_run()
    deps_ok, _ = local_deps_status()
    weights = local_weights_path()
    if not deps_ok:
        assert ok is False
        assert "missing packages" in detail or "weights" in detail
    elif weights is None or not Path(weights).exists():
        assert ok is False
        assert "weights" in detail or "BLOCKER" in detail
    with pytest.raises(VisionProviderError):
        provider.observe(b"\x89PNG")


def test_local_provider_runner_produces_scrubbed_observation():
    def runner(image_bytes: bytes):
        return {
            "summary": "Dashboard CTA",
            "elements": ["nav", "hero"],
            "text_seen": ["Buy now", "api_key=sk-ABCDEFGHIJKLMNOP"],
        }

    provider = LocalVisionProvider(runner=runner)
    ok, _ = provider.can_run()
    assert ok is True
    obs = provider.observe(b"\x89PNG")
    assert obs.provider == "local"
    assert obs.model_id == "qwen2-vl-2b-instruct"
    assert "api_key" not in " ".join(obs.text_seen)
    assert "sk-" not in " ".join(obs.text_seen)
    assert obs.summary == "Dashboard CTA"


def test_local_provider_rejects_empty_bytes():
    provider = LocalVisionProvider(runner=lambda b: "x")
    with pytest.raises(VisionProviderError):
        provider.observe(b"")


def test_local_weights_env_helper(monkeypatch):
    monkeypatch.delenv("VISION_LOCAL_WEIGHTS", raising=False)
    assert local_weights_path() is None
    monkeypatch.setenv("VISION_LOCAL_WEIGHTS", str(Path(__file__).parent))
    assert local_weights_path() == Path(__file__).parent


# ---------- provider validation on observe() ----------

def test_observe_rejects_bad_provider_and_non_bytes():
    with pytest.raises(ValueError):
        observe(b"\x00", provider="anthropic")
    with pytest.raises(TypeError):
        observe("not-bytes")  # type: ignore[arg-type]


# ---------- OpenAI provider (vault ref + paid gate) ----------

def test_openai_paid_gate_blocks_by_default():
    provider = OpenAIVisionProvider()
    with pytest.raises(VisionNotConfiguredError):
        provider.observe(b"\x89PNG")
    ok, detail = provider.can_run()
    assert ok is False
    assert "paid" in detail.lower() or "vault" in detail.lower() or "http" in detail.lower()


def test_openai_without_vault_ref_fails_closed():
    def missing_key() -> str:
        raise VisionNotConfiguredError("no secret ref for OpenAI vision")

    provider = OpenAIVisionProvider(allow_paid=True, key_resolver=missing_key)
    with pytest.raises(VisionNotConfiguredError):
        provider.observe(b"\x89PNG")


def test_openai_with_injected_transport_and_key():
    def key() -> str:
        return "vault-resolved-not-a-raw-key-placeholder"

    def http_post(api_key: str, model_id: str, image_b64: str, prompt: str) -> str:
        assert api_key.startswith("vault-resolved")
        assert model_id == OA_MODEL
        assert image_b64
        assert "summary" in prompt
        return (
            '{"summary": "Pricing page hero", "elements": ["pricing table"], '
            '"text_seen": ["$49", "sk-LEAKEDKEY0123456789"]}'
        )

    provider = OpenAIVisionProvider(
        allow_paid=True, key_resolver=key, http_post=http_post
    )
    obs = provider.observe(b"\x89PNG")
    assert obs.provider == "openai"
    assert "sk-" not in " ".join(obs.text_seen)
    assert obs.confidence in ("high", "medium", "low")


# ---------- OCR-leak negative ----------

def test_scrub_redacts_credentials():
    text = "Token sk-abcdefghijklmnop vault://installation/abc password: hunter2"
    cleaned = scrub_text(text)
    assert "sk-abcdefghijklmnop" not in cleaned
    assert "vault://installation" not in cleaned
    assert "hunter2" not in cleaned
    assert "[REDACTED]" in cleaned


def test_scrub_list_drops_credential_lines():
    values = ["plain label", "api_key=secretvalue123", "ok-line"]
    out = scrub_list(values)
    assert "plain label" in out
    assert "ok-line" in out
    assert all("secretvalue123" not in v for v in out)


def test_observation_never_contains_credentials_after_observe():
    def runner(image_bytes: bytes):
        return {
            "summary": "UI with leaked key api_key=sk-abcdefghijkl",
            "elements": ["vault://installation/deadbeef"],
            "text_seen": ["Authorization: Bearer abc.def.ghi", "مرحبا"],
        }

    obs = LocalVisionProvider(runner=runner).observe(b"img")
    blob = " ".join([obs.summary, *obs.elements, *obs.text_seen, obs.model_id])
    assert "sk-abcdefghijkl" not in blob
    assert "vault://installation" not in blob
    assert "abc.def.ghi" not in blob
    assert "مرحبا" in obs.text_seen


# ---------- W2 image ingress ----------

def _png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        + b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        + b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )


@pytest.fixture()
def files_root(tmp_path: Path) -> Path:
    root = tmp_path
    pid = "projA"
    safe = "abc123def456_shot.png"
    d = root / "data" / "projects" / pid / "files"
    d.mkdir(parents=True, exist_ok=True)
    (d / safe).write_bytes(_png_bytes())
    return root


def test_image_disk_path_and_read(files_root: Path):
    path = image_disk_path("projA", "abc123def456_shot.png", root=files_root)
    assert path.is_file()
    assert read_image_bytes(path) == _png_bytes()


def test_image_disk_path_rejects_traversal(files_root: Path):
    with pytest.raises(VisionIngressError):
        image_disk_path("projA", "../escape.png", root=files_root)
    with pytest.raises(VisionIngressError):
        image_disk_path("projA", "sub/name.png", root=files_root)
    with pytest.raises(VisionIngressError):
        image_disk_path("", "ok.png", root=files_root)


def test_missing_file_fails_closed(files_root: Path):
    with pytest.raises(VisionIngressError):
        read_image_bytes(
            image_disk_path("projA", "missing.png", root=files_root)
        )


def test_image_bytes_from_record_kind_gate(files_root: Path):
    good = {
        "project_id": "projA",
        "kind": "image",
        "mime_detected": "image/png",
        "safe_name": "abc123def456_shot.png",
        "extraction": "ready",
    }
    assert image_bytes_from_record(good, root=files_root) == _png_bytes()
    with pytest.raises(VisionIngressError):
        image_bytes_from_record({**good, "kind": "document"}, root=files_root)
    with pytest.raises(VisionIngressError):
        image_bytes_from_record({**good, "project_id": "projB"}, root=files_root)
    with pytest.raises(VisionIngressError):
        image_bytes_from_record({**good, "mime_detected": "text/plain"}, root=files_root)


def _files_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_files (
          file_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          original_name TEXT NOT NULL,
          safe_name TEXT NOT NULL,
          mime_detected TEXT NOT NULL,
          size INTEGER NOT NULL,
          sha256 TEXT NOT NULL,
          kind TEXT NOT NULL,
          width INTEGER,
          height INTEGER,
          extraction TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    conn.commit()


def test_observe_image_file_end_to_end(files_root: Path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _files_table(conn)
    data = _png_bytes()
    conn.execute(
        """
        INSERT INTO project_files (
          file_id, project_id, original_name, safe_name, mime_detected,
          size, sha256, kind, width, height, extraction
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "f" * 32,
            "projA",
            "shot.png",
            "abc123def456_shot.png",
            "image/png",
            len(data),
            "a" * 64,
            "image",
            1,
            1,
            "ready",
        ),
    )
    conn.commit()

    observed = observe_image_file(
        "projA",
        "f" * 32,
        conn=conn,
        root=files_root,
        provider="local",
    )
    assert observed.provider == "local"
    assert "FAKE" in observed.summary

    def _real_obs(image_bytes: bytes, *, provider: str = "local") -> VisionObservation:
        assert image_bytes == data
        return VisionObservation(
            summary="real from ingress",
            elements=[],
            text_seen=["Buy now"],
            confidence="medium",
            provider=provider,
            model_id="qwen2-vl-2b-instruct",
        )

    real = observe_image_file(
        "projA",
        "f" * 32,
        conn=conn,
        root=files_root,
        provider="local",
        observe_fn=_real_obs,
    )
    assert real.text_seen == ["Buy now"]
    conn.close()


def test_observe_image_file_missing_project_or_row(files_root: Path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _files_table(conn)
    with pytest.raises(VisionIngressError):
        observe_image_file("", "f" * 32, conn=conn, root=files_root)
    with pytest.raises(VisionIngressError):
        observe_image_file("projA", "missing", conn=conn, root=files_root)
    conn.close()


def test_load_file_row_requires_table():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with pytest.raises(VisionIngressError) as exc:
        load_file_row(conn, "projA", "f" * 32)
    assert "project_files" in str(exc.value)
    conn.close()


# ---------- TOON → LoRA sample round-trip ----------

def _parse_body(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    arrays: dict[str, list[str]] = {}
    for line in body.split("\n"):
        if not line:
            continue
        if "[" in line and line.split("[", 1)[0] in ("elements", "text_seen"):
            name, rest = line.split("[", 1)
            _n, after = rest.split("]:", 1)
            raw = after.strip()
            if not raw:
                arrays[name] = []
                continue
            # respect quoted segments minimally for sample round-trip
            items: list[str] = []
            buf = ""
            in_q = False
            esc = False
            for ch in raw:
                if esc:
                    buf += ch
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    buf += ch
                    continue
                if ch == '"':
                    in_q = not in_q
                    buf += ch
                    continue
                if ch == "," and not in_q:
                    items.append(buf)
                    buf = ""
                    continue
                buf += ch
            items.append(buf)
            cleaned = []
            for it in items:
                it = it.strip()
                if len(it) >= 2 and it[0] == '"' and it[-1] == '"':
                    inner = it[1:-1]
                    inner = (
                        inner.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
                    )
                    inner = inner.replace('\\"', '"').replace("\\\\", "\\")
                    it = inner
                cleaned.append(it)
            arrays[name] = cleaned
        else:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip().strip('"')
    fields["_arrays"] = arrays  # type: ignore[assignment]
    return fields


def test_toon_to_lora_sample_round_trip():
    obs = VisionObservation(
        summary="Dark UI dashboard with a bright CTA button",
        elements=["header nav", "hero image of a red sneaker"],
        text_seen=["Buy now", "مرحبا"],
        confidence="high",
        provider="local",
        model_id="qwen2-vl-2b-instruct",
    )
    block = vision_to_toon(obs)
    body = toon_body_from_block(block)
    parsed = _parse_body(body)
    arrays = parsed.pop("_arrays")
    assert parsed["summary"] == obs.summary
    assert parsed["provider"] == "local"
    assert parsed["model_id"] == obs.model_id
    assert parsed["confidence"] == "high"
    assert arrays["elements"] == obs.elements
    assert arrays["text_seen"] == obs.text_seen

    evidence = observation_to_lora_evidence(obs)
    assert evidence["toon_block"] == block
    assert evidence["schema_kind"] == "vision_observation"
    assert evidence["source"] == "vision:local/qwen2-vl-2b-instruct"

    lora_example = {
        "example_id": "vision-lora-001",
        "lang_slice": "english",
        "user_request": "Use the vision TOON evidence in the marketing answer.",
        "project_context": {
            "project_id": "proj-seed",
            "vision_evidence": evidence,
        },
        "available_tools": [],
        "expected_route": "state_only",
        "expected_tool_calls": [],
        "expected_toon_response": {
            "schema_version": "1",
            "route": "state_only",
            "confidence": 0.8,
            "tool_calls": [],
            "evidence_refs": ["vision:local/qwen2-vl-2b-instruct"],
            "approval": "green",
            "unknowns": [],
            "final_answer": "Summarize the visible CTA without inventing metrics.",
            "errors": [],
        },
        "final_answer": "Summarize the visible CTA without inventing metrics.",
        "approval_expectation": "green",
        "evidence_labels": [evidence],
        "source_family": "train-seed",
    }
    from app.evals.models.lora_dataset import validate_example

    problems = validate_example(lora_example)
    assert problems == []
    assert validate_response_fields(lora_example["expected_toon_response"]) == []
    assert safety_check(lora_example["expected_toon_response"]) == []


# ---------- honesty / no-LoRA-import ----------

def test_vision_package_does_not_import_lora():
    import app.services.vision as pkg
    import app.services.vision.interface as iface
    import app.services.vision.local_provider as local
    import app.services.vision.openai_provider as oa
    import app.services.vision.ingress as ing
    import app.services.vision.scrub as scrub

    for mod in (pkg, iface, local, oa, ing, scrub):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "from peft" not in src
        assert "import peft" not in src
        assert "MarketingLoRA" not in src


def test_interface_still_marks_fake_phase_a_for_bench():
    assert vision_interface.OBSERVE_IS_FAKE is True
    assert "FAKE" in (observe.__doc__ or "")
    assert "FAKE" in (vision_interface.__doc__ or "")
    assert "Phase A" in (vision_interface.__doc__ or "")
    obs = observe(b"\x89PNG-fake")
    assert "FAKE" in obs.summary
    assert obs.confidence == "low"
    assert obs.text_seen == []


def test_observation_is_frozen():
    obs = VisionObservation(
        summary="s",
        elements=[],
        text_seen=[],
        confidence="low",
        provider="local",
        model_id="m",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        obs.summary = "x"  # type: ignore[misc]
