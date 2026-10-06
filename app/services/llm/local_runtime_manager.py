"""Managed loopback llama-server lifecycle for the pinned Local profile."""
from __future__ import annotations

import json
import os
import ctypes
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .local_model_catalog import LocalModelCatalog, default_catalog
from .local_model_download import LocalModelDownloader


class LocalRuntimeError(RuntimeError):
    """Safe managed-runtime error with stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RuntimeObservation:
    ready: bool
    health_status: str
    model_ids: tuple[str, ...]


class _WindowsManagedProcess:
    """Minimal process handle recovered by PID after an OMOS restart."""

    def __init__(self, pid: int, executable: Path) -> None:
        self.pid = int(pid)
        self._executable = str(executable.resolve())
        self.returncode = None

    @staticmethod
    def _kernel32():
        return ctypes.WinDLL("kernel32", use_last_error=True)

    def _open(self, access: int):
        from ctypes import wintypes
        kernel = self._kernel32()
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(access, False, self.pid)
        return kernel, handle

    @classmethod
    def _image_path(cls, pid: int) -> str | None:
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            query = kernel.QueryFullProcessImageNameW
            query.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                              wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            query.restype = wintypes.BOOL
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if not query(handle, 0, buffer, ctypes.byref(length)):
                return None
            return buffer.value
        finally:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            kernel.CloseHandle(handle)

    def poll(self):
        image = self._image_path(self.pid)
        if image and os.path.normcase(os.path.abspath(image)) == os.path.normcase(self._executable):
            return None
        self.returncode = 1
        return self.returncode

    def terminate(self):
        if self.poll() is not None:
            return
        kernel, handle = self._open(0x0001 | 0x00100000)  # TERMINATE | SYNCHRONIZE
        if not handle:
            raise OSError("could not open managed llama process")
        try:
            kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel.TerminateProcess.restype = ctypes.c_int
            if not kernel.TerminateProcess(handle, 0):
                raise OSError("could not terminate managed llama process")
        finally:
            kernel.CloseHandle(handle)

    def kill(self):
        if self.poll() is not None:
            return
        kernel, handle = self._open(0x0001 | 0x00100000)
        if not handle:
            raise OSError("could not open managed llama process")
        try:
            kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel.TerminateProcess.restype = ctypes.c_int
            if not kernel.TerminateProcess(handle, 1):
                raise OSError("could not terminate managed llama process")
        finally:
            kernel.CloseHandle(handle)

    def wait(self, timeout=None):
        from ctypes import wintypes
        kernel, handle = self._open(0x00100000)  # SYNCHRONIZE
        if not handle:
            self.returncode = 1
            return self.returncode
        try:
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            millis = 0xFFFFFFFF if timeout is None else max(0, int(float(timeout) * 1000))
            result = kernel.WaitForSingleObject(handle, millis)
            if result == 0x00000102:  # WAIT_TIMEOUT
                raise subprocess.TimeoutExpired("llama-server", timeout)
            self.returncode = 0
            return self.returncode
        finally:
            kernel.CloseHandle(handle)


class LocalRuntimeManager:
    """Owns model discovery, explicit download, server start and shutdown.

    The server always binds to IPv4 loopback. The injected `probe` seam must
    return an observed `/health` status and `/v1/models` ids; configured
    identity is never reported as observed identity without that response.
    """

    def __init__(
        self,
        install_root: str | Path,
        *,
        catalog: LocalModelCatalog | None = None,
        downloader: LocalModelDownloader | None = None,
        process_factory: Callable | None = None,
        probe: Callable[[str], RuntimeObservation] | None = None,
        process_recover: Callable[[int, Path], object | None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        port: int = 8080,
        readiness_timeout_s: float = 45.0,
    ) -> None:
        self.install_root = Path(install_root).expanduser().resolve()
        self.catalog = catalog or default_catalog()
        self.downloader = downloader or LocalModelDownloader(
            self.install_root, catalog=self.catalog)
        self.runtime_dir = (self.install_root / "runtime").resolve()
        self.executable = (self.runtime_dir / self.catalog.runtime.executable).resolve()
        self._require_contained(self.runtime_dir, self.install_root)
        self._require_contained(self.executable, self.runtime_dir)
        if not 1 <= int(port) <= 65535:
            raise ValueError("port must be a valid TCP port")
        self.port = int(port)
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.readiness_timeout_s = max(0.1, float(readiness_timeout_s))
        self._process_factory = process_factory or subprocess.Popen
        self._process_recover = process_recover or self._recover_windows_process
        self._probe = probe or self._http_probe
        self._monotonic = monotonic
        self._sleep = sleep
        self._process = None
        self._lock = threading.RLock()
        self._runtime_state = "stopped"
        self._observed_model_id: str | None = None
        self._last_error_code: str | None = None
        self._last_observation: RuntimeObservation | None = None

    @staticmethod
    def _recover_windows_process(pid: int, executable: Path):
        if os.name != "nt":
            return None
        process = _WindowsManagedProcess(pid, executable)
        return process if process.poll() is None else None

    @property
    def _process_marker(self) -> Path:
        return self.runtime_dir / "managed-server.json"

    def _write_process_marker(self) -> None:
        pid = getattr(self._process, "pid", None)
        if not isinstance(pid, int) or pid <= 0:
            raise LocalRuntimeError("runtime_pid_unavailable", "Managed runtime process identity is unavailable")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        marker = self._process_marker
        tmp = marker.with_name(f"{marker.name}.{pid}.tmp")
        try:
            with tmp.open("x", encoding="utf-8") as stream:
                json.dump({
                    "pid": pid,
                    "model_id": self.catalog.model.model_id,
                    "runtime_revision": self.catalog.runtime.revision,
                }, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, marker)
        finally:
            tmp.unlink(missing_ok=True)

    def _recover_process_from_marker(self):
        try:
            marker = json.loads(self._process_marker.read_text(encoding="utf-8"))
            pid = int(marker["pid"])
            if (pid <= 0 or marker.get("model_id") != self.catalog.model.model_id or
                    marker.get("runtime_revision") != self.catalog.runtime.revision):
                return None
            return self._process_recover(pid, self.executable)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    @staticmethod
    def _require_contained(path: Path, root: Path) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise LocalRuntimeError("path_outside_install", "Managed runtime path is outside its install root") from exc

    @property
    def model_path(self) -> Path | None:
        return self.downloader.active_model_path

    def _http_probe(self, base_url: str) -> RuntimeObservation:
        def get_json(path: str) -> tuple[int, dict]:
            request = urllib.request.Request(base_url + path, method="GET")
            with urllib.request.urlopen(request, timeout=2) as response:
                return int(getattr(response, "status", 200)), json.loads(response.read().decode("utf-8"))

        try:
            health_code, health = get_json("/health")
            models_code, models = get_json("/v1/models")
        except (OSError, TimeoutError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            return RuntimeObservation(False, "unreachable", ())
        status = str(health.get("status", "")) if isinstance(health, dict) else ""
        rows = models.get("data", []) if isinstance(models, dict) else []
        ids = tuple(str(row.get("id")) for row in rows
                    if isinstance(row, dict) and row.get("id"))
        return RuntimeObservation(
            ready=health_code == 200 and models_code == 200 and
                  status.lower() in {"ok", "ready", "loaded", "success"},
            health_status=status.lower() if health_code == 200 else "unhealthy",
            model_ids=ids,
        )

    def _observe(self) -> RuntimeObservation:
        try:
            result = self._probe(self.base_url)
            if isinstance(result, RuntimeObservation):
                return result
            # Compact seam compatibility: fakes may return the status mapping.
            if isinstance(result, dict):
                ids = result.get("model_ids") or result.get("models") or ()
                return RuntimeObservation(
                    bool(result.get("ready")),
                    str(result.get("health_status", "")),
                    tuple(str(item) for item in ids),
                )
        except Exception:
            pass
        return RuntimeObservation(False, "unreachable", ())

    def rediscover(self) -> dict:
        """Revalidate persisted model activation after application restart."""
        with self._lock:
            model_dir = self.downloader.active_directory
            model_ready = model_dir is not None
            if self._process is None and model_ready:
                self._process = self._recover_process_from_marker()
            runtime_observation = self._observe() if model_ready else None
            if self._process is not None and self._process.poll() is None:
                if self._matches_model(runtime_observation):
                    self._runtime_state = "ready"
                    self._observed_model_id = self.catalog.model.expected_server_model_id
                    self._last_observation = runtime_observation
                else:
                    self.stop()
                    self._runtime_state = "failed"
                    self._last_error_code = "model_identity_mismatch"
            elif not model_ready:
                self._runtime_state = "model_missing"
                self._observed_model_id = None
            else:
                # The verified model and pinned runtime are rediscovered. The
                # server starts only through start(), after the host is ready.
                self._runtime_state = "stopped"
                self._observed_model_id = None
            return self.status()

    def _matches_model(self, observation: RuntimeObservation | None) -> bool:
        return bool(observation and observation.ready and
                    self.catalog.model.expected_server_model_id in observation.model_ids)

    def download_model(self, *, explicit: bool = False) -> dict:
        try:
            result = self.downloader.download(explicit=explicit)
        except Exception as exc:
            code = getattr(exc, "code", "download_failed")
            raise LocalRuntimeError(code, "Local model download or verification failed") from exc
        return {
            "model_id": result.model_id,
            "download_state": result.state,
            "verified": result.verified,
            "activated": result.activated,
            "downloaded_bytes": result.downloaded_bytes,
        }

    def _server_args(self) -> list[str]:
        model_path = self.model_path
        if model_path is None:
            raise LocalRuntimeError("model_not_verified", "A verified Local model is required before runtime start")
        if not self.executable.is_file():
            raise LocalRuntimeError("runtime_missing", "The pinned Local runtime is not installed")
        self._require_contained(model_path, self.install_root / "models")
        return [
            str(self.executable),
            "--model", str(model_path),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--alias", self.catalog.model.expected_server_model_id,
            "--ctx-size", "4096",
            "--n-gpu-layers", "0",
            "--parallel", "1",
        ]

    def start(self) -> dict:
        with self._lock:
            if self._process is None:
                self._process = self._recover_process_from_marker()
            if self._process is not None and self._process.poll() is None:
                observation = self._observe()
                if self._matches_model(observation):
                    self._runtime_state = "ready"
                    self._observed_model_id = self.catalog.model.expected_server_model_id
                    self._last_observation = observation
                    return self.status()
                raise LocalRuntimeError("runtime_identity_mismatch", "Running Local runtime reported an unexpected model")
            self._runtime_state = "starting"
            self._last_error_code = None
            args = self._server_args()
            kwargs = {
                "cwd": str(self.runtime_dir),
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self._process = self._process_factory(args, **kwargs)
                self._write_process_marker()
            except Exception as exc:
                if self._process is not None and self._process.poll() is None:
                    try:
                        self._process.terminate()
                    except Exception:
                        pass
                self._process = None
                self._runtime_state = "failed"
                self._last_error_code = "runtime_start_failed"
                raise LocalRuntimeError("runtime_start_failed", "Managed Local runtime could not start") from exc

            deadline = self._monotonic() + self.readiness_timeout_s
            while self._monotonic() < deadline:
                if self._process.poll() is not None:
                    self._runtime_state = "failed"
                    self._last_error_code = "runtime_exited_early"
                    raise LocalRuntimeError("runtime_exited_early", "Managed Local runtime exited before becoming ready")
                observation = self._observe()
                if self._matches_model(observation):
                    self._runtime_state = "ready"
                    self._observed_model_id = self.catalog.model.expected_server_model_id
                    self._last_observation = observation
                    return self.status()
                if observation.ready and observation.model_ids:
                    self.stop()
                    self._runtime_state = "failed"
                    self._last_error_code = "model_identity_mismatch"
                    raise LocalRuntimeError("model_identity_mismatch", "Local runtime reported an unexpected model")
                self._sleep(min(0.2, max(0, deadline - self._monotonic())))
            self.stop()
            self._runtime_state = "failed"
            self._last_error_code = "readiness_timeout"
            raise LocalRuntimeError("readiness_timeout", "Managed Local runtime did not become ready")

    def stop(self, *, timeout_s: float = 5.0) -> dict:
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=max(0.1, float(timeout_s)))
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=max(0.1, float(timeout_s)))
                except Exception as exc:
                    self._runtime_state = "failed"
                    self._last_error_code = "runtime_stop_failed"
                    raise LocalRuntimeError("runtime_stop_failed", "Managed Local runtime could not stop cleanly") from exc
            self._process = None
            self._process_marker.unlink(missing_ok=True)
            self._runtime_state = "stopped"
            self._observed_model_id = None
            self._last_observation = None
            return self.status()

    def status(self) -> dict:
        profile = self.catalog.model
        runtime = self.catalog.runtime
        metadata = self.catalog.public_model_metadata()
        model_dir = self.downloader.active_directory
        verified = model_dir is not None
        download_state = self.downloader.state
        if verified and download_state == "not_downloaded":
            download_state = "ready"
        return {
            **metadata,
            "model": metadata,
            "download_state": download_state,
            "download_progress_bytes": self.downloader.progress_bytes,
            "verification_state": "verified" if verified else "not_verified",
            "activation_state": "active" if verified else "inactive",
            "runtime_state": self._runtime_state,
            "runtime_version": runtime.version,
            "runtime_revision": runtime.revision,
            "observed_model_id": self._observed_model_id,
            "expected_model_id": profile.expected_server_model_id,
            "ready": self._runtime_state == "ready" and verified,
            "error_code": self._last_error_code,
        }


def get_local_runtime_manager(install_root: str | Path | None = None) -> LocalRuntimeManager:
    """Default factory; install root may be set by packaged app bootstrap."""
    if install_root is None:
        install_root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "OpenMarketingOS"
    return LocalRuntimeManager(install_root)
