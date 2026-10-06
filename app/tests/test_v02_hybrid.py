"""Hybrid delegation (SOL R3/R2): persistent jobs, post-turn creation, continuity."""
import json
import sys
import time

from app.database import repos
from app.database.seed import ensure_seed
from app.database.sqlite import connect, get_user_version


def _db(tmp_path, name="t.db"):
    conn = connect(tmp_path / name)
    ensure_seed(conn)
    return conn


def _convo(conn, project_id="starter"):
    from app.services import state as store
    return store.get_or_create_conversation(conn, project_id=project_id)


def test_contract_delegate_requests_passthrough():
    from app.services.manager_loop import parse_agent_contract
    c = parse_agent_contract(json.dumps({
        "reply": "r", "delegate_requests": [
            {"goal": "deep scan", "brief_md": "b", "lane": "research"},
            {"goal": "  "}, {"nope": 1}, "junk",
        ]}))
    assert c["delegate_requests"] == [
        {"goal": "deep scan", "brief_md": "b", "lane": "research"}]


def test_loop_creates_real_persistent_jobs(tmp_path, monkeypatch):
    from app.services import manager_loop
    from app.services import jobs as jobsvc
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse
    from app.services.tools import build_default_registry
    # Row creation is what this proves; never dispatch live runs from unit tests.
    monkeypatch.setattr(jobsvc._POOL, "submit", lambda *a, **k: None)
    conn = _db(tmp_path, "hy.db")
    c = _convo(conn)
    contract = {"reply": "Working on it.", "sources": [], "unknowns": [],
                "actions": [], "jobs_started": [], "approvals_required": [],
                "delegate_requests": [{"goal": "full market scan",
                                       "brief_md": "compare channels", "lane": "research"}]}
    p = FakeProvider(script=[LLMResponse(text="Working on it.", tool_calls=[],
                                        usage={"agent_contract": contract})])
    res = manager_loop.run_manager_turn(
        conn, project_id="starter", conversation_id=c["id"], user_text="full research please",
        provider=p, registry=build_default_registry(), root=str(tmp_path),
        budget={"db_path": str(tmp_path / "hy.db")})
    assert len(res["job_ids"]) == 1
    job = repos.BackgroundJobs.get(conn, res["job_ids"][0], "starter")
    assert job["conversation_id"] == c["id"] and job["job_type"] == "marketing_pm"
    assert "market scan" in job["brief_md"] and "compare channels" in job["brief_md"]
    assert job["created_at"]
    assert "background" in res["reply_md"].lower() or "where are we" in res["reply_md"].lower()
    conn.close()


def test_loop_skips_persistence_without_db_path(tmp_path):
    from app.services import manager_loop
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse
    from app.services.tools import build_default_registry
    conn = _db(tmp_path, "hy2.db")
    c = _convo(conn)
    contract = {"reply": "ok.", "delegate_requests": [{"goal": "x"}]}
    p = FakeProvider(script=[LLMResponse(text="ok.", tool_calls=[],
                                        usage={"agent_contract": contract})])
    res = manager_loop.run_manager_turn(
        conn, project_id="starter", conversation_id=c["id"], user_text="hi",
        provider=p, registry=build_default_registry(), root=str(tmp_path))
    assert res["job_ids"] == [] and res["mode"] == "AGENTIC"
    conn.close()


def test_persistent_cap_two_per_turn(tmp_path, monkeypatch):
    from app.services import manager_loop
    from app.services import jobs as jobsvc
    from app.services.llm import FakeProvider
    from app.services.llm.base import LLMResponse
    from app.services.tools import build_default_registry
    monkeypatch.setattr(jobsvc._POOL, "submit", lambda *a, **k: None)
    conn = _db(tmp_path, "hy3.db")
    c = _convo(conn)
    contract = {"reply": "ok.", "delegate_requests": [{"goal": f"g{i}"} for i in range(4)]}
    p = FakeProvider(script=[LLMResponse(text="ok.", tool_calls=[],
                                        usage={"agent_contract": contract})])
    res = manager_loop.run_manager_turn(
        conn, project_id="starter", conversation_id=c["id"], user_text="hi",
        provider=p, registry=build_default_registry(), root=str(tmp_path),
        budget={"db_path": str(tmp_path / "hy3.db")})
    assert len(res["job_ids"]) == 2
    conn.close()


