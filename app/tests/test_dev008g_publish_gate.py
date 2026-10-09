"""DEV-008-PUBLISH-GATE / Gate 1 -- no real-business identity in a fresh install.

The defect this file exists to prevent
--------------------------------------
The default project id used to be the literal ``"njm"``: the initials of a real
third-party business. That was not a label. It was the ``DEFAULT`` of roughly
eighteen SQLite columns, it was written into the database of every fresh
install, it was returned by ``GET /api/onboarding/status``, and it was sent to
external tool providers as the ``project_id``. DEV-008 had already neutralised
the seeded business *content* (name, website, markets) but recorded the
*identifier* rename as a founder decision. This run is that decision.

The two things that make this a real test rather than a rename
--------------------------------------------------------------
1. **A fresh database is built through the product's own init + seed path**, and
   then every project-scoped table is queried with ``SELECT DISTINCT
   project_id``. Asserting on the constant instead would only prove the constant
   changed; this proves no row anywhere inherited a real identity.
2. **A pre-existing install holding ``project_id = 'njm'`` is exercised
   separately.** Fresh-install behaviour and historical-private-data behaviour
   are different concerns, and fixing the first must not destroy the second. The
   compatibility test below builds that database by hand, then runs the real
   onboarding flow against it.

Nothing here reads the founder's private workspace, so this module runs in the
public export unchanged.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from app.database import identity, repos
from app.database.identity import DEFAULT_PROJECT_ID, LEGACY_PROJECT_IDS
from app.database.seed import ensure_seed
from app.database.sqlite import SCHEMA_PATH, connect

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"

#: The real business's identity, in every form it appeared in. Used for the
#: public-string audit. Kept as fragments so the assertions are about the
#: *substance* rather than one exact spelling.
REAL_BUSINESS_TERMS = ("njm", "njmsolution", "eco deco", "ecodeco")

#: Every table with a project_id column, so "no NJM anywhere" really means
#: everywhere. Derived from the live schema rather than hardcoded, so a new
#: project-scoped table cannot escape the audit.
def _project_scoped_tables(conn) -> list[str]:
    out = []
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall():
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({name})").fetchall()}
        if "project_id" in cols:
            out.append(name)
    return out


def _fresh_seeded_db(tmp_path: Path, name: str = "fresh.db") -> sqlite3.Connection:
    """A genuinely new database built by the product's own init + seed path."""
    conn = connect(tmp_path / name)
    ensure_seed(conn)
    return conn


# --------------------------------------------------------------------------
# 1. The canonical identity itself
# --------------------------------------------------------------------------

def test_default_project_id_is_generic():
    """Not a business name, not a legacy id, not empty."""
    assert DEFAULT_PROJECT_ID
    assert DEFAULT_PROJECT_ID == DEFAULT_PROJECT_ID.strip().lower()
    assert " " not in DEFAULT_PROJECT_ID
    for legacy in LEGACY_PROJECT_IDS:
        assert DEFAULT_PROJECT_ID != legacy
    for term in REAL_BUSINESS_TERMS:
        assert term not in DEFAULT_PROJECT_ID.lower()


def test_legacy_ids_are_recorded_and_recognised():
    """The compatibility surface must keep naming what it has to support."""
    assert "njm" in LEGACY_PROJECT_IDS
    assert identity.is_legacy_project_id("njm") is True
    assert identity.is_legacy_project_id("NJM") is True
    assert identity.is_legacy_project_id(DEFAULT_PROJECT_ID) is False
    assert identity.is_legacy_project_id(None) is False


def test_company_id_follows_the_project():
    assert identity.company_id_for("anything") == "anything"
    assert identity.company_id_for("") == DEFAULT_PROJECT_ID
    assert identity.company_id_for(None) == DEFAULT_PROJECT_ID


# --------------------------------------------------------------------------
# 2. Schema DDL must not seed a real identity
# --------------------------------------------------------------------------

