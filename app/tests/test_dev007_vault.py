"""DEV-007 W5: Integration Registry (I-2) + 3-scope credential vault (I-3).

Conventions: tmp_path SQLite via app.database.sqlite.connect, fake
in-memory backend + real DPAPI (Windows-gated) backend, fake vault://
refs, offline, no real secrets, no paid services, pathlib everywhere,
UTF-8, English. Covers: frozen-shape conformance, per-scope round-trips
(store -> ref -> resolve -> revoke), ref-only persistence (SQLite row +
file-byte scans for the secret value), status/log presence-only
invariants, and the w5.sql fragment in sync with the in-code DDL.
"""
import dataclasses
import inspect
import json
import logging
import sys
from pathlib import Path
from app.tests.internal_fixtures import requires_internal_runs

import pytest

from app.database.sqlite import connect
from app.services.credentials import vault
from app.services.credentials.store import DpapiFileBackend, validate_key
from app.services.integrations import registry
from app.services.integrations.registry import (
    IntegrationNotFound,
    IntegrationRecord,
    integration_status,
    register_integration,
    resolve_integration,
    resolve_provider,
)

FRAG = Path(__file__).resolve().parents[2] / "development" / "runs" / "DEV-007" / "migrations" / "w5.sql"
FAKE_SECRET = "FAKESECRET_d4e5f6a7b8c9d0e1f2a3"
REF_RE = vault.REF_RE


class MemoryBackend:
    """Test fake for the OS backend seam — bytes in RAM only."""

    name = "fake"

    def __init__(self):
        self.data = {}

    def put(self, key, plaintext):
        validate_key(key)
        if not isinstance(plaintext, bytes) or not plaintext:
            raise ValueError("plaintext must be non-empty bytes")
        self.data[key] = plaintext

    def get(self, key):
        validate_key(key)
        return self.data.get(key)

    def delete(self, key):
        validate_key(key)
        self.data.pop(key, None)


def _db(tmp_path, name="dev007.db"):
    return connect(tmp_path / name)


def _rec(**over):
    base = dict(
        integration_id="apify",
        capability="instagram_public_profile",
        provider="apify",
        scope="installation",
        status="connected",
        config={"actor_id": "apify/instagram-profile-scraper"},
        secret_ref=None,
        last_health=None,
    )
    base.update(over)
    return IntegrationRecord(**base)


def _scan_rows(conn, needle: str) -> list[str]:
    """Return tables whose rows contain the needle (secret-value leak probe)."""
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    hits = []
    for table in tables:
        for row in conn.execute(f"SELECT * FROM {table}"):
            for value in tuple(row):
                if needle in str(value):
                    hits.append(table)
    return hits


# ---- I-2 conformance: frozen IntegrationRecord + exact call shapes ----

def test_i2_record_is_frozen_with_exact_fields():
    assert dataclasses.is_dataclass(IntegrationRecord)
    assert IntegrationRecord.__dataclass_params__.frozen is True
    names = [f.name for f in dataclasses.fields(IntegrationRecord)]
    assert names == [
        "integration_id", "capability", "provider", "scope",
        "status", "config", "secret_ref", "last_health",
    ]
    for f in dataclasses.fields(IntegrationRecord):
        assert f.default is dataclasses.MISSING
        assert f.default_factory is dataclasses.MISSING
    ann = IntegrationRecord.__annotations__
    assert ann["integration_id"] is str
    assert ann["config"] is dict
    assert ann["secret_ref"] == str | None
    assert ann["last_health"] == str | None
    rec = _rec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        rec.status = "error"


