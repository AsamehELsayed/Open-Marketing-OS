"""DEV-007 W7: Instagram -> integration architecture (capability != provider).

Covers the frozen I-7 capability conformance, the credential-path table
(capability -> provider -> scope -> where the secret lives), registry
fail-closed resolution, vault-first provider credentials with env bootstrap
fallback only, the workspace-OAuth framework (FRAMEWORK READY / EXTERNAL AUTH
BLOCKER), presence-only status, unchanged base normalization shape, and a
zero-keys guarantee for project persistence.

All secrets here are mocks; real credentials never appear anywhere. The W5
registry/vault APIs are consumed through registry_bridge with deterministic
in-memory fakes (monkeypatched), so this suite is green both before and after
W5 lands in the integration tree.
"""
import json
import re
from pathlib import Path

import pytest

from app.services.social.instagram import (
    CAP_INSTAGRAM_OWNED_INSIGHTS,
    CAP_INSTAGRAM_PUBLIC_PROFILE,
    CREDENTIAL_SCOPE,
    DEFAULT_PROVIDER,
    FROZEN_CAPABILITIES,
    audit_capability,
    capability_status,
    connect_public_profile,
    register_owned_connection,
    resolve_capability,
)
from app.services.social.instagram import registry_bridge as bridge
from app.services.social.instagram import router as ig_router
from app.services.social.instagram import capabilities as caps
from app.services.social.instagram.base import (
    InstagramProvider,
    audit_result,
    normalize_account,
    normalize_post,
    unavailable,
)

PROVIDER_ENV = ("APIFY_API_TOKEN", "APIFY_IG_PROFILE_ACTOR", "APIFY_IG_POST_ACTOR",
                "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_IG_DATASET",
                "META_IG_ACCESS_TOKEN", "META_IG_ACCOUNT_ID", "META_IG_API_VERSION",
                "IG_BROWSER_PROFILE")

MOCK_APIFY_TOKEN = "mock-apify-installation-token"
MOCK_META_TOKEN = "mock-meta-workspace-oauth-token"


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch):
    for key in PROVIDER_ENV:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def fake_registry(monkeypatch):
    """In-memory stand-in for W5 integrations + vault, patched at the
    consume-only bridge boundary (never editing their internals)."""
    state = {"records": {}, "secrets": {}, "register_calls": [], "seq": 0}

    def _view(rec):
        return bridge.IntegrationView(
            integration_id=str(rec.get("integration_id") or ""),
            capability=str(rec.get("capability") or ""),
            provider=str(rec.get("provider") or ""),
            scope=str(rec.get("scope") or ""),
            status=str(rec.get("status") or ""),
            config=dict(rec.get("config") or {}),
            secret_ref=str(rec.get("secret_ref") or ""),
            last_health=rec.get("last_health"),
        )

    def resolve(capability, project_id=None):
        for rec in state["records"].values():
            if rec.get("capability") != capability:
                continue
            if rec.get("scope") == "project":
                cfg_project = (rec.get("config") or {}).get("project_id")
                if not project_id or cfg_project != project_id:
                    continue
            return _view(rec)
        return None

    def register(record):
        if not isinstance(record, dict) or not record.get("capability"):
            return False
        state["register_calls"].append(dict(record))
        key = str(record.get("integration_id") or f"rec-{len(state['records'])}")
        state["records"][key] = dict(record)
        return True

    def store_secret(value, *, scope, name=None):
        if not value or not scope:
            return None
        state["seq"] += 1
        ref = f"vault://{scope}/{name or 'secret'}-{state['seq']}"
        state["secrets"][ref] = value
        return ref

    def vault_resolve(secret_ref):
        if not secret_ref:
            return None
        return state["secrets"].get(secret_ref)

    monkeypatch.setattr(bridge, "resolve", resolve)
    monkeypatch.setattr(bridge, "register", register)
    monkeypatch.setattr(bridge, "store_secret", store_secret)
    monkeypatch.setattr(bridge, "vault_resolve", vault_resolve)
    monkeypatch.setattr(caps, "_i7_registered", False)
    return state


