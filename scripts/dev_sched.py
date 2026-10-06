"""Concurrent worker scheduler for development runs (stdlib only).

The external development orchestrator uses this to launch independent worker
PROCESSES concurrently (asyncio subprocesses — e.g. `opencode run` CLI
processes in isolated worktrees). Workers never sub-delegate: recursion is
structurally impossible because a worker process has no handle on the
orchestrator's scheduler.

Graph file: development/runs/<run_id>/graph.json
  {"max_concurrency": 4,
   "workers": [{"worker_id": "W1", "role": "implementer",
                "task": "...", "depends_on": [],
                "files_or_scope": ["app/..."], "can_run_parallel": true,
                "command": ["opencode", "run", "--auto"],
                "stdin_text": "...", "worktree": true, "timeout": 900}]}
Workers launched without an explicit command default to
["opencode", "run", "--auto"] (auto-approval under the founder's one plan
approval; opencode.json denies stay enforced).

Usage:
  python scripts/dev_sched.py init --id DEV-002 --graph <graph.json> [--max 4]
  python scripts/dev_sched.py run --id DEV-002
  python scripts/dev_sched.py clean-worktrees --id DEV-002

Every launch/completion is appended to executions.jsonl (started_at/
completed_at prove overlap). Dependents of a failed worker are recorded as
`blocked` and never launched. QA/reviewer gates in dev_run.py are untouched.
"""
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dev_run

REPO = Path(__file__).resolve().parent.parent


DEFAULT_WORKER_COMMAND = ["opencode", "run", "--auto"]


def now() -> str:
    # Fractional seconds: second-resolution timestamps cannot resolve
    # sub-second worker overlap (found by DEV-002 QA as suite flakiness).
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def default_max_concurrency() -> int:
    try:
        return max(1, int(os.environ.get("MAX_PARALLEL_WORKERS", "4")))
    except ValueError:
        return 4


def graph_path(run_id: str) -> Path:
    return dev_run.run_dir(run_id) / "graph.json"


def load_graph(run_id: str) -> dict:
    p = graph_path(run_id)
    if not p.exists():
        sys.exit(f"no graph for run: {run_id}")
    return json.loads(p.read_text(encoding="utf-8"))


def ready_workers(workers: list, launched: set, completed: set,
                  failed: set) -> list:
    """Workers whose deps all completed, with no failed dep, not yet launched."""
    out = []
    for w in workers:
        wid = w["worker_id"]
        if wid in launched:
            continue
        deps = w.get("depends_on", [])
        if any(d in failed for d in deps):
            continue
        if all(d in completed for d in deps):
            out.append(w)
    return out


def ensure_worktree(run_id: str, worker_id: str) -> Path:
    """Isolated git worktree per worker. Returns its path."""
    wt = REPO / "development" / "worktrees" / run_id / worker_id.lower()
    if wt.exists():
        return wt
    wt.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "worktree", "add", "--detach", str(wt)],
                       cwd=str(REPO), capture_output=True, text=True,
                       timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {r.stderr[-500:]}")
    return wt


def remove_worktrees(run_id: str) -> None:
    base = REPO / "development" / "worktrees" / run_id
    if not base.exists():
        return
    for wt in sorted(base.iterdir()):
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                       cwd=str(REPO), capture_output=True, text=True,
                       timeout=120)
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
    subprocess.run(["git", "worktree", "prune"], cwd=str(REPO),
                   capture_output=True, text=True, timeout=60)
    try:
        base.rmdir()
    except OSError:
        pass


def resolve_argv(cmd: list) -> list:
    """Same Windows-shim resolution as opencode_service._resolve_argv.

    asyncio (like CreateProcess) cannot execute .CMD/.BAT/.PS1 shims
    directly; route them through COMSPEC / PowerShell with the same argv
    list (no shell string parsing)."""
    if not cmd:
        return cmd
    resolved = shutil.which(cmd[0])
    if resolved is None:
        return cmd
    low = resolved.lower()
    if low.endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", resolved, *cmd[1:]]
    if low.endswith(".ps1"):
        return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                "Bypass", "-File", resolved, *cmd[1:]]
    if os.path.basename(cmd[0]).lower() in ("opencode",) or low.endswith(".exe"):
        return [resolved, *cmd[1:]]
    return [resolved, *cmd[1:]]


async def run_worker(run_id: str, w: dict, sem: asyncio.Semaphore) -> dict:
    """Run ONE worker process. Returns its execution record."""
    wid = w["worker_id"]
    cwd = str(REPO)
    if w.get("worktree"):
        try:
            cwd = str(ensure_worktree(run_id, wid))
        except Exception as e:
            rec = {"run_id": run_id, "parent_run_id": "dev-scheduler",
                   "role": w.get("role", "implementer"), "agent": "opencode",
                   "task": w.get("task", wid), "started_at": now(),
                   "completed_at": now(), "status": "failed",
                   "input_artifact": f"development/runs/{run_id}/graph.json",
                   "output_artifact": "",
                   "note": f"worktree setup failed: {e}"}
            append_record(run_id, rec)
            return rec
    # --auto: workers run under the founder's one plan approval.
    # Auto-approves only permissions not explicitly denied, so the deny
    # rules in opencode.json (rm/del/push/sudo/...) stay load-bearing.
    cmd = resolve_argv(w.get("command", DEFAULT_WORKER_COMMAND))
    stdin_text = w.get("stdin_text")
    timeout = int(w.get("timeout", 900))
    started = now()
    async with sem:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE if stdin_text else None,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=cwd)
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(stdin_text.encode("utf-8")
                                     if stdin_text else None),
                    timeout=timeout)
                ok = proc.returncode == 0
                status = "completed" if ok else "failed"
                note = "" if ok else f"exit={proc.returncode} err={err.decode('utf-8', 'replace')[-500:]}"
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                status, note = "failed", f"timeout after {timeout}s"
        except Exception as e:
            status, note = "failed", f"spawn failed: {e}"
    completed = now()
    rec = {"run_id": run_id, "parent_run_id": "dev-scheduler",
           "role": w.get("role", "implementer"), "agent": w.get("agent", "opencode"),
           "task": w.get("task", wid), "started_at": started,
           "completed_at": completed, "status": status,
           "input_artifact": f"development/runs/{run_id}/graph.json",
           "output_artifact": w.get("output_artifact", ""),
           "note": note, "worker_id": wid, "cwd": cwd}
    append_record(run_id, rec)
    await sync_run_json(run_id, rec)
    return rec