def test_i2_function_signatures_frozen_shapes():
    p = inspect.signature(register_integration).parameters
    assert list(p)[0] == "rec"
    assert all(p[k].kind is inspect.Parameter.KEYWORD_ONLY for k in list(p)[1:])
    rp = inspect.signature(resolve_provider).parameters
    assert list(rp)[:2] == ["capability", "project_id"]
    assert all(rp[k].kind is inspect.Parameter.KEYWORD_ONLY for k in list(rp)[2:])
    st = inspect.signature(integration_status).parameters
    assert list(st)[0] == "project_id"
    assert all(st[k].kind is inspect.Parameter.KEYWORD_ONLY for k in list(st)[1:])
    assert inspect.signature(register_integration).return_annotation is IntegrationRecord
    assert inspect.signature(resolve_provider).return_annotation is str
    assert inspect.signature(integration_status).return_annotation == list[dict]


def test_register_returns_the_record(tmp_path):
    conn = _db(tmp_path)
    rec = _rec()
    assert register_integration(rec, conn=conn) is rec


# ---- I-3 conformance: vault call shapes ----

def test_i3_vault_function_signatures_frozen_shapes():
    s = inspect.signature(vault.store).parameters
    assert list(s)[:3] == ["scope", "label", "value"]
    assert all(s[k].kind is inspect.Parameter.KEYWORD_ONLY for k in list(s)[3:])
    g = inspect.signature(vault.get_secret_ref).parameters
    assert list(g)[:2] == ["scope", "label"]
    assert all(g[k].kind is inspect.Parameter.KEYWORD_ONLY for k in list(g)[2:])
    r = inspect.signature(vault.resolve).parameters
    assert list(r)[0] == "secret_ref"
    assert all(r[k].kind is inspect.Parameter.KEYWORD_ONLY for k in list(r)[1:])
    rv = inspect.signature(vault.revoke).parameters
    assert list(rv)[0] == "secret_ref"
    assert all(rv[k].kind is inspect.Parameter.KEYWORD_ONLY for k in list(rv)[1:])


# ---- Per-scope round-trips: store -> ref -> resolve -> revoke ----

@pytest.mark.parametrize("scope,owner", [
    ("installation", {}),
    ("project", {"project_id": "p1"}),
    ("user", {"user_id": "u1"}),
])
def test_vault_roundtrip_each_scope(tmp_path, scope, owner):
    conn = _db(tmp_path, f"{scope}.db")
    be = MemoryBackend()
    label = f"label_{scope}"
    ref = vault.store(scope, label, FAKE_SECRET, conn=conn, backend=be, **owner)
    assert REF_RE.match(ref), ref
    assert REF_RE.match(ref).group(1) == scope
    assert REF_RE.match(ref).group(2) and len(REF_RE.match(ref).group(2)) == 32
    assert vault.get_secret_ref(scope, label, conn=conn, **owner) == ref
    assert vault.resolve(ref, conn=conn, backend=be) == FAKE_SECRET
    vault.revoke(ref, conn=conn, backend=be)
    with pytest.raises(ValueError):
        vault.resolve(ref, conn=conn, backend=be)
    assert vault.get_secret_ref(scope, label, conn=conn, **owner) is None
    assert be.data == {}


def test_vault_restore_rotates_ref_and_kills_old(tmp_path):
    conn = _db(tmp_path)
    be = MemoryBackend()
    ref1 = vault.store("installation", "rotate", "value-one", conn=conn, backend=be)
    ref2 = vault.store("installation", "rotate", "value-two", conn=conn, backend=be)
    assert ref1 != ref2
    assert vault.get_secret_ref("installation", "rotate", conn=conn) == ref2
    with pytest.raises(ValueError):
        vault.resolve(ref1, conn=conn, backend=be)
    assert vault.resolve(ref2, conn=conn, backend=be) == "value-two"
    assert len(be.data) == 1


