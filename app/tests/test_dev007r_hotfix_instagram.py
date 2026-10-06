import importlib
import json

import pytest
import httpx

from app import deps
import app.services.config_service as config_module
from app.services.config_service import ConfigService
from app.services.integrations import registry
from app.services.social.instagram.base import (
    AUTH_ERROR,
    CONFIG_ERROR,
    PROVIDER_NOT_FOUND,
    RATE_LIMITED,
    TIMEOUT,
    UNKNOWN,
    InstagramPublicProfile,
    normalize_handle,
)
from app.services.social.instagram.apify_client import (
    ApifyClient,
    canonical_actor_id,
    sanitize_secrets,
)
from app.services.social.instagram.apify_provider import ApifyProvider
from app.services.social.instagram import router as ig_router
from app.services.social.instagram import registry_bridge as bridge
from app.services.social.instagram import capabilities as caps
from app.services.tools.instagram_tools import detect_instagram_intent

secret_store = importlib.import_module("app.services.credentials.store")


class MemoryBackend:
    name = "test-memory"

    def __init__(self):
        self.values = {}

    def put(self, key, plaintext):
        self.values[key] = bytes(plaintext)

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture()
def isolated_runtime(tmp_path, monkeypatch):
    db = tmp_path / "instagram-runtime.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    for name in ("APIFY_API_TOKEN", "APIFY_IG_PROFILE_ACTOR", "APIFY_IG_POST_ACTOR", "AI_RUNTIME", "LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    deps.init_db(db)
    previous = secret_store._default
    secret_store.set_default_backend(MemoryBackend())
    monkeypatch.setattr(caps, "_i7_registered", False, raising=False)
    config_module.clear_all_test_overrides()
    yield db
    secret_store.set_default_backend(previous)
    config_module.clear_all_test_overrides()


def _connect_public(db, token):
    from app.database.sqlite import connect
    with connect(db) as conn:
        ref = ConfigService.store_apify_token(token, conn=conn)
        registry.register_integration(
            registry.IntegrationRecord(
                integration_id="apify-integration",
                capability="instagram_public_profile",
                provider="apify",
                scope="installation",
                status="connected",
                config={"actor_id": "apify/instagram-profile-scraper"},
                secret_ref=ref,
                last_health=None,
            ),
            conn=conn,
        )
    return ref
# 1. ACTOR ID MODEL & URL CONSTRUCTION TESTS
# ==============================================================================

def test_canonical_actor_id_uses_tilde():
    """Verify owner/name format uses '~' and never '/'."""
    assert canonical_actor_id("apify/instagram-profile-scraper") == "apify~instagram-profile-scraper"
    assert canonical_actor_id("apify~instagram-profile-scraper") == "apify~instagram-profile-scraper"
    assert canonical_actor_id("dSCLg0C3YEZ83HzYX") == "dSCLg0C3YEZ83HzYX"
    assert canonical_actor_id(" custom/scraper-name/ ") == "custom~scraper-name"


def test_apify_urls_never_construct_slash_acts():
    """Verify centralized client builds /v2/actors/owner~name endpoints, never /v2/acts/owner/name/."""
    client = ApifyClient()
    run_url = client.actor_run_sync_url("apify/instagram-profile-scraper")
    assert "/v2/actors/apify~instagram-profile-scraper/run-sync-get-dataset-items" in run_url
    assert "/acts/apify/instagram-profile-scraper" not in run_url
    assert run_url.startswith("https://api.apify.com/v2/actors/")

    actor_url = client.actor_url("apify/instagram-profile-scraper")
    assert actor_url == "https://api.apify.com/v2/actors/apify~instagram-profile-scraper"


# ==============================================================================
# 2. AUTH & SECRET REDACTION TESTS
# ==============================================================================

def test_headers_use_bearer_auth():
    client = ApifyClient()
    headers = client._headers("secret_apify_token_12345")
    assert headers["Authorization"] == "Bearer secret_apify_token_12345"
    assert headers["Content-Type"] == "application/json"


def test_sanitize_secrets_redacts_tokens():
    raw = "Failed with token apify_api_ABC123xyz456 and Bearer secret999 at https://api.apify.com?token=tok777"
    sanitized = sanitize_secrets(raw)
    assert "ABC123xyz456" not in sanitized
    assert "secret999" not in sanitized
    assert "tok777" not in sanitized
    assert "[REDACTED]" in sanitized


# ==============================================================================
# 3. REAL BOUNDED CONNECTION TEST
# ==============================================================================

def test_bounded_connection_test_success(monkeypatch):
    client = ApifyClient()

    def fake_get(url, headers=None, timeout=None, **kwargs):
        class R:
            status_code = 200
            def json(self):
                if "users/me" in url:
                    return {"data": {"id": "u1", "username": "tester"}}
                return {"data": {"id": "act1", "name": "instagram-profile-scraper"}}
        return R()

    monkeypatch.setattr(httpx, "get", fake_get)
    res = client.test_connection("valid-token", actor_id="apify/instagram-profile-scraper")
    assert res["credential_status"] == "connected"
    assert res["provider_health"] == "ok"
    assert res["capability_health"] == "verified"
    assert res["actor_id"] == "apify~instagram-profile-scraper"
    assert res["last_error_code"] is None


def test_bounded_connection_test_invalid_token(monkeypatch):
    client = ApifyClient()

    def fake_get(url, headers=None, timeout=None, **kwargs):
        class R:
            status_code = 401
            def json(self):
                return {"error": {"message": "Invalid token"}}
        return R()

    monkeypatch.setattr(httpx, "get", fake_get)
    res = client.test_connection("bad-token")
    assert res["credential_status"] == "auth_error"
    assert res["capability_health"] == "error"
    assert res["last_error_code"] == AUTH_ERROR


