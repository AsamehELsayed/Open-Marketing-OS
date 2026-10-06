"""W2 API contract tests using synthetic projects and frozen W1 interfaces."""
from pathlib import Path
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.routes.api_spa import router as spa_router
from app.routes.files import router as files_router
from app.services import state


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    db = tmp_path / "api.db"
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", root)
    frozen_service = types.ModuleType("app.services.knowledge_index")
    frozen_service.file_index_state = lambda conn, row: {
        "indexed": bool(row.get("indexed")),
        "index_status": "indexed" if row.get("indexed") else "pending",
        "index_error": "",
    }
    frozen_service.retrieval_status = lambda root, pid, **kwargs: {
        "search_mode": "FTS", "vector_status": "NOT_AVAILABLE",
        "vector_reason": "not_installed",
    }
    class Store:
        mode = "FTS_ONLY"
        offline_reason = "test runtime unavailable"
        _vector_error = False

    frozen_service.retrieval_runtime = lambda root, pid: (Store(), object())
    # The upload route now passes its live DB connection so this frozen
    # compatibility fixture explicitly stands in for a verified FTS index.
    frozen_service.retrieval_status_for_store = lambda store, **kwargs: {
        "search_mode": "FTS", "vector_status": "NOT_AVAILABLE",
        "vector_reason": "not_installed",
    }
    frozen_service.file_capabilities = lambda: {
        "accepted_types": [], "max_document_bytes": 25 * 1024 * 1024,
        "max_image_bytes": 15 * 1024 * 1024, "ocr_supported": False,
    }
    monkeypatch.setitem(sys.modules, "app.services.knowledge_index", frozen_service)
    deps.init_db(db)
    with deps.get_db() as conn:
        for pid in ("audit014-a", "audit014-b"):
            repos.Projects.upsert(conn, {
                "id": pid, "name": pid, "website": "", "goal": "",
                "status": "active", "settings_json": "{}",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            })
        repos.Settings.set(conn, "active_project_id", "audit014-b", state.now_iso())
    app = FastAPI()
    app.include_router(files_router)
    app.include_router(spa_router)
    with TestClient(app) as client:
        yield client, root


def test_upload_and_list_project_knowledge_state(api_env):
    client, _ = api_env
    uploaded = client.post("/files/upload",
        files={"file": ("canary.txt", b"ORANGE-MARKETING-ELEPHANT-927", "text/plain")},
        data={"project_id": "audit014-a", "attach_scope": "project"})
    assert uploaded.status_code == 201, uploaded.text
    data = uploaded.json()["data"]
    assert data["project_id"] == "audit014-a"
    assert data["attach_scope"] == "project"
    assert data["index_status"] == "indexed"
    assert data["search_mode"] == "FTS"
    assert "rel_path" not in data and "safe_name" not in data

    listed = client.get("/files", params={"project_id": "audit014-a", "attach_scope": "project"})
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1
    assert listed.json()["data"][0]["index_status"] == "indexed"
    assert client.get("/files", params={"project_id": "audit014-b"}).json()["data"] == []


def test_rebuild_forwards_selected_project_and_reports_partial(api_env, monkeypatch):
    client, _ = api_env
    calls = []

    def build_index(conn, root, project_id=None):
        calls.append((Path(root), project_id))
        return {"project_id": project_id, "discovered": 2, "indexed": 1,
                "skipped": 0, "deleted": 0, "quarantined": 0, "failed": 1,
                "chunks": 3, "search_mode": "FTS", "vector_status": "NOT_AVAILABLE",
                "vector_reason": "not_installed", "errors": [{"code": "extract_failed"}]}

    monkeypatch.setattr(state, "build_index", build_index)
    response = client.post("/api/settings/workspace/reindex", json={"project_id": "audit014-a"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert calls[0][1] == "audit014-a"  # active/default project was B
    assert data["project_id"] == "audit014-a"
    assert data["status"] == "partial"
    assert data["report"]["failed"] == 1
    assert "1 failed" in data["message"]


def test_rebuild_does_not_call_nonsearchable_only_report_success(api_env, monkeypatch):
    client, _ = api_env
    from app.services import state

    monkeypatch.setattr(state, "build_index", lambda conn, root, project_id=None: {
        "project_id": project_id, "discovered": 1, "indexed": 0,
        "skipped": 0, "deleted": 0, "quarantined": 0, "failed": 0,
        "not_searchable": 1, "chunks": 0, "search_mode": "FTS",
        "vector_status": "NOT_AVAILABLE", "errors": [{"code": "no_extractable_text"}],
    })
    response = client.post("/api/settings/workspace/reindex", json={"project_id": "audit014-a"})
    data = response.json()["data"]
    assert data["status"] == "partial"
    assert "1 not searchable" in data["message"]


def test_config_uses_live_runtime_status_not_saved_global(api_env, monkeypatch):
    client, _ = api_env
    from app.services.config_service import ConfigService
    ConfigService.set_setting("chroma_status", "hybrid")
    response = client.get("/api/settings/config", params={"project_id": "audit014-a"})
    assert response.status_code == 200
    files = response.json()["data"]["files_knowledge"]
    assert files["project_id"] == "audit014-a"
    assert files["search_mode"] == "FTS"
    assert files["vector_status"] == "NOT_AVAILABLE"
    assert files["max_image_bytes"] == 15 * 1024 * 1024
    assert files["ocr_supported"] is False


def test_config_status_exception_fails_closed_without_claiming_fts(api_env, monkeypatch):
    client, _ = api_env

    def fail_runtime(*_args, **_kwargs):
        raise RuntimeError("synthetic status construction failure")

    monkeypatch.setattr(sys.modules["app.services.knowledge_index"], "retrieval_status", fail_runtime)
    response = client.get("/api/settings/config", params={"project_id": "audit014-a"})

    assert response.status_code == 200
    files = response.json()["data"]["files_knowledge"]
    assert files["search_mode"] == "UNAVAILABLE"
    assert files["vector_status"] == "NOT_AVAILABLE"
    assert files["vector_reason"] == "runtime_status_unavailable"