def _fake_apify_post(monkeypatch, captured):
    import httpx

    def fake_post(url, params=None, json=None, timeout=None, headers=None, **kwargs):
        tok = (params or {}).get("token")
        if not tok and headers and "Authorization" in headers:
            auth_val = headers["Authorization"]
            if auth_val.startswith("Bearer "):
                tok = auth_val[len("Bearer "):]
            else:
                tok = auth_val
        captured["token"] = tok
        captured["url"] = url
        captured["headers"] = headers

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                if (json or {}).get("usernames"):
                    return [{"username": "starter_brand", "fullName": "Acme Test Company",
                             "biography": "bio", "followersCount": 1200,
                             "followsCount": 300, "postsCount": 45,
                             "verified": False, "externalUrl": "https://x.test"}]
                return [{"id": "p1", "url": "https://www.instagram.com/p/p1",
                         "type": "GraphImage", "caption": "hello",
                         "timestamp": "2026-01-01", "likesCount": 10,
                         "commentsCount": 2,
                         "displayUrl": "https://img.test/1.jpg"}]
        return R()

    monkeypatch.setattr(httpx, "post", fake_post)


def _fake_meta_get(monkeypatch, captured):
    import httpx

    def fake_get(url, params=None, timeout=None):
        captured["token"] = (params or {}).get("access_token")
        captured.setdefault("urls", []).append(url)

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                if "/insights" in url:
                    return {"data": [{"name": "reach",
                                      "values": [{"value": 5,
                                                  "end_time": "2026-01-01"}]}]}
                if "/media" in url:
                    return {"data": [{"id": "m1", "caption": "hi",
                                      "media_type": "PHOTO",
                                      "media_url": "https://img.test/m1.jpg",
                                      "timestamp": "2026-01-01",
                                      "like_count": 3, "comments_count": 1}]}
                return {"username": "starter_brand", "name": "Acme Test Company", "biography": "b",
                        "followers_count": 10, "follows_count": 2,
                        "media_count": 1, "website": "https://x.test"}
        return R()

    monkeypatch.setattr(httpx, "get", fake_get)


# ---- I-7 capability conformance ----

def test_i7_frozen_capability_surface():
    assert FROZEN_CAPABILITIES == ("instagram_public_profile",
                                   "instagram_owned_insights")
    assert CAP_INSTAGRAM_PUBLIC_PROFILE == "instagram_public_profile"
    assert CAP_INSTAGRAM_OWNED_INSIGHTS == "instagram_owned_insights"
    assert DEFAULT_PROVIDER == {"instagram_public_profile": "apify",
                                "instagram_owned_insights": "meta"}
    assert CREDENTIAL_SCOPE[CAP_INSTAGRAM_PUBLIC_PROFILE] == "installation"
    assert CREDENTIAL_SCOPE[CAP_INSTAGRAM_OWNED_INSIGHTS] == "project"
    # Fallback chain labels are PROVIDERS, never capabilities.
    assert ig_router.PUBLIC_ORDER[0] == "apify"
    assert ig_router.OWNED_ORDER[0] == "meta"
    for vendor in ("apify", "brightdata", "browser", "meta"):
        assert vendor not in FROZEN_CAPABILITIES
    assert "brightdata" in ig_router.PUBLIC_ORDER
    assert "browser" in ig_router.PUBLIC_ORDER


def test_i7_fail_closed_on_unknown_capability():
    assert resolve_capability("not_a_capability", "starter") is None
    assert bridge.resolve("", "starter") is None
    res = audit_capability("vendor:apify", "somebrand")
    assert res["evidence"]["status"] == "unavailable"
    assert res["attempts"] == [] and res["source_chain"] == []
    assert "unknown capability" in res["unknowns"][0]


