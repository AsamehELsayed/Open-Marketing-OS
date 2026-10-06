"""Instagram intelligence layer: contract, fallback, isolation, audit-before-screenshots."""
import importlib
import json

import pytest

from app import deps
from app.database.sqlite import connect
from app.services.config_service import ConfigService
from app.services.integrations import registry
from app.services.social.instagram import base as ig_base
from app.services.social.instagram import router as ig_router
secret_store = importlib.import_module("app.services.credentials.store")


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
def vault_apify(tmp_path, monkeypatch):
    token = "fixture-apify-vault-token"
    db = tmp_path / "instagram-vault.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    deps.init_db(db)
    previous_backend = secret_store._default
    secret_store.set_default_backend(MemoryBackend())
    try:
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
        yield token
    finally:
        secret_store.set_default_backend(previous_backend)


def test_normalize_never_invents_fields():
    acct = ig_base.normalize_account("apify", {"username": "x"})
    assert acct["username"] == "x"
    assert acct["followers"] is None and acct["bio"] == "" and acct["verified"] is False
    post = ig_base.normalize_post("apify", {"id": "1"})
    assert post["likes"] is None and post["media_urls"] == [] and post["caption"] == ""


def test_router_unconfigured_never_crashes(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "DB_PATH", tmp_path / "instagram-unconfigured.db")
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    deps.init_db(tmp_path / "instagram-unconfigured.db")
    for k in ("APIFY_API_TOKEN", "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_IG_DATASET",
              "META_IG_ACCESS_TOKEN", "META_IG_ACCOUNT_ID", "IG_BROWSER_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    res = ig_router.audit("somebrand", owned=False)
    assert res["account"] is None and res["posts"] == []
    assert res["evidence"]["status"] == "unavailable"
    assert res["unknowns"], "limitation must be explicit"
    assert all(a["status"] == "skipped_unconfigured" for a in res["attempts"])


def test_router_uses_registry_credential_without_env(vault_apify, monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "environment-must-not-win")
    status = ig_router.provider_status()
    assert status["apify"]["available"] is True
    assert ig_router.PUBLIC_ORDER[0] == "apify"
    assert vault_apify not in json.dumps(status)


def test_apify_success_normalized(vault_apify, monkeypatch):
    import httpx

    tokens = []

    def fake_post(url, params=None, json=None, timeout=None, headers=None, **kwargs):
        tokens.append((headers or {}).get("Authorization"))
        class R:
            def raise_for_status(self): pass

            def json(self):
                if "profile" in str(json) or (json or {}).get("usernames"):
                    return [{"username": "starter", "fullName": "Acme Test Company", "biography": "bio here",
                             "followersCount": 1200, "followsCount": 300, "postsCount": 45,
                             "verified": False, "externalUrl": "https://x.test"}]
                return [{"id": "p1", "url": "https://www.instagram.com/p/p1", "type": "GraphImage",
                         "caption": "hello", "timestamp": "2026-01-01",
                         "likesCount": 10, "commentsCount": 2,
                         "displayUrl": "https://img.test/1.jpg"}]
        return R()

    monkeypatch.setenv("APIFY_API_TOKEN", "environment-must-not-win")
    monkeypatch.setattr(httpx, "post", fake_post)
    res = ig_router.audit("starter", owned=False)
    assert res["evidence"]["status"] == "verified"
    assert res["account"]["followers"] == 1200
    assert len(res["posts"]) == 1 and res["posts"][0]["media_urls"] == ["https://img.test/1.jpg"]
    assert any("highlight" in u.lower() or "stories" in u.lower() for u in res["unknowns"])
    assert tokens and set(tokens) == {f"Bearer {vault_apify}"}
    assert vault_apify not in json.dumps(res)


def test_meta_unavailable_without_token(monkeypatch):
    monkeypatch.delenv("META_IG_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("META_IG_ACCOUNT_ID", raising=False)
    from app.services.social.instagram.meta_provider import MetaProvider
    res = MetaProvider().audit_owned("starter")
    assert res["evidence"]["status"] == "unavailable"


def test_provider_status_exposes_no_secrets(vault_apify, monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "environment-secret-must-not-appear")
    status = ig_router.provider_status()
    blob = json.dumps(status)
    assert vault_apify not in blob
    assert "environment-secret-must-not-appear" not in blob
    assert status["apify"]["available"] is True


def test_audit_tool_uses_live_provider_before_screenshots(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "DB_PATH", tmp_path / "ig.db")
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    for key in ("APIFY_API_TOKEN", "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_IG_DATASET",
                "META_IG_ACCESS_TOKEN", "META_IG_ACCOUNT_ID", "IG_BROWSER_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    from app.database.sqlite import connect
    from app.database.seed import ensure_seed
    from app.services.tools.instagram_tools import t_instagram_audit
    conn = connect(tmp_path / "ig.db")
    ensure_seed(conn)
    out = t_instagram_audit(conn, project_id="starter", root=tmp_path, args={})
    assert out["ok"] is True
    # No handle on project, no username given -> must ASK for handle, not screenshots
    assert out["status"] == "NEEDS_HANDLE" and "handle" in out["note"].lower()
    out2 = t_instagram_audit(conn, project_id="starter", root=tmp_path,
                             args={"username": "competitor_x"})
    assert out2["ok"] is True
    audit = out2["audit"]
    assert "attempts" in audit and len(audit["attempts"]) >= 1
    assert audit["evidence"]["status"] == "unavailable"  # nothing configured here
    conn.close()


def test_detect_instagram_intent_graph_era():
    # Graph-era equivalent: intent detection stays (adapters/tools consume it);
    # the old OpenCode prefetch test was deleted with W1's opencode_provider.
    from app.services.tools.instagram_tools import detect_instagram_intent
    assert detect_instagram_intent("راجع Instagram نجم") == {"username": ""}
    assert detect_instagram_intent("audit @somebrand instagram") == {"username": "somebrand"}
    assert detect_instagram_intent("what about pricing?") is None


def test_audit_project_isolation(tmp_path):
    from app.database.sqlite import connect
    from app.database.seed import ensure_seed
    from app.services import state as store
    from app.services.tools.instagram_tools import _handle_from_project
    conn = connect(tmp_path / "ig2.db")
    ensure_seed(conn)
    store.create_project(conn, "Beta")
    handle_a, _ = _handle_from_project(conn, "starter")
    handle_b, _ = _handle_from_project(conn, "beta")
    assert handle_a == "" and handle_b == ""  # neither leaks the other's handle
    import json as _json
    from app.database import repos
    proj = repos.Projects.get(conn, "starter")
    proj["settings_json"] = _json.dumps({"instagram_handle": "acme_test"})
    repos.Projects.upsert(conn, proj)
    assert _handle_from_project(conn, "starter")[0] == "acme_test"
    assert _handle_from_project(conn, "beta")[0] == ""
    conn.close()
