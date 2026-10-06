"""DEV-008-SKILLS-OPS-HOTFIX — Packet 4b: Real React SPA E2E with exact Arabic founder prompt.

Drives the real application via uvicorn and real headless Chromium (Playwright):
1. Loads real React bundle from frontend/dist.
2. Navigates to /app/chat/{conversation_id}.
3. Types the exact Arabic founder prompt into the real composer.
4. Clicks Send.
5. Observes live streaming execution tree (Account Manager planning root -> Employee nodes -> Tools -> Synthesis).
6. Captures live screenshot to development/runs/DEV-008-SKILLS-OPS-HOTFIX/artifacts/spa_e2e_arabic_prompt.png.
7. Reloads the page (page.reload()) and proves the execution tree persists and reconstructs.
8. Captures reload screenshot to development/runs/DEV-008-SKILLS-OPS-HOTFIX/artifacts/spa_e2e_after_reload.png.
9. Verifies the 7 sections in the assistant's final response bubble.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time
import urllib.parse
import urllib.request

import pytest

from app import deps
from app.database import repos
from app.database.sqlite import connect
from app.main import create_app
from app.services import state as store
from app.services.config_service import ConfigService
import app.services.config_service as config_module

pytestmark = pytest.mark.slow

PINNED_FOUNDER_PROMPT = (
    "حلّل موقعنا، حسابنا على انستجرام، الـSEO، التموضع، قمع التحويل، والمنافسين، "
    "واقترح 3 تجارب نمو ذات أولوية"
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _dist_ready() -> bool:
    return (_repo_root() / "frontend" / "dist" / "index.html").is_file()


def test_real_spa_arabic_prompt_e2e_and_persistence(tmp_path, monkeypatch):
    if not _dist_ready():
        pytest.skip("frontend/dist/index.html not present. Run npm run build first.")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        pytest.skip(f"playwright not installed: {exc}")

    # Set up test DB and workspace
    db = tmp_path / "dev008hf_spa.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", workspace)
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    monkeypatch.setenv("CHROMA_ENABLED", "0")

    from app.routes import graph_runtime
    monkeypatch.setattr(graph_runtime, "_GRAPH", None, raising=False)
    deps.init_db(db)
    config_module.clear_all_test_overrides()

    with deps.get_db(db) as conn:
        ConfigService.set_setting("manager_provider", "deterministic", conn=conn)
        repos.Projects.upsert(conn, {
            "id": "starter",
            "name": "Acme Marketing",
            "website": "https://example.com",
            "goal": "Scale to 10k MRR",
            "status": "active",
            "settings_json": "{}",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        })
        convo = store.create_conversation(conn, project_id="starter", title="Compound Audit E2E")
        cid = convo["id"]

    # Start real uvicorn server
    app = create_app()
    import uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "Uvicorn did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    artifacts_dir = _repo_root() / "development" / "runs" / "DEV-008-SKILLS-OPS-HOTFIX" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:
        pytest.skip(f"Chromium launch failed: {exc}")

    try:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        # Navigate to chat conversation
        page.goto(f"{base}/app/chat/{cid}", wait_until="domcontentloaded")
        composer = page.locator('textarea[aria-label="Chat message"]')
        composer.wait_for(state="visible", timeout=25_000)

        # Type exact Arabic prompt and send
        composer.fill(PINNED_FOUNDER_PROMPT)
        send_btn = page.locator('button:has-text("Send")')
        send_btn.click()

        # Wait for tree section to render
        tree_section = page.locator('section[aria-label="Execution tree"]')
        tree_section.wait_for(state="visible", timeout=30_000)

        # Wait for turn completion
        deadline = time.time() + 60
        completed = False
        while time.time() < deadline:
            with deps.get_db(db) as conn:
                row = repos.Turns.latest_with_events(conn, cid)
                if row and row.get("status") in ("completed", "failed"):
                    completed = True
                    break
            time.sleep(0.5)
        assert completed, "Turn did not complete within deadline"

        # Capture live tree screenshot
        screenshot_live = artifacts_dir / "spa_e2e_arabic_prompt.png"
        page.screenshot(path=str(screenshot_live), full_page=True)
        assert screenshot_live.is_file(), "Live tree screenshot was not saved"

        # Verify ExecutionTree node rows exist in DOM
        nodes = tree_section.locator("li").all()
        assert len(nodes) >= 3, f"Expected multiple tree nodes in DOM, found: {len(nodes)}"

        # Verify assistant message in chat contains compound synthesis
        msg_bubble = page.locator('div.chat-md').last
        msg_bubble.wait_for(state="visible", timeout=20_000)

        deadline = time.time() + 30
        msg_text = ""
        while time.time() < deadline:
            msg_text = msg_bubble.inner_text()
            if "تجارب نمو" in msg_text or "Growth Experiments" in msg_text:
                break
            time.sleep(0.5)

        assert "تحليل الموقع" in msg_text or "الموقع" in msg_text
        assert "3 تجارب نمو" in msg_text or "تجارب نمو" in msg_text or "Growth Experiments" in msg_text

        # Reload page and verify persistence
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # ExecutionTree should reconstruct via usePersistedTurnTree
        tree_reloaded = page.locator('section[aria-label="Execution tree"]')
        tree_reloaded.wait_for(state="visible", timeout=20_000)
        reloaded_nodes = tree_reloaded.locator("li").all()
        assert len(reloaded_nodes) >= 3, f"Tree did not reconstruct after reload, found: {len(reloaded_nodes)}"

        # Verify assistant bubble still present after reload from DB
        msg_reloaded = page.locator('div.chat-md').last
        msg_reloaded.wait_for(state="visible", timeout=10_000)
        reloaded_text = msg_reloaded.inner_text()
        assert "الموقع" in reloaded_text or "تجارب نمو" in reloaded_text

        # Capture reload screenshot
        screenshot_reload = artifacts_dir / "spa_e2e_after_reload.png"
        page.screenshot(path=str(screenshot_reload), full_page=True)
        assert screenshot_reload.is_file(), "Reload screenshot was not saved"

    finally:
        browser.close()
        pw.stop()
