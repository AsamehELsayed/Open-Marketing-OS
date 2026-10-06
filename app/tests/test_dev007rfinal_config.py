from __future__ import annotations

import ast
import importlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.database.sqlite import connect
from app.main import create_app
import app.services.config_service as config_module
from app.services.config_service import ConfigService
from app.services.credentials import vault as vault_api
secret_store = importlib.import_module("app.services.credentials.store")
from app.services.integrations import registry
from app.services.social.instagram import registry_bridge
from app.services.social.instagram import capabilities as instagram_capabilities
from app.services.social.instagram.apify_provider import ApifyProvider

ROOT = Path(__file__).resolve().parents[2]

PRODUCT_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "APIFY_API_TOKEN",
    "BRIGHTDATA_API_KEY",
    "BRIGHTDATA_API_TOKEN",
    "BRIGHTDATA_IG_DATASET",
    "META_IG_ACCESS_TOKEN",
    "META_IG_ACCOUNT_ID",
    "META_OAUTH_CLIENT_ID",
    "META_APP_ID",
    "META_OAUTH_CLIENT_SECRET",
    "META_APP_SECRET",
    "MANAGER_PROVIDER",
    "MANAGER_MODE",
    "MANAGER_PROVIDER_PRIORITY",
    "LLM_PROVIDER",
    "OPENAI_MANAGER_MODEL",
    "OMOS_CLOUD_PROVIDER",
)

ENV_ALLOWLIST = {
    # DEV-008: the frozen Windows launcher exports these so every mutable byte
    # lands under %LOCALAPPDATA%\OpenMarketingOS instead of the read-only
    # install folder. They are process-bootstrap paths in the same family as
    # the pre-existing OMOS_DATA_DIR / OMOS_CREDENTIALS_DIR entries, and none
    # of them can carry a provider secret. OMOS_FROZEN is a build marker
    # (cf. COMSPEC under SAFE_PROCESS_BOOTSTRAP); LOCALAPPDATA is supplied by
    # Windows itself.
    "DEV_BOOTSTRAP": frozenset({
        "OMOS_DATA_DIR", "OMOS_CREDENTIALS_DIR", "load_dotenv",
        "OMOS_WORKSPACE_DIR", "OMOS_LOG_DIR", "OMOS_BUNDLE_DIR",
        "OMOS_FROZEN", "LOCALAPPDATA",
    }),
    "CI_TEST": frozenset({
        "CHROMA_ENABLED", "LLM_PROVIDER", "OPENROUTER_TEST_MODEL",
        "W8_SHADOW", "W8_CUTOVER_REQUESTED", "W8_FOUNDER_GATE",
        "OMOS_RAG_DEBUG", "OMOS_RAG_DEBUG_DIR",
    }),
    "ADVANCED_SELF_HOST": frozenset({
        "AI_RUNTIME", "ROUTER_MODE", "LLAMA_ADAPTER", "LLAMA_QUANT",
        "LLAMA_MODEL", "LLAMA_BASE_URL", "LLAMA_TIMEOUT_S",
        "LLAMA_HEALTH_TIMEOUT_S", "LLAMA_N_CTX", "LLAMA_N_GPU_LAYERS",
        "OPENAI_SERVER_WEBSEARCH", "OPENROUTER_CATALOG_TTL_S",
        "OPENAI_MAX_TOOL_ITERS", "OPENAI_TURN_TIMEOUT_S",
        "OPENAI_PER_CALL_TIMEOUT_S", "OPENAI_MAX_CONTEXT_TOKENS",
        "VISION_LOCAL_WEIGHTS", "IG_BROWSER_PROFILE", "META_IG_API_VERSION",
        "APIFY_IG_PROFILE_ACTOR", "APIFY_IG_POST_ACTOR",
    }),
    "SAFE_PROCESS_BOOTSTRAP": frozenset({"MAX_AGENT_CONCURRENCY", "COMSPEC", "<items>"}),
}