def test_i7_ensure_registers_records_idempotently(fake_registry):
    assert caps.ensure_i7_registered() is True
    public = [c for c in fake_registry["register_calls"]
              if c["capability"] == CAP_INSTAGRAM_PUBLIC_PROFILE]
    assert len(public) == 1
    assert public[0]["provider"] == "apify"
    assert public[0]["scope"] == "installation"
    assert public[0]["status"] in ("connected", "not_configured")
    assert public[0]["secret_ref"] is None
    assert list(public[0]) == ["integration_id", "capability", "provider",
                               "scope", "status", "config", "secret_ref",
                               "last_health"]
    before = len(fake_registry["register_calls"])
    assert caps.ensure_i7_registered() is True
    assert len(fake_registry["register_calls"]) == before
    assert caps.ensure_i7_registered("starter") is True
    owned = [c for c in fake_registry["register_calls"]
             if c["capability"] == CAP_INSTAGRAM_OWNED_INSIGHTS]
    assert len(owned) == 1
    assert owned[0]["scope"] == "project" and owned[0]["provider"] == "meta"
    assert owned[0]["config"]["project_id"] == "starter"


# ---- credential path: public profile (installation vault) ----

def test_connect_public_profile_stores_installation_vault_ref(fake_registry):
    out = connect_public_profile(MOCK_APIFY_TOKEN)
    assert out["ok"] is True and out["status"] == "connected"
    assert out["secret_ref"].startswith("vault://installation/")
    assert MOCK_APIFY_TOKEN not in json.dumps(out)
    view = resolve_capability(CAP_INSTAGRAM_PUBLIC_PROFILE, None)
    assert view is not None and view.provider == "apify"
    assert view.status == "connected" and view.scope == "installation"
    assert view.secret_ref == out["secret_ref"]
    assert MOCK_APIFY_TOKEN not in json.dumps(view.__dict__)


def test_public_research_uses_vault_token_apify_first(fake_registry,
                                                       monkeypatch):
    connect_public_profile(MOCK_APIFY_TOKEN)
    captured = {}
    _fake_apify_post(monkeypatch=monkeypatch, captured=captured)
    res = ig_router.audit("starter_brand", owned=False)
    assert res["evidence"]["status"] == "verified"
    assert res["provider"] == "Apify"
    assert res["capability"] == CAP_INSTAGRAM_PUBLIC_PROFILE
    assert res["attempts"][0]["provider"] == "apify"
    assert res["integration"]["provider"] == "apify"
    assert res["integration"]["scope"] == "installation"
    assert res["account"]["followers"] == 1200
    assert res["posts"][0]["media_urls"] == ["https://img.test/1.jpg"]
    assert captured["token"] == MOCK_APIFY_TOKEN  # vault secret, not env
    blob = json.dumps(res) + json.dumps(ig_router.provider_status())
    assert MOCK_APIFY_TOKEN not in blob
    assert "vault://" not in json.dumps(ig_router.provider_status())
    assert ig_router.provider_status()["apify"]["available"] is True


def test_public_research_ignores_provider_env_without_vault_registry(fake_registry,
                                                                       monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "mock-env-bootstrap-token")
    captured = {}
    _fake_apify_post(monkeypatch=monkeypatch, captured=captured)
    res = ig_router.audit("somebrand", owned=False)
    assert res["evidence"]["status"] == "unavailable"
    assert captured == {}
    assert all(attempt["status"] == "skipped_unconfigured"
               for attempt in res["attempts"])
    assert "mock-env-bootstrap-token" not in json.dumps(res)


def test_public_research_without_any_token_honest_not_accessible(fake_registry,
                                                                 tmp_path):
    res = ig_router.audit("somebrand", owned=False)
    assert res["account"] is None and res["posts"] == []
    assert res["evidence"]["status"] == "unavailable"
    assert res["capability"] == CAP_INSTAGRAM_PUBLIC_PROFILE
    assert all(a["status"] == "skipped_unconfigured" for a in res["attempts"])
    assert res["unknowns"]
    from app.database.seed import ensure_seed
    from app.database.sqlite import connect
    from app.services.tools.instagram_tools import t_instagram_audit
    conn = connect(tmp_path / "w7-public.db")
    ensure_seed(conn)
    out = t_instagram_audit(conn, project_id="starter", root=tmp_path,
                            args={"username": "somebrand"})
    assert out["ok"] is True and out["status"] == "NOT ACCESSIBLE"
    assert out["provider"] == "Apify"
    conn.close()