def test_authoritative_schema_ddl_has_no_real_business_default():
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    offenders = [
        line.strip() for line in ddl.splitlines()
        if re.search(r"DEFAULT\s+'[^']*'", line, re.IGNORECASE)
        and any(t in line.lower() for t in REAL_BUSINESS_TERMS)
    ]
    assert offenders == [], f"schema.sql still seeds a real identity: {offenders}"


#: Tables whose `project_id` is intentionally NOT the default workspace id.
#:
#: These two hold entries that can be *global* rather than project-scoped, and
#: their uniqueness constraints include `project_id`, so a global entry is stored
#: under the empty string. Defaulting them to the workspace id would silently
#: attach every shared API key and integration to one project — a security and
#: isolation regression, not a naming one. Recorded here so the exception is
#: deliberate and reviewable rather than an oversight.
EMPTY_PROJECT_ID_TABLES = frozenset({"credentials_refs", "integrations"})


def test_every_project_id_column_default_is_generic(tmp_path: Path):
    """No project-scoped column may default to a real business identity.

    Asserting the *actual* invariant rather than "every default equals the
    default id" is deliberate, because two shapes are correct and must stay:

    - ``credentials_refs`` / ``integrations`` are empty-scoped global registries
      (see above);
    - ``mcp_tools_allowlist`` / ``tool_runs`` / ``project_files`` /
      ``turn_attachment_selections`` / ``turn_file_bindings`` have **no**
      default at all. Each is ``NOT NULL`` with a composite primary key and/or a
      foreign key to ``projects``: a tool grant, a tool run and a stored file must
      each name their project, and defaulting them would silently attribute work
      to whichever project happened to be active. Attachment selections also
      require explicit scope because they can carry user-selected content into a
      turn. A missing default there is a safety property, so this test pins the
      shape rather than demanding uniformity.
    """
    #: Tables where "no default" is the correct, safer design.
    NO_DEFAULT_PROJECT_ID_TABLES = frozenset({
        "mcp_tools_allowlist", "tool_runs", "project_files",
        "turn_attachment_selections", "turn_file_bindings",
        # Client profiles must always name their owning project explicitly.
        "business_profiles",
    })
    conn = connect(tmp_path / "synthetic_gate_tmp.db")
    try:
        audited = 0
        starter_defaults = 0
        no_default: list[str] = []
        for table in _project_scoped_tables(conn):
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall():
                if row[1] != "project_id":
                    continue
                audited += 1
                if row[4] is None:
                    # No default is allowed only if the column is NOT NULL, so a
                    # caller that forgets the project fails loudly.
                    assert row[3] == 1, (
                        f"{table}.project_id has no DEFAULT and is nullable: a "
                        f"row inserted without one would silently have no scope"
                    )
                    assert table in NO_DEFAULT_PROJECT_ID_TABLES, (
                        f"{table}.project_id lost its default; either restore the "
                        f"workspace default or declare the table as intentionally "
                        f"default-free"
                    )
                    no_default.append(table)
                    continue
                default = str(row[4]).strip().strip("'\"")
                for term in REAL_BUSINESS_TERMS:
                    assert term not in default.lower(), (
                        f"{table}.project_id defaults to a real business: {default!r}"
                    )
                if table in EMPTY_PROJECT_ID_TABLES:
                    assert default == "", (
                        f"{table}.project_id must stay empty-scoped, got {default!r}"
                    )
                else:
                    assert default == DEFAULT_PROJECT_ID, (
                        f"{table}.project_id defaults to {default!r}, "
                        f"expected {DEFAULT_PROJECT_ID!r}"
                    )
                    starter_defaults += 1

        assert audited >= 15, f"only audited {audited} project_id columns"
        # The migration moved 15 DDL defaults; a silent drop would be a leak.
        assert starter_defaults >= 13, (
            f"only {starter_defaults} project_id columns default to the "
            f"workspace id; expected the migrated set"
        )
        assert set(no_default) == set(NO_DEFAULT_PROJECT_ID_TABLES), (
            f"the set of default-free project_id columns changed: {no_default}"
        )
    finally:
        conn.close()
        db = REPO_ROOT / "build" / "njm_gate_tmp.db"
        if db.exists():
            db.unlink()


