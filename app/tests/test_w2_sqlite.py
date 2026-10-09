"""W2 acceptance: schema version, tables, idempotent seed (D1/D2)."""
from app.database import repos
from app.database.seed import ensure_seed
from app.database.sqlite import SCHEMA_VERSION, connect, get_user_version

EXPECTED_TABLES = {
    "companies", "conversations", "messages", "campaigns", "tasks",
    "approvals", "prospects", "experiments", "measurements", "learnings",
    "settings", "events", "background_jobs", "documents", "chunks", "chunks_fts",
}


def test_schema_version_and_tables(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert get_user_version(conn) == SCHEMA_VERSION
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert EXPECTED_TABLES <= tables
    assert {"projects", "memories"} <= tables
    assert {"turns", "execution_events"} <= tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    assert "client_message_id" in cols
    assert "turn_id" in cols
    assert {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()} >= {
        "model_provider", "model_id"}
    assert {r[1] for r in conn.execute("PRAGMA table_info(turns)").fetchall()} >= {
        "model_provider", "model_id"}
    conn.close()


def test_seed_idempotent(tmp_path):
    conn = connect(tmp_path / "t.db")
    ensure_seed(conn)
    ensure_seed(conn)
    companies = repos.Companies.list(conn)
    assert len(companies) == 1
    assert companies[0]["id"] == "starter"
    conn.close()


def test_crud_roundtrip(tmp_path):
    conn = connect(tmp_path / "t.db")
    repos.Campaigns.upsert(conn, {"id": "opp-01", "title": "Demo", "updated_at": "2026-09-16T00:00:00+00:00"})
    assert repos.Campaigns.get(conn, "opp-01")["title"] == "Demo"
    repos.Campaigns.set_status(conn, "opp-01", "approved")
    assert repos.Campaigns.get(conn, "opp-01")["status"] == "approved"
    conn.close()