def test_request_persistent_validates(tmp_path):
    from app.services import jobs as jobsvc
    conn = _db(tmp_path, "hy4.db")
    for bad in [dict(project_id="", goal="x"), dict(project_id="starter", goal="  ")]:
        try:
            jobsvc.request_persistent(conn, str(tmp_path / "hy4.db"), str(tmp_path),
                                      bad["project_id"], "c", bad["goal"])
            assert False, bad
        except ValueError:
            pass
    conn.close()


def test_job_run_writes_result_json(tmp_path):
    from app.services import jobs as jobsvc
    conn = _db(tmp_path, "hy5.db")
    row = jobsvc.submit(conn, str(tmp_path / "hy5.db"), str(tmp_path), kind="probe",
                        cmd=[sys.executable, "-c", "print('hello-worker')"],
                        side_effect="green", project_id="starter", conversation_id="c1",
                        job_type="probe", brief_md="b")
    deadline = time.time() + 60
    while time.time() < deadline:
        conn2 = connect(tmp_path / "hy5.db")
        job = repos.BackgroundJobs.get(conn2, row["id"])
        conn2.close()
        if job and job["status"] in ("completed", "failed"):
            break
        time.sleep(1)
    assert job["status"] == "completed"
    res = jobsvc.read_result_json(str(tmp_path), "starter", row["id"])
    assert res and "hello-worker" in res["stdout_tail"]
    conn.close()


def test_bundle_jobs_recent(tmp_path):
    from app.database.sqlite import connect as _connect
    from app.services import jobs as jobsvc
    from app.services.manager_loop import build_project_bundle
    conn = _db(tmp_path, "hy6.db")
    row = jobsvc.submit(conn, str(tmp_path / "hy6.db"), str(tmp_path), kind="probe",
                        cmd=[sys.executable, "-c", "print('done-1')"],
                        side_effect="green", project_id="starter", conversation_id="c1")
    deadline = time.time() + 60
    while time.time() < deadline:
        conn2 = _connect(tmp_path / "hy6.db")
        job = repos.BackgroundJobs.get(conn2, row["id"])
        conn2.close()
        if job and job["status"] == "completed":
            break
        time.sleep(1)
    bundle = build_project_bundle(conn, "starter", [], root=str(tmp_path))
    assert any(j["job_id"] == row["id"] and "done-1" in j["result_summary"]
               for j in bundle["jobs_recent"])
    conn.close()


def test_provider_error_attaches_snapshot(tmp_path):
    from app.services import manager_loop
    from app.services.tools import build_default_registry
    conn = _db(tmp_path, "hy7.db")
    c = _convo(conn)
    class Boom:
        name = "boom"
        def complete(self, **kw):
            raise ConnectionError("down")
        def supports_server_websearch(self):
            return False
    res = manager_loop.run_manager_turn(
        conn, project_id="starter", conversation_id=c["id"], user_text="hi",
        provider=Boom(), registry=build_default_registry(), root=str(tmp_path))
    assert res["mode"] == "PROVIDER_ERROR"
    assert "campaign(s)" in res["reply_md"] and "approval(s)" in res["reply_md"]
    assert "Traceback" not in res["reply_md"]
    assert "Showing last known state instead" not in res["reply_md"]
    conn.close()


def test_schema_v3_job_contract_columns(tmp_path):
    conn = _db(tmp_path, "hy8.db")
    assert get_user_version(conn) == 9
    cols = {r[1] for r in conn.execute("PRAGMA table_info(background_jobs)").fetchall()}
    assert {"job_type", "brief_md", "created_at", "updated_at"} <= cols
    conn.close()
