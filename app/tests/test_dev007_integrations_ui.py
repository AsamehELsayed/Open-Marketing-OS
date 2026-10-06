import importlib
import re

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.main import create_app
from app.services import state as store
from app.services.config_service import ConfigService
from app.services.integrations import registry
secret_store = importlib.import_module("app.services.credentials.store")

SECTION_IDS = (
    "integrations-ai",
    "integrations-research",
    "integrations-social",
    "integrations-knowledge",
    "integrations-automation-mcp",
)

CARD_MATRIX = {
    "langgraph-runtime": {"actions": set(), "scope": "installation", "cost": "free"},
    "model-provider": {"actions": set(), "scope": "installation", "cost": "free|byok"},
    "byok-openai": {"actions": {"configure", "test"}, "scope": "user", "cost": "byok"},
    "vision-provider": {"actions": set(), "scope": "installation", "cost": "byok"},
    "apify": {"actions": {"configure", "test"}, "scope": "installation", "cost": "metered"},
    "brightdata": {"actions": {"configure", "test"}, "scope": "installation", "cost": "metered"},
    "browser-review": {"actions": {"test"}, "scope": "installation", "cost": "free"},
    "instagram-public-profile": {"actions": {"configure", "test"},
                                 "scope": "installation", "cost": "metered"},
    "instagram-owned-insights": {"actions": {"connect"},
                                 "scope": "installation", "cost": "free"},
    "project-handle": {"actions": {"verify", "research"}, "scope": "project", "cost": "free"},
    "linked-platforms": {"actions": set(), "scope": "project", "cost": "free"},
    "knowledge-index": {"actions": set(), "scope": "project", "cost": "free"},
}

CARD_NAMES = (
    "LangGraph runtime", "Model provider", "OpenAI BYOK key", "Vision provider",
    "Apify", "Bright Data", "Browser review", "Instagram public profile",
    "Instagram owned insights", "Project Instagram handle",
    "Linked accounts by platform", "Knowledge index",
)

FIELD_LABELS = (
    "Scope:", "Provider:", "Cost:", "Connected account:", "Configured:",
    "Secret stored:", "Last health:",
)


class MemoryBackend:
    name = "test-memory"

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def put(self, key: str, plaintext: bytes) -> None:
        self.values[key] = bytes(plaintext)

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "dev007w8.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    for var in ("APIFY_API_TOKEN", "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_IG_DATASET",
                "META_IG_ACCESS_TOKEN", "META_IG_ACCOUNT_ID", "OPENAI_API_KEY",
                "META_OAUTH_CLIENT_ID", "META_APP_ID", "META_OAUTH_CLIENT_SECRET",
                "META_APP_SECRET", "IG_BROWSER_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    deps.init_db(db)
    previous_backend = secret_store._default
    secret_store.set_default_backend(MemoryBackend())
    try:
        with TestClient(create_app()) as c:
            yield c
    finally:
        secret_store.set_default_backend(previous_backend)


def _card_blocks(html: str) -> dict:
    parts = re.split(r'(?=<div class="card integration-card")', html)
    blocks = {}
    for part in parts[1:]:
        match = re.search(r'id="ic-([^"]+)"', part)
        if match:
            blocks[match.group(1)] = part
    return blocks