# ---- credential path: owned insights (workspace OAuth vault) ----

def test_owned_insights_meta_first_workspace_oauth_vault(fake_registry,
                                                         monkeypatch):
    ref = bridge.store_secret(MOCK_META_TOKEN, scope="project",
                              name="starter/meta_ig")
    assert ref and ref.startswith("vault://project/")
    assert register_owned_connection("starter", ref, config={"ig_account_id": "999"})
    captured = {}
    _fake_meta_get(monkeypatch=monkeypatch, captured=captured)
    res = ig_router.audit("starter_brand", owned=True, project_id="starter")
    assert res["evidence"]["status"] == "verified"
    assert res["provider"] == "Meta"
    assert res["capability"] == CAP_INSTAGRAM_OWNED_INSIGHTS
    assert res["attempts"][0]["provider"] == "meta"
    assert res["integration"]["provider"] == "meta"
    assert res["integration"]["scope"] == "project"
    assert res["account"]["account_type"] == "professional"
    assert res["posts"][0]["id"] == "m1"
    assert res["insights"]["series"]
    assert captured["token"] == MOCK_META_TOKEN  # vault workspace token
    blob = json.dumps(res) + json.dumps(ig_router.provider_status())
    assert MOCK_META_TOKEN not in blob
    assert MOCK_META_TOKEN not in json.dumps(capability_status("starter"))


def test_owned_unavailable_without_workspace_credential(fake_registry):
    res = ig_router.audit("starter_brand", owned=True, project_id="starter")
    assert res["evidence"]["status"] == "unavailable"
    assert res["capability"] == CAP_INSTAGRAM_OWNED_INSIGHTS
    assert res["attempts"][0]["provider"] == "meta"
    assert all(a["status"] == "skipped_unconfigured" for a in res["attempts"])


# ---- OAuth framework (FRAMEWORK READY / EXTERNAL AUTH BLOCKER) ----

def test_oauth_framework_ready_with_mock_transport(fake_registry):
    from app.services.social.instagram.oauth import (
        META_AUTHORIZE_URL,
        build_authorize_url,
        exchange_code,
        new_state,
    )
    state = new_state()
    assert len(state) >= 20
    url = build_authorize_url("meta", client_id="mock-app-id",
                               redirect_uri="https://localhost/cb", state=state)
    assert url.startswith(META_AUTHORIZE_URL)
    assert "mock-app-id" in url and f"state={state}" in url and "scope=" in url

    blocked = exchange_code("meta", client_id="", client_secret="",
                            code="c", redirect_uri="r", project_id="starter")
    assert blocked["ok"] is False
    assert blocked["reason"] == "missing_client_credentials"
    assert blocked["framework_ready"] is True

    out = exchange_code(
        "meta", client_id="mock-app-id", client_secret="mock-app-secret",
        code="mock-auth-code", redirect_uri="https://localhost/cb",
        project_id="starter",
        transport=lambda u, p: {"access_token": MOCK_META_TOKEN,
                                "scope": "instagram_basic"})
    assert out["ok"] is True and out["status"] == "connected"
    assert out["capability"] == CAP_INSTAGRAM_OWNED_INSIGHTS
    assert out["secret_ref"].startswith("vault://")
    assert "access_token" not in out
    assert MOCK_META_TOKEN not in json.dumps(out)
    view = resolve_capability(CAP_INSTAGRAM_OWNED_INSIGHTS, "starter")
    assert view is not None and view.status == "connected"
    assert view.provider == "meta" and view.secret_ref == out["secret_ref"]

    unsupported = exchange_code("tiktok", client_id="a", client_secret="b",
                                code="c", redirect_uri="d", project_id="starter")
    assert unsupported["ok"] is False
    assert unsupported["reason"] == "unsupported_provider"


# ---- registry resolution / fallback chain rules ----

