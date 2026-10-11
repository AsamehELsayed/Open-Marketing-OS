"""DEV-032 browser regression for guarding unsaved create-mode drafts."""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from playwright.sync_api import Route, sync_playwright


HERE = Path(__file__).resolve().parent
FRONTEND = HERE.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _envelope(data):
    return {"ok": True, "data": data}


def main() -> None:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required to run the Vite browser fixture")
    npm_cli = Path(node).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    if not npm_cli.is_file():
        raise RuntimeError("Could not locate npm-cli.js beside the Node.js executable")
    server = subprocess.Popen(
        [node, str(npm_cli), "run", "dev", "--", "--host", "127.0.0.1",
         "--port", str(port), "--strictPort"],
        cwd=FRONTEND,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError(f"Vite exited early with code {server.returncode}")
            try:
                with urlopen(base, timeout=1):
                    break
            except Exception:
                time.sleep(0.1)
        else:
            raise TimeoutError("Vite did not start within 30 seconds")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_init_script("""
              localStorage.setItem('omos.projectId', 'northstar');
              window.__confirmCalls = [];
              window.__confirmResult = false;
              window.confirm = (message) => {
                window.__confirmCalls.push(String(message));
                return Boolean(window.__confirmResult);
              };
            """)

            def route_api(route: Route) -> None:
                request = route.request
                path_name = urlsplit(request.url).path
                if not path_name.startswith("/api/"):
                    route.continue_()
                    return
                if path_name == "/api/campaigns/campaign-a":
                    if "project_id=northstar" not in request.url:
                        raise AssertionError("campaign detail request omitted its active project")
                    data = {
                        "campaign": {
                            "id": "campaign-a", "project_id": "northstar",
                            "title": "Create draft guard fixture", "status": "proposed",
                            "objective": "Synthetic objective",
                        },
                        "prospects": [], "tasks": [], "experiments": [],
                    }
                elif path_name == "/api/projects":
                    data = [{"id": "northstar", "name": "Northstar", "website": "", "goal": ""}]
                elif path_name == "/api/onboarding/status":
                    data = {"business_described": True}
                elif path_name == "/api/projects/northstar/campaigns/campaign-a/deliverables":
                    data = [{
                        "id": "saved-search-ad",
                        "project_id": "northstar",
                        "campaign_id": "campaign-a",
                        "type": "ad_copy",
                        "title": "Saved Search Ad",
                        "platform": "Search",
                        "content_md": "# SAVED_BODY_SENTINEL\nPersisted fixture body.",
                        "status": "DRAFT",
                        "current_version": 1,
                        "created_at": "2026-10-10T00:00:00Z",
                        "updated_at": "2026-10-10T00:00:00Z",
                    }]
                else:
                    data = []
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(_envelope(data)),
                )

            context.route("**/api/**", route_api)
            page = context.new_page()
            page_errors: list[str] = []
            api_responses: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("response", lambda response: api_responses.append(
                f"{response.status} {response.url}") if "/api/" in response.url else None)
            page.goto(f"{base}/app/campaigns/campaign-a", wait_until="domcontentloaded")
            try:
                page.get_by_role("tab", name="Content", exact=True).wait_for(timeout=20000)
            except Exception as exc:
                raise AssertionError(
                    "Campaign detail failed to render Content tab: "
                    f"body={page.locator('body').inner_text()!r}; "
                    f"api={api_responses!r}; errors={page_errors!r}") from exc
            page.get_by_role("tab", name="Content", exact=True).click()
            saved_row = page.get_by_role("button", name="Saved Search Ad", exact=False)
            saved_row.wait_for(state="visible")

            page.get_by_role("button", name="New deliverable", exact=True).click()
            assert page.evaluate("window.__confirmCalls") == [], (
                "opening a blank create form must not ask to discard anything"
            )
            type_select = page.locator("form select")
            type_select.select_option("ad_copy")
            page.locator("input[placeholder='e.g. LinkedIn, Instagram']").fill("Search")
            title = page.get_by_label("Title", exact=True)
            content = page.get_by_label("Deliverable Markdown content", exact=True)
            title.fill("Unsaved create title")
            content.fill("# CREATE_DRAFT_SENTINEL\nKeep this text on cancel.")

            page.get_by_role("button", name="New deliverable", exact=True).click()
            assert len(page.evaluate("window.__confirmCalls")) == 1, (
                "starting another create form must ask before replacing a dirty create draft"
            )
            assert type_select.input_value() == "ad_copy"
            assert page.locator("input[placeholder='e.g. LinkedIn, Instagram']").input_value() == "Search"
            assert title.input_value() == "Unsaved create title"
            assert content.input_value() == "# CREATE_DRAFT_SENTINEL\nKeep this text on cancel."
            page.evaluate("window.__confirmCalls = []")

            saved_row.click()
            assert len(page.evaluate("window.__confirmCalls")) == 1, (
                "selecting a saved row must ask before replacing a dirty create draft"
            )
            assert title.input_value() == "Unsaved create title", "Cancel must retain the create title"
            assert type_select.input_value() == "ad_copy", (
                "Cancel must retain the create type"
            )
            assert page.locator("input[placeholder='e.g. LinkedIn, Instagram']").input_value() == "Search", (
                "Cancel must retain the create platform"
            )
            assert content.input_value() == "# CREATE_DRAFT_SENTINEL\nKeep this text on cancel.", (
                "Cancel must retain the create Markdown"
            )
            assert page.locator("form").is_visible(), "Cancel must keep the create form open"
            assert page.locator("[aria-current='true']").count() == 0, (
                "Cancel must not select the saved row"
            )

            page.evaluate("window.__confirmResult = true; window.__confirmCalls = []")
            saved_row.click()
            assert len(page.evaluate("window.__confirmCalls")) == 1, (
                "accepting the discard prompt should continue the saved-row selection"
            )
            page.get_by_role("heading", name="Saved Search Ad", exact=True).wait_for(state="visible")
            assert page.get_by_text("SAVED_BODY_SENTINEL", exact=False).is_visible(), (
                "accepting must display the saved deliverable"
            )
            assert page.locator("form").count() == 0, "accepted selection closes the create form"
            assert page.locator("[aria-current='true']").count() == 1, (
                "accepted selection marks the saved row as current"
            )
            assert not page_errors, page_errors
            print("DEV-032 create draft guard: blank create, Cancel retention, and accepted saved-row selection passed.")
            context.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    main()