def test_vault_revoke_and_error_paths_fail_closed(tmp_path):
    conn = _db(tmp_path)
    be = MemoryBackend()
    ref = vault.store("installation", "killme", FAKE_SECRET, conn=conn, backend=be)
    vault.revoke(ref, conn=conn, backend=be)
    vault.revoke(ref, conn=conn, backend=be)  # idempotent once revoked
    with pytest.raises(KeyError):
        vault.revoke("vault://installation/" + "0" * 32, conn=conn, backend=be)
    with pytest.raises(ValueError):
        vault.revoke("not-a-ref", conn=conn, backend=be)
    with pytest.raises(KeyError) as ei:
        vault.resolve("vault://installation/" + "0" * 32, conn=conn, backend=be)
    assert FAKE_SECRET not in str(ei.value)
    with pytest.raises(ValueError):
        vault.resolve("vault://installation/nothex", conn=conn, backend=be)


def test_vault_ownership_and_input_validation_fail_closed(tmp_path):
    conn = _db(tmp_path)
    be = MemoryBackend()
    with pytest.raises(ValueError):
        vault.store("project", "k", "v", conn=conn, backend=be)
    with pytest.raises(ValueError):
        vault.store("user", "k", "v", conn=conn, backend=be)
    with pytest.raises(ValueError):
        vault.store("installation", "k", "v", project_id="p", conn=conn, backend=be)
    with pytest.raises(ValueError):
        vault.store("installation", "k", "v", user_id="u", conn=conn, backend=be)
    with pytest.raises(ValueError):
        vault.store("user", "k", "v", user_id="u", project_id="p", conn=conn, backend=be)
    with pytest.raises(ValueError):
        vault.store("team", "k", "v", conn=conn, backend=be)
    with pytest.raises(ValueError):
        vault.store("installation", "bad label!", "v", conn=conn, backend=be)
    with pytest.raises(ValueError):
        vault.store("installation", "k", "   ", conn=conn, backend=be)
    with pytest.raises(ValueError):
        vault.get_secret_ref("project", "k", conn=conn)
    with pytest.raises(ValueError):
        vault.get_secret_ref("installation", "k", user_id="u", conn=conn)


def test_vault_same_label_across_projects_stays_distinct(tmp_path):
    conn = _db(tmp_path)
    be = MemoryBackend()
    ref_a = vault.store("project", "shared", "value-a", project_id="alpha",
                        conn=conn, backend=be)
    ref_b = vault.store("project", "shared", "value-b", project_id="beta",
                        conn=conn, backend=be)
    assert ref_a != ref_b
    assert vault.resolve(ref_a, conn=conn, backend=be) == "value-a"
    assert vault.resolve(ref_b, conn=conn, backend=be) == "value-b"
    assert vault.get_secret_ref("project", "shared", project_id="alpha",
                                conn=conn) == ref_a


# ---- Ref-only persistence: scan SQLite rows + file bytes ----

def test_ref_only_persistence_no_secret_value_in_sqlite(tmp_path):
    conn = _db(tmp_path, "refs.db")
    be = MemoryBackend()
    ref = vault.store("installation", "persist", FAKE_SECRET, conn=conn, backend=be)
    register_integration(_rec(secret_ref=ref, capability="openai_chat",
                              provider="openai", integration_id="openai"),
                         conn=conn)
    assert _scan_rows(conn, FAKE_SECRET) == [], "secret value leaked into SQLite rows"
    ref_tables = _scan_rows(conn, ref)
    assert ref_tables, "vault ref must persist (positive control)"
    assert "credentials_refs" in ref_tables
    for path in tmp_path.iterdir():
        if path.is_file():
            assert FAKE_SECRET.encode("utf-8") not in path.read_bytes(), path.name
    rows = vault.export_status(conn=conn)
    assert FAKE_SECRET not in json.dumps(rows)


# ---- No-secret-in-status / log invariant ----