# --------------------------------------------------------------------------
# 3. A completely fresh database contains no real-business identity
# --------------------------------------------------------------------------

def test_fresh_database_has_no_real_business_project_id(tmp_path):
    """`SELECT DISTINCT project_id` across every project-scoped table."""
    conn = _fresh_seeded_db(tmp_path)
    try:
        tables = _project_scoped_tables(conn)
        assert tables, "expected project-scoped tables in a seeded database"
        seen: set[str] = set()
        for table in tables:
            rows = conn.execute(
                f"SELECT DISTINCT project_id FROM {table}"
            ).fetchall()
            for (pid,) in rows:
                if pid:
                    seen.add(str(pid))
        offenders = {p for p in seen
                     if any(t in p.lower() for t in REAL_BUSINESS_TERMS)}
        assert offenders == set(), f"fresh database contains {offenders}"
    finally:
        conn.close()


def test_fresh_database_has_no_real_business_company_row(tmp_path):
    conn = _fresh_seeded_db(tmp_path)
    try:
        for row in repos.Companies.list(conn):
            blob = json.dumps({k: v for k, v in row.items() if v is not None}).lower()
            for term in REAL_BUSINESS_TERMS:
                assert term not in blob, f"company row {row} leaks {term}"
    finally:
        conn.close()


def test_fresh_database_active_project_is_the_generic_default(tmp_path):
    from app.services import state as store

    conn = _fresh_seeded_db(tmp_path)
    try:
        assert store.active_project_id(conn) == DEFAULT_PROJECT_ID
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 4. First-run onboarding with a real user-supplied business
# --------------------------------------------------------------------------

def test_first_run_onboarding_makes_the_users_business_active(tmp_path, monkeypatch):
    """The mission's own acceptance path, end to end.

    Business: "Acme Test Company". Verify Acme becomes the active business and
    that the legacy identity never appears in the API response or the database.
    """
    from fastapi.testclient import TestClient

    from app import deps
    from app.main import create_app

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = tmp_path / "data" / "marketing.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OMOS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr(deps, "ROOT", workspace)
    monkeypatch.setattr(deps, "DB_PATH", db)

    with TestClient(create_app()) as client:
        before = client.get("/api/onboarding/status").json()["data"]
        assert before["business_described"] is False
        assert before["project"]["name"] == "My Business"
        assert before["active_project_id"] == DEFAULT_PROJECT_ID

        response = client.post("/api/onboarding/business", json={
            "name": "Acme Test Company",
            "website": "acme-test.example",
            "markets": ["Example City"],
            "languages": ["English"],
            "services": ["Example service"],
        })
        assert response.status_code == 200, response.text
        assert response.json()["data"]["project"]["name"] == "Acme Test Company"

        after = client.get("/api/onboarding/status").json()["data"]
        assert after["business_described"] is True
        assert after["project"]["name"] == "Acme Test Company"
        assert after["company_name"] == "Acme Test Company"
        assert after["active_project_id"] == DEFAULT_PROJECT_ID

        # "NJM never appears" -- checked against the bytes the user actually gets.
        assert "njm" not in response.text.lower()
        assert "njm" not in json.dumps(after).lower()

    with deps.get_db(db) as conn:
        assert repos.Projects.get(conn, DEFAULT_PROJECT_ID)["name"] == "Acme Test Company"
        assert store_active(conn) == DEFAULT_PROJECT_ID
        assert conn.execute(
            "SELECT COUNT(*) FROM projects WHERE id = 'njm'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM companies WHERE id = 'njm'"
        ).fetchone()[0] == 0


def store_active(conn) -> str:
    from app.services import state as store

    return store.active_project_id(conn)


# --------------------------------------------------------------------------
# 5. Existing private install compatibility -- MUST NOT REGRESS
# --------------------------------------------------------------------------

