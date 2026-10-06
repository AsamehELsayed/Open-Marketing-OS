"""DEV-008 — launcher / process-manager contract.

The launcher is the only thing a non-developer ever touches, so these tests
cover the failure modes that would otherwise surface as a raw crash: port
selection, loopback binding, child environment, friendly errors, and the
single-instance guard.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_DIR = REPO_ROOT / "launcher"

if str(LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHER_DIR))


@pytest.fixture()
def launcher(tmp_path, monkeypatch):
    """Import the launcher with an isolated user-data root."""
    monkeypatch.setenv("OMOS_WORKSPACE_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(tmp_path / "data" / "credentials"))
    monkeypatch.setenv("OMOS_LOG_DIR", str(tmp_path / "logs"))
    for name in list(sys.modules):
        if name == "omos_launcher":
            del sys.modules[name]
    import omos_launcher

    return omos_launcher


# ------------------------------------------------------------------- ports

def test_free_port_is_actually_bindable(launcher):
    port = launcher.find_free_port()
    assert 1024 < port < 65536
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))  # must not raise


def test_two_calls_usually_differ(launcher):
    """Documented as best-effort, not asserted as an invariant.

    The OS may legitimately hand back the same ephemeral port twice, so this
    must not be a hard assertion — the property that actually matters is that
    whatever port is returned can be bound, which the test above covers.
    """
    launcher.find_free_port()
    launcher.find_free_port()  # must not raise


def test_port_availability_probe(launcher):
    port = launcher.find_free_port()
    assert launcher._port_is_free("127.0.0.1", port) is True
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", port))
    holder.listen(1)
    try:
        assert launcher._port_is_free("127.0.0.1", port) is False
    finally:
        holder.close()


# ----------------------------------------------------------- child process

def test_child_env_redirects_every_mutable_path(launcher):
    env = launcher.build_child_env(4321)
    assert env["OMOS_FROZEN"] == "1"
    assert env["OMOS_PORT"] == "4321"
    assert env["OMOS_WORKSPACE_DIR"] == str(launcher.ROOTS["data"])
    assert env["OMOS_CREDENTIALS_DIR"] == str(launcher.ROOTS["credentials"])
    assert env["OMOS_LOG_DIR"] == str(launcher.ROOTS["logs"])
    # Nothing may point into the read-only install folder.
    assert str(launcher.ROOTS["bundle"]) not in env["OMOS_WORKSPACE_DIR"]


def test_child_env_scrubs_provider_secrets(launcher, monkeypatch):
    """A key in the user's shell must not leak into the shipped app."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leaked")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-leaked")
    monkeypatch.setenv("APIFY_API_TOKEN", "apify-leaked")
    env = launcher.build_child_env(1234)
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "APIFY_API_TOKEN",
                 "BRIGHTDATA_API_KEY"):
        assert env[name] == "", f"{name} leaked into the child environment"


def test_child_entry_never_binds_all_interfaces(launcher):
    source = (LAUNCHER_DIR / "omos_launcher.py").read_text(encoding="utf-8")
    assert "0.0.0.0" not in source
    assert 'host="127.0.0.1"' in source


def test_serve_module_binds_loopback():
    source = (LAUNCHER_DIR / "serve.py").read_text(encoding="utf-8")
    assert "0.0.0.0" not in source
    assert '"127.0.0.1"' in source


# ----------------------------------------------------------------- errors

def test_error_text_is_human_and_carries_a_remedy(launcher):
    err = launcher.LaunchError(
        "Open Marketing OS could not start its local service.",
        "Address already in use.",
        "Close other apps and try again.",
    )
    text = err.user_text()
    assert "could not start" in text
    assert "Close other apps and try again." in text
    for leak in ("Traceback", "uvicorn", "OSError", "errno"):
        assert leak not in text