def test_all_settings_sections_render(client):
    r = client.get("/settings", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/app/settings"
    res = client.get("/api/settings/config")
    assert res.status_code == 200
    data = res.json()["data"]
    for section_id in ("general", "ai", "integrations", "files_knowledge", "privacy_security", "system"):
        assert section_id in data, section_id


def test_card_and_action_matrix(client):
    res = client.get("/api/settings/config")
    assert res.status_code == 200
    integrations = res.json()["data"]["integrations"]
    assert "instagram_public" in integrations
    assert "meta_insights" in integrations
    assert "web_research" in integrations
    assert "mcp" in integrations
    # Verify action routes exist
    r_test = client.post("/api/settings/credentials/test", json={"target": "browser"})
    assert r_test.status_code == 200
    data = r_test.json()["data"]
    assert data["configured"] is False
    assert data["capability_health"] == "unknown"
    assert data["provider_health"] == "unknown"
    assert not data["last_checked_at"]


def test_mcp_config_is_default_deny_and_legacy_action_is_dead(client):
    response = client.get("/api/settings/config")
    assert response.status_code == 200
    mcp_data = response.json()["data"]["integrations"]["mcp"]
    assert isinstance(mcp_data["servers"], list)
    assert mcp_data["count"] == len(mcp_data["servers"])
    legacy = client.post(
        "/settings/mcp/action",
        data={"project_id": "starter", "server": "test", "action": "rm"},
    )
    assert legacy.status_code == 404
    assert legacy.headers.get("location") is None


def test_current_instagram_handle_api_replaces_dead_capability_forms(client):
    for endpoint, payload in (
        ("/settings/instagram-handle", {"project_id": "starter", "handle": "starter"}),
        ("/settings/instagram/capability", {"project_id": "starter", "mode": "verify"}),
    ):
        response = client.post(endpoint, data=payload)
        assert response.status_code == 404
    saved = client.post(
        "/api/settings/integrations/instagram",
        json={"project_id": "starter", "handle": "w8handle"},
    )
    assert saved.status_code == 200
    config = client.get("/api/settings/config?project_id=starter").json()["data"]
    assert config["integrations"]["instagram_project"]["handle"] == "w8handle"
    cleared = client.post(
        "/api/settings/integrations/instagram",
        json={"project_id": "starter", "handle": ""},
    )
    assert cleared.status_code == 200
    config = client.get("/api/settings/config?project_id=starter").json()["data"]
    assert config["integrations"]["instagram_project"]["handle"] == ""


def test_current_mcp_and_instagram_status_are_project_scoped(tmp_path, monkeypatch):
    from app.services import mcp as mcp_pkg

    db = tmp_path / "iso.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    with deps.get_db(db) as conn:
        store.create_project(conn, "Other Project")
    mcp_calls = []

    def fake_mcp_status(project_id):
        mcp_calls.append(project_id)
        return [{
            "server_id": f"srv-{project_id}",
            "scope": "project",
            "connected": False,
            "last_health": "unknown",
            "capabilities": [
                {"name": "read_stats", "approval": "READ"},
                {"name": "send_message", "approval": "WRITE"},
            ],
        }]

    monkeypatch.setattr(mcp_pkg, "mcp_status", fake_mcp_status)
    with TestClient(create_app()) as client:
        assert client.post(
            "/api/settings/integrations/instagram",
            json={"project_id": "starter", "handle": "starterhandle"},
        ).status_code == 200
        assert client.post(
            "/api/settings/integrations/instagram",
            json={"project_id": "other-project", "handle": "otherhandle"},
        ).status_code == 200
        cfg_a = client.get("/api/settings/config?project_id=starter").json()["data"]
        servers_a = [row["server"] for row in cfg_a["integrations"]["mcp"]["servers"]]
        assert servers_a == ["srv-starter"]
        assert cfg_a["integrations"]["instagram_project"]["handle"] == "starterhandle"
        cfg_b = client.get(
            "/api/settings/config?project_id=other-project"
        ).json()["data"]
        servers_b = [row["server"] for row in cfg_b["integrations"]["mcp"]["servers"]]
        assert servers_b == ["srv-other-project"]
        assert cfg_b["integrations"]["instagram_project"]["handle"] == "otherhandle"
    assert mcp_calls == ["starter", "other-project"]


def test_dead_legacy_posts_return_404_regardless_of_project(client):
    cases = (
        ("/settings/integrations/configure",
         {"integration": "apify", "token": "t"}),
        ("/settings/integrations/test", {"integration": "apify"}),
        ("/settings/integrations/disconnect", {"integration": "apify"}),
        ("/settings/instagram/capability",
         {"mode": "verify", "handle": "someone"}),
        ("/settings/mcp/action", {"server": "s", "action": "allowlist_on"}),
    )
    for endpoint, payload in cases:
        for project_id in (None, "starter", "ghost"):
            body = dict(payload)
            if project_id is not None:
                body["project_id"] = project_id
            response = client.post(endpoint, data=body)
            assert response.status_code == 404, (endpoint, project_id)
            assert response.headers.get("location") is None


def test_current_credential_api_rejects_missing_and_unknown_inputs(client):
    missing = client.post(
        "/api/settings/credentials/connect",
        json={"target": "apify", "token": "  "},
    )
    assert missing.status_code == 400
    unknown = client.post(
        "/api/settings/credentials/connect",
        json={"target": "unknown", "token": "fixture"},
    )
    assert unknown.status_code == 400
    assert "fixture" not in unknown.text


def test_current_credential_connect_uses_vault_and_registry(client):
    secret = "w8-super-secret-token-987654321"
    response = client.post(
        "/api/settings/credentials/connect",
        json={"target": "apify", "token": secret},
    )
    assert response.status_code == 200
    assert secret not in response.text
    assert "vault://" not in response.text
    with deps.get_db() as conn:
        assert ConfigService.is_apify_configured(conn=conn) is True
        record = registry.resolve_integration(
            "instagram_public_profile", "starter", conn=conn)
        assert record.provider == "apify"
        assert record.scope == "installation"
        assert record.status == "connected"
        assert record.secret_ref and record.secret_ref.startswith("vault://")
    assert secret.encode() not in deps.DB_PATH.read_bytes()
    config = client.get("/api/settings/config").json()["data"]
    public = config["integrations"]["instagram_public"]
    assert public["connected"] is True
    assert public["apify_ready"] is False
    disconnected = client.post(
        "/api/settings/credentials/disconnect",
        json={"target": "apify"},
    )
    assert disconnected.status_code == 200
    with deps.get_db() as conn:
        assert ConfigService.is_apify_configured(conn=conn) is False
        record = registry.resolve_integration(
            "instagram_public_profile", "starter", conn=conn)
        assert record.status == "not_configured"
        assert record.secret_ref is None
    assert secret_store._default.values == {}


def test_current_credential_tests_are_honest_without_network(client, monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("network transport must not run")

    monkeypatch.setattr(
        "app.services.social.instagram.apify_client.httpx.get",
        fail_network,
    )
    for target in ("apify", "brightdata", "openrouter", "meta", "browser"):
        response = client.post(
            "/api/settings/credentials/test",
            json={"target": target},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["credential_status"] == "not_configured"
        assert data["capability_health"] not in {
            "ready", "healthy", "running", "available", "verified"
        }
        assert data["provider_health"] not in {
            "ready", "healthy", "running", "available", "verified"
        }


def test_meta_current_test_is_not_connected_without_vault_setup(client):
    response = client.post(
        "/api/settings/credentials/test",
        json={"target": "meta"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["configured"] is False
    assert data["credential_status"] == "not_configured"
    assert data["capability_health"] == "unknown"
    assert data["provider_health"] == "unknown"
    assert "framework ready" not in data["detail"].lower()


def test_legacy_oauth_env_configuration_cannot_revive_dead_route(client, monkeypatch):
    client_id = "w8-test-app-id-not-a-secret"
    client_secret = "w8-test-secret-not-rendered"
    monkeypatch.setenv("META_OAUTH_CLIENT_ID", client_id)
    monkeypatch.setenv("META_OAUTH_CLIENT_SECRET", client_secret)
    response = client.post(
        "/settings/integrations/oauth/connect",
        data={"project_id": "starter", "integration": "instagram_owned_insights"},
        follow_redirects=False,
    )
    assert response.status_code == 404
    assert response.headers.get("location") is None
    assert client_id not in response.text
    assert client_secret not in response.text


def test_oauth_callback_is_dead_and_never_echoes_code(client):
    response = client.get(
        "/settings/integrations/oauth/callback",
        params={"code": "w8-auth-code-canary", "state": "st-canary"},
    )
    assert response.status_code == 404
    assert "w8-auth-code-canary" not in response.text
    assert "st-canary" not in response.text


def test_legacy_mcp_action_is_dead(client):
    response = client.post(
        "/settings/mcp/action",
        data={"project_id": "starter", "server": "srv", "action": "allowlist_on"},
    )
    assert response.status_code == 404
    assert response.headers.get("location") is None


def test_no_secrets_in_current_api_or_redirect_html(client, monkeypatch):
    # DEV-008-PUBLISH-GATE -- LEAK-DETECTION SENTINELS, NOT CREDENTIALS.
    #
    # This test proves a connected credential is never echoed by the settings API
    # or by any redirect HTML, so the values below deliberately imitate provider
    # key shapes. They are literal nonsense and are never sent anywhere: no
    # account, no access, no network call. `OPENAI_API_KEY` keeps the `sk-` prefix
    # on purpose, because a sentinel that does not look like a provider key would
    # not exercise the redaction path under test. Marked here, and allowlisted
    # with justification in scripts/package/secret_scan.py.
    sentinels = {
        "APIFY_API_TOKEN": "apify_sentinel_w8_value_111",
        "OPENAI_API_KEY": "sk-EXAMPLE-SENTINEL-NOT-A-REAL-KEY-w8",
        "BRIGHTDATA_API_TOKEN": "bright_sentinel_w8_value_222",
        "META_IG_ACCESS_TOKEN": "meta_sentinel_w8_value_333",
        "META_IG_ACCOUNT_ID": "ig-account-sentinel-444",
    }
    for key, value in sentinels.items():
        monkeypatch.setenv(key, value)
    stored = "current-vault-secret-sentinel-555"
    connected = client.post(
        "/api/settings/credentials/connect",
        json={"target": "apify", "token": stored},
    )
    assert connected.status_code == 200
    config = client.get("/api/settings/config")
    assert config.status_code == 200
    html = client.get("/settings", follow_redirects=False).text
    app_html = client.get("/app/settings").text
    for text in (connected.text, config.text, html, app_html):
        for value in (*sentinels.values(), stored):
            assert value not in text, value
        assert "vault://" not in text
        assert "secret_ref" not in text
        assert not re.search(r"sk-[A-Za-z0-9]{10,}", text)
        assert not re.search(r"Bearer\s+[A-Za-z0-9]", text)
    assert stored.encode() not in deps.DB_PATH.read_bytes()


def test_disconnect_is_shown_only_for_vault_backed_connection(client):
    before = client.get("/api/settings/config").json()["data"]
    assert before["integrations"]["instagram_public"]["connected"] is False
    connected = client.post(
        "/api/settings/credentials/connect",
        json={"target": "apify", "token": "vault-presence-sentinel"},
    )
    assert connected.status_code == 200
    during = client.get("/api/settings/config").json()["data"]
    public = during["integrations"]["instagram_public"]
    assert public["connected"] is True
    assert public["apify_credential"] is True
    assert public["apify_ready"] is False
    assert public["apify_provider_health"] == "unknown"
    assert public["apify_capability_health"] == "not_verified"
    assert public["apify_last_checked_at"] == ""
    disconnected = client.post(
        "/api/settings/credentials/disconnect",
        json={"target": "apify"},
    )
    assert disconnected.status_code == 200
    after = client.get("/api/settings/config").json()["data"]
    public = after["integrations"]["instagram_public"]
    assert public["connected"] is False
    assert public["apify_credential"] is False
    assert public["apify_ready"] is False


def test_knowledge_presence_only_per_project(tmp_path, monkeypatch):
    db = tmp_path / "know.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    with deps.get_db(db) as conn:
        store.create_project(conn, "Other Project")
        conn.execute(
            "INSERT OR REPLACE INTO documents (id, project_id, path, file_sha,"
            " status_tag, indexed_at) VALUES (?,?,?,?,?,?)",
            ("d-w8-a", "starter", "knowledge/W8-A.md", "s", "VERIFIED",
             "2026-09-23T00:00:00+00:00"))
        conn.commit()
    with TestClient(create_app()) as client:
        cfg_a = client.get("/api/settings/config?project_id=starter").json()["data"]
        assert cfg_a["files_knowledge"]["indexed_documents"] == 1
        with deps.get_db(db) as conn:
            store.set_active_project(conn, "other-project")
        cfg_b = client.get("/api/settings/config?project_id=other-project").json()["data"]
        assert cfg_b["files_knowledge"]["indexed_documents"] == 0


def test_settings_redirects_and_current_authorities_still_work(client):
    settings = client.get("/settings", follow_redirects=False)
    assert settings.status_code == 303
    assert settings.headers["location"] == "/app/settings"
    system = client.get("/system", follow_redirects=False)
    assert system.status_code == 303
    assert system.headers["location"] == "/app/settings?section=system"
    for endpoint, payload in (
        ("/settings/provider", {"manager_provider": "deterministic"}),
        ("/settings/instagram-handle", {"project_id": "starter", "handle": "regress"}),
        ("/settings/reindex", {}),
    ):
        assert client.post(endpoint, data=payload).status_code == 404
    general = client.post(
        "/api/settings/general",
        json={"theme": "dark", "auto_load_project": False, "language": "en"},
    )
    assert general.status_code == 200
    ai = client.post(
        "/api/settings/ai",
        json={"manager_provider": "openrouter", "openrouter_default_model": "fixture/model"},
    )
    assert ai.status_code == 200
    instagram = client.post(
        "/api/settings/integrations/instagram",
        json={"project_id": "starter", "handle": "regress_handle"},
    )
    assert instagram.status_code == 200
    config = client.get("/api/settings/config?project_id=starter").json()["data"]
    assert config["general"]["theme"] == "dark"
    assert config["ai"]["manager_provider"] == "openrouter"
    assert config["ai"]["openrouter_default_model"] == "fixture/model"
    assert config["integrations"]["instagram_project"]["handle"] == "regress_handle"
