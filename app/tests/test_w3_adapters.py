"""W3 acceptance: real-file parses (D3–D6) + idempotency (D2) + conflict (D7) + events (D8)."""
from pathlib import Path

from app.database import repos
from app.database.sqlite import connect
from app.services.adapters import importer, parsers

ROOT = Path(__file__).resolve().parents[2]


def _conn(tmp_path):
    return connect(tmp_path / "t.db")


def test_parse_weekly_plan_max3(tmp_path):
    tasks = parsers.parse_weekly_plan(ROOT / "production" / "weekly-plan.md")
    assert 1 <= len(tasks) <= 3
    assert tasks[0]["id"] == "weekly-p1"


def test_parse_approval_gate(tmp_path):
    approvals = parsers.parse_approval_queue(ROOT / "production" / "approval-queue.md")
    assert approvals
    assert approvals[0]["id"] == "launch-invisible-build-partner-01"
    assert approvals[0]["status"] == "approved"


def test_parse_backlog_schema_v2(tmp_path):
    campaigns = parsers.parse_backlog(ROOT / "strategy" / "opportunity-backlog.md")
    assert len(campaigns) == 10
    opp01 = next(c for c in campaigns if c["id"] == "opp-01")
    assert opp01["status"] == "approved"


def test_parse_actions_experiments_ids(tmp_path):
    tasks = parsers.parse_actions(ROOT / "state" / "actions.json")
    exps = parsers.parse_experiments(ROOT / "state" / "experiments.json")
    assert {t["id"] for t in tasks} == {"test-02-proof-block", "test-03-agency-one-pager"}
    assert {e["id"] for e in exps} >= {"test-02-proof-block", "test-03-agency-one-pager"}


def test_import_idempotent_and_events(tmp_path):
    conn = _conn(tmp_path)
    rows = parsers.parse_backlog(ROOT / "strategy" / "opportunity-backlog.md")
    s1 = importer.import_rows(conn, "backlog", ROOT / "strategy" / "opportunity-backlog.md",
                              rows, repos.Campaigns.upsert, repos.Campaigns.get)
    assert s1["inserted"] == 10
    s2 = importer.import_rows(conn, "backlog", ROOT / "strategy" / "opportunity-backlog.md",
                              rows, repos.Campaigns.upsert, repos.Campaigns.get)
    assert s2["skipped"] == 10
    assert repos.Events.list(conn)
    conn.close()


def test_conflict_surfaces_not_overwrites(tmp_path):
    conn = _conn(tmp_path)
    src = ROOT / "strategy" / "opportunity-backlog.md"
    rows = parsers.parse_backlog(src)
    importer.import_rows(conn, "backlog", src, rows, repos.Campaigns.upsert, repos.Campaigns.get)
    # Simulate a UI-side edit newer than the import.
    row = repos.Campaigns.get(conn, "opp-01")
    row["title"] = "UI EDITED TITLE"
    row["updated_at"] = "2099-01-01T00:00:00+00:00"
    repos.Campaigns.upsert(conn, row)
    # Simulate a file-side change (copy with appended line).
    altered = tmp_path / "backlog.md"
    altered.write_text(src.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    rows2 = parsers.parse_backlog(altered)
    stats = importer.import_rows(conn, "backlog", altered, rows2,
                                 repos.Campaigns.upsert, repos.Campaigns.get)
    assert "opp-01" in stats["conflicts"]
    assert repos.Campaigns.get(conn, "opp-01")["title"] == "UI EDITED TITLE"
    kinds = {e["kind"] for e in repos.Events.list(conn)}
    assert "import_conflict" in kinds
    conn.close()
