"""DEV-044 end-to-end UI journey in a real browser against the real product.

A synthetic owner types one sentence into the real chat composer and ends with
an exported approved package, using only the UI. The single test double is the
offline ``DeterministicCampaignProvider`` plugged into the real ModelRouter, so
the real production orchestration runs. This proves plumbing and honesty rules,
NOT the quality of any real AI model.

Skips (never passes) when frontend/dist or a Chromium for Playwright is missing.
"""
from __future__ import annotations

import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from app import deps
from app.database.sqlite import connect
from app.main import create_app
from app.services import campaign_production as production
from app.services import research_evidence
from app.services import state as store
import app.services.config_service as config_module
from app.tests.campaign_fake_provider import DeterministicCampaignProvider

pytestmark = pytest.mark.slow

REQUEST = "Create a 30-day Instagram and email campaign targeting local families."


def _dist_ready() -> bool:
    return (Path(__file__).resolve().parents[2] / "frontend" / "dist"
            / "index.html").is_file()


def _boot(tmp_path, monkeypatch):
    import uvicorn

    from app.routes import graph_runtime
    from app.services.llm.model_router import ModelRouter

    db = tmp_path / "journey.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", workspace)
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    monkeypatch.setenv("CHROMA_ENABLED", "0")
    monkeypatch.setattr(graph_runtime, "_GRAPH", None, raising=False)
    provider = DeterministicCampaignProvider()
    router = ModelRouter(local_provider=provider,
                         default_local_model="deterministic-test")
    monkeypatch.setattr(production, "_router", lambda: router)
    monkeypatch.setattr(graph_runtime, "_get_model_router", lambda: router)
    deps.init_db(db)
    config_module.clear_all_test_overrides()
    server = uvicorn.Server(uvicorn.Config(
        create_app(), host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    port = server.servers[0].sockets[0].getsockname()[1]
    return f"http://127.0.0.1:{port}", db, server, thread, provider



def _skip_or_fail(reason):
    """Browser journeys must never be silently skipped where they are required.

    CI and release gates set OMOS_REQUIRE_BROWSER=1, which turns a missing
    prerequisite into a hard failure instead of a green "skipped".
    """
    if os.environ.get("OMOS_REQUIRE_BROWSER") == "1":
        pytest.fail(f"required browser journey cannot run: {reason}")
    pytest.skip(reason)


def _send(page, text):
    composer = page.locator('textarea[aria-label="Chat message"]')
    composer.wait_for(state="visible", timeout=20_000)
    composer.fill(text)
    page.locator('button:has-text("Send")').click()


def test_owner_journey_request_to_approved_export(tmp_path, monkeypatch):
    if not _dist_ready():
        _skip_or_fail("frontend/dist missing - run `npm run build` in frontend/")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        _skip_or_fail(f"playwright unavailable: {exc}")

    base, db, server, thread, provider = _boot(tmp_path, monkeypatch)
    pw = sync_playwright().start()
    try:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:
            _skip_or_fail(f"chromium unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 1000},
                                accept_downloads=True)

        # 1. one natural-language sentence in the real chat composer
        with deps.get_db(db) as dbc:
            convo = store.create_conversation(
                dbc, project_id="starter", title="Corner Bakery planning")
        page.goto(f"{base}/app/chat/{convo['id']}", wait_until="domcontentloaded")
        page.wait_for_selector('nav[aria-label="Primary"]', state="attached",
                               timeout=20_000)
        _send(page, REQUEST)
        card = page.locator('[data-testid="campaign-chat-card"]')
        card.wait_for(state="visible", timeout=60_000)

        # 2. what OMOS understood: exactly what was said, nothing invented
        text = card.inner_text()
        assert "local families" in text
        assert "30 days" in text
        assert "Instagram, Email" in text
        assert "Offer" not in text and "Budget" not in text
        assert all(c["kind"] != "generation" for c in provider.calls), \
            "content must not be written before the owner says go"
        conn = connect(db)
        campaigns = [dict(r) for r in conn.execute("SELECT * FROM campaigns")]
        conn.close()
        assert len(campaigns) == 1
        cid, pid = campaigns[0]["id"], campaigns[0]["project_id"]
        intake = json.loads(campaigns[0]["workflow_json"])["campaign_intake"]
        assert intake["target_audience"] == "local families"
        assert intake["duration"] == "30 days"
        assert intake["channels"] == ["instagram", "email"]

        # 3. open the campaign: plan is reviewable, area question is asked
        page.get_by_role("link", name="Review the plan").click()
        page.locator('[data-testid="wp-proposal"]').wait_for(timeout=20_000)
        proposal = page.locator('[data-testid="wp-proposal"]').inner_text()
        assert "local families" in proposal and "Instagram" in proposal
        assert "Ideas from OMOS (suggestions, not facts)" in proposal
        missing = page.locator('[data-testid="wp-missing"]').inner_text()
        assert "Which town or area" in missing

        # 4. truthful evidence labels: project vs campaign research
        conn = connect(db)
        research_evidence.record_leads(conn, pid, "", [{
            "url": "https://baking-trends.net/lead", "title": "Project lead",
            "snippet": "families like bread"}], query="q")
        research_evidence.record_leads(conn, pid, cid, [{
            "url": "https://baking-trends.net/read", "title": "Read page",
            "snippet": "x"}], query="q2")
        research_evidence.mark_inspected(
            conn, pid, "https://baking-trends.net/read", title="Read page",
            http_status=200, campaign_id=cid)
        conn.close()
        page.reload(wait_until="domcontentloaded")
        page.locator('[data-testid="wp-evidence"]').wait_for(timeout=20_000)
        leads = page.locator('[data-testid="wp-sources-leads"]').inner_text()
        read = page.locator('[data-testid="wp-sources-checked"]').inner_text()
        assert "not treated as a fact" in leads and "whole business" in leads
        assert "Page opened and read" in read and "for this campaign" in read

        # 5. owner says go; real orchestration writes drafts only
        page.get_by_role("button", name="Write this content").click()
        page.locator('[data-testid="wp-notice"]').wait_for(timeout=60_000)
        page.locator('[data-testid="wp-deliverables"] li').first.wait_for(timeout=20_000)
        listed = page.locator('[data-testid="wp-deliverables"]').inner_text()
        assert "written by AI" in listed and "0 of 3 approved" in listed
        assert [c["kind"] for c in provider.calls
                if c["kind"] != "other"] == ["generation"]
        assert page.get_by_role("button", name="Download approved package").is_disabled()

        # 6. revise through the UI
        page.get_by_role("button", name="Review, change and approve").click()
        page.get_by_role("button", name="Revise with AI").wait_for(timeout=20_000)
        page.get_by_text("Social post", exact=False).first.click()
        page.locator("#ai-revise-instruction").fill("Make it warmer")
        page.get_by_role("button", name="Revise with AI").click()
        page.get_by_text("Changed as you asked").wait_for(timeout=60_000)
        assert "Revised as requested (Make it warmer)" in page.locator("pre").first.inner_text()

        # 7. approve the revised social post only (draft -> review -> approved)
        page.get_by_role("button", name="Send to review").click()
        page.get_by_text("Status is now In review").wait_for(timeout=20_000)
        page.get_by_role("button", name="Approve internally").click()
        page.get_by_text("Status is now Approved").wait_for(timeout=20_000)

        # 8. approved-only export from the UI contains just that item
        with page.expect_download(timeout=30_000) as dl:
            page.get_by_role("button", name="Download approved only (.zip)").click()
        archive = zipfile.ZipFile(io.BytesIO(Path(dl.value.path()).read_bytes()))
        names = archive.namelist()
        assert any(n.startswith("social_post/") for n in names)
        assert not any(n.startswith(("strategy_brief/", "ad_copy/")) for n in names)
        summary = archive.read("00-package-summary.md").decode()
        assert summary.count("NOT INCLUDED") == 2
        exported = archive.read(
            next(n for n in names if n.startswith("social_post/"))).decode()
        assert "Revised as requested (Make it warmer)" in exported

        # 9. persistence after a real reload
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("tab", name="Overview").click()
        dl_text = page.locator('[data-testid="wp-download"]').inner_text()
        assert "included" in dl_text and "Not included until approved" in dl_text
        assert "1 of 3 approved" in page.locator(
            '[data-testid="wp-deliverables"]').inner_text()

        # 10. campaign isolation: another project cannot see or change it
        for method, tail in (("GET", "/work-package"), ("POST", "/generate")):
            req = urllib.request.Request(
                f"{base}/api/projects/not-this-project/campaigns/{cid}{tail}",
                method=method)
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(req, timeout=20)
            assert err.value.code == 404
        browser.close()
    finally:
        pw.stop()
        server.should_exit = True
        thread.join(timeout=10)


def test_go_ahead_in_chat_writes_the_proposed_plan_once(tmp_path, monkeypatch):
    if not _dist_ready():
        _skip_or_fail("frontend/dist missing - run `npm run build` in frontend/")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        _skip_or_fail(f"playwright unavailable: {exc}")

    base, db, server, thread, provider = _boot(tmp_path, monkeypatch)
    pw = sync_playwright().start()
    try:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:
            _skip_or_fail(f"chromium unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        with deps.get_db(db) as dbc:
            convo = store.create_conversation(
                dbc, project_id="starter", title="Go ahead")
        page.goto(f"{base}/app/chat/{convo['id']}", wait_until="domcontentloaded")
        _send(page, REQUEST)
        page.locator('[data-testid="campaign-chat-card"]').wait_for(timeout=60_000)
        _send(page, "Yes, go ahead and write the content")
        page.get_by_text("0 of 3 approved").wait_for(timeout=60_000)
        conn = connect(db)
        rows = conn.execute(
            "SELECT type, platform, status FROM campaign_deliverables").fetchall()
        conn.close()
        assert sorted(r["type"] for r in rows) == [
            "ad_copy", "social_post", "strategy_brief"]
        assert all(r["status"] == "DRAFT" for r in rows)
        assert sum(1 for c in provider.calls if c["kind"] == "generation") == 1
        browser.close()
    finally:
        pw.stop()
        server.should_exit = True
        thread.join(timeout=10)
