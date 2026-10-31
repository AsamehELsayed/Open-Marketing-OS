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


def test_extract_deliverable_parts_no_suggestion_fallback():
    """AI suggestions must NEVER be promoted to publish-ready copy as a fallback."""
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
    assert parts["has_final_copy"] is False, "Suggestions must not be promoted to final copy!"
    assert parts["final_copy"] == ""
    assert parts["has_audit"] is True
    assert "Handcrafted breads baked fresh daily." in parts["audit_md"]


def test_social_post_ready_to_post_contract():
    social_sample = (
        "## Final copy\n"
        "Crispy crust, tender crumb, and nothing artificial. We wake up before sunrise "
        "so your family starts the morning right. 🥖☀️\n\n"
        "Stop by today for our fresh sourdough loaves!\n"
        "#FreshBaking #ArtisanBread #FamilyBakery\n\n"
        "## Persisted user facts\n"
        "- Product: Artisan sourdough\n\n"
        "## Retrieved evidence\n"
        "No project evidence available\n\n"
        "## AI suggestions\n"
        "Suggestion: Post at 7:30 AM.\n\n"
        "## Unknown or unsupported information\n"
        "None.\n"
    )
    parts = deliverables.extract_deliverable_parts(social_sample)
    assert parts["has_final_copy"] is True
    assert parts["has_audit"] is True
    assert "#FreshBaking" in parts["final_copy"]
    assert "Crispy crust" in parts["final_copy"]
    assert "## Persisted user facts" not in parts["final_copy"]
    assert "Suggestion: Post at 7:30 AM." in parts["audit_md"]


def test_email_subject_and_body_ready_to_post_contract():
    email_sample = (
        "## Final copy\n"
        "Subject: Warm morning loaves are fresh out of the oven!\n\n"
        "Good morning,\n\n"
        "There is nothing quite like warm, fresh bread on a crisp morning. "
        "Come join us this weekend and bring home something special for breakfast.\n\n"
        "Warmly,\n"
        "The Neighborhood Bakers\n\n"
        "## Persisted user facts\n"
        "- Audience: local families\n\n"
        "## Retrieved evidence\n"
        "No project evidence available\n\n"
        "## AI suggestions\n"
        "Suggestion: Send on Friday morning.\n\n"
        "## Unknown or unsupported information\n"
        "None.\n"
    )
    parts = deliverables.extract_deliverable_parts(email_sample)
    assert parts["has_final_copy"] is True
    assert parts["has_audit"] is True
    assert "Subject: Warm morning loaves" in parts["final_copy"]
    assert "Good morning," in parts["final_copy"]
    assert "## Persisted user facts" not in parts["final_copy"]


def test_ad_copy_headline_and_cta_contract():
    ad_sample = (
        "## Final copy\n"
        "Headline: Handcrafted Bread Made Fresh Daily\n"
        "Body: Wholesome ingredients and artisan recipes made for family tables. Taste real craft.\n"
        "Call to action: Visit Bakery\n\n"
        "## Persisted user facts\n"
        "- Audience: local families\n\n"
        "## Retrieved evidence\n"
        "No project evidence available\n\n"
        "## AI suggestions\n"
        "Suggestion: Target parents within 5 miles.\n\n"
        "## Unknown or unsupported information\n"
        "None.\n"
    )
    parts = deliverables.extract_deliverable_parts(ad_sample)
    assert parts["has_final_copy"] is True
    assert "Headline: Handcrafted Bread" in parts["final_copy"]
    assert "Call to action: Visit Bakery" in parts["final_copy"]
    assert "Suggestion: Target parents" not in parts["final_copy"]