FORBIDDEN_ENV_NAMES = frozenset({
    "OPENAI_API_KEY", "OPENROUTER_API_KEY", "APIFY_API_TOKEN",
    "BRIGHTDATA_API_KEY", "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_IG_DATASET",
    "META_IG_ACCESS_TOKEN", "META_IG_ACCOUNT_ID", "META_OAUTH_CLIENT_ID",
    "META_APP_ID", "META_OAUTH_CLIENT_SECRET", "META_APP_SECRET",
    "MANAGER_PROVIDER", "MANAGER_MODE", "MANAGER_PROVIDER_PRIORITY",
    "OPENAI_MANAGER_MODEL", "OMOS_CLOUD_PROVIDER",
})

FORBIDDEN_ENV_SYMBOLS = (
    "OPENAI_API_KEY", "OPENROUTER_API_KEY", "APIFY_API_TOKEN",
    "BRIGHTDATA_API_KEY", "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_IG_DATASET",
    "META_IG_ACCESS_TOKEN", "META_IG_ACCOUNT_ID", "META_OAUTH_CLIENT_ID",
    "META_APP_ID", "META_OAUTH_CLIENT_SECRET", "META_APP_SECRET",
    "MANAGER_PROVIDER", "MANAGER_MODE", "MANAGER_PROVIDER_PRIORITY",
    "AI_RUNTIME", "ROUTER_MODE", "OMOS_CLOUD_PROVIDER", "OPENROUTER_TEST_MODEL",
)

FORBIDDEN_PAYLOAD_TOKENS = (
    "os.environ", "os.getenv", "load_dotenv", ".env", "env_symbol",
    "unimported_env", "migrate-env", "vault://", "Traceback",
)

DYNAMIC_ENV_ALLOWLIST = {
    "app/services/credentials/store.py": "DEV_BOOTSTRAP",
    # DEV-008: user-data / workspace / log path resolution for the frozen build.
    "app/paths.py": "DEV_BOOTSTRAP",
    "app/services/jobs.py": "SAFE_PROCESS_BOOTSTRAP",
    "app/services/vision/local_provider.py": "ADVANCED_SELF_HOST",
    "app/services/llm/local_config.py": "ADVANCED_SELF_HOST",
    "app/services/llm/config.py": "ADVANCED_SELF_HOST",
    # DEV-007R-HOTFIX-3: dev-only RAG debug trace env reads (graphs layer).
    "app/graphs/account_manager_graph.py": "CI_TEST",
    # DEV-008: OMOS_SKILLS_DIR is a library-location override, not a product
    # setting. The drift gate needs it to point the loader at a temp copy and
    # prove the gate actually bites, so it is a path seam in the same family as
    # the OMOS_RAG_DEBUG_DIR entry above. It can never carry a secret and it is
    # not read by any provider, so CI_TEST is the correct category.
    "app/services/skills/registry.py": "CI_TEST",
}


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
def isolated_client(tmp_path, monkeypatch):
    db = tmp_path / "config.db"
    workspace = tmp_path / "workspace"
    vault_dir = tmp_path / "vault"
    workspace.mkdir()
    vault_dir.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", workspace)
    for name in PRODUCT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OMOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(vault_dir))
    monkeypatch.setenv("CHROMA_ENABLED", "0")
    deps.init_db(db)
    previous_backend = secret_store._default
    backend = MemoryBackend()
    secret_store.set_default_backend(backend)
    config_module.clear_all_test_overrides()
    from app.services.llm import openrouter_provider
    openrouter_provider.clear_catalog_cache()
    from app.services.mcp import reset_gateway
    reset_gateway()
    monkeypatch.setattr(instagram_capabilities, "_i7_registered", False, raising=False)
    from app.routes import graph_runtime
    monkeypatch.setattr(graph_runtime, "_GRAPH", None, raising=False)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client, db, backend
    openrouter_provider.clear_catalog_cache()
    reset_gateway()
    secret_store.set_default_backend(previous_backend)
    config_module.clear_all_test_overrides()