def test_registry_connected_provider_fronts_chain(fake_registry):
    fake_registry["records"].clear()
    fake_registry["register_calls"].clear()
    assert bridge.register({
        "integration_id": "installation:custom-public",
        "capability": CAP_INSTAGRAM_PUBLIC_PROFILE,
        "provider": "brightdata", "scope": "installation",
        "status": "connected", "config": {}, "secret_ref": None,
        "last_health": None,
    })
    res = ig_router.audit("somebrand", owned=False)
    assert res["attempts"][0]["provider"] == "brightdata"
    assert ig_router.PUBLIC_ORDER[0] == "apify"  # frozen default preserved


def test_registry_unknown_vendor_never_runs(fake_registry):
    fake_registry["records"].clear()
    fake_registry["register_calls"].clear()
    caps._i7_registered = False
    assert bridge.register({
        "integration_id": "installation:scrapedo-public",
        "capability": CAP_INSTAGRAM_PUBLIC_PROFILE,
        "provider": "scrapedo", "scope": "installation",
        "status": "connected", "config": {}, "secret_ref": None,
        "last_health": None,
    })
    res = ig_router.audit("somebrand", owned=False)
    tried = [a["provider"] for a in res["attempts"]]
    assert "scrapedo" not in tried
    assert tried[0] == "apify"
    assert any("not available in this build" in u for u in res["unknowns"])


def test_registry_not_connected_does_not_reorder(fake_registry):
    # ensure() registers a not_configured default; frozen order must hold.
    res = ig_router.audit("somebrand", owned=False)
    tried = [a["provider"] for a in res["attempts"]]
    assert tried == list(ig_router.PUBLIC_ORDER)
    res_owned = ig_router.audit("somebrand", owned=True, project_id="starter")
    tried_owned = [a["provider"] for a in res_owned["attempts"]]
    assert tried_owned == list(ig_router.OWNED_ORDER)


# ---- status surfaces: presence-only, never secrets ----

def test_capability_status_presence_only(fake_registry):
    connect_public_profile(MOCK_APIFY_TOKEN)
    status = capability_status()
    pub = status[CAP_INSTAGRAM_PUBLIC_PROFILE]
    assert pub["provider"] == "apify"
    assert pub["configured"] is True
    assert pub["credential_scope"] == "installation"
    assert pub["registry_status"] == "connected"
    owned = status[CAP_INSTAGRAM_OWNED_INSIGHTS]
    assert owned["provider"] == "meta"
    assert owned["configured"] is False
    assert owned["credential_scope"] == "project"
    blob = json.dumps(status)
    assert MOCK_APIFY_TOKEN not in blob
    assert "vault://" not in blob


# ---- project persistence: zero keys ----