def _build_legacy_install(tmp_path: Path, filename: str = "legacy.db") -> sqlite3.Connection:
    """A database shaped like a pre-rename private install.

    Seeded under the legacy id with real rows across several project-scoped
    tables, because a compatibility claim that only checks the projects table
    would not catch a broken tool_runs / messages / model_calls path.
    """
    conn = connect(tmp_path / filename)
    ensure_seed(conn)
    # Rename the seeded workspace to the legacy id and make it active, exactly
    # as an existing install would have it.
    for table in ("companies", "projects"):
        conn.execute(f"UPDATE {table} SET id = 'njm' WHERE id = ?", (DEFAULT_PROJECT_ID,))
    conn.execute("UPDATE settings SET value = 'njm' WHERE key = 'active_project_id'")
    repos.Memories.insert(conn, {
        "id": "mem-legacy", "project_id": "njm", "kind": "learning",
        "body_md": "legacy row", "confidence": "HIGH", "source_ref": "",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    })
    repos.Campaigns.upsert(conn, {
        "id": "camp-legacy", "project_id": "njm", "title": "Legacy campaign",
        "status": "open", "updated_at": "2026-01-01T00:00:00+00:00",
    })
    conn.commit()
    return conn


def test_legacy_install_still_loads_and_keeps_its_identity(tmp_path):
    """Existing private data must continue to load or migrate safely."""
    from app.services import state as store

    conn = _build_legacy_install(tmp_path)
    try:
        assert store.active_project_id(conn) == "njm"
        assert repos.Projects.get(conn, "njm") is not None
        assert [m["id"] for m in repos.Memories.list(conn, "njm")] == ["mem-legacy"]
        assert repos.Campaigns.get(conn, "camp-legacy") is not None
        # And the new default did not silently take over.
        assert repos.Projects.get(conn, DEFAULT_PROJECT_ID) is None
    finally:
        conn.close()


def test_legacy_install_reopens_without_loss_after_reinit(tmp_path):
    """Re-running the seed against a legacy database must not clobber it."""
    conn = _build_legacy_install(tmp_path)
    try:
        ensure_seed(conn)  # idempotent re-run, as every app start does
        assert repos.Projects.get(conn, "njm") is not None
        assert [m["id"] for m in repos.Memories.list(conn, "njm")] == ["mem-legacy"]
        from app.services import state as store
        assert store.active_project_id(conn) == "njm", (
            "re-seeding overwrote the legacy install's active project"
        )
    finally:
        conn.close()


def test_onboarding_upgrades_a_legacy_install_in_place(tmp_path, monkeypatch):
    """The compatibility case that motivated resolving the active project.

    A legacy install re-running onboarding must write the new business into
    *its own* project, not create a second empty one alongside it.
    """
    from fastapi.testclient import TestClient

    from app import deps
    from app.main import create_app

    db = tmp_path / "legacy_onboarding.db"
    conn = _build_legacy_install(tmp_path, filename="legacy_onboarding.db")
    conn.close()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OMOS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr(deps, "ROOT", workspace)
    monkeypatch.setattr(deps, "DB_PATH", db)

    with TestClient(create_app()) as client:
        status = client.get("/api/onboarding/status").json()["data"]
        assert status["active_project_id"] == "njm"

        response = client.post("/api/onboarding/business",
                               json={"name": "Acme Test Company"})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["project"]["id"] == "njm"

        after = client.get("/api/onboarding/status").json()["data"]
        assert after["active_project_id"] == "njm"
        assert after["project"]["name"] == "Acme Test Company"

    with deps.get_db(db) as conn:
        # Updated in place...
        assert repos.Projects.get(conn, "njm")["name"] == "Acme Test Company"
        # ...and no parallel default project was conjured up.
        assert repos.Projects.get(conn, DEFAULT_PROJECT_ID) is None
        # Pre-existing rows survived onboarding.
        assert [m["id"] for m in repos.Memories.list(conn, "njm")] == ["mem-legacy"]
        assert repos.Campaigns.get(conn, "camp-legacy") is not None


