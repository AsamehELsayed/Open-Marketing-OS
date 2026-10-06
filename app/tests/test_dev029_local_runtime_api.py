from __future__ import annotations

from fastapi import HTTPException

from app.routes import api_spa


class FakeManager:
    def __init__(self):
        self.download_calls = []
        self.start_calls = 0
        self.stop_calls = 0

    @staticmethod
    def status():
        return {
            "model": {
                "model_id": "fixture-qwen",
                "display_name": "Fixture Qwen",
                "source": "fixture/repo",
                "source_url": "https://example.invalid/model",
                "publisher": "Fixture publisher",
                "license": "Apache-2.0",
                "license_url": "https://example.invalid/license",
                "model_bytes": 123,
                "temporary_disk_bytes": 246,
                "shards": [{"filename": "part.gguf", "size_bytes": 123,
                            "sha256": "a" * 64}],
                "runtime": {"version": "fixture", "revision": "b" * 40},
            },
            "download_state": "ready",
            "download_progress_bytes": 123,
            "verification_state": "verified",
            "activation_state": "active",
            "runtime_state": "ready",
            "observed_model_id": "fixture-qwen",
            "expected_model_id": "fixture-qwen",
            "ready": True,
            "error_code": None,
            # These values must never cross the API boundary.
            "private_path": r"C:\private\model.gguf",
            "command_line": "--model secret-path",
            "credential": "synthetic-secret-sentinel",
        }

    def rediscover(self):
        return self.status()

    def download_model(self, *, explicit=False):
        self.download_calls.append(explicit)
        if not explicit:
            raise AssertionError("download must be explicitly requested")
        return {"verified": True}

    def start(self):
        self.start_calls += 1
        return self.status()

    def stop(self):
        self.stop_calls += 1
        return {**self.status(), "runtime_state": "stopped", "ready": False}


def test_runtime_get_is_flat_safe_and_preserves_shard_provenance(monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(api_spa, "_local_runtime_manager", lambda: manager)

    result = api_spa.spa_local_runtime_status()
    data = str(result)

    assert "'model_id': 'fixture-qwen'" in data
    assert "'ready': True" in data
    assert 'part.gguf:' in data
    assert "private_path" not in data
    assert "command_line" not in data
    assert "synthetic-secret-sentinel" not in data
    assert r"C:\private" not in data


def test_only_download_action_triggers_model_download(monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(api_spa, "_local_runtime_manager", lambda: manager)

    api_spa.spa_local_runtime_start()
    api_spa.spa_local_runtime_stop()
    assert manager.download_calls == []
    assert manager.start_calls == 1
    assert manager.stop_calls == 1

    api_spa.spa_local_model_download()
    assert manager.download_calls == [True]


def test_start_error_returns_only_stable_safe_code(monkeypatch):
    class Broken(FakeManager):
        def start(self):
            error = RuntimeError("C:\\private\\model.gguf --token synthetic")
            error.code = "runtime_missing"
            raise error

    monkeypatch.setattr(api_spa, "_local_runtime_manager", lambda: Broken())
    try:
        api_spa.spa_local_runtime_start()
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail == {"code": "runtime_missing"}
        assert "private" not in str(exc.detail)
    else:
        raise AssertionError("expected a safe runtime failure")