def _product_python_files() -> list[Path]:
    paths = [ROOT / "app.py"]
    paths.extend(path for path in (ROOT / "app").rglob("*.py") if "tests" not in path.parts)
    return paths


def _env_reads() -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self, relative_path: str) -> None:
            self.relative_path = relative_path

        def _add(self, node: ast.AST) -> None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                records.append((node.value, self.relative_path))
            else:
                records.append(("<dynamic>", self.relative_path))

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"load_dotenv", "dotenv_values", "find_dotenv"}:
                records.append((func.id, self.relative_path))
            if isinstance(func, ast.Attribute) and func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os":
                if node.args:
                    self._add(node.args[0])
            if isinstance(func, ast.Attribute) and func.attr == "get" and self._is_environ(func.value):
                if node.args:
                    self._add(node.args[0])
            if isinstance(func, ast.Attribute) and func.attr in {"items", "keys", "values"} and self._is_environ(func.value):
                records.append(("<items>", self.relative_path))
            if isinstance(func, ast.Name) and func.id in {
                "_env", "_int", "_priority", "_env_flag", "_flag",
            } and node.args:
                self._add(node.args[0])
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if self._is_environ(node):
                records.append(("<mapping>", self.relative_path))
            self.generic_visit(node)

        @staticmethod
        def _is_environ(node: ast.AST) -> bool:
            return (
                isinstance(node, ast.Attribute)
                and node.attr == "environ"
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
            )

    for path in _product_python_files():
        relative = path.relative_to(ROOT).as_posix()
        Visitor(relative).visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return records


def _record_is_allowed(record: tuple[str, str]) -> bool:
    symbol, path = record
    if symbol in FORBIDDEN_ENV_NAMES:
        return False
    if any(symbol in names for names in ENV_ALLOWLIST.values()):
        return True
    if symbol in {"<mapping>", "<items>", "<dynamic>"}:
        return path in DYNAMIC_ENV_ALLOWLIST
    return False


def _register_installation(conn, *, provider: str, capability: str,
                           secret_ref: str, config: dict | None = None):
    record = registry.IntegrationRecord(
        integration_id=f"{provider}-integration",
        capability=capability,
        provider=provider,
        scope="installation",
        status="connected",
        config=dict(config or {}),
        secret_ref=secret_ref,
        last_health=None,
    )
    return registry.register_integration(record, conn=conn)


def test_product_env_reads_use_only_the_four_explicit_categories():
    records = _env_reads()
    assert records
    forbidden = sorted({symbol for symbol, _ in records if symbol in FORBIDDEN_ENV_NAMES})
    assert forbidden == []
    unclassified = sorted({f"{path}:{symbol}" for symbol, path in records if not _record_is_allowed((symbol, path))})
    assert unclassified == []