def test_malformed_and_incomplete_model_responses():
    """Ensure malformed model outputs are rejected by output validation."""
    context = {"evidence_ids": [], "project_id": "p_test"}

    # Missing all headings
    with pytest.raises(production.DeliverableValidationError) as exc:
        production.validate_generated_items(
            '## DELIVERABLE type="social_post" title="Post" platform="Instagram"\n'
            'Just some raw copy without required audit sections.\n',
            types=["social_post"], allowed_ids=[], context=context, user_request=""
        )
    assert "did not separate" in str(exc.value)

    # Missing requested type
    with pytest.raises(production.DeliverableValidationError) as exc:
        production.validate_generated_items(
            '## DELIVERABLE type="ad_copy" title="Ad" platform=""\n'
            '## Final copy\nAd copy\n'
            '## Persisted user facts\nNo persisted facts supplied.\n'
            '## Retrieved evidence\nNo project evidence available\n'
            '## AI suggestions\nSuggestion: test\n'
            '## Unknown or unsupported information\nNone.\n',
            types=["social_post"], allowed_ids=[], context=context, user_request=""
        )
    assert "asked for social_post" in str(exc.value) or "omitted requested deliverable type" in str(exc.value)

    # Unsupported citations in content
    with pytest.raises(production.DeliverableValidationError) as exc:
        production.validate_generated_items(
            '## DELIVERABLE type="social_post" title="Post" platform="Instagram"\n'
            '## Final copy\nPost text\n'
            '## Persisted user facts\nNo persisted facts supplied.\n'
            '## Retrieved evidence\nFabricated claim [doc1:chunk99]\n'
            '## AI suggestions\nSuggestion: test\n'
            '## Unknown or unsupported information\nNone.\n',
            types=["social_post"], allowed_ids=[], context=context, user_request=""
        )
    assert "unsupported" in str(exc.value).lower() or "citation" in str(exc.value).lower()


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

    # 3. Simulate stale lock (started and last heartbeat 200s ago)
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    meta["generation_in_progress"]["started_at"] = stale_time
    meta["generation_in_progress"]["heartbeat_at"] = stale_time
    production._write_campaign_metadata(conn, "p_test", "c_lock", meta)

    # 4. New acquisition recovers stale lock automatically
    lease3 = flow._acquire_db_lock(conn, "p_test", "c_lock", "turn-3")
    campaign_after = deliverables._require_scope(conn, "p_test", "c_lock")
    meta_after = json.loads(campaign_after["workflow_json"])
    assert meta_after["generation_in_progress"]["turn_id"] == "turn-3"

    # 5. Release cleans up with lease token
    flow._release_db_lock(conn, "p_test", "c_lock", lease3)
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