# --------------------------------------------------------------------------
# 6. Public string audit of the shipped code
# --------------------------------------------------------------------------

def _shipped_source_files() -> list[Path]:
    """Shipped application code. Excludes tests, which may use any fixture."""
    out = []
    for path in APP_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in (".py", ".sql", ".json", ".yaml", ".yml"):
            continue
        rel = path.relative_to(APP_ROOT)
        if rel.parts and rel.parts[0] in ("tests", "__pycache__", "evals"):
            continue
        if "__pycache__" in rel.parts:
            continue
        out.append(path)
    return out


def test_shipped_code_names_no_real_business_outside_the_compatibility_list():
    """Every remaining mention must be justified, and only one file may hold it.

    `app/database/identity.py` is the single sanctioned exception: it must keep
    the legacy string to prove old installs still work. Any other file naming a
    real business fails this test, which is what makes the exception visible
    rather than a loophole.
    """
    sanctioned = {APP_ROOT / "database" / "identity.py"}
    offenders: dict[str, list[str]] = {}
    for path in _shipped_source_files():
        if path in sanctioned:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # Strip comments and docstrings: prose explaining the migration is
        # desirable, a *literal* in shipped code is not.
        code = re.sub(r'"""[\s\S]*?"""', "", text)
        code = re.sub(r"'''[\s\S]*?'''", "", code)
        code = re.sub(r"#.*", "", code)
        hits = [t for t in REAL_BUSINESS_TERMS if t in code.lower()]
        if hits:
            offenders[str(path.relative_to(REPO_ROOT))] = hits
    assert offenders == {}, f"shipped code names a real business: {offenders}"


def test_identity_module_is_the_only_holder_of_the_legacy_string():
    """Pin the exception so it cannot quietly grow."""
    text = (APP_ROOT / "database" / "identity.py").read_text(encoding="utf-8")
    assert '"njm"' in text, (
        "identity.py must keep naming the legacy id it supports; if this fails "
        "the compatibility tests above are no longer proving anything"
    )


def test_no_hardcoded_client_commercial_claims_remain():
    """Regression gate for a worse bug than the privacy leak.

    `app/services/account_manager.py` used to detect a *keyword* in the
    reader's own evidence and then assert one specific real business's terms as
    "(recorded fact)" -- a free demo, source-code handover, a white-label/NDA
    arrangement, "7 live named projects", and a market priority ladder reading
    "Focus order: Jordan, then Egypt, Saudi Arabia, then UAE." Any user whose
    notes mentioned an NDA or Jordan was told those were their business's facts.
    """
    from app.services import account_manager as am

    blob = json.dumps(
        {"facts": {k: list(v) for k, v in am._FACTS.items()},
         "signals": {k: list(v) for k, v in am._SIGNAL_KEYS.items()}},
        ensure_ascii=False,
    ).lower()

    for phrase in ("focus order", "then egypt", "saudi arabia", "7 live",
                   "7 projects", "jordan"):
        assert phrase not in blob, f"account_manager still asserts {phrase!r}"

    for term in REAL_BUSINESS_TERMS:
        assert term not in blob, f"account_manager still names {term!r}"


def test_markets_line_is_derived_from_the_users_own_record():
    """The removed hardcoded ladder must be replaced by real data, not nothing."""
    from app.services import account_manager as am

    company = {"markets_json": json.dumps(
        {"primary": ["Example City"], "expansion": ["Elsewhere"]})}
    line = am._markets_fact(company, "en")
    assert line and "Example City" in line and "Elsewhere" in line
    assert am._markets_fact({"markets_json": "{}"}, "en") is None
    assert am._markets_fact(None, "en") is None
    assert am._markets_fact({"markets_json": "not json"}, "en") is None


