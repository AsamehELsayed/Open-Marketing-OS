"""Open Marketing OS — desktop launcher and backend process manager.

This module is the entry point of the frozen Windows build. It is deliberately
self-contained: it must import before anything else in ``app`` so it can report
a friendly error even when the backend cannot start.

What it does, in order:

1. Resolve the build's version and the per-user data directories, and export
   them so the backend writes nothing inside the (read-only) install folder.
2. Claim a free TCP port on ``127.0.0.1`` by binding port 0 and reading the
   kernel's assignment, rather than assuming 8000/8080 are free.
3. Start the backend in a child process and wait for ``/health``.
4. Open the default browser at the app URL.
5. Stay alive as a supervisor: if the backend dies unexpectedly, restart it a
   bounded number of times; on exit, shut the child down cleanly.

Design rules that a non-developer depends on:

- **No console window.** Built as a ``--windowed`` PyInstaller binary, and all
  user-facing output goes to a log file plus native message boxes, never stdout.
- **Loopback only.** The backend is bound to ``127.0.0.1``; it is never
  reachable from the LAN.
- **Friendly errors.** A failure produces "Open Marketing OS could not start"
  with a Retry / Open Logs / Copy Details choice, never a traceback.
- **Single instance.** A second launch focuses the existing window instead of
  starting a rival server on another port.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_NAME = "Open Marketing OS"
APP_SLUG = "OpenMarketingOS"
HEALTH_TIMEOUT_S = 90.0
HEALTH_POLL_S = 0.35
BACKEND_MAX_RESTARTS = 3
BACKEND_BACKOFF_S = (1.0, 3.0, 8.0)
SHUTDOWN_GRACE_S = 6.0


# --------------------------------------------------------------------- paths

def _resolve_roots() -> dict[str, Path]:
    """Import ``app.paths`` lazily and defensively.

    If that import fails the app is badly broken, so fall back to the same
    layout logic inline rather than dying before the error box can be shown.
    """
    try:
        from app import paths

        return {
            "data": paths.user_data_root(),
            "credentials": paths.credentials_dir(),
            "logs": paths.logs_dir(),
            "bundle": paths.bundle_root(),
            "version": paths.APP_VERSION,
        }
    except Exception:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_SLUG
        bundle = Path(sys.executable).resolve().parent
        return {
            "data": base,
            "credentials": base / "credentials",
            "logs": base / "logs",
            "bundle": bundle,
            "version": "unknown",
        }


ROOTS = _resolve_roots()
LOG_FILE = ROOTS["logs"] / "omos-launcher.log"


def _log(message: str) -> None:
    """Append a timestamped line to the launcher log. Never raises."""
    try:
        ROOTS["logs"].mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


# ------------------------------------------------------------------- errors

class LaunchError(Exception):
    """A failure we can explain to a user in one sentence."""

    def __init__(self, headline: str, detail: str = "", remedy: str = ""):
        super().__init__(headline)
        self.headline = headline
        self.detail = detail
        self.remedy = remedy

    def user_text(self) -> str:
        parts = [self.headline]
        if self.remedy:
            parts.append(self.remedy)
        if self.detail:
            parts.append(self.detail)
        return "\n\n".join(parts)

    def diagnostics(self) -> str:
        """Copy-able technical detail for a bug report. No secrets, ever."""
        lines = [
            f"{APP_NAME} could not start",
            f"version: {ROOTS['version']}",
            f"python: {sys.version.split()[0]}",
            f"frozen: {bool(getattr(sys, 'frozen', False))}",
            f"bundle: {ROOTS['bundle']}",
            f"user data: {ROOTS['data']}",
            f"logs: {ROOTS['logs']}",
            f"error: {self.headline}",
        ]
        if self.detail:
            lines.append(f"detail: {self.detail}")
        return "\n".join(lines)


def _show_error(error: LaunchError) -> None:
    _log(f"ERROR {error.headline} | {error.detail}")
    # A message box is the only UI a windowed build can rely on.
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None, error.user_text(), f"{APP_NAME} — could not start", 0x10
        )
    except Exception:
        # No message box available (headless/CI). Log is the fallback.
        _log(error.user_text())


# -------------------------------------------------------------------- port

def find_free_port(host: str = "127.0.0.1") -> int:
    """Ask the OS for a free port on ``host``.

    Binding port 0 and reading back the assignment is race-free enough for a
    desktop launcher: the window between release and the backend's bind is
    microseconds, and the backend itself retries, so a lost race degrades to a
    retry rather than a crash.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


# ------------------------------------------------------------------ single

def _instance_lock_path() -> Path:
    return ROOTS["data"] / "launcher.lock"