def _walk_suspicious_keys(obj, hits, path="$"):
    if isinstance(obj, dict):
        for key, val in obj.items():
            if re.search(r"(?i)(token|secret|credential|password|api_?key|vault)",
                         str(key)):
                hits.append(f"{path}.{key}")
            _walk_suspicious_keys(val, hits, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            _walk_suspicious_keys(val, hits, f"{path}[{i}]")
    elif isinstance(obj, str) and obj.strip().startswith("{"):
        try:
            _walk_suspicious_keys(json.loads(obj), hits, f"{path}(json)")
        except Exception:
            pass


def test_project_persistence_contains_zero_keys(fake_registry, tmp_path,
                                                 monkeypatch):
    from app.database import repos
    from app.database.seed import ensure_seed
    from app.database.sqlite import connect
    conn = connect(tmp_path / "w7-zero.db")
    ensure_seed(conn)
    repos.SocialAccounts.set_handle(conn, "starter", "instagram", "acme_test",
                                    status="VERIFIED", source="website",
                                    evidence_url="https://example.com")
    proj = repos.Projects.get(conn, "starter")
    proj["settings_json"] = json.dumps({"instagram_handle": "acme_test"})
    repos.Projects.upsert(conn, proj)

    connect_public_profile(MOCK_APIFY_TOKEN)
    ref = bridge.store_secret(MOCK_META_TOKEN, scope="project",
                              name="starter/meta_ig")
    assert register_owned_connection("starter", ref, config={"ig_account_id": "999"})

    captured = {}
    _fake_apify_post(monkeypatch=monkeypatch, captured=captured)
    assert ig_router.audit("acme_test", owned=False,
                           project_id="starter")["evidence"]["status"] == "verified"
    captured = {}
    _fake_meta_get(monkeypatch=monkeypatch, captured=captured)
    assert ig_router.audit("acme_test", owned=True,
                           project_id="starter")["evidence"]["status"] == "verified"

    projects = repos.Projects.list(conn)
    socials = repos.SocialAccounts.for_project(conn, "starter")
    blob = json.dumps({"projects": projects, "social": socials})
    for secret in (MOCK_APIFY_TOKEN, MOCK_META_TOKEN,
                   "mock-env-bootstrap-token"):
        assert secret not in blob
    assert "vault://" not in blob
    assert re.search(
        r"(?i)(access_token|client_secret|api[_-]?fy[_-]?api[_-]?token"
        r"|bearer\s+\S{8,}|EAA[A-Za-z0-9]{15,})", blob) is None
    suspicious: list = []
    _walk_suspicious_keys({"projects": projects, "social": socials}, suspicious)
    assert suspicious == [], f"token-like keys persisted: {suspicious}"
    # SocialAccounts rows: handle/status identity only, shape unchanged.
    row = next(r for r in socials if r["platform"] == "instagram")
    assert set(row) <= {"id", "project_id", "platform", "handle", "url",
                        "status", "source", "evidence_url", "observed_at",
                        "created_at", "updated_at"}
    assert row["handle"] == "acme_test" and row["status"] == "VERIFIED"
    conn.close()


# ---- base normalization contract (unchanged shape) ----

def test_base_normalization_shape_unchanged():
    acct = normalize_account("apify", {"username": "x"})
    assert list(acct) == ["username", "display_name", "bio", "followers",
                          "following", "media_count", "verified", "website",
                          "account_type", "collected_at", "source"]
    post = normalize_post("apify", {"id": "1"})
    assert list(post) == ["id", "url", "type", "caption", "published_at",
                          "likes", "comments", "views", "media_urls", "source"]
    res = audit_result("apify", "verified")
    assert list(res) == ["account", "posts", "insights", "evidence", "unknowns"]
    assert list(res["evidence"]) == ["source", "status", "collected_at"]
    un = unavailable("apify", "why")
    assert un["evidence"]["status"] == "unavailable"
    assert un["unknowns"] == ["why"]
    for method in ("available", "audit_owned", "audit_public"):
        assert hasattr(InstagramProvider, method)


# ---- graph/nodes: capability strings only, zero vendor details ----

def test_graph_nodes_reference_no_vendor_impl(tmp_path, monkeypatch):
    from app import deps
    monkeypatch.setattr(deps, "DB_PATH", tmp_path / "instagram-graph.db")
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    deps.init_db(tmp_path / "instagram-graph.db")
    for key in ("APIFY_API_TOKEN", "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_IG_DATASET",
                "META_IG_ACCESS_TOKEN", "META_IG_ACCOUNT_ID", "IG_BROWSER_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    graphs_dir = Path(__file__).resolve().parents[1] / "graphs"
    assert graphs_dir.is_dir()
    vendor_tokens = ("apify", "brightdata", "graph.facebook.com",
                     "APIFY_API_TOKEN", "META_IG_ACCESS_TOKEN",
                     "access_token", "run-sync-get-dataset-items",
                     "vault://")
    hits = []
    for path in sorted(graphs_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for token in vendor_tokens:
            if token.lower() in lowered:
                hits.append(f"{path.name}:{token}")
    assert hits == [], f"graph layer leaked vendor details: {hits}"
    import app.services.social.instagram as ig
    assert ig.FROZEN_CAPABILITIES == FROZEN_CAPABILITIES
    assert callable(ig.audit_capability)
    res = ig.audit_capability(CAP_INSTAGRAM_PUBLIC_PROFILE, "somebrand")
    assert res["capability"] == CAP_INSTAGRAM_PUBLIC_PROFILE
    assert res["evidence"]["status"] == "unavailable"  # nothing configured
