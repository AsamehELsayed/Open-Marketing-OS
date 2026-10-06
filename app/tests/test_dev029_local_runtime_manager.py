"""Managed server state, identity checks and clean shutdown fakes."""
import hashlib
import io
import zipfile
from dataclasses import replace

import pytest

from app.services.llm.local_model_catalog import (
    LocalModelCatalog, ModelProfile, ModelShard, RuntimeProfile,
)
from app.services.llm.local_model_download import LocalModelDownloader
from app.services.llm.local_runtime_manager import (
    LocalRuntimeError, LocalRuntimeManager, RuntimeObservation,
)


def _catalog(payload=b"synthetic model shard"):
    shard = ModelShard("model-00001-of-00001.gguf",
                      "https://huggingface.co/synthetic/repo/resolve/a/file.gguf",
                      len(payload), hashlib.sha256(payload).hexdigest())
    model = ModelProfile("synthetic-model", "Synthetic Model", "Test Publisher",
                         "synthetic/repo", "a" * 40,
                         "https://huggingface.co/synthetic/repo/commit/a",
                         "Apache-2.0", "https://huggingface.co/synthetic/repo/blob/main/LICENSE",
                         "Q4_K_M", (shard,), len(payload), len(payload) * 2)
    runtime = RuntimeProfile("llama.cpp", "b-test", "ggml-org/llama.cpp",
                             "b" * 40, "runtime.zip",
                             "https://github.com/ggml-org/llama.cpp/releases/download/test/runtime.zip",
                             1, "c" * 64, "MIT",
                             "https://github.com/ggml-org/llama.cpp/blob/main/LICENSE",
                             "https://github.com/ggml-org/llama.cpp/attestations/test",
                             "llama-server.exe")
    return LocalModelCatalog(1, model.model_id, model, runtime), payload


class Response(io.BytesIO):
    status = 200

    def geturl(self):
        return "https://huggingface.co/synthetic/repo/file.gguf"


class FakeProcess:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pid = 1234
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _prepared(tmp_path, *, probe=None, process_factory=None, port=8899):
    catalog, payload = _catalog()
    downloader = LocalModelDownloader(
        tmp_path, catalog=catalog,
        open_url=lambda *_args, **_kwargs: Response(payload),
        free_bytes=lambda _path: catalog.model.temporary_disk_bytes,
    )
    downloader.download(explicit=True)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(exist_ok=True)
    (runtime_dir / "llama-server.exe").write_bytes(b"test runtime placeholder")
    made = []

    def create(*args, **kwargs):
        proc = FakeProcess(*args, **kwargs)
        made.append(proc)
        return proc

    manager = LocalRuntimeManager(
        tmp_path, catalog=catalog, downloader=downloader,
        process_factory=process_factory or create,
        probe=probe or (lambda _url: RuntimeObservation(
            True, "ok", (catalog.model.expected_server_model_id,))),
        monotonic=iter([0.0, 0.1, 0.2, 0.3]).__next__,
        sleep=lambda _duration: None,
        port=port,
        readiness_timeout_s=10,
    )
    return manager, made


def test_start_is_loopback_only_and_ready_requires_observed_identity(tmp_path):
    manager, made = _prepared(tmp_path)
    state = manager.start()
    assert state["ready"] is True
    assert state["runtime_state"] == "ready"
    assert state["observed_model_id"] == "synthetic-model"
    args = made[0].args[0]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--alias") + 1] == "synthetic-model"
    assert args[args.index("--port") + 1] == "8899"
    manager.stop()
    assert made[0].terminated is True
    assert manager.status()["runtime_state"] == "stopped"


def test_wrong_server_model_identity_is_not_ready_and_is_stopped(tmp_path):
    manager, made = _prepared(
        tmp_path,
        probe=lambda _url: RuntimeObservation(True, "ok", ("unexpected-model",)),
    )
    with pytest.raises(LocalRuntimeError) as exc:
        manager.start()
    assert exc.value.code == "model_identity_mismatch"
    assert made[0].terminated is True
    assert manager.status()["ready"] is False
    assert manager.status()["runtime_state"] == "failed"
    assert manager.status()["error_code"] == "model_identity_mismatch"


def test_start_without_model_or_runtime_never_launches_process(tmp_path):
    catalog, _payload = _catalog()
    made = []
    manager = LocalRuntimeManager(
        tmp_path, catalog=catalog, process_factory=lambda *a, **kw: made.append(a),
        probe=lambda _url: RuntimeObservation(False, "unreachable", ()),
    )
    with pytest.raises(LocalRuntimeError) as exc:
        manager.start()
    assert exc.value.code == "model_not_verified"
    assert made == []


def test_rediscovery_restores_verified_model_and_start_recovers_runtime(tmp_path):
    manager, _made = _prepared(tmp_path)
    rediscovered = manager.rediscover()
    assert rediscovered["verification_state"] == "verified"
    assert rediscovered["activation_state"] == "active"
    assert rediscovered["runtime_state"] == "stopped"
    assert manager.start()["ready"] is True


def test_process_handle_is_recovered_after_manager_restart_and_stops_cleanly(tmp_path):
    manager1, processes1 = _prepared(tmp_path)
    manager1.start()
    previous_process = processes1[0]
    marker = manager1._process_marker
    assert marker.exists()

    manager2, processes2 = _prepared(tmp_path)
    manager2._process_recover = lambda pid, executable: (
        previous_process if pid == previous_process.pid and
        executable == manager2.executable else None
    )
    recovered = manager2.rediscover()
    assert recovered["runtime_state"] == "ready"
    assert recovered["observed_model_id"] == "synthetic-model"
    assert manager2.start()["ready"] is True
    assert processes2 == []

    manager2.stop()
    assert previous_process.terminated is True
    assert not marker.exists()


def test_verified_model_without_managed_binary_never_falls_back_to_path(tmp_path):
    manager, _made = _prepared(tmp_path)
    (tmp_path / "runtime" / "llama-server.exe").unlink()
    with pytest.raises(LocalRuntimeError) as exc:
        manager.start()
    assert exc.value.code == "runtime_missing"
