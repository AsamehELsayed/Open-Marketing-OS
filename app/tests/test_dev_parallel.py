"""Parallel scheduler tests: graph, real concurrent launch, cap, blocking,
isolation, timestamps, parallelism proof, worktrees, gate ordering.

All worker processes are REAL subprocesses doing real CPU work (no sleeps).
No network. Worktree test uses the local git repo only.
"""
import asyncio
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPTS = REPO / "scripts"

import pytest

TMP_RUNS = None


def _setup_module():
    global TMP_RUNS
    import tempfile
    TMP_RUNS = Path(tempfile.mkdtemp(prefix="devruns_"))
    os.environ["DEV_RUNS_DIR"] = str(TMP_RUNS)
    sys.path.insert(0, str(SCRIPTS))
    # DEV-008-PUBLISH-GATE. This import used to fail hard when `scripts/` was
    # absent, which it is in some checkouts. `internal_fixtures` declares this
    # module as skip-when-withheld, but that skip is applied by
    # `pytest_collection_modifyitems`, which runs *after* collection has already
    # imported this file -- so the declaration could never take effect and the
    # published repository failed at collection instead of skipping.
    #
    # A module-level skip is the only mechanism early enough to work, and it
    # states the real reason rather than hiding behind an import error.
    missing = [name for name in ("dev_run", "dev_sched")
               if not (SCRIPTS / f"{name}.py").is_file()]
    if missing:
        pytest.skip(
            f"parallel-scheduler tooling not present ({', '.join(missing)}); "
            f"expected {SCRIPTS}",
            allow_module_level=True,
        )
    import dev_run
    import dev_sched
    globals()["dev_run"] = dev_run
    globals()["dev_sched"] = dev_sched


_setup_module()

WORK = ("import sys,time,hashlib;t0=time.time();h=hashlib.sha256();"
        "[h.update(str(i).encode()) for i in range(1500000)];t1=time.time();"
        "open(sys.argv[1],'w').write(f'{t0} {t1} {h.hexdigest()}')")


def marker_concurrency(paths):
    iv = []
    for p in paths:
        s, e, _ = p.read_text().split()
        iv.append((float(s), float(e)))
    best = 0
    for t in [s for s, _ in iv] + [e for _, e in iv]:
        best = max(best, sum(1 for s, e in iv if s <= t < e))
    return best
FAIL = "import sys;sys.exit(3)"


def make_run(run_id, classification="MEDIUM"):
    d = TMP_RUNS / run_id
    (d).mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "run_id": run_id, "title": "t", "classification": classification,
        "status": "implementing", "current_stage": "implementing"}))
    (d / "executions.jsonl").write_text("")
    return d


def w(wid, cmd, deps=None, out="", agent="opencode-stub"):
    return {"worker_id": wid, "role": "implementer", "task": f"task-{wid}",
            "depends_on": deps or [], "files_or_scope": [f"scope/{wid}"],
            "can_run_parallel": not deps, "command": cmd,
            "output_artifact": out, "agent": agent, "timeout": 120}


def py_cmd(code, marker):
    return [sys.executable, "-c", code, str(marker)]


def read_execs(run_id):
    p = TMP_RUNS / run_id / "executions.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def ts(e, k):
    return dev_run.parse_ts(e[k])


def max_concurrency(recs):
    pts = [(ts(e, "started_at"), 1) for e in recs]
    pts += [(ts(e, "completed_at"), -1) for e in recs]
    pts.sort(key=lambda p: (p[0], p[1]))
    cur = best = 0
    for _, d in pts:
        cur += d
        best = max(best, cur)
    return best


