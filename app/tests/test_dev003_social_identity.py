"""DEV-003 (W5): 12-property offline suite for project-scoped social identity.

Conventions: tmp_path + connect + ensure_seed, monkeypatch env, offline,
no secrets. Interface contracts C1-C7 (plan.md). Direct imports preferred;
W4-not-yet-landed surface is probed at call time and fails honestly
(pre-merge failures EXPECTED — INT confirms green post-merge).
"""
import json
import re
from pathlib import Path

import pytest

from app import deps
from app.database import repos
from app.database.seed import ensure_seed
from app.database.sqlite import connect, get_user_version

SECRET_KEYS = ("APIFY_API_TOKEN", "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_IG_DATASET",
               "META_IG_ACCESS_TOKEN", "META_IG_ACCOUNT_ID", "IG_BROWSER_PROFILE")


def _db(tmp_path, name="dev003.db"):
    conn = connect(tmp_path / name)
    ensure_seed(conn)
    return conn


def _clear_provider_env(monkeypatch):
    for k in SECRET_KEYS:
        monkeypatch.delenv(k, raising=False)


# ---- T1 migration fresh + v5->v6 (W1) ----
def test_dev003_t1_migration_fresh_and_v5_to_v6(tmp_path):
    conn = _db(tmp_path, "t1.db")
    assert get_user_version(conn) == 9
    cols = {r[1] for r in conn.execute("PRAGMA table_info(social_accounts)").fetchall()}
    assert {"id", "project_id", "platform", "handle", "url", "status",
            "source", "evidence_url", "observed_at", "created_at", "updated_at"} <= cols
    idx = [r["name"] for r in conn.execute("PRAGMA index_list(social_accounts)").fetchall()]
    assert "idx_social_project" in idx
    # Simulated v5: drop table, pin version 5, keep a legacy row elsewhere.
    conn.execute("INSERT OR REPLACE INTO campaigns (id, project_id, title, updated_at)"
                 " VALUES (?,?,?,?)", ("camp-legacy", "starter", "legacy", "2026-01-01"))
    conn.commit()
    conn.execute("DROP TABLE social_accounts")
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    from app.database import sqlite as sqlite_mod
    migrate = getattr(sqlite_mod, "_migrate_v5_to_v6", None)
    assert callable(migrate), "W1 _migrate_v5_to_v6 missing (producer not landed)"
    migrate(conn)  # upgrade in place
    migrate(conn)  # idempotent
    cols2 = {r[1] for r in conn.execute("PRAGMA table_info(social_accounts)").fetchall()}
    assert "platform" in cols2 and "status" in cols2
    rows = conn.execute("SELECT id FROM campaigns WHERE id='camp-legacy'").fetchall()
    assert len(rows) == 1, "v5->v6 must preserve existing rows"
    conn.close()


# ---- T2 repo CRUD + validation (W1) ----
def test_dev003_t2_repo_crud_validation(tmp_path):
    conn = _db(tmp_path, "t2.db")
    row = repos.SocialAccounts.set_handle(conn, "starter", "instagram", "@acme_test",
                                          status="LIKELY", source="manual")
    assert row["handle"] == "acme_test"  # leading @ stripped
    got = repos.SocialAccounts.get(conn, row["id"], "starter")
    assert got is not None and got["handle"] == "acme_test"
    listed = repos.SocialAccounts.for_project(conn, "starter")
    assert any(r["platform"] == "instagram" for r in listed)
    with pytest.raises(ValueError):
        repos.SocialAccounts.set_handle(conn, "starter", "myspace", "x")
    with pytest.raises(ValueError):
        repos.SocialAccounts.set_handle(conn, "starter", "instagram", "bad handle!!")
    with pytest.raises(ValueError):
        repos.SocialAccounts.set_handle(conn, "starter", "instagram", "")
    # updated_at advances on re-upsert (pin an old stamp first: _now() has 1s resolution).
    conn.execute("UPDATE social_accounts SET updated_at='2000-01-01T00:00:00+00:00'"
                 " WHERE id=?", (row["id"],))
    conn.commit()
    row2 = repos.SocialAccounts.set_handle(conn, "starter", "instagram", "acme_test2",
                                           status="VERIFIED", source="website",
                                           evidence_url="https://example.com")
    assert row2["updated_at"] != "2000-01-01T00:00:00+00:00"
    assert row2["status"] == "VERIFIED" and row2["source"] == "website"
    conn.close()