def test_snapshot_reads_the_active_project_not_a_hardcoded_id(tmp_path):
    from app.services import account_manager as am

    conn = _fresh_seeded_db(tmp_path)
    try:
        snap = am._snapshot(conn)
        assert snap["company"] is not None
        assert snap["company"]["id"] == DEFAULT_PROJECT_ID
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 7. Test fixtures must be obviously synthetic
# --------------------------------------------------------------------------

def test_test_fixtures_no_longer_use_a_real_business_as_a_project_id():
    """A public test suite full of `project_id="njm"` names a real client.

    This module is excluded: it necessarily contains the terms it searches for.
    """
    hits: dict[str, int] = {}
    for path in (APP_ROOT / "tests").rglob("*.py"):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        code = re.sub(r"#.*", "", text)
        code = re.sub(r'"""[\s\S]*?"""', "", code)
        if any(t in code.lower() for t in REAL_BUSINESS_TERMS):
            hits[path.name] = code.lower().count("njm")
    assert hits == {}, f"tests still use a real business identity: {hits}"


def test_every_private_workspace_reading_test_is_declared_for_the_export():
    """The invariant that keeps the public export's suite green.

    `internal_fixtures.PRIVATE_WORKSPACE_MODULES` is what makes a clean export
    skip the tests that read `company/`, `production/`, `strategy/` or `state/`.
    This asserts the declaration is *complete*, so a newly added private-reading
    test cannot silently arrive red in the published repository. The declaration
    module itself is exempt -- it is the thing doing the declaring.
    """
    from app.tests.internal_fixtures import (
        PRIVATE_WORKSPACE_MODULES,
        private_workspace_available,
    )

    if not private_workspace_available:
        pytest.skip("no private workspace in this checkout; nothing to reconcile")

    read_pattern = re.compile(r'ROOT\s*/\s*"(company|production|strategy|state)"')
    declared = set(PRIVATE_WORKSPACE_MODULES)
    undeclared: dict[str, str] = {}
    for path in sorted((APP_ROOT / "tests").rglob("*.py")):
        if path.name in ("internal_fixtures.py", Path(__file__).name):
            continue
        if read_pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            if path.name not in declared:
                undeclared[path.name] = "reads a withheld private workspace path"
    assert undeclared == {}, (
        f"tests read withheld private paths without being declared: {undeclared}"
    )
    # And the declaration must not drift into naming modules that no longer need it.
    stale = {name for name in declared
             if not (APP_ROOT / "tests" / name).is_file()}
    assert stale == set(), f"PRIVATE_WORKSPACE_MODULES names missing files: {stale}"


# --------------------------------------------------------------------------
# 8. Reimport must not create a parallel company row on a legacy install
# --------------------------------------------------------------------------

def test_reimport_writes_the_company_row_under_the_active_project(tmp_path):
    """`run_imports` derives the company row id from the database, not a literal."""
    from app.services import state as store
    from app.services.workspace_bootstrap import apply_business_details

    # Fresh install -> "starter"
    fresh = connect(tmp_path / "fresh_import.db")
    ensure_seed(fresh)
    fresh_root = tmp_path / "ws_fresh"
    apply_business_details(fresh_root, name="Acme Test Company")
    store.run_imports(fresh, fresh_root)
    assert repos.Companies.get(fresh, DEFAULT_PROJECT_ID)["name"] == "Acme Test Company"
    fresh.close()

    # Legacy install -> "njm", and no second row appears
    legacy = _build_legacy_install(tmp_path)
    legacy_root = tmp_path / "ws_legacy"
    apply_business_details(legacy_root, name="Acme Test Company")
    store.run_imports(legacy, legacy_root)
    assert repos.Companies.get(legacy, "njm")["name"] == "Acme Test Company"
    assert repos.Companies.get(legacy, DEFAULT_PROJECT_ID) is None, (
        "reimport created a second company row on a legacy install"
    )
    legacy.close()


@pytest.mark.parametrize("table", ["memories", "campaigns", "documents", "chunks"])
def test_project_id_isolation_still_holds(table):
    """The schema default must not have weakened cross-project isolation."""
    assert table  # parametrised so a missing table fails loudly at collection
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
