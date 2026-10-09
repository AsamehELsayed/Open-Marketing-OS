"""Gate 2 remediation tests (R1/R4/R5)."""
from pathlib import Path

from app.database import repos
from app.database.sqlite import SCHEMA_VERSION, connect, get_user_version
from app.services.adapters import importer, parsers

ROOT = Path(__file__).resolve().parents[2]


def test_r1_updated_at_columns_exist(tmp_path):
    conn = connect(tmp_path / "t.db")
    for table in ("tasks", "approvals", "experiments"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert "updated_at" in cols, table
    assert get_user_version(conn) == SCHEMA_VERSION
    conn.close()


def test_r1_old_db_upgraded_in_place(tmp_path):
    db = tmp_path / "old.db"
    conn = connect(db)
    repos.Campaigns.upsert(conn, {"id": "opp-01", "title": "Keep me", "updated_at": "2026-09-16T00:00:00+00:00"})
    conn.execute("ALTER TABLE tasks DROP COLUMN updated_at")
    conn.execute("ALTER TABLE approvals DROP COLUMN updated_at")
    conn.execute("ALTER TABLE experiments DROP COLUMN updated_at")
    conn.commit()
    conn.close()
    conn2 = connect(db)  # must upgrade, not destroy
    for table in ("tasks", "approvals", "experiments"):
        cols = {r[1] for r in conn2.execute(f"PRAGMA table_info({table})").fetchall()}
        assert "updated_at" in cols, table
    assert repos.Campaigns.get(conn2, "opp-01")["title"] == "Keep me"
    assert get_user_version(conn2) == SCHEMA_VERSION
    conn2.close()


# DEV-008-PUBLISH-GATE. This used to read the private `production/approval-queue.md`
# and assert a real campaign id, which both leaked private material and made the
# module impossible to run in the public export (production/ is a forbidden
# path). The behaviour under test is the conflict rule, not the campaign, so the
# fixture is now written here.
_SYNTHETIC_QUEUE = """# Approval Queue

## ID: sample-campaign-01

- TYPE: Launch.
- STATUS: approved.
"""


def test_r1_approvals_conflict_keeps_ui_decision(tmp_path):
    conn = connect(tmp_path / "t.db")
    src = tmp_path / "approval-queue.md"
    src.write_text(_SYNTHETIC_QUEUE, encoding="utf-8")
    rows = parsers.parse_approval_queue(src)
    importer.import_rows(conn, "approvals", src, rows, repos.Approvals.upsert, repos.Approvals.get)
    row = repos.Approvals.get(conn, "sample-campaign-01")
    row["status"] = "rejected"  # UI-side decision after import
    row["updated_at"] = "2099-01-01T00:00:00+00:00"
    repos.Approvals.upsert(conn, row)
    altered = tmp_path / "altered-approval-queue.md"
    altered.write_text(src.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    stats = importer.import_rows(conn, "approvals", altered, parsers.parse_approval_queue(altered),
                                 repos.Approvals.upsert, repos.Approvals.get)
    assert "sample-campaign-01" in stats["conflicts"]
    assert repos.Approvals.get(conn, "sample-campaign-01")["status"] == "rejected"
    conn.close()


def test_r4_returned_status_parse(tmp_path):
    queue = tmp_path / "q.md"
    queue.write_text(
        "# Approval Queue\n\n## ID: demo-01\n\n- TYPE: Test.\n- STATUS: RETURNED with objections (copy unclear).\n",
        encoding="utf-8",
    )
    rows = parsers.parse_approval_queue(queue)
    assert rows[0]["status"] == "returned"


_SYNTHETIC_COMPANY_YAML = """company:
  name: "Acme Test Company"
  website: "https://acme-test.example/"

markets:
  primary:
    - Example City
  expansion: []

languages:
  - English

known_services:
  - Example service

seed_hypotheses: []
"""


def test_r5_company_sync(tmp_path):
    # DEV-008-PUBLISH-GATE. This test used to read the *private* workspace file
    # at `<repo>/company/company.yaml`, which in the development checkout holds
    # a real named business's name, website, markets and services. Two problems:
    # it asserted that real identity back at us, and `company/` is a forbidden
    # path in the public export, so the test could not pass in the published
    # repository at all. It now builds its own obviously-synthetic file, which
    # is what a public test suite has to do.
    company_file = tmp_path / "company.yaml"
    company_file.write_text(_SYNTHETIC_COMPANY_YAML, encoding="utf-8")

    row = parsers.parse_company(company_file)
    assert row["id"] == "starter"
    assert row["name"] == "Acme Test Company"
    conn = connect(tmp_path / "t.db")
    from app.database.seed import ensure_seed
    ensure_seed(conn)
    s1 = importer.import_rows(conn, "company", company_file,
                              [row], repos.Companies.upsert, repos.Companies.get)
    assert s1["updated"] == 1  # seed owns the starter row; import refreshes it from file
    s2 = importer.import_rows(conn, "company", company_file,
                              [row], repos.Companies.upsert, repos.Companies.get)
    assert s2["skipped"] == 1
    assert repos.Companies.get(conn, "starter")["website"] == "https://acme-test.example/"
    conn.close()