def test_approved_export_blocks_malformed_or_missing_final_copy_legacy_fallback(client_env):
    """Concern 4: Malformed or missing final copy must NEVER bypass validation
    through a legacy fallback in approved_package_export or transition.
    """
    client, conn, _ = client_env
    _make_campaign(conn, "p_test", "c_export_guard")

    # 1. Valid generated deliverable -> approved -> MUST be included
    r = client.post("/api/projects/p_test/campaigns/c_export_guard/generate")
    assert r.status_code == 200
    generated = deliverables.list_deliverables(conn, "p_test", "c_export_guard")
    valid_item = next(x for x in generated if x["type"] == "social_post")
    valid_item = deliverables.transition(
        conn, "p_test", "c_export_guard", valid_item["id"], valid_item["current_version"], "IN_REVIEW"
    )
    valid_item = deliverables.transition(
        conn, "p_test", "c_export_guard", valid_item["id"], valid_item["current_version"], "APPROVED"
    )

    # 2. Deliverable with audit sections but MISSING '## Final copy' (legacy audit-only shape)
    audit_only_md = (
        "## Persisted user facts\n"
        "- Audience: local families\n\n"
        "## Retrieved evidence\n"
        "No project evidence available\n\n"
        "## AI suggestions\n"
        "- Suggestion, not a fact: Post a morning photo.\n\n"
        "## Unknown or unsupported information\n"
        "None.\n"
    )
    d_audit_only = deliverables.create_manual(conn, "p_test", "c_export_guard", {
        "type": "social_post",
        "title": "Audit Only Legacy Post",
        "platform": "Instagram",
        "content_md": audit_only_md,
    })
    d_audit_only = deliverables.transition(
        conn, "p_test", "c_export_guard", d_audit_only["id"], d_audit_only["current_version"], "IN_REVIEW"
    )
    # Transitioning to APPROVED must fail validation because final_copy is missing!
    with pytest.raises(deliverables.DeliverableValidationError, match="missing or malformed final copy"):
        deliverables.transition(
            conn, "p_test", "c_export_guard", d_audit_only["id"], d_audit_only["current_version"], "APPROVED"
        )

    # Force-seed APPROVED revisions in SQLite to simulate pre-existing/legacy DB rows
    # attempting to bypass validation at export time:
    bad_cases = [
        ("Audit Only Legacy Bypass", "social_post", "Instagram", audit_only_md, False),
        (
            "Empty Final Copy Bypass",
            "social_post",
            "Instagram",
            "## Final copy\n   \n\n" + audit_only_md,
            False,
        ),
        (
            "Unlabelled Preamble Before Audit Bypass",
            "social_post",
            "Instagram",
            "Unlabelled preamble text that is not under Final copy\n\n" + audit_only_md,
            False,
        ),
        (
            "Generated Stripped Audit Fallback Bypass",
            "social_post",
            "Instagram",
            "Plain text trying to use manual fallback on a generated row.",
            True,
        ),
        (
            "Placeholder Final Copy Bypass",
            "social_post",
            "Instagram",
            "## Final copy\nStop by for [insert offer here] today!\n\n" + audit_only_md,
            False,
        ),
        (
            "Email Missing Subject Bypass",
            "ad_copy",
            "Email",
            "## Final copy\nHi families, warm bread is ready this morning.\n\n" + audit_only_md,
            True,
        ),
    ]

    for idx, (title, dtype, plat, body_md, mark_generated) in enumerate(bad_cases):
        deliv_id = f"del_bad_{idx}"
        conn.execute(
            "INSERT INTO campaign_deliverables "
            "(id, project_id, campaign_id, type, title, platform, content_md, status, "
            "current_version, generation_idempotency_key, generation_ordinal, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'APPROVED', 1, ?, ?, '2026-01-01', '2026-01-01')",
            (
                deliv_id,
                "p_test",
                "c_export_guard",
                dtype,
                title,
                plat,
                body_md,
                "gen_key_1" if mark_generated else None,
                idx if mark_generated else None,
            ),
        )
        conn.execute(
            "INSERT INTO campaign_deliverable_revisions "
            "(revision_id, project_id, campaign_id, deliverable_id, version, operation, "
            "type, title, platform, content_md, status, provenance_json, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 'APPROVED', ?, '2026-01-01')",
            (
                f"rev_bad_{idx}",
                "p_test",
                "c_export_guard",
                deliv_id,
                "GENERATED" if mark_generated else "STATUS_TRANSITION",
                dtype,
                title,
                plat,
                body_md,
                json.dumps({"provider": "test"}) if mark_generated else "{}",
            ),
        )
    conn.commit()

    export_zip = deliverables.approved_package_export(conn, "p_test", "c_export_guard")
    archive = zipfile.ZipFile(io.BytesIO(export_zip))
    names = archive.namelist()
    summary = archive.read("00-package-summary.md").decode("utf-8")

    # Only the 1 valid deliverable file (+ 00-package-summary.md) may be in the ZIP!
    deliverable_files = [n for n in names if n != "00-package-summary.md"]
    assert len(deliverable_files) == 1, f"Malformed deliverables leaked into export: {deliverable_files}"
    exported_body = archive.read(deliverable_files[0]).decode("utf-8")
    assert f"# {valid_item['title']}" in exported_body
    assert "Fresh mornings start with wholesome baking." in exported_body

    # Every malformed deliverable must be explicitly listed as NOT INCLUDED in summary
    for title, *_ in bad_cases:
        assert f"NOT INCLUDED (invalid or missing final copy" in summary
        assert title in summary
        assert title not in exported_body


def test_validate_generated_items_enforces_final_copy_in_phase4_flow():
    """Verify validate_generated_items rejects missing or malformed final copy when require_final_copy=True."""
    context = {"evidence_ids": [], "project_id": "p_test", "profile": {}, "brief_md": "", "evidence": []}

    # Missing ## Final copy heading when require_final_copy=True
    with pytest.raises(production.DeliverableValidationError, match="invalid or missing final copy"):
        production.validate_generated_items(
            '## DELIVERABLE type="social_post" title="Post" platform="Instagram"\n'
            '## Persisted user facts\nNo persisted facts supplied.\n'
            '## Retrieved evidence\nNo project evidence available\n'
            '## AI suggestions\nSuggestion, not a fact: test\n'
            '## Unknown or unsupported information\nNone.\n',
            types=["social_post"],
            allowed_ids=[],
            context=context,
            require_final_copy=True,
        )

    # Empty ## Final copy section even when require_final_copy=False (since heading is present)
    with pytest.raises(production.DeliverableValidationError, match="invalid or missing final copy"):
        production.validate_generated_items(
            '## DELIVERABLE type="social_post" title="Post" platform="Instagram"\n'
            '## Final copy\n   \n'
            '## Persisted user facts\nNo persisted facts supplied.\n'
            '## Retrieved evidence\nNo project evidence available\n'
            '## AI suggestions\nSuggestion, not a fact: test\n'
            '## Unknown or unsupported information\nNone.\n',
            types=["social_post"],
            allowed_ids=[],
            context=context,
        )

