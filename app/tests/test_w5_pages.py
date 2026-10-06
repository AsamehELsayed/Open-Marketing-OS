from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.main import create_app
from app.services import state as store
from app.services.adapters import importer, parsers

ROOT = Path(__file__).resolve().parents[2]
config_store = importlib.import_module("app.services.credentials.store")


class MemoryBackend:
    name = "test-memory"

    def __init__(self):
        self.values = {}

    def put(self, key, plaintext):
        self.values[key] = bytes(plaintext)

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "w5.db"
    vault_dir = tmp_path / "credentials"
    vault_dir.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(vault_dir))
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "APIFY_API_TOKEN", "BRIGHTDATA_API_KEY", "BRIGHTDATA_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    deps.init_db(db)
    with deps.get_db(db) as conn:
        store.run_imports(conn, ROOT)
    previous = config_store._default
    config_store.set_default_backend(MemoryBackend())
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client
    config_store.set_default_backend(previous)


def test_legacy_pages_are_not_product_pages(client):
    for path, target in (
        ("/", "/app"),
        ("/chat", "/app/chat/new"),
        ("/campaigns", "/app/campaigns"),
        ("/tasks", "/app/jobs"),
        ("/approvals", "/app/approvals"),
        ("/companies", "/app"),
        ("/brain", "/app/knowledge"),
        ("/results", "/app/results"),
        ("/settings", "/app/settings"),
        ("/jobs", "/app/jobs"),
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == target
        assert "<html" not in response.text.lower()


def test_react_assets_and_health_are_current(client):
    assert client.get("/app").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/api/settings/config").status_code == 200


def test_approval_business_rule_survives_dead_legacy_route(client):
    assert client.post("/approvals/missing/decide", data={"decision": "approved"}).status_code == 404
    with deps.get_db() as conn:
        repos.Approvals.upsert(conn, {
            "id": "test-pending-01", "project_id": "starter", "kind": "Test", "title": "t",
            "body_md": "", "status": "pending", "fields_json": "{}", "decided_by": None,
            "decided_at": None, "updated_at": "2026-09-16T00:00:00+00:00",
        })
    with deps.get_db() as conn:
        decided = store.decide_approval(conn, "test-pending-01", "approved", project_id="starter")
    assert decided["status"] == "approved"
    with pytest.raises(ValueError):
        with deps.get_db() as conn:
            store.decide_approval(conn, "test-pending-01", "rejected", project_id="starter")


def test_task_and_campaign_business_rules_survive_dead_routes(client):
    assert client.post("/tasks/weekly-p1/status", data={"status": "done"}).status_code == 404
    assert client.post("/campaigns/opp-01/status", data={"status": "executing"}).status_code == 404
    with deps.get_db() as conn:
        task = store.set_task_status(conn, "weekly-p1", "in_progress", project_id="starter")
        campaign = store.set_campaign_status(conn, "opp-01", "executing", project_id="starter")
    assert task["status"] == "in_progress"
    assert campaign["status"] == "executing"
    with pytest.raises(ValueError):
        with deps.get_db() as conn:
            store.set_task_status(conn, "weekly-p1", "bogus", project_id="starter")
    with pytest.raises(ValueError):
        with deps.get_db() as conn:
            store.set_campaign_status(conn, "opp-01", "launched", project_id="starter")


def test_unknown_status_stays_pending(tmp_path):
    queue = tmp_path / "q.md"
    queue.write_text("# Q\n\n## ID: x-01\n\n- TYPE: Ambiguous block, no status line.\n", encoding="utf-8")
    assert parsers.parse_approval_queue(queue)[0]["status"] == "pending"


def test_conflict_resolution_is_a_dead_legacy_surface(client):
    with deps.get_db() as conn:
        repos.Events.log(conn, {
            "id": "evt-conflict-test", "kind": "import_conflict", "ref_id": "opp-01",
            "detail_json": json.dumps({"adapter": "backlog", "source": str(ROOT / "strategy" / "opportunity-backlog.md")}),
            "created_at": store.now_iso(),
        })
    assert client.post("/settings/conflicts/resolve", data={"adapter": "backlog", "row_id": "opp-01", "keep": "db"}).status_code == 404
    with deps.get_db() as conn:
        result = store.resolve_conflict(conn, ROOT, "backlog", "opp-01", "db")
    assert result["row_id"] == "opp-01"


def test_reindex_uses_react_workspace_action(client):
    response = client.post("/api/settings/workspace/reindex", json={"project_id": "starter"})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "indexed"


def test_reimport_is_idempotent_through_react_api(client):
    first = client.post("/api/settings/workspace/reimport", json={"project_id": "starter"})
    assert first.status_code == 200
    second = client.post("/api/settings/workspace/reimport", json={"project_id": "starter"})
    assert second.status_code == 200


def test_measurement_business_rule_survives_dead_legacy_route(client):
    assert client.post("/experiments/test-02-proof-block/measurements", data={"decision": "CONTINUE", "evidence_md": "Clicks up 10%."}).status_code == 404
    with deps.get_db() as conn:
        row = store.record_measurement_decision(conn, "test-02-proof-block", "Clicks up 10%.", "CONTINUE", project_id="starter")
    assert row["decision"] == "CONTINUE"
    with pytest.raises(ValueError):
        with deps.get_db() as conn:
            store.record_measurement_decision(conn, "test-02-proof-block", "   ", "STOP", project_id="starter")


def test_learning_gate_rejects_unsourced(client):
    with pytest.raises(ValueError):
        with deps.get_db() as conn:
            repos.Learnings.insert(conn, {"id": "l1", "body_md": "x", "source": "", "observed_at": "2026-09-16T00:00:00+00:00"})


def test_no_source_writes(client):
    targets = [ROOT / "production" / "approval-queue.md", ROOT / "company" / "company.yaml", ROOT / "strategy" / "opportunity-backlog.md"]
    before = {str(path): importer.file_sha(path) for path in targets}
    client.post("/api/settings/workspace/reimport", json={"project_id": "starter"})
    with deps.get_db() as conn:
        store.set_task_status(conn, "weekly-p2", "done", project_id="starter")
    after = {str(path): importer.file_sha(path) for path in targets}
    assert before == after
