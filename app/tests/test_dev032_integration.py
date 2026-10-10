"""DEV-032 integration checks across W2 generation, W1 storage, and API."""

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import deps
from app.database.sqlite import connect
from app.main import create_app
from app.routes import api_spa, campaign_deliverables as deliverables_api
from app.services import campaign_production


def _seed(db_path):
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO projects(id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("northstar", "Northstar Coffee", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO projects(id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("harbor", "Harbor Studio", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO campaigns(id,project_id,title,updated_at) VALUES(?,?,?,?)",
        ("northstar-campaign", "northstar", "Weekday launch", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO campaigns(id,project_id,title,updated_at) VALUES(?,?,?,?)",
        ("harbor-campaign", "harbor", "Spring campaign", "2026-01-01"),
    )
    conn.commit()
    return conn


def _model_output(title):
    return (
        f'## DELIVERABLE type="social_post" title="{title}" '
        'platform="Instagram"\n\n'
        "## Persisted user facts\nNo persisted facts supplied.\n\n"
        "## Retrieved evidence\nNo project evidence available.\n\n"
        "## AI suggestions\nInvite readers to try a weekday coffee.\n\n"
        "## Unknown or unsupported information\nNo promotion or price was supplied."
    )


def test_generation_persists_through_w1_and_is_visible_only_in_scoped_api(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "dev032-integration.db"
    conn = _seed(db_path)
    monkeypatch.setattr(deps, "DB_PATH", db_path)
    generated_titles = iter(("Northstar weekday post", "Changed retry title"))
    monkeypatch.setattr(
        campaign_production,
        "complete",
        lambda *_args, **_kwargs: (
            _model_output(next(generated_titles)),
            {
                "provider": "deterministic-test",
                "model": "synthetic",
                "requested_model": "synthetic",
                "route_mode": "TEST",
                "route_reason": "integration fake",
                "call_id": "dev032-integration",
                "total_tokens": 0,
                "estimated_cost_usd": 0,
            },
        ),
    )

    first = campaign_production.generate_batch(
        conn,
        project_id="northstar",
        campaign_id="northstar-campaign",
        turn_id="turn-northstar-1",
        user_request="Create a social post for the weekday campaign.",
        types=["social_post"],
        campaign_title="Weekday launch",
        provider="TEST",
        model_id="synthetic",
    )
    assert first["ok"] is True
    assert first["status"] == "saved"
    assert first["idempotent_replay"] is False
    assert first["types"] == ["social_post"]
    assert len(first["deliverables"]) == 1
    row = first["deliverables"][0]
    assert row["project_id"] == "northstar"
    assert row["campaign_id"] == "northstar-campaign"
    assert row["current_version"] == 1
    assert row["title"] == "Northstar weekday post"
    conn.close()

    scope = deliverables_api.list_deliverables(
        "northstar", "northstar-campaign")
    persisted = scope["data"]
    assert [item["id"] for item in persisted] == [row["id"]]
    assert persisted[0]["content_md"] == row["content_md"]

    with pytest.raises(HTTPException) as foreign_scope:
        deliverables_api.list_deliverables(
            "harbor", "northstar-campaign")
    assert foreign_scope.value.status_code == 404
    harbor_rows = deliverables_api.list_deliverables(
        "harbor", "harbor-campaign")
    assert harbor_rows["data"] == []

    retry_conn = connect(db_path)
    retry = campaign_production.generate_batch(
        retry_conn,
        project_id="northstar",
        campaign_id="northstar-campaign",
        turn_id="turn-northstar-1",
        user_request="Create a social post for the weekday campaign.",
        types=["social_post"],
        campaign_title="Weekday launch",
        provider="TEST",
        model_id="synthetic",
    )
    retry_conn.close()
    assert retry["ok"] is True
    assert retry["idempotent_replay"] is True
    assert retry["deliverables"][0]["id"] == row["id"]
    assert retry["deliverables"][0]["title"] == "Northstar weekday post"
    assert retry["deliverables"][0]["content_md"] == row["content_md"]

    after_retry = deliverables_api.list_deliverables(
        "northstar", "northstar-campaign")["data"]
    assert len(after_retry) == 1
    assert after_retry[0]["title"] == "Northstar weekday post"


def test_campaign_detail_projects_w2_intake_fields_without_exposing_workflow(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "campaign-overview.db"
    conn = _seed(db_path)
    conn.execute(
        "UPDATE campaigns SET workflow_json=? WHERE id=? AND project_id=?",
        (json.dumps({
            "campaign_intake": {
                "objective": "Grow weekday awareness",
                "target_audience": "Local coffee drinkers",
                "channels": ["instagram", "newsletter"],
                "duration": "4 weeks",
                "request_facts": {
                    "requested_types": ["social_post", "unknown-secret-field"],
                    "user_request": "private original prompt with a secret",
                    "turn_id": "private-turn-id",
                },
            },
            "internal_only": "must not be returned",
        }), "northstar-campaign", "northstar"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(deps, "DB_PATH", db_path)

    # Intake fields are returned only after the caller's project scope is
    # checked against the campaign row.
    unscoped = api_spa.spa_campaign_detail(
        "northstar-campaign", project_id=None)["data"]["campaign"]
    assert unscoped["project_id"] == "northstar"
    for private_field in (
        "objective", "target_audience", "channels", "duration", "request",
    ):
        assert private_field not in unscoped

    detail = api_spa.spa_campaign_detail(
        "northstar-campaign", project_id="northstar")
    campaign = detail["data"]["campaign"]
    assert campaign["project_id"] == "northstar"
    assert campaign["objective"] == "Grow weekday awareness"
    assert campaign["target_audience"] == "Local coffee drinkers"
    assert campaign["channels"] == ["instagram", "newsletter"]
    assert campaign["duration"] == "4 weeks"
    assert campaign["request"] == "Requested deliverables: social_post"
    assert "workflow_json" not in campaign
    assert "request_facts" not in campaign
    assert "user_request" not in campaign
    assert "turn_id" not in campaign
    assert "internal_only" not in campaign

    # A project ID is not enough on its own: the detail route verifies the
    # campaign belongs to that client before returning any intake projection.
    with pytest.raises(HTTPException) as foreign_scope:
        api_spa.spa_campaign_detail("northstar-campaign", project_id="harbor")
    assert foreign_scope.value.status_code == 403

    # Exercise the actual HTTP surface as well: omission preserves the legacy
    # detail shape without intake, a valid scope receives it, and a foreign
    # scope is rejected before projection.
    with TestClient(create_app()) as client:
        unscoped_http = client.get(
            "/api/campaigns/northstar-campaign").json()["data"]["campaign"]
        for private_field in (
            "objective", "target_audience", "channels", "duration", "request",
        ):
            assert private_field not in unscoped_http
        scoped_http = client.get(
            "/api/campaigns/northstar-campaign?project_id=northstar"
        ).json()["data"]["campaign"]
        assert scoped_http["objective"] == "Grow weekday awareness"
        assert scoped_http["channels"] == ["instagram", "newsletter"]
        assert client.get(
            "/api/campaigns/northstar-campaign?project_id=harbor"
        ).status_code == 403