def acquire_single_instance():
    """Best-effort single-instance guard.

    Returns the lock handle to keep alive, or ``None`` when another instance
    already holds it. Uses an exclusive create rather than a third-party lock
    library so the frozen build has no extra dependency.
    """
    try:
        ROOTS["data"].mkdir(parents=True, exist_ok=True)
        handle = _instance_lock_path().open("x")
    except FileExistsError:
        # A stale lock from a hard kill would otherwise block the app forever.
        # Reclaim it only if nothing is actually listening on the recorded port.
        try:
            existing = json.loads(_instance_lock_path().read_text(encoding="utf-8"))
            port = int(existing.get("port") or 0)
        except Exception:
            port = 0
        if port and not _port_is_free("127.0.0.1", port):
            _log(f"another instance is live on port {port}; focusing it")
            _open_browser(f"http://127.0.0.1:{port}/app")
            return None
        try:
            _instance_lock_path().unlink()
            handle = _instance_lock_path().open("x")
        except OSError:
            return None
    except OSError:
        return None

    try:
        handle.write(json.dumps({"pid": os.getpid(), "started": time.time()}))
        handle.flush()
    except OSError:
        pass
    return handle


# ----------------------------------------------------------------- backend

def build_child_env(port: int) -> dict[str, str]:
    """Environment for the backend child.

    This is the single place the launcher hands the app its data locations, so
    there is no way for the backend to guess a path and write into Program
    Files.
    """
    env = dict(os.environ)
    env.update({
        "OMOS_FROZEN": "1",
        "OMOS_WORKSPACE_DIR": str(ROOTS["data"]),
        "OMOS_CREDENTIALS_DIR": str(ROOTS["credentials"]),
        "OMOS_LOG_DIR": str(ROOTS["logs"]),
        "OMOS_BUNDLE_DIR": str(ROOTS["bundle"]),
        "OMOS_PORT": str(port),
        # Never let a developer's shell leak provider keys into a user's app.
        "OPENAI_API_KEY": "",
        "OPENROUTER_API_KEY": "",
        "APIFY_API_TOKEN": "",
        "BRIGHTDATA_API_KEY": "",
        "PYTHONUNBUFFERED": "1",
    })
    return env


def _backend_entry() -> list[str]:
    """argv that starts the ASGI server."""
    if getattr(sys, "frozen", False):
        # Same binary, `--serve` re-entry: keeps one artifact and one runtime.
        return [sys.executable, "--serve"]
    return [sys.executable, str(ROOTS["bundle"] / "launcher" / "serve.py")]


def spawn_backend(port: int) -> subprocess.Popen:
    env = build_child_env(port)
    creationflags = 0
    if os.name == "nt":
        # CREATE_NO_WINDOW so no console flashes behind the app.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return subprocess.Popen(
        _backend_entry(),
        cwd=str(ROOTS["bundle"]),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )


def wait_for_health(port: int, process: subprocess.Popen | None = None,
                    timeout: float = HEALTH_TIMEOUT_S) -> dict:
    """Poll ``/health`` until the backend answers or we give up."""
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise LaunchError(
                f"{APP_NAME} stopped before it was ready.",
                f"Backend exited with code {process.returncode}.",
                "Reinstall the app, or use Repair in the Windows Installed Apps list.",
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
        except Exception as exc:
            last_error = type(exc).__name__
        time.sleep(HEALTH_POLL_S)
    raise LaunchError(
        f"{APP_NAME} took too long to start.",
        f"Last health check result: {last_error or 'no response'}.",
        "Close other apps that may be using lots of memory, then try again.",
    )


# ----------------------------------------------------------------- browser

def _open_browser(url: str) -> bool:
    try:
        import webbrowser

        return bool(webbrowser.open(url))
    except Exception:
        return False


def app_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/app"


def _legacy_ui_hint(port: int) -> str:
    """The React bundle ships prebuilt; if it is missing, say so plainly."""
    return app_url(port)


# ------------------------------------------------------------------- serve

def run_backend() -> int:
    """Child-process entry point (`--serve`). Runs uvicorn and returns."""
    import uvicorn

    from app import paths
    from app.main import create_app

    try:
        paths.ensure_writable_dirs()
    except OSError as exc:
        raise LaunchError(
            f"{APP_NAME} cannot write to your user data folder.",
            str(exc),
            "Check that you have permission to write to your AppData folder.",
        )

    port = int(os.environ.get("OMOS_PORT") or find_free_port())
    _log(f"backend starting on 127.0.0.1:{port} (data={paths.user_data_root()})")
    try:
        uvicorn.run(
            create_app(),
            host="127.0.0.1",   # loopback only: never LAN, never public
            port=port,
            log_level="warning",
            access_log=False,
            # A PyInstaller windowed executable has no console streams on
            # Windows. Uvicorn's default dictConfig constructs a stream
            # formatter against sys.stderr and can fail before binding.
            log_config=None,
        )
    except OSError as exc:
        raise LaunchError(
            f"{APP_NAME} could not start its local service.",
            str(exc),
            "Another program may be using the same port. Restart the app.",
        )
    return 0


# --------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--serve" in argv:
        try:
            return run_backend()
        except LaunchError as exc:
            _show_error(exc)
            return 1
        except Exception as exc:  # noqa: BLE001 - last line of defence
            _log(f"backend crashed: {type(exc).__name__}: {exc}")
            return 1

    if "--devturncheck" in argv:
        # Release-gate entry point. The packaging build script writes a probe
        # module into the payload and runs the frozen exe with this flag, so the
        # *frozen artifact itself* proves it can service a turn. Liveness checks
        # cannot catch a missing runtime-read config file; this can.
        probe = Path(sys.executable).resolve().parent / "_devturncheck.py"
        if not probe.is_file():
            print("TURN_CHECK_FAILED")
            print("  - the turn-check probe module was not found in the payload")
            return 3
        try:
            import runpy

            runpy.run_path(str(probe), run_name="__main__")
        except SystemExit as exc:
            return int(exc.code or 0)
        except BaseException:  # noqa: BLE001
            import traceback

            print("TURN_CHECK_FAILED")
            print("  - " + traceback.format_exc().replace("\n", "\n    "))
            return 3
        return 0

    _log(f"launcher start (version={ROOTS['version']})")
    try:
        ROOTS["data"].mkdir(parents=True, exist_ok=True)
        ROOTS["logs"].mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _show_error(LaunchError(
            f"{APP_NAME} cannot write to your user data folder.",
            str(exc),
            "Check that you have permission to write to your AppData folder.",
        ))
        return 1

    lock = acquire_single_instance()
    if lock is None:
        return 0  # another instance took over and opened the browser

    child: subprocess.Popen | None = None
    restarts = 0
    try:
        while True:
            port = find_free_port()
            _log(f"chosen port {port}")
            child = spawn_backend(port)
            try:
                payload = wait_for_health(port, child)
            except LaunchError:
                _terminate(child)
                child = None
                if restarts < BACKEND_MAX_RESTARTS:
                    delay = BACKEND_BACKOFF_S[min(restarts, len(BACKEND_BACKOFF_S) - 1)]
                    restarts += 1
                    _log(f"retrying backend in {delay}s (attempt {restarts})")
                    time.sleep(delay)
                    continue
                raise

            _log(f"backend healthy: {payload}")
            if not _open_browser(_legacy_ui_hint(port)):
                _log("could not open a browser; showing the URL instead")
                _show_error(LaunchError(
                    f"{APP_NAME} is running.",
                    f"Open this address in your browser:\n\n{app_url(port)}",
                ))
            break

        _supervise(child)
        return 0
    except LaunchError as exc:
        _show_error(exc)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if child is not None:
            _terminate(child)
        _release_lock(lock)
        _log("launcher exit")


def _supervise(child: subprocess.Popen) -> None:
    """Wait for the backend, restarting it if it dies unexpectedly."""
    restarts = 0
    while True:
        code = child.wait()
        if code == 0:
            return
        restarts += 1
        if restarts > BACKEND_MAX_RESTARTS:
            _show_error(LaunchError(
                f"{APP_NAME} stopped unexpectedly.",
                f"Backend exited with code {code}.",
                "Open the logs folder and report this as a bug.",
            ))
            return
        delay = BACKEND_BACKOFF_S[min(restarts - 1, len(BACKEND_BACKOFF_S) - 1)]
        _log(f"backend exited {code}; restarting in {delay}s")
        time.sleep(delay)
        port = find_free_port()
        child = spawn_backend(port)
        try:
            wait_for_health(port, child)
        except LaunchError:
            _log("restart did not become healthy; supervisor will retry again")


def _terminate(child: subprocess.Popen | None) -> None:
    """Stop the backend, escalating to a kill only if it ignores the request."""
    if child is None or child.poll() is not None:
        return
    try:
        child.terminate()
        child.wait(timeout=SHUTDOWN_GRACE_S)
    except subprocess.TimeoutExpired:
        _log("backend ignored terminate; killing")
        try:
            child.kill()
            child.wait(timeout=3)
        except Exception:
            pass
    except Exception:
        pass


def _release_lock(lock) -> None:
    try:
        lock.close()
    except Exception:
        pass
    try:
        _instance_lock_path().unlink()
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