def test_bounded_connection_test_actor_not_found_not_collapsing_to_unconnected(monkeypatch):
    client = ApifyClient()

    def fake_get(url, headers=None, timeout=None, **kwargs):
        class R:
            def __init__(self, code):
                self.status_code = code
            def json(self):
                return {}
        if "users/me" in url:
            return R(200)
        return R(404)

    monkeypatch.setattr(httpx, "get", fake_get)
    res = client.test_connection("valid-token", actor_id="nonexistent/actor")
    # Credential is valid, but Actor is broken -> must NOT claim "not connected"
    assert res["credential_status"] == "connected"
    assert res["capability_health"] == "config_error"
    assert res["last_error_code"] == PROVIDER_NOT_FOUND


# ==============================================================================
# 6. INPUT HANDLE NORMALIZATION TESTS
# ==============================================================================

@pytest.mark.parametrize("raw,expected", [
    ("@acme_test", "acme_test"),
    ("acme_test", "acme_test"),
    ("https://instagram.com/acme_test/", "acme_test"),
    ("https://www.instagram.com/acme_test?igsh=123", "acme_test"),
    ("  @acme_test  ", "acme_test"),
    ("http://instagram.com/starter.tech/#section", "starter.tech"),
])
def test_normalize_handle(raw, expected):
    assert normalize_handle(raw) == expected


def test_detect_instagram_intent_handles():
    assert detect_instagram_intent("scrap starter instagram account") == {"username": "starter"}
    assert detect_instagram_intent("scrape @acme_test on instagram") == {"username": "acme_test"}
    assert detect_instagram_intent("check instagram for starter_app") == {"username": "starter_app"}
    assert detect_instagram_intent("راجع Instagram نجم") == {"username": ""}
    assert detect_instagram_intent("what are the marketing ideas?") is None


# ==============================================================================
# 7. OUTPUT NORMALIZATION TESTS
# ==============================================================================

def test_instagram_public_profile_normalization():
    prof = InstagramPublicProfile(
        handle="acme_test",
        display_name="Acme Test Company",
        bio="Engineering AI Marketing",
        followers=5400,
        following=120,
        posts_count=42,
        verified=True,
        business_category="Software",
        external_url="https://starter.example",
        recent_posts=[{"id": "p1", "caption": "Hello world"}],
        source_provider="apify",
        cost_type="metered",
    )
    d = prof.to_dict()
    assert d["handle"] == "acme_test"
    assert d["display_name"] == "Acme Test Company"
    assert d["followers"] == 5400
    assert d["verified"] is True
    assert d["source_provider"] == "apify"
    assert d["cost_type"] == "metered"
    assert len(d["recent_posts"]) == 1


# ==============================================================================
# 8 & 11. PROVIDER FALLBACK CHAIN & ERROR CLASSIFICATION TESTS
# ==============================================================================

def test_fallback_chain_continues_on_apify_config_error(monkeypatch, isolated_runtime):
    """When Apify returns 404 (wrong actor ID), chain classifies it as FAILED_CONFIG
    and does not claim 'providers aren't connected'."""
    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        class R:
            status_code = 404
            def raise_for_status(self):
                req = httpx.Request("POST", url)
                resp = httpx.Response(404, request=req)
                raise httpx.HTTPStatusError("Actor not found", request=req, response=resp)
        return R()

    _connect_public(isolated_runtime, "valid-token")
    monkeypatch.setattr(httpx, "post", fake_post)

    res = ig_router.audit("starter", owned=False)
    assert res["evidence"]["status"] == "unavailable"
    assert res["has_configured_provider"] is True
    apify_attempt = [a for a in res["attempts"] if a["provider"] == "apify"][0]
    assert apify_attempt["status"] == "FAILED_CONFIG"
    assert apify_attempt["error_code"] == PROVIDER_NOT_FOUND


# ==============================================================================
# 9 & 10. NO FALSE RAG FALLBACK & NO DEBUG SLOP IN CHAT
# ==============================================================================

def test_no_false_rag_fallback_on_scrape_failure(tmp_path, monkeypatch, isolated_runtime):
    """Explicit scrape failure must return clear error with action items,
    never substituting unrelated RAG website knowledge silently."""
    from app.database.sqlite import connect
    from app.database.seed import ensure_seed
    from app.database import repos
    from app.services.account_manager import handle_turn

    db = isolated_runtime
    _connect_public(db, "valid-token")
    conn = connect(db)
    ensure_seed(conn)
    proj = repos.Projects.get(conn, "starter")
    if not proj:
        proj = {"id": "starter", "name": "Acme Test Company"}
    proj["settings_json"] = json.dumps({"instagram_handle": "starter"})
    repos.Projects.upsert(conn, proj)

    def fake_post(url, **kwargs):
        class R:
            def raise_for_status(self):
                req = httpx.Request("POST", url)
                resp = httpx.Response(404, request=req)
                raise httpx.HTTPStatusError("404 Not Found", request=req, response=resp)
        return R()
    monkeypatch.setattr(httpx, "post", fake_post)

    res = handle_turn(conn, tmp_path, "scrap starter instagram account")
    reply = res["reply_md"]

    # 1. Must NOT silently claim website knowledge is the instagram data
    assert "Offers a free homepage" not in reply
    # 2. Must state clear outcome and actions
    assert "Instagram research couldn't run" in reply
    assert "[Retry]" in reply
    assert "[Test Instagram Connection]" in reply
    # 3. Must NOT show raw internal debug tags in user chat
    assert "STATUS: NOT ACCESSIBLE" not in reply
    assert "CONFIDENCE: HIGH" not in reply
    # 4. Diagnostics available under collapsible details without secrets
    assert "<details><summary>Show details</summary>" in reply
    assert "valid-token" not in reply
    conn.close()
