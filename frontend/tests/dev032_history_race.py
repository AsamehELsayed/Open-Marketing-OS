"""DEV-032 real-browser regression for a stale history response after selection."""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlsplit

from playwright.sync_api import Route, sync_playwright


HERE = Path(__file__).resolve().parent
FRONTEND = HERE.parent
REPO_ROOT = FRONTEND.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _envelope(data):
    return {"ok": True, "data": data}


def _deliverable(identifier: str, title: str, campaign_id: str) -> dict:
    return {
        "id": identifier,
        "project_id": "northstar",
        "campaign_id": campaign_id,
        "type": "social_post",
        "title": title,
        "platform": "instagram",
        "content_md": f"# {title}\nSynthetic browser fixture.",
        "status": "DRAFT",
        "current_version": 1,
        "created_at": "2026-10-10T00:00:00Z",
        "updated_at": "2026-10-10T00:00:00Z",
    }


def _revision(identifier: str, title: str, marker: str,
              campaign_id: str) -> dict:
    return {
        "revision_id": f"{identifier}-revision-1",
        "project_id": "northstar",
        "campaign_id": campaign_id,
        "deliverable_id": identifier,
        "version": 1,
        "operation": "CREATE",
        "type": "social_post",
        "title": title,
        "platform": "instagram",
        "content_md": f"# {marker}",
        "status": "DRAFT",
        "created_at": "2026-10-10T00:00:00Z",
    }


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
        [node, str(npm_cli), "run", "dev", "--",
         "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=FRONTEND,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pending_alpha_routes: list[Route] = []
    pending_beta_route: dict[str, Route] = {}

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
            context.add_init_script("localStorage.setItem('omos.projectId', 'northstar')")

            def route_api(route: Route) -> None:
                request = route.request
                path_name = urlsplit(request.url).path
                if not path_name.startswith("/api/"):
                    route.continue_()
                    return
                if path_name in ("/api/campaigns/campaign-a",
                                 "/api/campaigns/campaign-b"):
                    if "project_id=northstar" not in request.url:
                        raise AssertionError("campaign detail request omitted its active project")
                    campaign_id = path_name.rsplit("/", 1)[-1]
                    data = {
                        "campaign": {
                            "id": campaign_id, "project_id": "northstar",
                            "title": f"Race Test {campaign_id}", "status": "proposed",
                            "objective": "Synthetic objective",
                        },
                        "prospects": [], "tasks": [], "experiments": [],
                    }
                elif path_name == "/api/projects":
                    data = [{"id": "northstar", "name": "Northstar", "website": "", "goal": ""}]
                elif path_name == "/api/onboarding/status":
                    data = {"business_described": True}
                elif path_name == "/api/projects/northstar/campaigns/campaign-a/deliverables":
                    data = [_deliverable("shared", "Alpha Post", "campaign-a")]
                elif path_name == "/api/projects/northstar/campaigns/campaign-b/deliverables":
                    data = [_deliverable("shared", "Beta Post", "campaign-b")]
                elif path_name == "/api/projects/northstar/campaigns/campaign-a/deliverables/shared/history":
                    pending_alpha_routes.append(route)
                    return
                elif path_name == "/api/projects/northstar/campaigns/campaign-b/deliverables/shared/history":
                    pending_beta_route["route"] = route
                    return
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
            console_errors: list[str] = []
            api_responses: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("console", lambda message: console_errors.append(message.text)
                    if message.type == "error" else None)
            page.on("response", lambda response: api_responses.append(
                f"{response.status} {response.url}") if "/api/" in response.url else None)
            page.goto(f"{base}/app/campaigns/campaign-a", wait_until="domcontentloaded")
            try:
                page.get_by_role("tab", name="Content", exact=True).wait_for(timeout=20000)
            except Exception as exc:
                raise AssertionError(
                    "Campaign detail failed to render Content tab: "
                    f"body={page.locator('body').inner_text()!r}; "
                    f"api={api_responses!r}; console={console_errors!r}; "
                    f"page={page_errors!r}") from exc
            page.get_by_role("tab", name="Content", exact=True).click()
            page.get_by_role("button", name="Alpha Post", exact=False).wait_for(state="visible")

            alpha_history_url = (
                "**/api/projects/northstar/campaigns/campaign-a/"
                "deliverables/shared/history*"
            )
            for _ in range(2):
                with page.expect_request(alpha_history_url, timeout=10000):
                    page.get_by_role(
                        "button", name="Revision history", exact=True).click()
            assert len(pending_alpha_routes) == 2, (
                "both deferred Alpha history requests should be captured"
            )

            with page.expect_response(
                lambda response: (
                    urlsplit(response.url).path == "/api/campaigns/campaign-b"
                    and "project_id=northstar" in response.url
                ),
                timeout=10000,
            ):
                page.evaluate("""() => {
                  window.history.pushState({}, "", "/app/campaigns/campaign-b");
                  window.dispatchEvent(new PopStateEvent("popstate"));
                }""")
            page.get_by_role("tab", name="Content", exact=True).click()
            page.get_by_role("button", name="Beta Post", exact=False).wait_for(state="visible")
            beta_history_url = (
                "**/api/projects/northstar/campaigns/campaign-b/"
                "deliverables/shared/history*"
            )
            with page.expect_request(beta_history_url, timeout=10000):
                page.get_by_role(
                    "button", name="Revision history", exact=True).click()
            assert "route" in pending_beta_route, (
                "the Beta history request should remain deferred"
            )

            pending_alpha_routes[0].fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_envelope([
                    _revision("shared", "Alpha Post", "ALPHA_HISTORY_SENTINEL",
                              "campaign-a")
                ])),
            )
            panel = page.locator("section[aria-labelledby='deliverables-heading']")
            page.wait_for_timeout(150)
            assert "Loading history" in panel.inner_text(), (
                "the stale Alpha completion must not clear Beta's loading state"
            )
            assert "ALPHA_HISTORY_SENTINEL" not in panel.inner_text()

            pending_alpha_routes[1].fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps({"detail": "STALE_CAMPAIGN_ERROR_SENTINEL"}),
            )
            page.wait_for_timeout(150)
            assert "Loading history" in panel.inner_text(), (
                "a stale campaign error/finally must not clear Beta's loading state"
            )
            assert "STALE_CAMPAIGN_ERROR_SENTINEL" not in panel.inner_text()

            pending_beta_route["route"].fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_envelope([
                    _revision("shared", "Beta Post", "BETA_HISTORY_SENTINEL",
                              "campaign-b")
                ])),
            )
            page.get_by_text("BETA_HISTORY_SENTINEL", exact=False).wait_for(state="visible")
            page.wait_for_timeout(100)
            rendered_history = panel.inner_text()
            assert "BETA_HISTORY_SENTINEL" in rendered_history
            assert "ALPHA_HISTORY_SENTINEL" not in rendered_history, (
                "the delayed Alpha response appeared beneath Beta Post"
            )
            assert not page_errors, page_errors
            print("DEV-032 history race: deferred success and error from campaign-a were discarded after switching to campaign-b.")
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