# 1. dependency graph validation -------------------------------------------
def test_graph_init_rejects_bad_refs(tmp_path):
    make_run("G1")
    g = {"workers": [w("W1", ["true"]), w("W1", ["true"])]}
    gf = tmp_path / "g.json"
    gf.write_text(json.dumps({"workers": g["workers"]}))
    r = subprocess.run([sys.executable, str(SCRIPTS / "dev_sched.py"),
                        "init", "--id", "G1", "--graph", str(gf)],
                       capture_output=True, text=True, cwd=str(REPO),
                       env={**os.environ})
    assert r.returncode != 0 and "duplicate" in (r.stdout + r.stderr).lower()
    gf.write_text(json.dumps({"workers": [dict(w("W3", ["true"]),
                                             depends_on=["NOPE"])] }))
    r = subprocess.run([sys.executable, str(SCRIPTS / "dev_sched.py"),
                        "init", "--id", "G1", "--graph", str(gf)],
                       capture_output=True, text=True, cwd=str(REPO),
                       env={**os.environ})
    assert r.returncode != 0 and "unknown" in (r.stdout + r.stderr).lower()


# 2. ready worker calculation ------------------------------------------------
def test_ready_workers():
    ws = [w("W1", ["true"]), w("W2", ["true"]),
          w("W3", ["true"], deps=["W1"])]
    assert {x["worker_id"] for x in
            dev_sched.ready_workers(ws, set(), set(), set())} == {"W1", "W2"}
    assert [x["worker_id"] for x in
            dev_sched.ready_workers(ws, {"W1", "W2"}, {"W1"}, set())] == ["W3"]
    got = dev_sched.ready_workers(ws, {"W1", "W2"}, {"W1", "W2"}, set())
    assert [x["worker_id"] for x in got] == ["W3"]
    assert [x["worker_id"] for x in
            dev_sched.ready_workers(ws, {"W1"}, set(), {"W1"})] == ["W2"]


# 3. real concurrent launch with overlap ------------------------------------
def test_real_concurrent_launch_overlaps(tmp_path):
    make_run("C1")
    ws = [w(f"W{i}", py_cmd(WORK, tmp_path / f"m{i}")) for i in (1, 2, 3)]
    (TMP_RUNS / "C1" / "graph.json").write_text(
        json.dumps({"max_concurrency": 3, "workers": ws}))
    res = asyncio.run(dev_sched.run_all_async("C1", 3))
    assert res == {"completed": ["W1", "W2", "W3"], "failed": [], "blocked": []}
    recs = [e for e in read_execs("C1") if e["status"] == "completed"]
    assert len(recs) == 3
    paths = [tmp_path / f"m{i}" for i in (1, 2, 3)]
    for p in paths:
        assert p.exists()  # real work product
    assert marker_concurrency(paths) >= 2  # real overlap, no sleeps used


# 4. concurrency cap ----------------------------------------------------------
def test_concurrency_cap(tmp_path):
    make_run("C2")
    ws = [w(f"W{i}", py_cmd(WORK, tmp_path / f"c{i}")) for i in range(4)]
    (TMP_RUNS / "C2" / "graph.json").write_text(
        json.dumps({"max_concurrency": 2, "workers": ws}))
    res = asyncio.run(dev_sched.run_all_async("C2", 2))
    assert len(res["completed"]) == 4
    paths = [tmp_path / f"c{i}" for i in range(4)]
    assert marker_concurrency(paths) <= 2


# 5. dependent worker blocking ------------------------------------------------
def test_dependent_blocked_on_prereq_failure(tmp_path):
    make_run("C3")
    ws = [w("W1", py_cmd(FAIL, tmp_path / "x")),
          w("W3", py_cmd(WORK, tmp_path / "m3"), deps=["W1"])]
    (TMP_RUNS / "C3" / "graph.json").write_text(
        json.dumps({"max_concurrency": 2, "workers": ws}))
    res = asyncio.run(dev_sched.run_all_async("C3", 2))
    assert res["failed"] == ["W1"] and res["blocked"] == ["W3"]
    recs = read_execs("C3")
    assert any(e.get("worker_id") == "W3" and e["status"] == "blocked"
               for e in recs)
    assert not (tmp_path / "m3").exists()  # never launched


