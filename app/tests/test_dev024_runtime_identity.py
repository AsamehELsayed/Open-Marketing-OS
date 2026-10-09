"""Offline contract tests for DEV-024 runtime identity acceptance gate."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "scripts" / "runtime_identity.py").is_file():
    pytest.skip(
        "requires the DEV-024 runtime identity script, which the public export omits",
        allow_module_level=True,
    )
SPEC = importlib.util.spec_from_file_location("runtime_identity", ROOT / "scripts" / "runtime_identity.py")
runtime_identity = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runtime_identity)


@pytest.fixture
def expected(tmp_path):
    return {
        "checkout": str(tmp_path),
        "source_hash": "backend-source-hash",
        "app_version": "test-version",
        "telemetry_capabilities": {"model_completed_event": "hash-a", "retrieval_telemetry_projection": "hash-b"},
        "frontend": {
            "mode": "static", "checkout": str(tmp_path), "source_hash": "frontend-source-hash",
            "build_hash": "frontend-build-hash", "build_file_count": 2,
        },
        "test_executable": str(Path(__file__).resolve()),
    }


@pytest.fixture
def manifest(expected):
    return {
        "schema": runtime_identity.SCHEMA,
        "backend": {
            "pid": 4120,
            "executable": expected["test_executable"],
            "command_line": f'python {expected["checkout"]}\\launcher\\serve.py',
            "working_directory": expected["checkout"],
            "port": 8123,
            "listener_owner_pid": 4120,
            "started_at_utc": "2026-10-03T17:00:00.000Z",
            "source_hash": expected["source_hash"],
            "app_version": expected["app_version"],
            "telemetry_capabilities": expected["telemetry_capabilities"],
        },
        "frontend": {
            **expected["frontend"],
            "serving_backend_pid": 4120,
        },
    }


def test_complete_matching_identity_passes(manifest, expected):
    runtime_identity.validate_identity(manifest, expected)


@pytest.mark.parametrize("change, message", [
    (lambda m: m.pop("backend"), "backend process identity is missing"),
    (lambda m: m["backend"].update(listener_owner_pid=9000), "listening port owner"),
    (lambda m: m["backend"].update(working_directory="C:/other-checkout"), "working directory"),
    (lambda m: m["backend"].update(source_hash="stale"), "source_hash"),
    (lambda m: m["backend"].update(app_version="stale"), "app_version"),
    (lambda m: m["backend"].update(telemetry_capabilities={}), "telemetry_capabilities"),
    (lambda m: m["frontend"].update(build_hash="stale"), "frontend build_hash"),
    (lambda m: m["frontend"].update(serving_backend_pid=9000), "not tied"),
])
def test_mismatch_or_missing_identity_fails_closed(manifest, expected, change, message):
    change(manifest)
    with pytest.raises(runtime_identity.IdentityError, match=message):
        runtime_identity.validate_identity(manifest, expected)


def test_unknown_health_status_cannot_replace_identity(manifest, expected):
    manifest["health"] = {"status": "ok"}
    manifest["backend"].pop("listener_owner_pid")
    with pytest.raises(runtime_identity.IdentityError, match="backend identity fields missing"):
        runtime_identity.validate_identity(manifest, expected)


def test_dev_frontend_requires_its_own_process_and_port_owner(manifest, expected):
    expected["frontend"]["mode"] = "dev"
    manifest["frontend"].update({
        "mode": "dev",
        "process": {
            "pid": 5000, "executable": expected["test_executable"],
            "command_line": f"node {Path(expected['checkout']) / 'frontend' / 'node_modules' / '.bin' / 'vite'} --host 127.0.0.1", "working_directory": str(Path(expected["checkout"]) / "frontend"),
            "port": 5173, "listener_owner_pid": 5000,
        },
    })
    runtime_identity.validate_identity(manifest, expected)
    manifest["frontend"]["process"]["listener_owner_pid"] = 5001
    with pytest.raises(runtime_identity.IdentityError, match="frontend listening port owner"):
        runtime_identity.validate_identity(manifest, expected)


def test_backend_start_timestamp_is_required_and_must_be_utc(manifest, expected):
    manifest["backend"].pop("started_at_utc")
    with pytest.raises(runtime_identity.IdentityError, match="backend identity fields missing.*started_at_utc"):
        runtime_identity.validate_identity(manifest, expected)
    manifest["backend"]["started_at_utc"] = "2026-10-03T17:00:00"
    with pytest.raises(runtime_identity.IdentityError, match="must be UTC"):
        runtime_identity.validate_identity(manifest, expected)


def test_source_hash_covers_declared_code_but_excludes_local_state(tmp_path):
    root = tmp_path / "checkout"
    (root / ".git").mkdir(parents=True)
    for directory in ("app", "config", "scripts", "frontend/src"):
        (root / directory).mkdir(parents=True)
    (root / "app/paths.py").write_text('APP_VERSION = "1.2.3"\n', encoding="utf-8")
    (root / "app/main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "config/app.yaml").write_text("mode: test\n", encoding="utf-8")
    (root / "scripts/run.py").write_text("print('run')\n", encoding="utf-8")
    (root / "frontend/src/main.tsx").write_text("export const value = 1\n", encoding="utf-8")
    initial = runtime_identity.source_identity(root)["source_hash"]
    for relative in (".env", "app/data/marketing.db", "app/chroma/index.bin",
                     "development/runs/DEV-024/qa.md", ".pytest_cache/state.json"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private or runtime data", encoding="utf-8")
    assert runtime_identity.source_identity(root)["source_hash"] == initial
    (root / "app/main.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert runtime_identity.source_identity(root)["source_hash"] != initial


@pytest.mark.parametrize("mode", ["static", "dev"])
def test_frontend_runtime_component_changes_identity_in_both_modes(tmp_path, mode):
    root = tmp_path / "checkout"
    runtime_component = root / "frontend/src/components/runtime/TurnRuntimePanel.tsx"
    runtime_component.parent.mkdir(parents=True)
    runtime_component.write_text("export const state = 'before'\n", encoding="utf-8")
    if mode == "static":
        dist = root / "frontend/dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("<main>acceptance fixture</main>", encoding="utf-8")
    before = runtime_identity.frontend_identity(root, mode)["source_hash"]
    runtime_component.write_text("export const state = 'after'\n", encoding="utf-8")
    after = runtime_identity.frontend_identity(root, mode)["source_hash"]
    assert after != before