# ---- T3 project isolation (W1) ----
def test_dev003_t3_project_isolation(tmp_path):
    conn = _db(tmp_path, "t3.db")
    from app.services import state as store
    store.create_project(conn, "Beta")
    repos.SocialAccounts.set_handle(conn, "starter", "instagram", "acme_test")
    repos.SocialAccounts.set_handle(conn, "beta", "instagram", "beta_official")
    starter_rows = repos.SocialAccounts.for_project(conn, "starter")
    beta_rows = repos.SocialAccounts.for_project(conn, "beta")
    assert {r["handle"] for r in starter_rows} == {"acme_test"}
    assert {r["handle"] for r in beta_rows} == {"beta_official"}
    with pytest.raises(ValueError):
        repos.SocialAccounts.get(conn, "starter:instagram", "beta")
    with pytest.raises(ValueError):
        repos.SocialAccounts.get(conn, "beta:instagram", "starter")
    conn.close()


# ---- T4 discovery VERIFIED on website link (W2) ----
def test_dev003_t4_discovery_verified_on_website_link(tmp_path):
    _ = _db(tmp_path, "t4.db")
    from app.services.social import discovery as disc
    html = ('<html><body><a href="https://instagram.com/acme_test">IG</a>'
            '<a href="https://example.com/about">about</a></body></html>')
    cands = disc.discover_from_website("https://example.com", fetcher=lambda site: html)
    ig = [c for c in cands if c["platform"] == "instagram"]
    assert len(ig) == 1
    assert ig[0]["handle"] == "acme_test"
    assert ig[0]["status"] == "VERIFIED" and ig[0]["source"] == "website"
    assert ig[0]["evidence_url"] == "https://example.com"


# ---- T5 discovery LIKELY without link (W2) ----
def test_dev003_t5_discovery_likely_without_link(tmp_path):
    _ = _db(tmp_path, "t5.db")
    from app.services.social import discovery as disc
    assert disc.classify_status(website_linked=True) == "VERIFIED"
    assert disc.classify_status(website_linked=False) == "LIKELY"
    cands = disc.discover_from_website(
        "https://example.com", fetcher=lambda site: "<html><body>no links</body></html>")
    assert cands == []
    merged = disc.discover_project_socials(
        "starter", "https://example.com",
        [{"project_id": "starter", "platform": "instagram", "handle": "solo_handle",
          "url": "", "status": "LIKELY", "source": "manual", "evidence_url": ""}],
        discovered=[],
    )
    assert merged[0]["status"] == "LIKELY", "bare username must never auto-VERIFY"


# ---- T6 six platforms (W2) ----
def test_dev003_t6_six_platforms(tmp_path):
    _ = _db(tmp_path, "t6.db")
    from app.services.social import platforms as plat
    assert tuple(plat.PLATFORMS) == ("instagram", "tiktok", "x", "facebook", "linkedin", "youtube")
    urls = {
        "instagram": "https://www.instagram.com/somebrand/",
        "tiktok": "https://www.tiktok.com/@somebrand",
        "x": "https://x.com/somebrand",
        "facebook": "https://www.facebook.com/somebrand",
        "linkedin": "https://www.linkedin.com/company/somebrand",
        "youtube": "https://www.youtube.com/@somebrand",
    }
    for platform, url in urls.items():
        got_platform, handle = plat.platform_from_url(url)
        assert got_platform == platform and handle, f"{platform} not detected from {url}"
        canon = plat.canonical_url(platform, handle)
        assert canon, f"empty canonical for {platform}"
        back_platform, back_handle = plat.platform_from_url(canon)
        assert (back_platform, back_handle) == (platform, handle), f"round-trip failed for {platform}"
    assert plat.normalize_handle("instagram", "@abc") == "abc"


# ---- T7 availability honesty (W4/router) ----
def test_dev003_t7_provider_env_cannot_fake_availability(tmp_path, monkeypatch):
    db_path = tmp_path / "t7.db"
    monkeypatch.setattr(deps, "DB_PATH", db_path)
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    conn = _db(tmp_path, "t7.db")
    conn.close()
    _clear_provider_env(monkeypatch)
    from app.services.social.instagram import router as ig_router
    status = ig_router.provider_status()
    assert set(status) >= {"meta", "apify", "brightdata", "browser"}
    assert all(v["available"] is False for v in status.values())
    assert "super-secret-value" not in json.dumps(status)
    monkeypatch.setenv("APIFY_API_TOKEN", "super-secret-value")
    status2 = ig_router.provider_status()
    assert status2["apify"]["available"] is False
    assert status2["meta"]["available"] is False
    assert status2["brightdata"]["available"] is False
    assert status2["browser"]["available"] is False
    assert "super-secret-value" not in json.dumps(status2)