def test_status_and_logs_never_contain_secret(tmp_path, caplog):
    conn = _db(tmp_path)
    be = MemoryBackend()
    with caplog.at_level(logging.DEBUG):
        ref = vault.store("installation", "quiet", FAKE_SECRET, conn=conn, backend=be)
        assert vault.resolve(ref, conn=conn, backend=be) == FAKE_SECRET
        register_integration(_rec(secret_ref=ref), conn=conn)
        cards = integration_status("starter", conn=conn)
        export = vault.export_status(conn=conn)
        try:
            vault.resolve("vault://installation/" + "f" * 32, conn=conn, backend=be)
        except (KeyError, ValueError):
            pass
    assert FAKE_SECRET not in caplog.text
    cards_json = json.dumps(cards, ensure_ascii=False)
    export_json = json.dumps(export, ensure_ascii=False)
    assert FAKE_SECRET not in cards_json
    assert FAKE_SECRET not in export_json
    assert "vault://" not in cards_json, "status cards must be presence-only"
    assert "vault://" not in export_json
    card = cards[0]
    assert card["has_secret"] is True
    assert card["configured"] is True
    assert isinstance(card["last_health"], (str, type(None)))
    for mod in (registry, vault):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "import logging" not in src
        assert "print(" not in src


# ---- Registry: capability vs provider, precedence, fail-closed ----

def test_registry_register_and_resolve_precedence(tmp_path):
    conn = _db(tmp_path)
    register_integration(_rec(provider="browser", integration_id="builtin"),
                         conn=conn)
    register_integration(_rec(provider="apify", scope="project",
                              integration_id="apify"),
                         project_id="alpha", conn=conn)
    register_integration(_rec(provider="meta", scope="project",
                              integration_id="meta"),
                         project_id="beta", conn=conn)
    assert resolve_provider("instagram_public_profile", "alpha", conn=conn) == "apify"
    assert resolve_provider("instagram_public_profile", "beta", conn=conn) == "meta"
    assert resolve_provider("instagram_public_profile", "gamma", conn=conn) == "browser"
    got = resolve_integration("instagram_public_profile", "alpha", conn=conn)
    assert isinstance(got, IntegrationRecord)
    assert got.scope == "project" and got.provider == "apify"


def test_registry_fail_closed_on_missing_project_id(tmp_path):
    conn = _db(tmp_path)
    register_integration(_rec(), conn=conn)
    for bad in (None, "", "   "):
        with pytest.raises(ValueError):
            resolve_provider("instagram_public_profile", bad, conn=conn)
        with pytest.raises(ValueError):
            resolve_integration("instagram_public_profile", bad, conn=conn)
        with pytest.raises(ValueError):
            integration_status(bad, conn=conn)


def test_registry_unknown_capability_raises_not_default(tmp_path):
    conn = _db(tmp_path)
    with pytest.raises(IntegrationNotFound):
        resolve_provider("tiktok_profile", "starter", conn=conn)
    assert integration_status("starter", conn=conn) == []


def test_registry_user_scope_never_auto_resolved(tmp_path):
    conn = _db(tmp_path)
    register_integration(_rec(scope="user", integration_id="openai",
                              provider="openai", capability="openai_chat"),
                         user_id="u1", conn=conn)
    with pytest.raises(IntegrationNotFound):
        resolve_provider("openai_chat", "starter", conn=conn)
    cards = integration_status("starter", conn=conn)
    assert [c["scope"] for c in cards] == ["user"]


def test_registry_prefers_connected_over_not_configured(tmp_path):
    conn = _db(tmp_path)
    register_integration(_rec(integration_id="aaa", status="connected"),
                         conn=conn)
    register_integration(_rec(integration_id="zzz", status="not_configured"),
                         conn=conn)
    conn.execute("UPDATE integrations SET updated_at = ? WHERE integration_id = 'zzz'",
                 ("2999-01-01T00:00:00+00:00",))
    conn.commit()
    assert resolve_provider("instagram_public_profile", "starter", conn=conn) == "apify"
    assert resolve_integration("instagram_public_profile", "starter",
                               conn=conn).integration_id == "aaa"


