"""Offline checks for acceptance request destination and live-process binding."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import founder_accept as acceptance
from runtime_identity import SCHEMA, expected_identity, validate_identity


class FakeSnapshot:
    def __init__(self, owner_pid, process=None, error=None):
        self.owner_pid = owner_pid
        self.process_data = process
        self.error = error

    def listener_owner(self, port):
        return self.owner_pid

    def process(self, pid):
        if self.error:
            raise self.error
        return dict(self.process_data or {})


@pytest.fixture
def valid_manifest(tmp_path):
    expected = expected_identity(ROOT, "dev")
    pid = 4120
    manifest = {
        "schema": SCHEMA,
        "backend": {
            "pid": pid,
            "executable": str(Path(sys.executable).resolve()),
            "command_line": f'{sys.executable} "{ROOT / "scripts" / "founder_server.py"}" scratch.db 8099',
            "working_directory": str(ROOT),
            "port": 8099,
            "listener_owner_pid": pid,
            "started_at_utc": "2026-10-03T17:00:00.000Z",
            "source_hash": expected["source_hash"],
            "app_version": expected["app_version"],
            "telemetry_capabilities": expected["telemetry_capabilities"],
        },
        "frontend": {
            **expected["frontend"],
            "process": {
                "pid": 5199,
                "executable": str(Path(sys.executable).resolve()),
                "command_line": f"node {ROOT / 'frontend' / 'vite'} --host 127.0.0.1",
                "working_directory": str(ROOT / "frontend"),
                "port": 5173,
                "listener_owner_pid": 5199,
            },
        },
        "scratch_db": str(tmp_path / "acceptance.db"),
    }
    validate_identity(manifest, expected)
    return manifest


def _live_process(manifest):
    return {
        "pid": manifest["backend"]["pid"],
        "executable": manifest["backend"]["executable"],
        "command_line": manifest["backend"]["command_line"],
    }


def test_stale_manifest_different_listener_never_reaches_request_or_sse(valid_manifest, monkeypatch):
    monkeypatch.setattr(acceptance, "BASE", "http://127.0.0.1:8099")
    monkeypatch.setattr(acceptance, "ACCEPTANCE_IDENTITY", valid_manifest)
    monkeypatch.setattr(acceptance, "LIVE_PROCESS_SNAPSHOT", FakeSnapshot(owner_pid=9999))
    network_calls = []
    monkeypatch.setattr(acceptance.urllib.request, "urlopen", lambda *a, **k: network_calls.append((a, k)))
    action_calls = []

    http_step = lambda: (action_calls.append("req"), acceptance.req("GET", "/"))
    sse_step = lambda: (action_calls.append("sse_read"), acceptance.sse_read("/chat/events"))
    with pytest.raises(acceptance.AcceptanceIdentityError, match="live listener PID"):
        acceptance.run_acceptance_step("s1", {"s1": http_step})
    with pytest.raises(acceptance.AcceptanceIdentityError, match="live listener PID"):
        acceptance.run_acceptance_step("s2", {"s2": sse_step})
    with pytest.raises(acceptance.AcceptanceIdentityError, match="live listener PID"):
        acceptance.req("GET", "/")
    with pytest.raises(acceptance.AcceptanceIdentityError, match="live listener PID"):
        acceptance.sse_read("/chat/events")

    assert action_calls == []
    assert network_calls == []


def test_live_executable_or_command_mismatch_fails_before_network(valid_manifest, monkeypatch):
    monkeypatch.setattr(acceptance, "ACCEPTANCE_IDENTITY", valid_manifest)
    live = _live_process(valid_manifest)
    live["command_line"] += " --different-source"
    monkeypatch.setattr(acceptance, "LIVE_PROCESS_SNAPSHOT", FakeSnapshot(4120, live))
    network_calls = []
    monkeypatch.setattr(acceptance.urllib.request, "urlopen", lambda *a, **k: network_calls.append((a, k)))
    with pytest.raises(acceptance.AcceptanceIdentityError, match="command_line"):
        acceptance.req("GET", "/")
    assert network_calls == []


def test_non_8099_manifest_fails_before_acceptance_action(valid_manifest, monkeypatch):
    valid_manifest["backend"]["port"] = 8123
    monkeypatch.setattr(acceptance, "ACCEPTANCE_IDENTITY", valid_manifest)
    monkeypatch.setattr(acceptance, "LIVE_PROCESS_SNAPSHOT", FakeSnapshot(4120, _live_process(valid_manifest)))
    monkeypatch.setattr(acceptance, "BASE", "http://127.0.0.1:8099")
    reached = []
    with pytest.raises(acceptance.AcceptanceIdentityError, match="does not match identity manifest"):
        acceptance.run_acceptance_step("s1", {"s1": lambda: reached.append(True)})
    assert reached == []


@pytest.mark.parametrize("bad_url", [
    "http://localhost:8099",
    "http://127.0.0.1:8123",
    "https://127.0.0.1:8099",
    "http://192.168.1.2:8099",
])
def test_non_loopback_or_mismatched_url_fails_before_request(valid_manifest, monkeypatch, bad_url):
    monkeypatch.setattr(acceptance, "BASE", bad_url)
    monkeypatch.setattr(acceptance, "ACCEPTANCE_IDENTITY", valid_manifest)
    monkeypatch.setattr(acceptance, "LIVE_PROCESS_SNAPSHOT", FakeSnapshot(4120, _live_process(valid_manifest)))
    network_calls = []
    monkeypatch.setattr(acceptance.urllib.request, "urlopen", lambda *a, **k: network_calls.append((a, k)))
    reached = []
    with pytest.raises(acceptance.AcceptanceIdentityError):
        acceptance.run_acceptance_step("s1", {"s1": lambda: reached.append("request path")})
    assert reached == []
    assert network_calls == []


def test_process_inspection_error_fails_before_sse_network(valid_manifest, monkeypatch):
    monkeypatch.setattr(acceptance, "ACCEPTANCE_IDENTITY", valid_manifest)
    monkeypatch.setattr(acceptance, "LIVE_PROCESS_SNAPSHOT", FakeSnapshot(4120, error=PermissionError("denied")))
    network_calls = []
    monkeypatch.setattr(acceptance.urllib.request, "urlopen", lambda *a, **k: network_calls.append((a, k)))
    with pytest.raises(acceptance.AcceptanceIdentityError, match="inspection failed"):
        acceptance.sse_read("/events")
    assert network_calls == []