# 6. failure isolation ---------------------------------------------------------
def test_failure_isolation(tmp_path):
    make_run("C4")
    ws = [w("W1", py_cmd(WORK, tmp_path / "ok")),
          w("W2", py_cmd(FAIL, tmp_path / "x"))]
    (TMP_RUNS / "C4" / "graph.json").write_text(
        json.dumps({"max_concurrency": 2, "workers": ws}))
    res = asyncio.run(dev_sched.run_all_async("C4", 2))
    assert res["completed"] == ["W1"] and res["failed"] == ["W2"]
    assert (tmp_path / "ok").exists()


# 7. execution timestamps -------------------------------------------------------
def test_execution_timestamps():
    recs = [e for e in read_execs("C1") if e["status"] == "completed"]
    assert recs
    for e in recs:
        assert ts(e, "started_at") < ts(e, "completed_at")


# 8. parallelism calculation ------------------------------------------------------
def test_parallelism_verdicts():
    buf = io.StringIO()
    with redirect_stdout(buf):
        dev_run.cmd_parallelism(type("A", (), {"id": "C1"})())
    out = buf.getvalue()
    assert "PARALLELISM: REAL" in out and "MAX CONCURRENCY:" in out
    make_run("SEQ")
    base = datetime(2026, 1, 1, 0, 0, 0)
    lines = []
    for i in range(2):
        s = base.replace(minute=i * 10)
        e = base.replace(minute=i * 10 + 5)
        lines.append({"role": "implementer", "worker_id": f"W{i}",
                      "status": "completed",
                      "started_at": s.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "completed_at": e.strftime("%Y-%m-%dT%H:%M:%SZ")})
    (TMP_RUNS / "SEQ" / "executions.jsonl").write_text(
        "".join(json.dumps(l) + "\n" for l in lines))
    buf = io.StringIO()
    with redirect_stdout(buf):
        dev_run.cmd_parallelism(type("A", (), {"id": "SEQ"})())
    assert "PARALLELISM: NO" in buf.getvalue()


def test_run_json_mirror(tmp_path):
    make_run("M1")
    ws = [w("W1", py_cmd(WORK, tmp_path / "m"))]
    (TMP_RUNS / "M1" / "graph.json").write_text(
        json.dumps({"max_concurrency": 1, "workers": ws}))
    asyncio.run(dev_sched.run_all_async("M1", 1))
    run = json.loads((TMP_RUNS / "M1" / "run.json").read_text())
    assert run["workers"] == [{"worker_id": "W1", "agent": "opencode-stub",
                               "status": "completed", "artifact": ""}]


def test_approval_bridge_gate():
    _cli("new", "--id", "AUTHG", "--title", "t", "--class", "MEDIUM")
    r = _cli("gate", "--id", "AUTHG", "--stage", "implementing")
    assert r.returncode != 0 and "plan.md" in r.stdout  # no plan yet
    (TMP_RUNS / "AUTHG" / "plan.md").write_text("plan")
    r = _cli("gate", "--id", "AUTHG", "--stage", "implementing")
    assert r.returncode != 0 and "approve-plan" in r.stdout  # no approval yet
    assert _cli("approve-plan", "--id", "AUTHG").returncode == 0
    run = json.loads((TMP_RUNS / "AUTHG" / "run.json").read_text())
    assert run["founder_plan_approved"] is True
    assert _cli("gate", "--id", "AUTHG", "--stage", "implementing").returncode == 0


def test_default_worker_command_is_auto():
    assert dev_sched.DEFAULT_WORKER_COMMAND == ["opencode", "run", "--auto"]


