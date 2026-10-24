"""DEV-044: revision validates against the owner's own campaign statements.

A revised deliverable keeps the "Persisted user facts" that came from the
owner's original request. Those must be accepted as sources, but nothing the
owner never said (and nothing from another project) may be.
"""
from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import deps
from app.routes.campaign_deliverables import router
from app.services import campaign_production as production
from app.tests.campaign_fake_provider import DeterministicCampaignProvider
from app.tests.test_dev044_chat_to_campaign import (
    REQUEST, _make_campaign, _seed, _url)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from app.services.llm.model_router import ModelRouter

    db = tmp_path / "dev044_rev.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    conn = _seed(db)
    provider = DeterministicCampaignProvider()
    router_obj = ModelRouter(local_provider=provider,
                             default_local_model="deterministic-test")
    monkeypatch.setattr(production, "_router", lambda: router_obj)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), conn, provider


def _generated_post(client, conn):
    _make_campaign(conn, "bakery", "c1")
    assert client.post(_url(tail="/generate")).status_code == 200
    return next(x for x in client.get(_url(tail="/deliverables")).json()["data"]
                if x["type"] == "social_post")


def _scripted_revision(provider, monkeypatch, extra_fact):
    """Make the model keep the current deliverable but add one persisted fact."""
    def revise(user):
        current = user["current_deliverable"]
        body = str(current["content_md"]).replace(
            "## Persisted user facts\n",
            f"## Persisted user facts\n- {extra_fact}\n", 1)
        attrs = (f'type="{current["type"]}" title="{current["title"]}" '
                 f'platform="{current.get("platform") or ""}"')
        return f"## DELIVERABLE {attrs}\n\n{body.strip()}\n"
    monkeypatch.setattr(provider, "_revise", revise)


def _revise(client, post, text="Make it warmer"):
    return client.post(_url(tail=f"/deliverables/{post['id']}/ai-revise"),
                       json={"instruction": text})


def test_revision_accepts_facts_that_came_from_the_original_request(env):
    client, conn, _ = env
    post = _generated_post(client, conn)
    assert re.search(r"(?i)local families", post["content_md"])
    r = _revise(client, post)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["current_version"] == post["current_version"] + 1
    assert "local families" in r.json()["data"]["content_md"].lower()
    history = client.get(_url(tail=f"/deliverables/{post['id']}/history")).json()["data"]
    assert [h["version"] for h in history][-2:] == [1, 2]
    assert history[0]["content_md"] == post["content_md"]


def test_revision_still_rejects_an_unsupported_high_risk_claim(env, monkeypatch):
    client, conn, provider = env
    post = _generated_post(client, conn)
    _scripted_revision(provider, monkeypatch, "Offer: 50% discount for everyone")
    r = _revise(client, post)
    assert r.status_code >= 400
    assert "output_validation" in r.text
    assert "50%" in r.text
    after = next(x for x in client.get(_url(tail="/deliverables")).json()["data"]
                 if x["id"] == post["id"])
    assert after["current_version"] == post["current_version"]
    assert after["content_md"] == post["content_md"]


def test_a_fact_only_in_another_projects_intake_is_not_a_source(env, monkeypatch):
    client, conn, provider = env
    _make_campaign(conn, "other", "c9",
                   text="Create a 30-day Instagram campaign with 50% discount "
                        "for Riverside students.")
    post = _generated_post(client, conn)
    _scripted_revision(provider, monkeypatch, "Offer: 50% discount for Riverside students")
    r = _revise(client, post)
    assert r.status_code >= 400
    assert "output_validation" in r.text
    assert "50%" in r.text
    after = next(x for x in client.get(_url(tail="/deliverables")).json()["data"]
                 if x["id"] == post["id"])
    assert after["current_version"] == post["current_version"]
    assert REQUEST  # bakery's own request never mentioned the discount