def test_error_diagnostics_are_copyable_and_secret_free(launcher):
    err = launcher.LaunchError("Boom.", "detail", "remedy")
    diag = err.diagnostics()
    assert "version:" in diag and "logs:" in diag and "error:" in diag
    for leak in ("sk-", "sk-or-v1", "Bearer", "OPENROUTER_API_KEY"):
        assert leak not in diag


# -------------------------------------------------------- single instance

def test_single_instance_lock_blocks_a_second_launcher(launcher):
    """A second launch must not start a rival backend on another port."""
    first = launcher.acquire_single_instance()
    assert first is not None

    path = launcher._instance_lock_path()
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()

    # A live lock recording a port that is genuinely in use must be respected:
    # the second launcher returns None (and focuses the existing window) rather
    # than starting a competing server.
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    live_port = holder.getsockname()[1]
    try:
        path.write_text(
            json.dumps({"pid": os.getpid(), "port": live_port}), encoding="utf-8"
        )
        assert launcher.acquire_single_instance() is None, \
            "a second launcher took over while the first backend was still listening"
    finally:
        holder.close()

    launcher._release_lock(first)
    assert not path.exists()


def test_stale_lock_is_reclaimed(launcher):
    path = launcher._instance_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": 999999, "port": 1}), encoding="utf-8")
    handle = launcher.acquire_single_instance()
    assert handle is not None, "a stale lock from a dead pid must not block launch"
    launcher._release_lock(handle)


# ---------------------------------------------------------------- logging

def test_logging_never_raises_on_an_unwritable_log_path(launcher, tmp_path):
    """`_log` must swallow I/O failures — logging must never crash the launcher.

    `chmod(0o500)` does not make a directory unwritable on Windows, so the
    unwritable location is created as a *file* where a directory is required.
    That fails identically on every platform.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    monkey_target = blocker / "sub" / "omos.log"
    original = launcher.LOG_FILE
    launcher.LOG_FILE = monkey_target
    try:
        launcher._log("this must not raise")  # no exception
    finally:
        launcher.LOG_FILE = original


def test_url_points_at_the_spa(launcher):
    assert launcher.app_url(1234) == "http://127.0.0.1:1234/app"


# ------------------------------------------------------- live smoke test

@pytest.mark.slow
def test_backend_serves_health_and_the_spa_on_a_dynamic_port(tmp_path):
    """Real end-to-end: start the backend the way the launcher does."""
    env = dict(os.environ)
    env.update({
        "OMOS_FROZEN": "1",
        # OMOS_WORKSPACE_DIR is the *user data root*; the database and the
        # workspace files live under it, so the db path is <root>/data/...
        "OMOS_WORKSPACE_DIR": str(tmp_path),
        "OMOS_CREDENTIALS_DIR": str(tmp_path / "credentials"),
        "OMOS_LOG_DIR": str(tmp_path / "logs"),
        "OMOS_BUNDLE_DIR": str(REPO_ROOT),
    })
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    env["OMOS_PORT"] = str(port)

    child = subprocess.Popen(
        [sys.executable, str(LAUNCHER_DIR / "serve.py")],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        payload = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if child.poll() is not None:
                pytest.fail(f"backend exited early with {child.returncode}")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=2
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    break
            except Exception:
                time.sleep(0.3)
        assert payload is not None, "backend never reported healthy"
        assert payload["status"] == "ok"

        from app import paths

        assert payload["version"] == paths.APP_VERSION

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/favicon.ico", timeout=5
        ) as response:
            favicon = response.read()
            assert response.headers.get_content_type() == "image/x-icon"
        assert favicon[:4] == b"\x00\x00\x01\x00"

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/onboarding/status", timeout=5
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["ok"] is True
        assert body["data"]["business_described"] is False

        # The starter workspace and database must exist after a frozen first run.
        assert (tmp_path / "company" / "company.yaml").is_file()
        assert (tmp_path / "data" / "marketing.db").is_file()
        assert (tmp_path / "credentials").is_dir()
    finally:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
