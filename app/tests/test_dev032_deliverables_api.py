import io
import json
import sqlite3
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import deps
from app.database.sqlite import connect, get_user_version
from app.routes.campaign_deliverables import router
from app.services import campaign_deliverables as service


def _seed(db):
    conn = connect(db)
    conn.execute(
        "INSERT INTO projects(id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("northstar", "Northstar Coffee", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO projects(id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("harbor", "Harbor Studio", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO campaigns(id,project_id,title,updated_at) VALUES(?,?,?,?)",
        ("northstar-campaign", "northstar", "Launch", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO campaigns(id,project_id,title,updated_at) VALUES(?,?,?,?)",
        ("harbor-campaign", "harbor", "Spring", "2026-01-01"),
    )
    conn.commit()
    conn.close()


def _client(tmp_path, monkeypatch):
    db = tmp_path / "deliverables.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    _seed(db)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), db


def _base(project="northstar", campaign="northstar-campaign"):
    return f"/api/projects/{project}/campaigns/{campaign}/deliverables"


def _payload(title="Weekday launch", body="## Post\nTry a weekday latte."):
    return {
        "type": "social_post",
        "title": title,
        "platform": "Instagram",
        "content_md": body,
    }


def _deliverable_ddl(conn):
    names = (
        "campaign_deliverable_batches",
        "campaign_deliverables",
        "campaign_deliverable_revisions",
        "idx_campaign_deliverables_scope",
        "idx_campaign_deliverable_revisions_scope",
    )
    placeholders = ",".join("?" for _ in names)
    rows = conn.execute(
        f"SELECT type,name,sql FROM sqlite_master WHERE name IN ({placeholders}) "
        "ORDER BY type,name",
        names,
    ).fetchall()
    return tuple(
        (row["type"], row["name"], " ".join((row["sql"] or "").split()).lower())
        for row in rows
    )


def test_v13_to_v14_migration_is_additive_and_repeated(tmp_path, monkeypatch):
    db = tmp_path / "legacy-v13.db"
    from app.database import sqlite as sqlite_module

    # Build a real v13 schema fixture from the canonical schema, excluding the
    # DEV-032 DDL. SCHEMA_PATH stays pinned to this fixture during connect(), so
    # schema.sql cannot create v14 tables ahead of the migration under test.
    canonical_schema_path = sqlite_module.SCHEMA_PATH
    canonical_schema = canonical_schema_path.read_text(encoding="utf-8")
    deliverables_marker = (
        "-- DEV-032: campaign-scoped Markdown deliverables and immutable snapshots."
    )
    start = canonical_schema.index(deliverables_marker)
    end = canonical_schema.index("CREATE TABLE IF NOT EXISTS tasks (", start)
    legacy_schema = canonical_schema[:start] + canonical_schema[end:]
    legacy_schema = legacy_schema.replace(
        "PRAGMA user_version = 14;", "PRAGMA user_version = 13;", 1
    )
    legacy_schema_path = tmp_path / "schema-v13.sql"
    legacy_schema_path.write_text(legacy_schema, encoding="utf-8")

    # Seed the old database with raw SQLite, without calling the current
    # connect() function before switching its schema source to the v13 fixture.
    legacy = sqlite3.connect(db)
    legacy.executescript(legacy_schema)
    legacy.execute(
        "INSERT INTO projects(id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("northstar", "Northstar Coffee", "2026-01-01", "2026-01-01"),
    )
    legacy.execute(
        "INSERT INTO campaigns(id,project_id,title,updated_at) VALUES(?,?,?,?)",
        ("northstar-campaign", "northstar", "Launch", "2026-01-01"),
    )
    assert legacy.execute("PRAGMA user_version").fetchone()[0] == 13
    legacy_tables = {
        row[0] for row in legacy.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert not {
        "campaign_deliverables", "campaign_deliverable_revisions",
        "campaign_deliverable_batches",
    } & legacy_tables
    legacy.commit()
    legacy.close()

    monkeypatch.setattr(sqlite_module, "SCHEMA_PATH", legacy_schema_path)
    try:
        migrated = connect(db)
        assert get_user_version(migrated) == 14
        migrated_ddl = _deliverable_ddl(migrated)
        assert migrated.execute(
            "SELECT name FROM projects WHERE id='northstar'"
        ).fetchone()[0] == "Northstar Coffee"
        assert migrated.execute(
            "SELECT title FROM campaigns WHERE id='northstar-campaign'"
        ).fetchone()[0] == "Launch"
        assert migrated.execute(
            "SELECT COUNT(*) FROM campaign_deliverables"
        ).fetchone()[0] == 0
        migrated.close()

        # Reopening against the same v13 schema re-runs the additive migration;
        # its IF NOT EXISTS DDL must preserve both legacy rows and new tables.
        reopened = connect(db)
        assert get_user_version(reopened) == 14
        assert reopened.execute(
            "SELECT COUNT(*) FROM campaign_deliverable_revisions"
        ).fetchone()[0] == 0
        assert reopened.execute(
            "SELECT COUNT(*) FROM campaign_deliverable_batches"
        ).fetchone()[0] == 0
        assert reopened.execute(
            "SELECT title FROM campaigns WHERE id='northstar-campaign'"
        ).fetchone()[0] == "Launch"
        reopened.close()

        # The migration copy and authoritative fresh-install DDL must describe
        # identical tables and indexes, matching the repository's dual-DDL
        # convention for additive migrations.
        monkeypatch.setattr(sqlite_module, "SCHEMA_PATH", canonical_schema_path)
        fresh = connect(tmp_path / "fresh-v14.db")
        assert _deliverable_ddl(fresh) == migrated_ddl
        fresh.close()
    finally:
        monkeypatch.undo()


def test_manual_create_edit_history_status_and_cas(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch)
    base = _base()
    created_response = client.post(base, json=_payload())
    assert created_response.status_code == 200
    created = created_response.json()["data"]
    deliverable_id = created["id"]
    assert created["status"] == "DRAFT"
    assert created["current_version"] == 1

    edited_response = client.put(f"{base}/{deliverable_id}", json={
        "expected_version": 1,
        "title": "Edited weekday launch",
        "content_md": "## Revised post\nUpdated copy.",
    })
    assert edited_response.status_code == 200
    edited = edited_response.json()["data"]
    assert edited["current_version"] == 2
    assert edited["title"] == "Edited weekday launch"

    stale = client.put(f"{base}/{deliverable_id}", json={
        "expected_version": 1, "content_md": "Stale overwrite"
    })
    assert stale.status_code == 409
    assert client.get(f"{base}/{deliverable_id}").json()["data"]["content_md"] == (
        "## Revised post\nUpdated copy."
    )

    in_review = client.post(f"{base}/{deliverable_id}/status", json={
        "expected_version": 2, "status": "IN_REVIEW"
    })
    assert in_review.status_code == 200
    approved = client.post(f"{base}/{deliverable_id}/status", json={
        "expected_version": 3, "status": "APPROVED"
    })
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "APPROVED"

    reopened = client.put(f"{base}/{deliverable_id}", json={
        "expected_version": 4, "content_md": "Founder edit after approval"
    })
    assert reopened.status_code == 200
    assert reopened.json()["data"]["status"] == "DRAFT"
    assert reopened.json()["data"]["current_version"] == 5

    history = client.get(f"{base}/{deliverable_id}/history")
    assert history.status_code == 200
    revisions = history.json()["data"]
    assert [row["version"] for row in revisions] == [1, 2, 3, 4, 5]
    assert [row["operation"] for row in revisions] == [
        "CREATE", "EDIT", "STATUS_TRANSITION", "STATUS_TRANSITION", "EDIT"
    ]
    assert revisions[3]["status"] == "APPROVED"
    assert revisions[4]["status"] == "DRAFT"
    assert revisions[4]["content_md"] == "Founder edit after approval"

    invalid_transition = client.post(f"{base}/{deliverable_id}/status", json={
        "expected_version": 5, "status": "APPROVED"
    })
    assert invalid_transition.status_code == 409
    assert len(client.get(f"{base}/{deliverable_id}/history").json()["data"]) == 5
    assert client.put(f"{base}/{deliverable_id}", json={
        "expected_version": 5, "type": "unsupported"
    }).status_code == 422
    assert client.post(base, json={**_payload(), "status": "APPROVED"}).status_code == 422


def test_scoped_markdown_and_zip_export_and_cross_project_rejection(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch)
    base = _base()
    created = client.post(base, json=_payload("Northstar Post", "Northstar secret copy"))
    assert created.status_code == 200
    deliverable_id = created.json()["data"]["id"]

    markdown = client.get(f"{base}/{deliverable_id}/export.md")
    assert markdown.status_code == 200
    assert markdown.text == "Northstar secret copy"
    assert "attachment;" in markdown.headers["content-disposition"]

    package = client.get(f"{base}/exports/package.zip")
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        names = archive.namelist()
        assert len(names) == 1
        assert archive.read(names[0]).decode("utf-8") == "Northstar secret copy"

    harbor_base = _base("harbor", "harbor-campaign")
    assert client.get(f"{harbor_base}/{deliverable_id}").status_code == 404
    assert client.get(f"{harbor_base}/{deliverable_id}/history").status_code == 404
    assert client.get(f"{harbor_base}/{deliverable_id}/export.md").status_code == 404
    assert client.get(
        _base("harbor", "northstar-campaign")
    ).status_code == 404
    empty_package = client.get(f"{harbor_base}/exports/package.zip")
    assert empty_package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(empty_package.content)) as archive:
        assert archive.namelist() == []


def test_generated_batch_is_atomic_idempotent_and_preserves_later_edits(tmp_path):
    db = tmp_path / "generated.db"
    _seed(db)
    conn = connect(db)
    items = [{
        "type": "strategy_brief",
        "title": "Northstar launch strategy",
        "content_md": "## Goal\nBuild weekday awareness.",
    }, {
        "type": "social_post",
        "title": "Monday post",
        "platform": "Instagram",
        "content_md": "Coffee for a calmer Monday.",
    }]
    saved = service.save_generated_batch(
        conn, "northstar", "northstar-campaign", "turn-1", items,
        {"provider": "fake", "model": "synthetic"},
    )
    assert len(saved) == 2
    assert [row["type"] for row in saved] == ["strategy_brief", "social_post"]
    original_id = saved[0]["id"]

    edited = service.revise(
        conn, "northstar", "northstar-campaign", original_id, 1,
        {"content_md": "Founder revised the strategy."},
    )
    service.transition(
        conn, "northstar", "northstar-campaign", original_id, 2, "IN_REVIEW"
    )
    approved = service.transition(
        conn, "northstar", "northstar-campaign", original_id, 3, "APPROVED"
    )
    assert approved["status"] == "APPROVED"
    retried = service.save_generated_batch(
        conn, "northstar", "northstar-campaign", "turn-1", [{
            "type": "strategy_brief", "title": "Changed retry",
            "content_md": "Should never overwrite saved or approved output.",
        }], {"provider": "different", "model": "different"},
    )
    assert retried[0]["id"] == original_id
    assert retried[0]["title"] == saved[0]["title"]
    assert retried[0]["content_md"] == edited["content_md"]
    assert retried[0]["status"] == "APPROVED"
    assert retried[0]["current_version"] == 4
    replay_batch = service.get_generated_batch(
        conn, "northstar", "northstar-campaign", "turn-1")
    assert replay_batch == retried
    assert service.get_generated_batch(
        conn, "northstar", "northstar-campaign", "different-turn") is None
    assert service.get_generated_batch(
        conn, "harbor", "harbor-campaign", "turn-1") is None
    assert len(service.history(
        conn, "northstar", "northstar-campaign", original_id
    )) == 4

    before = conn.execute(
        "SELECT COUNT(*) FROM campaign_deliverables"
    ).fetchone()[0]
    try:
        service.save_generated_batch(
            conn, "northstar", "northstar-campaign", "bad-batch", [
                {"type": "ad_copy", "title": "Valid", "content_md": "Valid"},
                {"type": "invalid", "title": "Invalid", "content_md": "Bad"},
            ], {},
        )
    except service.DeliverableValidationError:
        pass
    else:
        raise AssertionError("invalid generated batch should be rejected")
    assert conn.execute(
        "SELECT COUNT(*) FROM campaign_deliverables"
    ).fetchone()[0] == before
    conn.close()