def test_registry_rejects_secret_bearing_config(tmp_path):
    conn = _db(tmp_path)
    with pytest.raises(ValueError):
        register_integration(_rec(config={"api_token": "abc"}), conn=conn)
    with pytest.raises(ValueError):
        register_integration(_rec(config={"credentials": "abc"}), conn=conn)
    with pytest.raises(ValueError):
        register_integration(_rec(config={"endpoint": "sk-abcdefgh12345678"}),
                             conn=conn)
    with pytest.raises(ValueError):
        register_integration(_rec(config="not-a-dict"), conn=conn)
    ok = _rec(config={"endpoint": "https://example.test/api",
                      "keyword": "instagram"})
    assert register_integration(ok, conn=conn) is ok


def test_registry_validates_enums_and_ids(tmp_path):
    conn = _db(tmp_path)
    with pytest.raises(ValueError):
        register_integration(_rec(provider="weird"), conn=conn)
    with pytest.raises(ValueError):
        register_integration(_rec(scope="team"), conn=conn)
    with pytest.raises(ValueError):
        register_integration(_rec(status="broken"), conn=conn)
    with pytest.raises(ValueError):
        register_integration(_rec(capability="Bad Cap!"), conn=conn)
    with pytest.raises(ValueError):
        register_integration(_rec(integration_id=""), conn=conn)
    with pytest.raises(TypeError):
        register_integration({"integration_id": "x"}, conn=conn)


def test_registry_owner_matrix_fail_closed(tmp_path):
    conn = _db(tmp_path)
    with pytest.raises(ValueError):
        register_integration(_rec(scope="project"), project_id=None, conn=conn)
    with pytest.raises(ValueError):
        register_integration(_rec(scope="project"), project_id="p1",
                             user_id="u1", conn=conn)
    with pytest.raises(ValueError):
        register_integration(_rec(scope="installation"), project_id="p1",
                             conn=conn)
    with pytest.raises(ValueError):
        register_integration(_rec(scope="user"), user_id=None, conn=conn)
    good = _rec(scope="project")
    assert register_integration(good, project_id="p1", conn=conn) is good


def test_registry_secret_ref_integrity_checks(tmp_path):
    conn = _db(tmp_path)
    dangling = "vault://installation/" + "ab" * 16
    with pytest.raises(ValueError):
        register_integration(_rec(secret_ref=dangling), conn=conn)
    ref = vault.store("installation", "ig", FAKE_SECRET, conn=conn,
                      backend=MemoryBackend())
    mismatch = _rec(secret_ref=ref, scope="project")
    with pytest.raises(ValueError):
        register_integration(mismatch, project_id="p1", conn=conn)
    with pytest.raises(ValueError):
        register_integration(_rec(secret_ref="sk-not-a-ref"), conn=conn)
    ok = _rec(secret_ref=ref)
    register_integration(ok, conn=conn)
    vault.revoke(ref, conn=conn, backend=MemoryBackend())
    with pytest.raises(ValueError):
        register_integration(ok, conn=conn)


def test_registry_status_visibility_and_upsert(tmp_path):
    conn = _db(tmp_path)
    register_integration(_rec(), conn=conn)  # installation
    register_integration(_rec(provider="meta", integration_id="meta",
                              scope="project"), project_id="alpha", conn=conn)
    register_integration(_rec(provider="brightdata", integration_id="bd",
                              scope="project"), project_id="beta", conn=conn)
    alpha = {c["integration_id"] for c in integration_status("alpha", conn=conn)}
    beta = {c["integration_id"] for c in integration_status("beta", conn=conn)}
    assert alpha == {"apify", "meta"}
    assert beta == {"apify", "bd"}
    assert all(set(c) == {"integration_id", "capability", "provider", "scope",
                          "status", "configured", "has_secret", "last_health",
                          "config"} for c in integration_status("alpha", conn=conn))
    conn.execute(
        "UPDATE integrations SET created_at = ? WHERE integration_id = 'apify'",
        ("2020-01-01T00:00:00+00:00",))
    conn.commit()
    register_integration(_rec(last_health="ok"), conn=conn)
    row = conn.execute(
        "SELECT created_at, last_health FROM integrations WHERE integration_id = 'apify'"
    ).fetchone()
    assert row["created_at"] == "2020-01-01T00:00:00+00:00"
    assert row["last_health"] == "ok"
    count = conn.execute("SELECT COUNT(*) AS n FROM integrations").fetchone()["n"]
    assert count == 3


