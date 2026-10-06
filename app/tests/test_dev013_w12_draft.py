"""DEV-013 W12: real-browser composer lifecycle during project bootstrap.

The test holds the real GET /api/projects response with an async Playwright
route, types into the real Composer, then releases the response. React and the
real served SPA are exercised throughout; only the network response timing is
controlled. The required assertion intentionally fails on draft loss.
"""
from __future__ import annotations

import asyncio
import re
import threading
import time

import pytest

from app import deps
from app.database import repos
from app.main import create_app
from app.services import state as store
from app.services.config_service import ConfigService
import app.services.config_service as config_module

pytestmark = pytest.mark.slow


def _root():
    from pathlib import Path
    return Path(__file__).resolve().parents[2]


def _boot(tmp_path, monkeypatch):
    """Start an isolated real SPA and return base URL, conversation, server."""
    if not (_root() / "frontend" / "dist" / "index.html").is_file():
        pytest.skip("frontend/dist/index.html is missing; build the SPA first")
    from app.routes import graph_runtime
    import uvicorn

    db = tmp_path / "dev013-w12-draft.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", workspace)
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    monkeypatch.setenv("CHROMA_ENABLED", "0")
    monkeypatch.setattr(graph_runtime, "_GRAPH", None, raising=False)
    deps.init_db(db)
    config_module.clear_all_test_overrides()
    with deps.get_db(db) as conn:
        ConfigService.set_setting("manager_provider", "deterministic", conn=conn)
        project = repos.Projects.get(conn, "starter")
        assert project is not None, "deps.init_db did not create starter project"
        conversation = store.create_conversation(
            conn, project_id="starter", title="DEV-013 composer bootstrap")
        cid = conversation["id"]
    server = uvicorn.Server(uvicorn.Config(
        create_app(), host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    return f"http://127.0.0.1:{port}", cid, server, thread


def test_real_chat_draft_survives_deferred_project_bootstrap(tmp_path, monkeypatch):
    """Typing before the real project bootstrap resolves must retain draft."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        pytest.skip(f"Playwright async API unavailable: {exc}")

    base, cid, server, server_thread = _boot(tmp_path, monkeypatch)

    async def scenario():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True)
            except Exception as exc:
                pytest.skip(f"Chromium launch failed: {exc}")
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            # Record actual composer DOM identity transitions and value at each
            # attach/detach. This is instrumentation only; it does not alter UI.
            await context.add_init_script("""
              (() => {
                window.__composerLifecycle = [];
                const ids = new WeakMap(); let next = 1;
                const record = (kind, el) => {
                  if (!ids.has(el)) ids.set(el, next++);
                  window.__composerLifecycle.push({kind, id: ids.get(el), value: el.value});
                };
                const scan = (node, kind) => {
                  if (!(node instanceof Element)) return;
                  if (node.matches('textarea[aria-label="Chat message"]')) record(kind, node);
                  node.querySelectorAll?.('textarea[aria-label="Chat message"]').forEach(el => record(kind, el));
                };
                new MutationObserver(records => records.forEach(r => {
                  r.removedNodes.forEach(n => scan(n, 'removed'));
                  r.addedNodes.forEach(n => scan(n, 'added'));
                })).observe(document, {subtree:true, childList:true});
              })();
            """)
            page = await context.new_page()
            entered = asyncio.Event()
            release = asyncio.Event()
            project_route_count = 0
            project_response_ids = []

            async def hold_projects(route):
                nonlocal project_route_count
                project_route_count += 1
                entered.set()
                await release.wait()
                response = await route.fetch()
                payload = await response.json()
                rows = payload.get("data", []) if isinstance(payload, dict) else payload
                project_response_ids.extend(
                    row.get("id") for row in rows if isinstance(row, dict))
                await route.fulfill(response=response)

            await page.route("**/api/projects", hold_projects)
            navigation = asyncio.create_task(page.goto(
                f"{base}/app/chat/{cid}", wait_until="domcontentloaded"))
            await asyncio.wait_for(entered.wait(), timeout=20)
            composer = page.locator('textarea[aria-label="Chat message"]')
            await composer.wait_for(state="visible", timeout=20_000)
            draft = "DEV-013 bootstrap draft — retain exactly"
            await composer.fill(draft)
            before = {
                "value": await composer.input_value(),
                "send_enabled": await page.get_by_role("button", name="Send").is_enabled(),
                "stored_project": await page.evaluate("localStorage.getItem('omos.projectId')"),
                "loading_projects_skeletons": await page.locator('[aria-label="Loading projects"]').count(),
                "lifecycle": await page.evaluate("window.__composerLifecycle"),
            }
            release.set()
            await navigation
            await page.locator('[aria-label="Loading projects"]').wait_for(
                state="hidden", timeout=20_000)
            after = {
                "value": await page.locator('textarea[aria-label="Chat message"]').input_value(),
                "send_enabled": await page.get_by_role("button", name="Send").is_enabled(),
                "stored_project": await page.evaluate("localStorage.getItem('omos.projectId')"),
                "project_button": await page.locator('button[aria-haspopup="menu"]').first.inner_text(),
                "loading_projects_skeletons": await page.locator('[aria-label="Loading projects"]').count(),
                "project_response_ids": list(project_response_ids),
                "lifecycle": await page.evaluate("window.__composerLifecycle"),
                "route_count": project_route_count,
            }
            # Close browser resources before assertions so the expected
            # baseline draft-loss failure cannot leak a Chromium process.
            await page.unroute("**/api/projects", hold_projects)
            await browser.close()
            print(f"DEV-013 baseline bootstrap observation: before={before!r}; after={after!r}")
            assert before["stored_project"] is None, (
                f"fresh browser unexpectedly had a stored project: {before!r}")
            assert before["loading_projects_skeletons"] > 0, (
                f"did not capture the held project-bootstrap state: {before!r}")
            assert after["loading_projects_skeletons"] == 0, (
                f"project bootstrap did not visibly complete: {after!r}")
            assert project_route_count == 1, (
                f"expected exactly one held real project request, got {project_route_count}")
            assert after["project_response_ids"] == ["starter"], (
                "expected the held real API response to resolve null→starter, "
                f"got {after['project_response_ids']!r}")
            assert after["value"] == draft, (
                "project bootstrap changed/lost the real Composer draft; "
                f"before={before!r}, after={after!r}")
            assert after["send_enabled"], f"Send became disabled: {after!r}"

    try:
        asyncio.run(scenario())
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)


def test_project_switch_isolates_drafts_and_sends_once_to_bound_conversation(
        tmp_path, monkeypatch):
    """A→B keeps drafts scoped; returning to A sends once to A's chat."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        pytest.skip(f"Playwright async API unavailable: {exc}")

    base, cid_a, server, server_thread = _boot(tmp_path, monkeypatch)
    db = tmp_path / "dev013-w12-draft.sqlite"
    with deps.get_db(db) as conn:
        project_a = repos.Projects.get(conn, "starter")
        assert project_a is not None
        project_a.update({"name": "Synthetic Project A", "website": "https://a.example"})
        repos.Projects.upsert(conn, project_a)
        store.set_active_project(conn, "starter")
        project_b = store.create_project(
            conn, "Synthetic Project B", "https://b.example", "Lifecycle test")
        cid_b = store.create_conversation(
            conn, project_id=project_b["id"], title="DEV-013 project B")["id"]
        assert repos.Conversations.get(conn, cid_a, "starter")["project_id"] == "starter"
        assert repos.Conversations.get(conn, cid_b, project_b["id"])["project_id"] == project_b["id"]

    async def scenario():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True)
            except Exception as exc:
                pytest.skip(f"Chromium launch failed: {exc}")
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            await context.add_init_script(
                "localStorage.setItem('omos.projectId', 'starter')")
            page = await context.new_page()
            sent_requests = []
            page.on("request", lambda request: sent_requests.append(request)
                    if request.method == "POST" and request.url.endswith("/chat/turn")
                    else None)
            await page.goto(f"{base}/app/chat/{cid_a}", wait_until="domcontentloaded")
            composer = page.locator('textarea[aria-label="Chat message"]')
            await composer.wait_for(state="visible", timeout=20_000)
            project_switcher = page.locator('button[aria-haspopup="menu"]').first
            await page.wait_for_function(
                "document.querySelector('button[aria-haspopup=\\\"menu\\\"]')?.textContent?.includes('Synthetic Project A')",
                timeout=20_000)

            draft_a = "draft scoped to project A conversation"
            await composer.fill(draft_a)
            send_button = page.get_by_role("button", name="Send")
            await page.wait_for_function(
                "Array.from(document.querySelectorAll('button')).some(b => b.textContent.trim() === 'Send' && !b.disabled)",
                timeout=20_000)
            assert await send_button.is_enabled(), "A's valid conversation should enable Send"

            await project_switcher.click()
            menu_items = await page.get_by_role("menuitem").all_text_contents()
            assert any("Synthetic Project A" in item for item in menu_items), menu_items
            assert any("Synthetic Project B" in item for item in menu_items), menu_items
            await page.get_by_role("menuitem", name="Synthetic Project B").click()
            await page.wait_for_function(
                "document.querySelector('button[aria-haspopup=\\\"menu\\\"]')?.textContent?.includes('Synthetic Project B')",
                timeout=20_000)
            assert await composer.input_value() == "", "A's draft leaked into project B scope"
            assert not await send_button.is_enabled(), (
                "project B must not send to the still-open project A conversation")

            await project_switcher.click()
            await page.get_by_role("menuitem", name="Synthetic Project A").click()
            await page.wait_for_function(
                "document.querySelector('button[aria-haspopup=\\\"menu\\\"]')?.textContent?.includes('Synthetic Project A')",
                timeout=20_000)
            assert await composer.input_value() == draft_a, "returning to A did not restore its draft"
            assert await send_button.is_enabled(), "A's bound conversation should restore valid Send"

            async with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and response.url.endswith("/chat/turn"), timeout=30_000) as ack:
                # Deliver two same-tick native clicks. The in-flight guard must
                # accept the first submission and reject the duplicate callback.
                await send_button.evaluate("""button => {
                  const click = () => button.dispatchEvent(new MouseEvent('click',
                    {bubbles: true, cancelable: true, view: window}));
                  click(); click();
                }""")
            ack_response = await ack.value
            assert ack_response.ok, "real /chat/turn request did not succeed"
            ack_html = await ack_response.text()
            turn_match = re.search(r'data-turn-stream="([^\"]+)"', ack_html)
            assert turn_match, f"real ack omitted turn ID: {ack_html[:300]!r}"
            turn_id = turn_match.group(1)
            assert len(sent_requests) == 1, (
                f"expected one POST /chat/turn, observed {len(sent_requests)}")
            body = sent_requests[0].post_data or ""
            assert cid_a in body, f"conversation id missing from request: {body!r}"
            assert draft_a in body, f"exact draft missing from request: {body!r}"
            with deps.get_db(db) as conn:
                bound = repos.Conversations.get(conn, cid_a, "starter")
                assert bound and bound["project_id"] == "starter"
                turn = repos.Turns.get(conn, turn_id)
                assert turn is not None, f"acknowledged turn {turn_id!r} was not persisted"
                assert turn["project_id"] == "starter", turn
                assert turn["conversation_id"] == cid_a, turn
            await browser.close()

    try:
        asyncio.run(scenario())
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)


