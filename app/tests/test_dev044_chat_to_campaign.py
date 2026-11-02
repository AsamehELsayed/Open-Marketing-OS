"""DEV-044: natural-language request -> reviewed, approved, exported package.

Every AI call here goes through the real ``ModelRouter`` and the real campaign
production path, with the offline ``DeterministicCampaignProvider`` as the only
fake. It proves the plumbing and the honesty rules, not real AI quality.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import deps
from app.database.sqlite import connect
from app.routes.campaign_deliverables import router
from app.services import campaign_production as production
from app.services import campaign_request as request_mod
from app.tests.campaign_fake_provider import DeterministicCampaignProvider

REQUEST = "Create a 30-day Instagram and email campaign targeting local families."


def _seed(db):
    conn = connect(db)
    for pid, name in (("bakery", "Corner Bakery"), ("other", "Other Shop")):
        conn.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES(?,?,?,?)",
            (pid, name, "2026-01-01", "2026-01-01"))
    conn.commit()
    return conn


def _make_campaign(conn, project_id, campaign_id, text=REQUEST, objective=""):
    intake = production.campaign_intake(text, objective=objective, turn_id="t1")
    meta = {"campaign_intake": intake}
    conn.execute(
        "INSERT INTO campaigns(id,project_id,title,workflow_json,updated_at) "
        "VALUES(?,?,?,?,?)",
        (campaign_id, project_id, request_mod.title_from_intake(intake) or "Campaign",
         json.dumps(meta), "2026-01-01"))
    conn.commit()
    return intake


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from app.services.llm.model_router import ModelRouter

    db = tmp_path / "dev044.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    conn = _seed(db)
    provider = DeterministicCampaignProvider()
    router_obj = ModelRouter(local_provider=provider,
                             default_local_model="deterministic-test")
    monkeypatch.setattr(production, "_router", lambda: router_obj)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), conn, provider


def _url(cid="c1", pid="bakery", tail=""):
    return f"/api/projects/{pid}/campaigns/{cid}{tail}"


# ---------------------------------------------------------------- extraction

def test_example_request_is_understood_without_inventing_anything():
    intake = production.campaign_intake(REQUEST, objective="")
    assert intake["duration"] == "30 days"
    assert intake["channels"] == ["instagram", "email"]
    assert intake["target_audience"] == "local families"
    # Nothing the owner did not say.
    for key in ("offer", "geography", "business", "product", "constraints"):
        assert key not in intake
    assert intake["objective"] == ""


def test_placeholder_goal_is_never_recorded_as_the_owners_goal():
    intake = production.campaign_intake(REQUEST, objective="website marketing")
    assert intake["objective"] == ""


def test_richer_request_captures_every_stated_field():
    text = ("Create a two-week Facebook and email campaign for my bakery in "
            "Austin to get more weekend orders, aimed at busy parents with "
            "10% off first order. No emojis.")
    intake = production.campaign_intake(text, objective="")
    assert intake["duration"] == "2 weeks"
    assert intake["channels"] == ["facebook", "email"]
    assert intake["target_audience"] == "busy parents"
    assert intake["geography"] == "Austin"
    assert intake["offer"] == "10% off first order"
    assert intake["business"] == "bakery"
    assert intake["objective"] == "get more weekend orders"
    assert intake["constraints"] == ["No emojis"]


@pytest.mark.parametrize("text, field", [
    ("Create a campaign.", "target_audience"),
    ("Create a campaign to reach more customers", "target_audience"),
    ("Create a campaign for our launch", "target_audience"),
    ("Create a campaign in March", "geography"),
    ("Create a campaign, target 100 sign-ups", "target_audience"),
    ("Build audience awareness with a campaign", "target_audience"),
    ("Create a campaign with a big budget mindset", "constraints"),
])
def test_unsupported_details_are_not_inferred(text, field):
    assert field not in production.campaign_intake(text, objective="") or \
        not production.campaign_intake(text, objective="")[field]


def test_missing_essentials_become_short_questions():
    intake = production.campaign_intake("Create a campaign for my shop.", objective="")
    view = request_mod.understanding(intake, {})
    asked = {m["field"] for m in view["missing"] if m["essential"]}
    assert asked == {"target_audience", "channels"}
    assert view["ready_to_generate"] is False
    assert all(len(m["question"]) < 80 for m in view["missing"])


def test_business_profile_fills_a_gap_and_is_labelled_as_such():
    intake = production.campaign_intake(
        "Create a campaign on Instagram to get more orders.", objective="")
    view = request_mod.understanding(intake, {"audience": "Office workers"})
    audience = next(f for f in view["facts"] if f["field"] == "target_audience")
    assert audience["value"] == "Office workers"
    assert audience["source"] == "business_profile"
    assert audience["source_label"] == "From your business profile"
    assert view["ready_to_generate"] is True


def test_local_audience_asks_which_area():
    intake = production.campaign_intake(REQUEST, objective="")
    fields = {m["field"] for m in request_mod.understanding(intake, {})["missing"]}
    assert "geography" in fields


def test_plan_follows_stated_channels_only():
    plan = request_mod.planned_deliverables(production.campaign_intake(REQUEST))
    assert [(p["type"], p["platform"]) for p in plan] == [
        ("strategy_brief", None), ("social_post", "Instagram"),
        ("ad_copy", "Email")]
    assert request_mod.planned_deliverables({}) == []


@pytest.mark.parametrize("text", [
    "Yes, go ahead and write the content", "go ahead", "Looks good, generate it",
    "Generate everything"])
def test_go_ahead_is_recognised(text):
    assert production.is_generation_go_ahead(text)


@pytest.mark.parametrize("text", [
    "Create a campaign for X", "Write the posts about our update policy",
    "go ahead and publish it to Instagram", "What is the plan?"])
def test_ordinary_requests_are_not_go_aheads(text):
    assert not production.is_generation_go_ahead(text)


# ----------------------------------------------- generate -> revise -> approve

def test_generate_requires_essentials_and_never_invents(env):
    client, conn, provider = env
    _make_campaign(conn, "bakery", "c1", "Create a campaign for my shop.")
    r = client.post(_url(tail="/generate"))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "needs_input"
    assert provider.calls == []


def test_generate_writes_the_planned_deliverables_once(env):
    client, conn, provider = env
    _make_campaign(conn, "bakery", "c1")
    r = client.post(_url(tail="/generate"))
    assert r.status_code == 200, r.text
    rows = client.get(_url(tail="/deliverables")).json()["data"]
    assert sorted(x["type"] for x in rows) == ["ad_copy", "social_post", "strategy_brief"]
    assert {x["platform"] for x in rows} >= {"Instagram", "Email"}
    assert all(x["status"] == "DRAFT" for x in rows)
    assert len(provider.calls) == 1
    again = client.post(_url(tail="/generate"))
    assert again.status_code == 409
    assert len(provider.calls) == 1
    assert len(client.get(_url(tail="/deliverables")).json()["data"]) == 3


def test_generated_text_states_only_owner_facts_and_labels_suggestions(env):
    client, conn, _ = env
    _make_campaign(conn, "bakery", "c1")
    client.post(_url(tail="/generate"))
    post = next(x for x in client.get(_url(tail="/deliverables")).json()["data"]
                if x["type"] == "social_post")
    md = post["content_md"]
    assert "Audience: local families" in md
    assert "## AI suggestions" in md and "## Unknown or unsupported information" in md


def test_generation_failure_saves_nothing_and_says_so(env, monkeypatch):
    client, conn, provider = env
    _make_campaign(conn, "bakery", "c1")
    monkeypatch.setattr(provider, "_generate", lambda user: "not a deliverable")
    r = client.post(_url(tail="/generate"))
    assert r.status_code == 422
    assert client.get(_url(tail="/deliverables")).json()["data"] == []


def test_ai_revise_creates_a_new_version_and_keeps_the_old(env):
    client, conn, provider = env
    _make_campaign(conn, "bakery", "c1")
    client.post(_url(tail="/generate"))
    post = next(x for x in client.get(_url(tail="/deliverables")).json()["data"]
                if x["type"] == "social_post")
    r = client.post(_url(tail=f"/deliverables/{post['id']}/ai-revise"),
                    json={"instruction": "Make it warmer"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["current_version"] == post["current_version"] + 1
    assert "Make it warmer" in r.json()["data"]["content_md"]
    history = client.get(_url(tail=f"/deliverables/{post['id']}/history")).json()["data"]
    assert [h["version"] for h in history][-2:] == [1, 2]
    assert history[0]["content_md"] == post["content_md"]


def test_ai_revise_rejects_empty_and_foreign_requests(env):
    client, conn, _ = env
    _make_campaign(conn, "bakery", "c1")
    client.post(_url(tail="/generate"))
    post = client.get(_url(tail="/deliverables")).json()["data"][0]
    assert client.post(_url(tail=f"/deliverables/{post['id']}/ai-revise"),
                       json={"instruction": "   "}).status_code == 422
    foreign = client.post(
        f"/api/projects/other/campaigns/c1/deliverables/{post['id']}/ai-revise",
        json={"instruction": "Make it shorter"})
    assert foreign.status_code == 404


def test_revising_approved_copy_reopens_it_and_export_keeps_last_approved(env):
    client, conn, _ = env
    _make_campaign(conn, "bakery", "c1")
    client.post(_url(tail="/generate"))
    rows = client.get(_url(tail="/deliverables")).json()["data"]
    post = next(x for x in rows if x["type"] == "social_post")
    brief = next(x for x in rows if x["type"] == "strategy_brief")
    for item in (post,):
        v = item["current_version"]
        for status in ("IN_REVIEW", "APPROVED"):
            r = client.post(_url(tail=f"/deliverables/{item['id']}/status"),
                            json={"expected_version": v, "status": status})
            assert r.status_code == 200, r.text
            v = r.json()["data"]["current_version"]
    revised = client.post(_url(tail=f"/deliverables/{post['id']}/ai-revise"),
                          json={"instruction": "Add a friendlier opening"})
    assert revised.json()["data"]["status"] == "DRAFT"
    package = client.get(_url(tail="/deliverables/exports/package.zip?approved_only=true"))
    names = zipfile.ZipFile(io.BytesIO(package.content)).namelist()
    assert any(n.startswith("social_post/") for n in names)
    assert not any(n.startswith("strategy_brief/") for n in names)
    summary = zipfile.ZipFile(io.BytesIO(package.content)).read(
        "00-package-summary.md").decode()
    assert f"NOT INCLUDED (not approved yet): {brief['title']}" in summary


# ------------------------------------------------------------ answers & scope

def test_owner_answers_complete_the_campaign_and_unlock_generation(env):
    client, conn, _ = env
    _make_campaign(conn, "bakery", "c1", "Create a campaign for my shop.")
    r = client.put(_url(tail="/intake"), json={"answers": {
        "objective": "Get more weekend orders",
        "target_audience": "Busy parents", "channels": ["instagram"]}})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["understanding"]["ready_to_generate"] is True
    assert client.post(_url(tail="/generate")).status_code == 200


def test_answers_reject_unknown_fields_and_channels(env):
    client, conn, _ = env
    _make_campaign(conn, "bakery", "c1")
    assert client.put(_url(tail="/intake"), json={"answers": {"title": "x"}}).status_code == 422
    assert client.put(_url(tail="/intake"),
                      json={"answers": {"channels": ["carrier pigeon"]}}).status_code == 422


def test_projects_cannot_touch_each_others_campaigns(env):
    client, conn, provider = env
    _make_campaign(conn, "bakery", "c1")
    for method, tail, body in (
            ("post", "/generate", None),
            ("put", "/intake", {"answers": {"offer": "free coffee"}}),
            ("get", "/work-package", None)):
        r = getattr(client, method)(_url(pid="other", tail=tail),
                                    **({"json": body} if body else {}))
        assert r.status_code == 404, (tail, r.status_code)
    assert provider.calls == []


# ------------------------------------------------------------- work package

def test_work_package_reports_understanding_proposal_and_stage(env):
    client, conn, _ = env
    _make_campaign(conn, "bakery", "c1")
    pkg = client.get(_url(tail="/work-package")).json()["data"]
    assert pkg["stage"]["id"] == "proposal"
    facts = {f["field"]: f for f in pkg["understanding"]["facts"]}
    assert facts["target_audience"]["value"] == "local families"
    assert facts["channels"]["value"] == "Instagram, Email"
    assert pkg["proposal"]["audience"] == "local families"
    assert "Ideas from OMOS, not facts" in pkg["proposal"]["recommendations_note"]
    client.post(_url(tail="/generate"))
    pkg = client.get(_url(tail="/work-package")).json()["data"]
    assert pkg["stage"]["id"] == "review"
    assert pkg["download"]["approved"] == []
    assert len(pkg["download"]["not_included"]) == 3


def test_search_leads_are_never_presented_as_proven(env):
    from app.services import research_evidence

    client, conn, _ = env
    _make_campaign(conn, "bakery", "c1")
    research_evidence.record_leads(
        conn, "bakery", "", [{"url": "https://baking-trends.net/a", "title": "A",
                              "snippet": "families like bread"}], query="q")
    research_evidence.record_leads(
        conn, "bakery", "c1", [{"url": "https://baking-trends.net/b", "title": "B",
                                "snippet": "x"}], query="q2")
    research_evidence.mark_inspected(conn, "bakery", "https://baking-trends.net/b",
                                     title="B", http_status=200, campaign_id="c1")
    pkg = client.get(_url(tail="/work-package")).json()["data"]
    sources = {s["url"]: s for s in pkg["evidence"]["web_sources"]}
    lead, page = sources["https://baking-trends.net/a"], sources["https://baking-trends.net/b"]
    assert lead["verification"] == "search_result" or lead["verification"] != "inspected"
    assert "not treated as a fact" in lead["use"]
    assert lead["scope"] == "project" and "whole business" in lead["scope_label"]
    assert page["verification"] == "inspected" and page["scope"] == "campaign"
    assert pkg["proposal"]["research"]["leads"] == 1
    assert pkg["proposal"]["research"]["inspected"] == 1


def test_simultaneous_generate_makes_one_provider_call(env):
    import threading

    from app.services import campaign_flow

    client, conn, provider = env
    _make_campaign(conn, "bakery", "c1")
    outcomes = []

    def run():
        c = connect(deps.DB_PATH)
        try:
            campaign_flow.generate_from_plan(c, project_id="bakery",
                                             campaign_id="c1")
            outcomes.append("ok")
        except campaign_flow.FlowError as exc:
            outcomes.append(exc.code)
        finally:
            c.close()

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ok") == 1
    assert all(result in {"ok", "already_generated", "generation_in_progress"} for result in outcomes)
    assert sum(1 for c in provider.calls if c["kind"] == "generation") == 1


# DEV-045: a style restriction is not a channel exclusion.
import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize("text,expected", [
    ("Plan a 30-day campaign, no emojis on Instagram, email to local families",
     ["instagram", "email"]),
    ("No hashtags on Instagram or Facebook, just email",
     ["instagram", "facebook", "email"]),
    ("avoid discounts in emails and no price talk on Instagram",
     ["instagram", "email"]),
    ("I want an email campaign, no Instagram", ["email"]),
    ("skip Instagram, do email", ["email"]),
])
def test_style_restriction_does_not_remove_channel(text, expected):
    from app.services import campaign_request
    assert sorted(campaign_request.extract_channels(text)) == sorted(expected)