def append_record(run_id: str, rec: dict) -> None:
    with open(dev_run.run_dir(run_id) / "executions.jsonl", "a",
              encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


_RUN_LOCK = None


def _lock() -> asyncio.Lock:
    global _RUN_LOCK
    if _RUN_LOCK is None:
        _RUN_LOCK = asyncio.Lock()
    return _RUN_LOCK


async def sync_run_json(run_id: str, rec: dict) -> None:
    """Mirror scheduler completions into run.json so trace/matrix stay true."""
    async with _lock():
        run = dev_run.load_run(run_id)
        entry = {"worker_id": rec.get("worker_id"),
                 "agent": rec.get("agent"), "status": rec.get("status"),
                 "artifact": rec.get("output_artifact", "")}
        if rec.get("role") in ("implementer", "integrator"):
            run.setdefault("workers", []).append(entry)
        elif rec.get("role") in ("planner", "qa", "reviewer"):
            run[rec["role"]] = entry
        dev_run.save_run(run_id, run)


async def run_all_async(run_id: str, cap: int) -> dict:
    graph = load_graph(run_id)
    workers = graph["workers"]
    by_id = {w["worker_id"]: w for w in workers}
    launched, completed, failed = set(), set(), set()
    sem = asyncio.Semaphore(cap)
    in_flight: dict = {}
    order_done: list = []

    while True:
        for w in ready_workers(workers, launched, completed, failed):
            launched.add(w["worker_id"])
            in_flight[w["worker_id"]] = asyncio.create_task(
                run_worker(run_id, w, sem))
        if not in_flight:
            break
        done, _ = await asyncio.wait(set(in_flight.values()),
                                     return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            rec = task.result()
            wid = rec.get("worker_id")
            del in_flight[wid]
            order_done.append(wid)
            (completed if rec["status"] == "completed" else failed).add(wid)

    blocked = [w["worker_id"] for w in workers if w["worker_id"] not in launched]
    for wid in blocked:
        w = by_id[wid]
        bad = [d for d in w.get("depends_on", []) if d in failed]
        rec = {"run_id": run_id, "parent_run_id": "dev-scheduler",
               "role": w.get("role", "implementer"), "agent": w.get("agent", "opencode"),
               "task": w.get("task", wid), "started_at": now(),
               "completed_at": now(), "status": "blocked",
               "input_artifact": f"development/runs/{run_id}/graph.json",
               "output_artifact": "",
               "note": f"blocked: prerequisite failed: {bad}", "worker_id": wid}
        append_record(run_id, rec)
        await sync_run_json(run_id, rec)
    return {"completed": sorted(completed), "failed": sorted(failed),
            "blocked": sorted(blocked)}


def cmd_init(a) -> None:
    dev_run.load_run(a.id)
    graph = json.loads(Path(a.graph).read_text(encoding="utf-8"))
    graph["max_concurrency"] = int(a.max or graph.get("max_concurrency")
                                   or default_max_concurrency())
    ids = [w["worker_id"] for w in graph["workers"]]
    if len(ids) != len(set(ids)):
        sys.exit("duplicate worker_id in graph")
    known = set(ids)
    for w in graph["workers"]:
        for d in w.get("depends_on", []):
            if d not in known:
                sys.exit(f"{w['worker_id']} depends on unknown {d}")
        w.setdefault("role", "implementer")
        w.setdefault("can_run_parallel", not w.get("depends_on"))
    graph_path(a.id).write_text(json.dumps(graph, indent=2), encoding="utf-8")
    print(f"graph stored: {len(ids)} workers, cap={graph['max_concurrency']}")


def cmd_run(a) -> None:
    graph = load_graph(a.id)
    cap = int(a.max or graph.get("max_concurrency") or default_max_concurrency())
    print(f"run {a.id}: {len(graph['workers'])} workers, cap={cap}")
    result = asyncio.run(run_all_async(a.id, cap))
    print(json.dumps(result, indent=2))
    if result["failed"] or result["blocked"]:
        sys.exit(1)


def cmd_clean(a) -> None:
    remove_worktrees(a.id)
    print(f"worktrees removed for {a.id}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--id", required=True)
    i.add_argument("--graph", required=True)
    i.add_argument("--max", default=None)
    i.set_defaults(fn=cmd_init)
    r = sub.add_parser("run")
    r.add_argument("--id", required=True)
    r.add_argument("--max", default=None)
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("clean-worktrees")
    c.add_argument("--id", required=True)
    c.set_defaults(fn=cmd_clean)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
