"""Offline startup/capture tests; all process and listener access is faked."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "scripts" / "start_acceptance_runtime.py").is_file():
    pytest.skip(
        "requires the DEV-024 runtime acceptance script, which the public export omits",
        allow_module_level=True,
    )
sys.path.insert(0, str(ROOT / "scripts"))
import start_acceptance_runtime as startup


class FakeChild:
    pid = 4120
    returncode = None

    def poll(self):
        return None


class FakeSnapshot:
    def __init__(self, owners, executable, *, deny_process=False, command_line=None, parent_pid=None):
        self.owners = iter(owners)
        self.executable = str(executable)
        self.deny_process = deny_process
        self.process_calls = 0
        self.command_line = command_line
        self.parent_pid = parent_pid

    def listener_owner(self, port):
        return next(self.owners)

    def process(self, pid, launched_cwd=None):
        self.process_calls += 1
        if self.deny_process:
            raise PermissionError("fake CIM access denied")
        return {
            "pid": pid,
            "parent_process_id": self.parent_pid,
            "executable": self.executable,
            "command_line": self.command_line or f"python {ROOT / 'scripts' / 'founder_server.py'} scratch.db 8099",
            "working_directory": str(launched_cwd.resolve()) if launched_cwd else None,
        }


@pytest.fixture
def fake_expected(monkeypatch, tmp_path):
    expected = {
        "checkout": str(ROOT),
        "source_hash": "source-hash",
        "app_version": "test-version",
        "telemetry_capabilities": {"model_completed_event": "a", "retrieval_telemetry_projection": "b"},
        "frontend": {
            "mode": "static", "checkout": str(ROOT), "source_hash": "frontend-source",
            "build_hash": "frontend-build", "build_file_count": 2,
        },
    }
    monkeypatch.setattr(startup, "expected_identity", lambda root, mode: expected)
    return expected


def test_harness_launches_and_captures_actual_listener_identity(tmp_path, fake_expected):
    executable = Path(__file__).resolve()
    child = FakeChild()
    calls = []
    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return child

    manifest_path = tmp_path / "identity.json"
    launch_snapshot = FakeSnapshot([None, child.pid], executable, deny_process=True)
    result = startup.launch_and_capture(
        ROOT, executable, 8099, manifest_path, tmp_path / "scratch.db",
        snapshot=launch_snapshot,
        popen_factory=fake_popen, monotonic=iter([0.0, 0.1]).__next__,
        sleep=lambda _: None,
        utcnow=lambda: datetime(2026, 10, 3, 17, 0, tzinfo=timezone.utc),
    )
    assert len(calls) == 1
    assert calls[0][1]["cwd"] == str(ROOT)
    assert launch_snapshot.process_calls == 0, "harness-owned PID must not require CIM access"
    assert result["backend"]["pid"] == child.pid
    assert result["backend"]["listener_owner_pid"] == child.pid
    assert result["backend"]["executable"] == str(executable.resolve())
    assert result["backend"]["working_directory"] == str(ROOT)
    assert result["backend"]["command_line"] == __import__("subprocess").list2cmdline(calls[0][0][0])
    assert result["backend"]["started_at_utc"] == "2026-10-03T17:00:00.000Z"
    assert result["frontend"]["serving_backend_pid"] == child.pid
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == result
    reused = startup.launch_and_capture(
        ROOT, executable, 8099, manifest_path, tmp_path / "scratch.db",
        snapshot=FakeSnapshot([child.pid], executable, command_line=result["backend"]["command_line"]),
        popen_factory=lambda *a, **k: pytest.fail("matching live process must not be relaunched"),
    )
    assert reused == result


def test_exited_harness_child_cannot_be_accepted(tmp_path, fake_expected):
    class ExitedChild(FakeChild):
        returncode = 7

        def poll(self):
            return self.returncode

    with pytest.raises(startup.StartupIdentityError, match="exited with code 7"):
        startup.launch_and_capture(
            ROOT, Path(__file__).resolve(), 8099, tmp_path / "identity.json", tmp_path / "scratch.db",
            snapshot=FakeSnapshot([None, 4120], Path(__file__).resolve()),
            popen_factory=lambda *a, **k: ExitedChild(),
            monotonic=iter([0.0, 0.1]).__next__, sleep=lambda _: None,
        )
    assert not (tmp_path / "identity.json").exists()


def test_occupied_port_with_unverifiable_owner_fails_without_launch(tmp_path, fake_expected):
    launches = []
    snapshot = FakeSnapshot([88], Path(__file__).resolve())
    with pytest.raises(startup.StartupIdentityError, match="occupied by PID 88"):
        startup.launch_and_capture(
            ROOT, Path(__file__).resolve(), 8099, tmp_path / "identity.json", tmp_path / "scratch.db",
            snapshot=snapshot, popen_factory=lambda *a, **k: launches.append(1),
        )
    assert launches == []
    assert not (tmp_path / "identity.json").exists()


def test_denied_process_snapshot_for_prior_owner_fails_closed(tmp_path, fake_expected):
    executable = Path(__file__).resolve()
    child = FakeChild()
    manifest_path = tmp_path / "identity.json"
    scratch = tmp_path / "scratch.db"
    result = startup.launch_and_capture(
        ROOT, executable, 8099, manifest_path, scratch,
        snapshot=FakeSnapshot([None, child.pid], executable),
        popen_factory=lambda *a, **k: child,
        monotonic=iter([0.0, 0.1]).__next__, sleep=lambda _: None,
    )
    denied_snapshot = FakeSnapshot(
        [child.pid], executable, deny_process=True,
        command_line=result["backend"]["command_line"],
    )
    with pytest.raises(PermissionError, match="CIM access denied"):
        startup.launch_and_capture(
            ROOT, executable, 8099, manifest_path, scratch,
            snapshot=denied_snapshot,
            popen_factory=lambda *a, **k: pytest.fail("occupied port must not launch another child"),
        )


def test_listener_owned_by_other_pid_fails_without_killing_or_claiming(tmp_path, fake_expected):
    child = FakeChild()
    launches = []
    with pytest.raises(startup.StartupIdentityError, match="not a direct child of launched PID"):
        startup.launch_and_capture(
            ROOT, Path(__file__).resolve(), 8099, tmp_path / "identity.json", tmp_path / "scratch.db",
            snapshot=FakeSnapshot([None, 999], Path(__file__).resolve()),
            popen_factory=lambda *a, **k: (launches.append(1) or child),
            monotonic=iter([0.0, 0.1]).__next__, sleep=lambda _: None,
        )
    assert launches == [1]
    assert child.poll() is None
    assert not (tmp_path / "identity.json").exists()


def test_direct_child_listener_is_recorded_as_backend_identity(tmp_path, fake_expected):
    executable = Path(__file__).resolve()
    child = FakeChild()
    listener_pid = 9912
    command_line = f"{executable} {ROOT / 'scripts' / 'founder_server.py'} scratch.db 8099"
    snapshot = FakeSnapshot([None, listener_pid], executable, command_line=command_line, parent_pid=child.pid)
    result = startup.launch_and_capture(
        ROOT, executable, 8099, tmp_path / "identity.json", tmp_path / "scratch.db",
        snapshot=snapshot,
        popen_factory=lambda *a, **k: child,
        monotonic=iter([0.0, 0.1]).__next__, sleep=lambda _: None,
    )
    assert result["backend"]["pid"] == listener_pid
    assert result["backend"]["listener_owner_pid"] == listener_pid
    assert result["backend"]["parent_process_id"] == child.pid
    assert result["backend"]["launcher_pid"] == child.pid
    assert result["frontend"]["serving_backend_pid"] == listener_pid