def test_normal_product_secret_and_provider_env_reads_are_ignored(isolated_client, monkeypatch):
    client, db, _ = isolated_client
    values = {
        "OPENAI_API_KEY": "env-openai",
        "OPENROUTER_API_KEY": "env-openrouter",
        "APIFY_API_TOKEN": "env-apify",
        "BRIGHTDATA_API_KEY": "env-brightdata",
        "MANAGER_PROVIDER": "openai",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with connect(db) as conn:
        assert ConfigService.is_openai_configured(conn=conn) is False
        assert ConfigService.is_openrouter_configured(conn=conn) is False
        assert ConfigService.is_apify_configured(conn=conn) is False
        assert ConfigService.is_brightdata_configured(conn=conn) is False
        assert ConfigService.get_openai_api_key(conn=conn) == ""
        assert ConfigService.get_openrouter_api_key(conn=conn) == ""
        assert ConfigService.get_apify_token(conn=conn) == ""
    payload = client.get("/api/settings/config")
    assert payload.status_code == 200
    assert "env-openai" not in payload.text
    assert "env-openrouter" not in payload.text
    assert "env-apify" not in payload.text


def test_vault_only_openai_apify_and_openrouter_resolution(isolated_client):
    _, db, backend = isolated_client
    values = {
        "openai": "vault-openai",
        "apify": "vault-apify",
        "openrouter": "vault-openrouter",
    }
    with connect(db) as conn:
        openai_ref = ConfigService.store_openai_api_key(values["openai"], conn=conn)
        apify_ref = ConfigService.store_apify_token(values["apify"], conn=conn)
        openrouter_ref = ConfigService.store_openrouter_key(values["openrouter"], conn=conn)
        assert openai_ref.startswith("vault://installation/")
        assert apify_ref.startswith("vault://installation/")
        assert openrouter_ref.startswith("vault://installation/")
        assert ConfigService.get_openai_api_key(conn=conn) == values["openai"]
        assert ConfigService.get_apify_token(conn=conn) == values["apify"]
        assert ConfigService.get_openrouter_api_key(conn=conn) == values["openrouter"]
    raw = Path(db).read_bytes()
    for value in values.values():
        assert value.encode() not in raw
    assert all(isinstance(value, bytes) for value in backend.values.values())


def test_credential_vault_keeps_ref_only_and_revocable(isolated_client):
    _, db, backend = isolated_client
    token = "vault-revocation-fixture"
    with connect(db) as conn:
        ref = vault_api.store("installation", "fixture_credential", token, conn=conn)
        row = vault_api.get_ref_row(ref, conn=conn)
        assert row["secret_ref"] == ref
        assert row["status"] == "active"
        assert vault_api.resolve(ref, conn=conn) == token
        assert token.encode() not in Path(db).read_bytes()
        vault_api.revoke(ref, conn=conn)
        assert vault_api.get_secret_ref("installation", "fixture_credential", conn=conn) is None
    assert backend.values == {}


def test_apify_registry_bridge_preserves_valid_record_and_vault_ref(isolated_client):
    _, db, _ = isolated_client
    token = "vault-apify-record"
    with connect(db) as conn:
        ref = ConfigService.store_apify_token(token, conn=conn)
        _register_installation(
            conn,
            provider="apify",
            capability=instagram_capabilities.CAP_INSTAGRAM_PUBLIC_PROFILE,
            secret_ref=ref,
            config={"actor_id": "apify/instagram-profile-scraper"},
        )
    view = registry_bridge.resolve(
        instagram_capabilities.CAP_INSTAGRAM_PUBLIC_PROFILE, "starter")
    assert view is not None
    assert view.provider == "apify"
    assert view.scope == "installation"
    assert view.status == "connected"
    assert view.secret_ref == ref
    assert view.config["actor_id"] == "apify/instagram-profile-scraper"
    assert registry_bridge.vault_resolve(view.secret_ref) == token


def test_apify_provider_uses_registry_vault_token_without_env_fallback(isolated_client, monkeypatch):
    _, db, _ = isolated_client
    token = "vault-apify-provider"
    calls: list[dict[str, Any]] = []

    class FakeClient:
        def run_actor_sync(self, actor_id, payload, *, token, timeout):
            calls.append({"actor_id": actor_id, "payload": payload, "token": token})
            if "profile" in actor_id:
                return [{
                    "username": "acme_test",
                    "fullName": "Acme Test Company",
                    "followersCount": 1234,
                    "followsCount": 56,
                    "postsCount": 2,
                    "biography": "Engineering AI Marketing",
                }]
            return [{"id": "post-1", "caption": "Launch week", "timestamp": "2026-01-01T00:00:00Z"}]

        def classify_error(self, exc):
            return "CONFIG_ERROR"

    with connect(db) as conn:
        ref = ConfigService.store_apify_token(token, conn=conn)
        _register_installation(
            conn,
            provider="apify",
            capability=instagram_capabilities.CAP_INSTAGRAM_PUBLIC_PROFILE,
            secret_ref=ref,
            config={"actor_id": "apify/instagram-profile-scraper"},
        )
    monkeypatch.setenv("APIFY_API_TOKEN", "env-apify-fallback")
    monkeypatch.setenv("APIFY_IG_PROFILE_ACTOR", "env/instagram-profile-scraper")
    monkeypatch.setenv("APIFY_IG_POST_ACTOR", "env/instagram-post-scraper")
    provider = ApifyProvider(FakeClient())
    result = provider.audit_public("acme_test", project_id="starter")
    assert result["evidence"]["status"] in {"verified", "partial"}
    assert calls
    assert {call["token"] for call in calls} == {token}
    assert "env-apify-fallback" not in json.dumps(result)


def test_brightdata_key_and_dataset_are_authoritative(isolated_client, monkeypatch):
    _, db, _ = isolated_client
    token = "vault-brightdata"
    calls: list[dict[str, Any]] = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(url, **kwargs):
        calls.append({"method": "post", "url": url, **kwargs})
        return Response({"snapshot_id": "snapshot-1"})

    def fake_get(url, **kwargs):
        calls.append({"method": "get", "url": url, **kwargs})
        return Response([{
            "username": "acme_test",
            "display_name": "Acme Test Company",
            "followers": 1234,
            "following": 56,
            "posts_count": 1,
            "recent_posts": [{"id": "post-1", "caption": "Launch week"}],
        }])

    with connect(db) as conn:
        ref = ConfigService.store_brightdata_key(token, conn=conn)
        _register_installation(
            conn,
            provider="brightdata",
            capability=instagram_capabilities.CAP_INSTAGRAM_PUBLIC_PROFILE,
            secret_ref=ref,
            config={"dataset_id": "dataset-123"},
        )
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "env-brightdata")
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "env-brightdata-token")
    monkeypatch.setenv("BRIGHTDATA_IG_DATASET", "env-dataset")
    monkeypatch.setattr("app.services.social.instagram.brightdata_provider.httpx.post", fake_post)
    monkeypatch.setattr("app.services.social.instagram.brightdata_provider.httpx.get", fake_get)
    from app.services.social.instagram.brightdata_provider import BrightDataProvider
    result = BrightDataProvider().audit_public("acme_test", project_id="starter")
    assert result["evidence"]["status"] in {"verified", "partial"}
    assert calls
    assert calls[0]["headers"]["Authorization"] == f"Bearer {token}"
    assert calls[0]["params"]["dataset_id"] == "dataset-123"
    assert "env-brightdata" not in json.dumps(result)
    assert "env-dataset" not in json.dumps(result)