# ---- w5.sql fragment: valid, idempotent, in sync with code DDL ----

def _table_info(conn, table):
    return [tuple(r) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _index_names(conn, table):
    return sorted(r["name"] for r in conn.execute(f"PRAGMA index_list({table})").fetchall())


@requires_internal_runs
def test_w5_sql_fragment_valid_idempotent_and_in_sync(tmp_path):
    assert FRAG.exists(), f"missing migration fragment: {FRAG}"
    frag_conn = connect(tmp_path / "frag.db")
    sql = FRAG.read_text(encoding="utf-8")
    frag_conn.executescript(sql)
    frag_conn.executescript(sql)  # idempotent
    code_conn = connect(tmp_path / "code.db")
    registry.ensure_integrations(code_conn)
    vault.ensure_credentials_refs(code_conn)
    registry.ensure_integrations(code_conn)  # idempotent
    vault.ensure_credentials_refs(code_conn)
    for table in ("integrations", "credentials_refs"):
        frag_info = _table_info(frag_conn, table)
        assert frag_info, f"{table} missing from w5.sql"
        assert frag_info == _table_info(code_conn, table), f"{table} DDL drift"
        assert _index_names(frag_conn, table) == _index_names(code_conn, table)
        cols = {r[1]: r for r in frag_info}
        assert cols["project_id"][3] == 1, f"{table}.project_id must be NOT NULL"


# ---- Windows DPAPI backend ----

@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_backend_roundtrip_encrypts_on_disk(tmp_path):
    root = tmp_path / "creds"
    be = DpapiFileBackend(root)
    token = "ab" * 16
    key = f"installation/{token}"
    be.put(key, b"fake-dpapi-plaintext")
    path = root / "installation" / f"{token}.bin"
    assert path.exists()
    assert b"fake-dpapi-plaintext" not in path.read_bytes(), "ciphertext must be encrypted"
    assert be.get(key) == b"fake-dpapi-plaintext"
    be.delete(key)
    assert be.get(key) is None
    assert not path.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_vault_full_roundtrip_no_plaintext_on_disk(tmp_path):
    conn = _db(tmp_path, "dpapi.db")
    be = DpapiFileBackend(tmp_path / "creds")
    ref = vault.store("installation", "dpapi_label", FAKE_SECRET,
                      conn=conn, backend=be)
    assert REF_RE.match(ref)
    assert vault.resolve(ref, conn=conn, backend=be) == FAKE_SECRET
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert FAKE_SECRET.encode("utf-8") not in path.read_bytes(), str(path)
    vault.revoke(ref, conn=conn, backend=be)
    with pytest.raises(ValueError):
        vault.resolve(ref, conn=conn, backend=be)
    leftovers = [p for p in (tmp_path / "creds").rglob("*") if p.is_file()]
    assert leftovers == [], "revoke must delete ciphertext files"


def test_backend_key_validation_blocks_path_tricks(tmp_path):
    be = DpapiFileBackend(tmp_path)
    token = "cd" * 16
    assert be._path(f"installation/{token}").name == f"{token}.bin"
    for bad in (
        f"../installation/{token}",
        "installation/NOTHEX",
        "installation/" + "a" * 31,
        "Installation/" + "a" * 32,
        token,
        "",
    ):
        with pytest.raises(ValueError):
            be._path(bad)
        with pytest.raises(ValueError):
            validate_key(bad)
    assert validate_key(f"user/{token}") == f"user/{token}"