def test_fresh_business_setup_opens_account_manager_without_starter_draft(
        tmp_path, monkeypatch):
    """Real fresh-user business setup must open a clean, correctly scoped chat."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        pytest.skip(f"Playwright async API unavailable: {exc}")

    base, starter_cid, server, server_thread = _boot(tmp_path, monkeypatch)
    db = tmp_path / "dev013-w12-draft.sqlite"

    async def scenario():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True)
            except Exception as exc:
                pytest.skip(f"Chromium launch failed: {exc}")
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            await context.add_init_script(
                "localStorage.setItem('omos.projectId', 'starter')")
            page = await context.new_page()
            await page.goto(
                f"{base}/app/chat/{starter_cid}", wait_until="domcontentloaded")
            composer = page.locator('textarea[aria-label="Chat message"]')
            await composer.wait_for(state="visible", timeout=20_000)
            starter_draft = "starter-only unsent draft must never cross setup"
            await composer.fill(starter_draft)

            # Fresh install exposes the business setup action in the real
            # project switcher. Selecting it routes through the shipped form.
            switcher = page.locator('button[aria-haspopup="menu"]').first
            await page.wait_for_function(
                "document.querySelector('button[aria-haspopup=\\\"menu\\\"]')?.textContent?.includes('Set up your business')",
                timeout=20_000)
            await switcher.click()
            await page.get_by_role(
                "menuitem", name=re.compile("Set up your business")).click()
            await page.get_by_label("Business name").wait_for(state="visible")

            business_name = "DEV013 Synthetic Fresh Business"
            await page.get_by_label("Business name").fill(business_name)
            await page.get_by_label("Website").fill("fresh-business.example")
            async with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and response.url.endswith("/api/onboarding/business"),
                    timeout=30_000) as business_ack:
                await page.get_by_role(
                    "button", name="Save and open Account Manager").click()
            business_response = await business_ack.value
            assert business_response.ok, "real business setup API request failed"
            business_payload = await business_response.json()
            business_project_id = business_payload["data"]["project"]["id"]
            assert business_payload["data"]["project"]["name"] == business_name

            await page.wait_for_url("**/app", timeout=20_000)
            await page.get_by_text("What do you want to work on?").wait_for(
                state="visible", timeout=20_000)
            await page.wait_for_function(
                "document.querySelector('button[aria-haspopup=\\\"menu\\\"]')?.textContent?.includes('DEV013 Synthetic Fresh Business')",
                timeout=20_000)
            assert await page.evaluate(
                "localStorage.getItem('omos.projectId')") == business_project_id

            # Account Manager home → real NewChat route → POST /chat/new.
            await page.get_by_role("button", name="Start a blank chat").click()
            await page.get_by_role("heading", name="New chat").wait_for(
                state="visible", timeout=20_000)
            await page.wait_for_function(
                "Array.from(document.querySelectorAll('button')).some(b => b.textContent.trim() === 'Start chat' && !b.disabled)",
                timeout=20_000)
            async with page.expect_request(
                    lambda request: request.method == "POST"
                    and request.url.endswith("/chat/new"), timeout=20_000) as new_chat_request:
                await page.get_by_role("button", name="Start chat").click()
            created_request = await new_chat_request.value
            assert business_project_id in (created_request.post_data or ""), (
                "new chat did not use the active business project: "
                f"{created_request.post_data!r}")
            await page.wait_for_function(
                "location.pathname.startsWith('/app/chat/') && !location.pathname.endsWith('/new')",
                timeout=20_000)
            new_conversation_id = page.url.rstrip("/").rsplit("/", 1)[-1]
            composer = page.locator('textarea[aria-label="Chat message"]')
            await composer.wait_for(state="visible", timeout=20_000)
            assert await composer.input_value() == "", (
                "starter draft leaked into the fresh Account Manager conversation")
            send_button = page.get_by_role("button", name="Send")
            assert not await send_button.is_enabled(), "empty new chat must keep Send disabled"
            with deps.get_db(db) as conn:
                conversation = repos.Conversations.get(
                    conn, new_conversation_id, business_project_id)
                assert conversation is not None
                assert conversation["project_id"] == business_project_id

            prompt = "For this synthetic business, suggest one useful first marketing task."
            await composer.fill(prompt)
            await page.wait_for_function(
                "Array.from(document.querySelectorAll('button')).some(b => b.textContent.trim() === 'Send' && !b.disabled)",
                timeout=20_000)
            async with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and response.url.endswith("/chat/turn"), timeout=30_000) as turn_ack:
                await send_button.click()
            response = await turn_ack.value
            assert response.ok, "Account Manager turn request failed"
            ack_html = await response.text()
            turn_match = re.search(r'data-turn-stream="([^\"]+)"', ack_html)
            assert turn_match, f"real turn ack omitted ID: {ack_html[:300]!r}"
            turn_id = turn_match.group(1)
            with deps.get_db(db) as conn:
                turn = repos.Turns.get(conn, turn_id)
                assert turn is not None
                assert turn["project_id"] == business_project_id, turn
                assert turn["conversation_id"] == new_conversation_id, turn

            # The ack only means the background Account Manager turn started.
            # Observe its persisted terminal status before shutting down the
            # browser/server, so LangGraph callbacks are not abandoned at teardown.
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                with deps.get_db(db) as conn:
                    turn = repos.Turns.get(conn, turn_id)
                if turn and turn.get("status") in {"completed", "failed", "closed"}:
                    break
                await asyncio.sleep(0.05)
            assert turn is not None and turn.get("status") == "completed", (
                "Account Manager turn did not complete before teardown; "
                f"last persisted row={turn!r}")
            await browser.close()

    try:
        asyncio.run(scenario())
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