def test_opencode_json_allows_routine_denies_destructive():
    cfg = json.loads((REPO / "opencode.json").read_text(encoding="utf-8"))
    bash = cfg["permission"]["bash"]
    for pat in ("python scripts/dev_run.py *", "python scripts/dev_sched.py *",
                "python -m pytest*", "pytest*", "git status*", "git diff*",
                "git log*", "git worktree*", "git add*", "opencode run*"):
        assert bash.get(pat) == "allow", pat
    for pat in ("git push *", "rm *", "del *", "Remove-Item *", "sudo *"):
        assert bash.get(pat) == "deny", pat
    assert bash.get("*") == "ask"


def test_resolve_argv_no_bare_shim():
    argv = dev_sched.resolve_argv(["opencode", "run"])
    assert argv[0].lower() != "opencode"  # never a bare shim name
    assert Path(argv[-2] if argv[0].lower().endswith("cmd.exe")
                else argv[0]).exists() or argv[0].lower() == "powershell"


# 9. worktree isolation -------------------------------------------------------------
def test_worktree_isolation():
    git = subprocess.run(["git", "rev-parse"], cwd=str(REPO),
                         capture_output=True)
    if git.returncode != 0:
        pytest.skip("no git repo")
    p1 = dev_sched.ensure_worktree("TESTWT", "w1")
    p2 = dev_sched.ensure_worktree("TESTWT", "w2")
    try:
        assert p1 != p2 and p1.exists() and p2.exists()
        (p1 / "w1-only.txt").write_text("w1")
        assert not (p2 / "w1-only.txt").exists()
        lst = subprocess.run(["git", "worktree", "list", "--porcelain"],
                             cwd=str(REPO), capture_output=True, text=True)
        norm = lst.stdout.replace("/", "\\")
        assert str(p1) in norm and str(p2) in norm
    finally:
        dev_sched.remove_worktrees("TESTWT")
    assert not p1.exists() and not p2.exists()


# 10. integration after workers -------------------------------------------------------
def test_integrator_runs_last(tmp_path):
    make_run("C5")
    ws = [w("W1", py_cmd(WORK, tmp_path / "a")),
          w("W2", py_cmd(WORK, tmp_path / "b")),
          dict(w("INT", py_cmd(WORK, tmp_path / "c"), deps=["W1", "W2"]),
               role="integrator")]
    (TMP_RUNS / "C5" / "graph.json").write_text(
        json.dumps({"max_concurrency": 3, "workers": ws}))
    res = asyncio.run(dev_sched.run_all_async("C5", 3))
    assert res["blocked"] == [] and len(res["completed"]) == 3
    recs = {e["worker_id"]: e for e in read_execs("C5")
            if e["status"] == "completed"}
    assert ts(recs["INT"], "started_at") >= max(
        ts(recs["W1"], "completed_at"), ts(recs["W2"], "completed_at"))


# 11+12. QA waits for implementation, reviewer waits for QA ------------------------------
def _cli(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / "dev_run.py"), *args],
                          capture_output=True, text=True, cwd=str(REPO),
                          env={**os.environ})


def test_gate_ordering_qa_then_review():
    assert _cli("new", "--id", "ORD", "--title", "t",
                "--class", "MEDIUM").returncode == 0
    (TMP_RUNS / "ORD" / "plan.md").write_text("plan")
    assert _cli("approve-plan", "--id", "ORD").returncode == 0
    r = _cli("gate", "--id", "ORD", "--stage", "review")
    assert r.returncode != 0 and "TESTED" in r.stdout  # QA first
    (TMP_RUNS / "ORD" / "plan.md").write_text("plan")
    # QA completed with an artifact that exists (real repo file as stand-in)
    (TMP_RUNS / "ORD" / "executions.jsonl").write_text(json.dumps({
        "run_id": "ORD", "parent_run_id": "x", "role": "qa", "agent": "g",
        "task": "t", "started_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T00:01:00Z", "status": "completed",
        "input_artifact": "i", "output_artifact": "app/main.py"}) + "\n")
    r = _cli("gate", "--id", "ORD", "--stage", "approved")
    assert r.returncode != 0 and "APPROVE" in r.stdout  # reviewer first