def test_empty_instagram_handle_clears_without_error(isolated_client):
    client, db, _ = isolated_client
    response = client.post("/api/settings/integrations/instagram", json={"handle": ""})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "updated"
    with connect(db) as conn:
        rows = repos.SocialAccounts.for_project(conn, "starter")
    assert all(row.get("handle") != "" for row in rows)


def test_meta_absent_setup_is_not_ready(isolated_client):
    client, _, _ = isolated_client
    config = client.get("/api/settings/config")
    assert config.status_code == 200
    meta = config.json()["data"]["integrations"]["meta_insights"]
    assert meta["connected"] is False
    assert str(meta.get("status", "")).lower() not in {"ready", "healthy", "running", "available", "verified"}
    response = client.post("/api/settings/credentials/test", json={"target": "meta"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["credential_status"] == "not_configured"
    assert data["capability_health"] not in {"ready", "healthy", "running", "available", "verified"}
    assert "framework ready" not in data.get("detail", "").lower()


def test_openrouter_catalog_presence_is_distinct_from_empty_catalog(isolated_client, monkeypatch):
    client, db, _ = isolated_client
    with connect(db) as conn:
        ConfigService.store_openrouter_key("vault-openrouter-catalog", conn=conn)
    from app.services.llm import openrouter_provider
    openrouter_provider.clear_catalog_cache()
    monkeypatch.setattr(openrouter_provider, "_fetch_models", lambda: [])
    response = client.get("/api/ai/models")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["connected"] is True
    assert data["models"] == []


def test_openrouter_streaming_preserves_order_and_vault_identity(isolated_client, monkeypatch):
    _, db, _ = isolated_client
    token = "vault-openrouter-stream"
    with connect(db) as conn:
        ConfigService.store_openrouter_key(token, conn=conn)
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-openrouter-stream")

    class Delta:
        def __init__(self, text):
            self.content = text

    class Choice:
        def __init__(self, text):
            self.delta = Delta(text)

    class Chunk:
        def __init__(self, text, model="fake/model", usage=None):
            self.choices = [Choice(text)]
            self.model = model
            self.usage = usage

    class Completions:
        def create(self, **payload):
            self.payload = payload
            return iter([
                Chunk("Alpha"),
                Chunk("Beta"),
                Chunk("Gamma", usage={"prompt_tokens": 4, "completion_tokens": 3}),
            ])

    class Client:
        def __init__(self):
            self.chat = type("Chat", (), {})()
            self.chat.completions = Completions()

    from app.services.llm import openrouter_provider
    fake_client = Client()
    events: list[tuple[str, str]] = []

    def open_client(timeout):
        assert openrouter_provider.get_key() == token
        return fake_client

    monkeypatch.setattr(openrouter_provider.OpenRouterProvider, "_open_client", lambda self, timeout: open_client(timeout))
    response = openrouter_provider.OpenRouterProvider(model="fake/model").complete_streaming(
        system="system",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        opts={},
        on_event=lambda kind, text: events.append((kind, text)),
    )
    assert response.text == "AlphaBetaGamma"
    assert [text for kind, text in events if kind == "delta"] == ["Alpha", "AlphaBeta", "AlphaBetaGamma"]
    assert response.usage["actual_model"] == "fake/model"
    assert token not in json.dumps(response.usage)


def test_config_payload_and_react_source_have_no_raw_environment_or_secret_surface(isolated_client):
    client, _, _ = isolated_client
    response = client.get("/api/settings/config")
    assert response.status_code == 200
    text = response.text
    for token in FORBIDDEN_ENV_SYMBOLS:
        assert token not in text, token
    for token in FORBIDDEN_PAYLOAD_TOKENS:
        assert token.lower() not in text.lower(), token
    settings_source = (ROOT / "frontend" / "src" / "routes" / "Settings.tsx").read_text(encoding="utf-8")
    for token in (
        "unimported_env", "migrate-env", "handleMigrateEnv", "Configuration Snapshot",
        "JSON.stringify(config", "OAuth Framework Ready", "Trained on verified campaign frameworks",
    ):
        assert token not in settings_source


def test_built_settings_bundle_has_no_forbidden_user_surface():
    """Scan the *built* React bundle for surfaces that must never ship.

    DEV-008: this asserted `frontend/dist/index.html` exists with no guard.
    `frontend/dist` is a build artifact and is gitignored, so on a fresh clone —
    and in the public repository, where it is never committed — this test failed
    on arrival, making CI red for every contributor. It now skips with an
    explicit reason when the bundle has not been built, and runs whenever it has.
    """
    dist = ROOT / "frontend" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        pytest.skip(
            "frontend/dist is not built. Run: cd frontend && npm ci && npm run build"
        )
    assets = sorted((dist / "assets").glob("*.js"))
    assert assets
    bundle = "\n".join(path.read_text(encoding="utf-8") for path in [index, *assets])
    for token in FORBIDDEN_ENV_SYMBOLS:
        assert token not in bundle
    for token in FORBIDDEN_PAYLOAD_TOKENS:
        assert token.lower() not in bundle.lower()
