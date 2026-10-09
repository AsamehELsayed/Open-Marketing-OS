from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]

LEGACY_REDIRECTS = (
    ("/", "/app"),
    ("/legacy", "/app"),
    ("/settings", "/app/settings"),
    ("/system", "/app/settings?section=system"),
    ("/chat/new", "/app/chat/new"),
    ("/chat/missing-conversation", "/app/chat/missing-conversation"),
    ("/chat", "/app/chat/new"),
    ("/projects", "/app"),
    ("/campaigns", "/app/campaigns"),
    ("/approvals", "/app/approvals"),
    ("/brain", "/app/knowledge"),
    ("/results", "/app/results"),
    ("/jobs", "/app/jobs"),
    ("/tasks", "/app/jobs"),
    ("/companies", "/app"),
    ("/jobs/missing-job/process", "/app/jobs"),
    ("/jobs/missing-job/log", "/app/jobs"),
    ("/jobs/missing-job/status", "/app/jobs"),
)

DEAD_OPERATIONS = (
    ("GET", "/api/settings/overview"),
    ("POST", "/api/settings/migrate-env"),
    ("POST", "/chat"),
    ("POST", "/chat/missing-conversation/archive"),
    ("POST", "/projects/new"),
    ("POST", "/projects/switch"),
    ("POST", "/projects/archive"),
    ("POST", "/projects/social/save"),
    ("POST", "/projects/social/remove"),
    ("POST", "/campaigns/missing-campaign/status"),
    ("POST", "/tasks/missing-task/status"),
    ("POST", "/approvals/missing-approval/decide"),
    ("POST", "/experiments/missing-experiment/measurements"),
    ("POST", "/settings/instagram-handle"),
    ("POST", "/settings/provider"),
    ("POST", "/settings/reimport"),
    ("POST", "/settings/reindex"),
    ("POST", "/settings/backup"),
    ("POST", "/settings/conflicts/resolve"),
    ("POST", "/settings/integrations/configure"),
    ("POST", "/settings/integrations/test"),
    ("POST", "/settings/integrations/disconnect"),
    ("POST", "/settings/integrations/oauth/connect"),
    ("GET", "/settings/integrations/oauth/callback"),
    ("POST", "/settings/instagram/capability"),
    ("POST", "/settings/mcp/action"),
    ("POST", "/jobs/missing-job/cancel"),
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "surface.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.mark.parametrize(("path", "target"), LEGACY_REDIRECTS)
def test_every_legacy_ui_get_is_one_hop_303(client, path, target):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers.get("location") == target
    assert response.history == []


def test_reachable_openapi_descriptions_avoid_forbidden_legacy_and_environment_wording():
    forbidden_terms = ("legacy", "environment variable", "environment-variable")
    descriptions = [
        operation.get("description", "")
        for path_item in create_app().openapi()["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and operation.get("description")
    ]
    violations = [
        description
        for description in descriptions
        if any(term in description.lower() for term in forbidden_terms)
    ]

    assert descriptions
    assert violations == []


@pytest.mark.parametrize(("path", "target"), LEGACY_REDIRECTS)
def test_legacy_ui_redirect_has_no_jinja_or_legacy_product_body(client, path, target):
    response = client.get(path, follow_redirects=False)
    body = response.text.lower()
    assert not any(marker in body for marker in (
        "<!doctype", "<html", "<form", "jinja", "template",
        "data-turn-stream", "legacy product",
    ))


@pytest.mark.parametrize(("method", "path"), DEAD_OPERATIONS)
def test_every_dead_operation_is_404(client, method, path):
    kwargs = {"follow_redirects": False}
    if method == "POST":
        kwargs["data"] = {}
    response = client.request(method, path, **kwargs)
    assert response.status_code == 404
    assert response.headers.get("location") is None


def test_dead_post_chat_operation_is_not_registered():
    app = create_app()
    registered = [
        route
        for route in app.routes
        if getattr(route, "path", None) == "/chat" and "POST" in getattr(route, "methods", set())
    ]
    assert registered == []


def test_react_has_one_settings_authority_and_no_dead_client_surface():
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    client_source = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    settings_routes = re.findall(r'<Route\s+path=["\']/app/settings["\']', app_source)
    assert len(settings_routes) == 1
    assert len(re.findall(r'import\("\./routes/Settings"\)', app_source)) == 1
    assert "/api/settings/overview" not in client_source
    assert "migrate-env" not in client_source
    assert "migrateEnvCredential" not in client_source


def test_react_route_inventory_is_exactly_the_frozen_set():
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    routes = re.findall(r'<Route\s+path="([^"]+)"', app_source)
    assert routes == [
        "/",
        "/app",
        "/app/start",
        "/app/business/new",
        "/app/workspace",
        "/app/chat/new",
        "/app/chat/:id",
        "/app/campaigns",
        "/app/campaigns/:id",
        "/app/approvals",
        "/app/graph",
        "/app/results",
        "/app/jobs",
        "/app/knowledge",
        "/app/settings",
        "*",
    ]


def test_react_navigation_has_no_legacy_product_destination():
    source_root = ROOT / "frontend" / "src"
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_root.rglob("*.tsx"))
    for legacy_path in ("/legacy", "/settings", "/system", "/projects", "/campaigns", "/approvals", "/jobs", "/tasks"):
        assert not re.search(rf'(?:to|href)=["\']{re.escape(legacy_path)}["\']', source)