# ---- T8 router priority (W4) ----
def test_dev003_t8_router_priority(tmp_path, monkeypatch):
    db_path = tmp_path / "t8.db"
    monkeypatch.setattr(deps, "DB_PATH", db_path)
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    conn = _db(tmp_path, "t8.db")
    conn.close()
    _clear_provider_env(monkeypatch)
    from app.services.social.instagram import router as ig_router
    assert ig_router.OWNED_ORDER[0] == "meta"
    assert ig_router.PUBLIC_ORDER[0] == "apify"
    res = ig_router.audit("somebrand", owned=False)
    assert res["account"] is None and res["posts"] == []
    assert all(a["status"] == "skipped_unconfigured" for a in res["attempts"])
    assert res.get("unknowns"), "limitation must be explicit"
    assert "source_chain" in res, "C7 source_chain passthrough missing"


# ---- T9 NEEDS_HANDLE before screenshots (W4 tool) ----
def test_dev003_t9_needs_handle_before_screenshots(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    conn = _db(tmp_path, "t9.db")
    from app.services.tools import instagram_tools as ig_tools
    out = ig_tools.t_instagram_audit(conn, project_id="starter", root=tmp_path, args={})
    assert out["ok"] is True and out["status"] == "NEEDS_HANDLE"
    assert "handle" in out["note"].lower()
    assert "screenshot" not in out["note"].lower(), "must not demand screenshots upfront"


# ---- T10 context injection (W4) ----
def test_dev003_t10_context_injection(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    conn = _db(tmp_path, "t10.db")
    from app.services import state as store
    store.create_project(conn, "Beta")
    repos.SocialAccounts.set_handle(conn, "starter", "instagram", "starter_ig",
                                    status="VERIFIED", source="website",
                                    evidence_url="https://starter.example")
    repos.SocialAccounts.set_handle(conn, "starter", "tiktok", "starter_tk", status="LIKELY", source="manual")
    repos.SocialAccounts.set_handle(conn, "beta", "instagram", "beta_ig",
                                    status="VERIFIED", source="website",
                                    evidence_url="https://beta.example")
    from app.services import manager_loop as ml
    bundle = ml.build_project_bundle(conn, "starter", [{"role": "user", "content": "hello"}])
    assert "social_accounts" in bundle, "W4 build_bundle injection not landed"
    assert "social_identity_line" in bundle, "W4 social_identity_line not landed"
    accts = bundle["social_accounts"]
    assert len(accts) <= 12
    assert all("beta_ig" not in json.dumps(a) for a in accts), "foreign-project leak"
    assert accts[0]["status"] == "VERIFIED", "VERIFIED-first ordering required"
    line = bundle["social_identity_line"]
    assert "starter_ig" in line and "beta_ig" not in line
    system, _msgs = ml._compact_context(conn, "starter", "c1", "hello")
    assert "starter_ig" in system and "beta_ig" not in system
    # C6: GREEN get_social_accounts tool registered.
    from app.services.tools import build_default_registry
    reg = build_default_registry()
    assert "get_social_accounts" in reg.names(), "W4 get_social_accounts tool not landed"
    assert any(s["name"] == "get_social_accounts" and s["side_effect"] == "green"
               for s in reg.specs())
    got = reg.execute(conn, project_id="starter", root=str(tmp_path), name="get_social_accounts", args={})
    assert got["ok"] is True and any(a["platform"] == "instagram" for a in got["accounts"])
    assert all("beta_ig" not in json.dumps(a) for a in got["accounts"])
    conn.close()


# ---- T11 screenshots-last ordering (W4) ----
def test_dev003_t11_screenshots_last_ordering(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    conn = _db(tmp_path, "t11.db")
    from app.services.tools.instagram_tools import t_instagram_audit
    res = t_instagram_audit(conn, project_id="starter", root=str(tmp_path), args={"username": "starter_ig"})
    assert res["ok"] is True
    blob = json.dumps(res)
    assert "screenshot" not in blob.lower(), "audit must run before any screenshot ask"
    res2 = t_instagram_audit(conn, project_id="empty_proj", root=str(tmp_path), args={})
    assert res2["ok"] is True
    assert res2["status"] == "NEEDS_HANDLE"
    assert "screenshot" not in json.dumps(res2).lower()
    conn.close()


# ---- T12 no hard-coded Acme Test Company (repo-wide) ----
def test_dev003_t12_no_hardcoded_starter():
    root = Path(__file__).resolve().parents[2]
    pat = re.compile(r"starter solution|acme_test|@acme_test", re.IGNORECASE)
    hits = []
    for sub in ("app/services", "app/routes", "app/templates", "agents"):
        base = root / sub
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix == ".pyc":
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="strict")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pat.search(line):
                    hits.append(f"{path.relative_to(root)}:{i}:{line.strip()[:120]}")
    assert hits == [], f"hard-coded Acme Test Company in product code:\n" + "\n".join(hits)
