"""Phase 4 Deliverable Regression and Lock Recovery Tests.

Tests:
1. Ready-to-post deliverable copy extraction and audit separation.
2. Approved package export structure (ready copy on top, audit trail on bottom).
3. Cross-process database lock acquisition, duplicate rejection, and stale lock auto-recovery.
4. Generation timing visibility (elapsed_s) and model metadata.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import deps
from app.database.sqlite import connect
from app.routes.campaign_deliverables import router
from app.services import campaign_deliverables as deliverables
from app.services import campaign_flow as flow
from app.services import campaign_production as production
from app.services import campaign_request as request_mod
from app.tests.campaign_fake_provider import DeterministicCampaignProvider


def _seed(db):
    conn = connect(db)
    conn.execute(
        "INSERT INTO projects(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("p_test", "Test Bakery", "2026-01-01", "2026-01-01")
    )
    conn.commit()
    return conn


def _make_campaign(conn, project_id="p_test", campaign_id="c_test", text="Create a 30-day Instagram campaign targeting local families."):
    intake = production.campaign_intake(text, objective="", turn_id="t1")
    meta = {"campaign_intake": intake}
    conn.execute(
        "INSERT INTO campaigns(id, project_id, title, workflow_json, updated_at) VALUES (?, ?, ?, ?, ?)",
        (campaign_id, project_id, "Test Campaign", json.dumps(meta), "2026-01-01")
    )
    conn.commit()
    return intake


@pytest.fixture()
def client_env(tmp_path, monkeypatch):
    from app.services.llm.model_router import ModelRouter

    db = tmp_path / "phase4.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    conn = _seed(db)
    provider = DeterministicCampaignProvider()
    router_obj = ModelRouter(local_provider=provider, default_local_model="deterministic-test")
    monkeypatch.setattr(production, "_router", lambda: router_obj)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), conn, provider


def test_extract_deliverable_parts_with_final_copy():
    sample = (
        "## Final copy\n"
        "Fresh mornings start with wholesome baking.\n\n"
        "Stop by this weekend.\n\n"
        "## Persisted user facts\n"
        "- Audience: local families\n\n"
        "## Retrieved evidence\n"
        "No project evidence available\n\n"
        "## AI suggestions\n"
        "Suggestion: Keep tone warm.\n\n"
        "## Unknown or unsupported information\n"
        "None.\n"
    )
    parts = deliverables.extract_deliverable_parts(sample)
    assert parts["has_final_copy"] is True
    assert parts["has_audit"] is True
    assert "Fresh mornings start with wholesome baking." in parts["final_copy"]
    assert "Stop by this weekend." in parts["final_copy"]
    assert "## Persisted user facts" in parts["audit_md"]
    assert "## AI suggestions" in parts["audit_md"]


def test_extract_deliverable_parts_legacy_fallback():
    sample = (
        "## Persisted user facts\n"
        "- Audience: local families\n\n"
        "## Retrieved evidence\n"
        "No project evidence available\n\n"
        "## AI suggestions\n"
        "- Suggestion, not a fact: Handcrafted breads baked fresh daily.\n"
        "Suggestion: Visit today.\n\n"
        "## Unknown or unsupported information\n"
        "None.\n"
    )
    parts = deliverables.extract_deliverable_parts(sample)
    assert parts["has_final_copy"] is True
    assert parts["has_audit"] is True
    assert "Handcrafted breads baked fresh daily." in parts["final_copy"]
    assert "Visit today." in parts["final_copy"]


def test_approved_package_export_structure(client_env):
    client, conn, _ = client_env
    _make_campaign(conn, "p_test", "c_test")
    r = client.post("/api/projects/p_test/campaigns/c_test/generate")
    assert r.status_code == 200

    items = deliverables.list_deliverables(conn, "p_test", "c_test")
    assert len(items) >= 2

    # Approve only the first deliverable
    first = items[0]
    first = deliverables.transition(conn, "p_test", "c_test", first["id"], first["current_version"], "IN_REVIEW")
    first = deliverables.transition(conn, "p_test", "c_test", first["id"], first["current_version"], "APPROVED")

    export_zip = deliverables.approved_package_export(conn, "p_test", "c_test")
    archive = zipfile.ZipFile(io.BytesIO(export_zip))
    names = archive.namelist()

    assert "00-package-summary.md" in names
    summary = archive.read("00-package-summary.md").decode("utf-8")
    assert f"- Included: {first['title']} - approved version {first['current_version']}" in summary

    # Unapproved items should be noted as NOT INCLUDED
    for item in items[1:]:
        assert f"- NOT INCLUDED (not approved yet): {item['title']}" in summary

    # Find the approved deliverable file
    deliverable_files = [n for n in names if n != "00-package-summary.md"]
    assert len(deliverable_files) == 1
    content = archive.read(deliverable_files[0]).decode("utf-8")

    # Ready copy is on top, followed by separator and Audit & Provenance
    assert f"# {first['title']}" in content
    assert "---" in content
    assert "## Audit & Provenance" in content


def test_cross_process_db_lock_and_recovery(client_env):
    _, conn, _ = client_env
    _make_campaign(conn, "p_test", "c_lock")

    # 1. Acquire initial lock
    flow._acquire_db_lock(conn, "p_test", "c_lock", "turn-1")
    campaign = deliverables._require_scope(conn, "p_test", "c_lock")
    meta = json.loads(campaign["workflow_json"])
    assert "generation_in_progress" in meta
    assert meta["generation_in_progress"]["turn_id"] == "turn-1"

    # 2. Second concurrent acquisition within timeout raises 409
    with pytest.raises(flow.FlowError) as exc_info:
        flow._acquire_db_lock(conn, "p_test", "c_lock", "turn-2")
    assert exc_info.value.status == 409
    assert exc_info.value.code == "generation_in_progress"

    # 3. Simulate stale lock (started 200s ago)
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    meta["generation_in_progress"]["started_at"] = stale_time
    production._write_campaign_metadata(conn, "p_test", "c_lock", meta)

    # 4. New acquisition recovers stale lock automatically
    flow._acquire_db_lock(conn, "p_test", "c_lock", "turn-3")
    campaign_after = deliverables._require_scope(conn, "p_test", "c_lock")
    meta_after = json.loads(campaign_after["workflow_json"])
    assert meta_after["generation_in_progress"]["turn_id"] == "turn-3"

    # 5. Release cleans up
    flow._release_db_lock(conn, "p_test", "c_lock")
    campaign_clean = deliverables._require_scope(conn, "p_test", "c_lock")
    meta_clean = json.loads(campaign_clean["workflow_json"])
    assert "generation_in_progress" not in meta_clean


def test_generate_from_plan_returns_elapsed_and_model_status(client_env):
    client, conn, _ = client_env
    _make_campaign(conn, "p_test", "c_gen")

    res = client.post("/api/projects/p_test/campaigns/c_gen/generate")
    assert res.status_code == 200
    data = res.json()["data"]

    assert "created" in data and len(data["created"]) > 0
    assert "titles" in data and len(data["titles"]) > 0
    assert "provider" in data
    assert "model" in data
    assert "elapsed_s" in data
    assert isinstance(data["elapsed_s"], (int, float))
    assert data["elapsed_s"] >= 0.0
