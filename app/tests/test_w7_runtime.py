"""W7/W8 acceptance: runtime safety, background jobs, approval gating (Step 5).

DEV-007 W1: product uses app.services.jobs (approval gate + local runner /
LangGraph worker). External agent runtime helpers live under scripts/ only.
"""
import time
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.database.sqlite import connect
from app.main import create_app
from app.services import jobs as jobsvc

ROOT = Path(__file__).resolve().parents[2]
PY = ["python", "-c"]


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "w7.db"
    conn = connect(path)
    yield conn, path, tmp_path
    conn.close()


def _approve(conn):
    repos.Approvals.upsert(conn, {"id": "ap-ok", "kind": "T", "title": "t", "body_md": "",
                                  "status": "approved", "fields_json": "{}",
                                  "decided_by": "f", "decided_at": "2026-09-16T00:00:00+00:00",
                                  "updated_at": "2026-09-16T00:00:00+00:00"})


def _terminal(conn, job_id, limit=15.0):
    start = time.time()
    while time.time() - start < limit:
        job = repos.BackgroundJobs.get(conn, job_id)
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.2)
    raise TimeoutError(job_id)


def test_external_agent_cmd_blocked(db):
    conn, _, tmp = db
    res = jobsvc.run_task(conn, ROOT, ["opencode", "run", "x"])
    assert res["status"] == "blocked"
    assert "not product executors" in res["blocked_reason"]


def test_green_runs_and_captures(db):
    conn, _, tmp = db
    res = jobsvc.run_task(conn, tmp, [PY[0], "-c", "print('hi-out')"], cwd=tmp)
    assert res["status"] == "completed" and "hi-out" in res["stdout"]


def test_cwd_escape_blocked(db):
    conn, _, tmp = db
    res = jobsvc.run_task(conn, tmp, [PY[0], "-c", "print(1)"], cwd=tmp.parent)
    assert res["status"] == "blocked" and "outside workspace" in res["blocked_reason"]


def test_no_shell_injection(db):
    conn, _, tmp = db
    res = jobsvc.run_task(conn, tmp, [PY[0], "-c", "import sys; print(sys.argv[1])",
                                      "hello; echo PWNED"], cwd=tmp)
    assert res["status"] == "completed" and res["stdout"].strip() == "hello; echo PWNED"


def test_timeout_normalized(db):
    conn, _, tmp = db
    res = jobsvc.run_task(conn, tmp, [PY[0], "-c", "import time; time.sleep(10)"],
                          cwd=tmp, timeout=1)
    assert res["timed_out"] is True and res["status"] == "failed"


def test_green_job_completes(db):
    conn, path, tmp = db
    job = jobsvc.submit(conn, str(path), str(tmp), "echo", [PY[0], "-c", "print('job-hi')"])
    done = _terminal(conn, job["id"])
    assert done["status"] == "completed" and done["exit_code"] == 0
    info = jobsvc.summary(conn, tmp, job["id"])
    assert any("job-hi" in line for line in info["log_tail"])


def test_failed_job_records(db):
    conn, path, tmp = db
    job = jobsvc.submit(conn, str(path), str(tmp), "boom", [PY[0], "-c", "raise SystemExit(3)"])
    done = _terminal(conn, job["id"])
    assert done["status"] == "failed" and done["exit_code"] == 3


def test_yellow_without_approval_parks(db):
    conn, path, tmp = db
    job = jobsvc.submit(conn, str(path), str(tmp), "send", [PY[0], "-c", "print(1)"],
                        side_effect="yellow")
    assert job["status"] == "waiting_approval"
    assert not (tmp / "data" / "job-logs" / f"{job['id']}.log").exists()
    cancelled = jobsvc.cancel(conn, job["id"])
    assert cancelled["status"] == "cancelled"


def test_yellow_with_approval_runs_and_red_refused(db):
    conn, path, tmp = db
    _approve(conn)
    job = jobsvc.submit(conn, str(path), str(tmp), "send", [PY[0], "-c", "print('sent')"],
                        side_effect="yellow", approval_id="ap-ok")
    done = _terminal(conn, job["id"])
    assert done["status"] == "completed"
    res = jobsvc.run_task(conn, tmp, [PY[0], "-c", "print(1)"], side_effect="red", cwd=tmp)
    assert res["status"] == "blocked" and "requires an approved approval" in res["blocked_reason"]
    with pytest.raises(ValueError):
        jobsvc.cancel(conn, job["id"])  # terminal jobs cannot be cancelled


def test_jobs_pages(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    with TestClient(create_app()) as client:
        root = client.get("/jobs", follow_redirects=False)
        assert root.status_code == 303
        assert root.headers["location"] == "/app/jobs"
        for suffix in ("status", "log", "process"):
            response = client.get(
                f"/jobs/nope/{suffix}", follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == "/app/jobs"
            assert "<html" not in response.text.lower()


def test_backup_roundtrip(tmp_path):
    import zipfile

    from app.services import backup as backupsvc

    root = tmp_path / "ws"
    (root / "data").mkdir(parents=True)
    db = root / "data" / "marketing.db"
    connect(db).close()
    dest = backupsvc.create_backup(root, db)
    assert dest.exists()
    names = zipfile.ZipFile(dest).namelist()
    assert "marketing.db" in names
    assert backupsvc.list_backups(root) == [dest]


def test_resolve_argv_routes_windows_cmd_shim(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, 'which', lambda c: r'C:\npm\node.CMD' if c == 'node' else None)
    argv = jobsvc._resolve_argv(['node', 'hi'])
    assert argv[1] == '/c' and argv[2].endswith('node.CMD') and argv[3:] == ['hi']


def test_resolve_argv_keeps_posix_binary(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, 'which', lambda c: '/usr/bin/node' if c == 'node' else None)
    assert jobsvc._resolve_argv(['node', 'hi']) == ['/usr/bin/node', 'hi']


def test_run_task_stdin_utf8_passthrough(tmp_path):
    import sys
    from app.database.sqlite import connect
    conn = connect(tmp_path / "s.db")
    cmd = [sys.executable, "-c", "import sys; print(sys.stdin.read(), end='')"]
    text = "Arabic + arrow test"
    r = jobsvc.run_task(conn, tmp_path, cmd, side_effect="green", timeout=60,
                        cwd=tmp_path, stdin_text=text)
    assert r["status"] == "completed" and text in r["stdout"]
    conn.close()


def test_langgraph_worker_job_path(tmp_path):
    """marketing_pm jobs run in-process via the LangGraph worker, not a subprocess."""
    from app.database.sqlite import connect
    from app.database.seed import ensure_seed
    conn = connect(tmp_path / "lg.db")
    ensure_seed(conn)
    row = jobsvc.request_persistent(
        conn, str(tmp_path / "lg.db"), str(tmp_path),
        project_id="starter", conversation_id="c1",
        goal="research channel fit", brief_md="focus on agencies")
    assert row["kind"].startswith("marketing_pm:")
    assert row["status"] in ("queued", "running", "completed", "waiting_approval")
    # cmd is LangGraph-native (not an external agent binary)
    import json as _json
    spec = _json.loads(row["cmd_json"])
    assert spec["cmd"][0] == "langgraph"
    deadline = time.time() + 60
    while time.time() < deadline:
        job = repos.BackgroundJobs.get(conn, row["id"])
        if job["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.5)
    assert job["status"] in ("completed", "failed")
    if job["status"] == "completed":
        log = (tmp_path / job["log_path"]).read_text(encoding="utf-8")
        assert "stdout" in log
    conn.close()
