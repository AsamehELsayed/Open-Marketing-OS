"""Local background jobs (plan §14). In-process ThreadPoolExecutor, no Redis/Celery.

States: queued → running → completed/failed; waiting_approval (yellow/red
without approval — never dispatched); cancelled (queued/waiting only).
Job rows persist in SQLite; stdout/stderr go to data/job-logs/<id>.log.
Each worker opens its own connection (sqlite handles are not shared).

DEV-007 W1: marketing_pm jobs run in-process via the LangGraph path;
generic green utility argv jobs still use the approval-gated local runner.
No OpenCode import from the product graph.
"""
import json
import os
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from app.contracts.events import sanitize_user_text
from app.database import repos
from app.database.identity import DEFAULT_PROJECT_ID
from app.services import state as store

GREEN, YELLOW, RED = "green", "yellow", "red"
SCRUB_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")

_POOL = ThreadPoolExecutor(max_workers=2)
TERMINAL = ("completed", "failed", "cancelled")


def check_side_effect(conn, side_effect: str, approval_id: str | None) -> tuple[bool, str]:
    """Approval gate. Returns (allowed, reason). GREEN always runs."""
    if side_effect not in (GREEN, YELLOW, RED):
        return False, f"unknown side-effect class: {side_effect}"
    if side_effect == GREEN:
        return True, ""
    if not approval_id:
        return False, f"{side_effect} side effect requires an approved approval"
    row = repos.Approvals.get(conn, approval_id)
    if row is None:
        return False, f"approval {approval_id} not found"
    if row["status"] != "approved":
        return False, f"approval {approval_id} is {row['status']}, not approved"
    return True, ""


def scrub_env() -> dict:
    return {k: v for k, v in os.environ.items()
            if not any(m in k.upper() for m in SCRUB_MARKERS)}


def _cwd_ok(root: Path, cwd: Path) -> bool:
    try:
        cwd.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _resolve_argv(cmd: list[str]) -> list[str]:
    """Resolve cmd[0] via PATH. Windows npm shims resolve to .CMD/.BAT, which
    CreateProcess cannot execute directly — route through COMSPEC /c with the
    same argv list (still no shell string parsing)."""
    if not cmd:
        return cmd
    resolved = shutil.which(cmd[0])
    if resolved is None:
        return cmd
    if resolved.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", resolved, *cmd[1:]]
    return [resolved, *cmd[1:]]


def run_task(conn, root: str | Path, cmd: list[str], side_effect: str = GREEN,
             approval_id: str | None = None, timeout: int = 300,
             cwd: str | Path | None = None, stdin_text: str | None = None) -> dict:
    """Execute local green utility argv synchronously. Never raises; failure is data.
    Optional stdin_text is piped via stdin (no shell involved)."""
    root = Path(root)
    if not cmd:
        return {"ok": False, "status": "failed", "blocked_reason": "empty command",
                "stdout": "", "stderr": "", "exit_code": None, "timed_out": False}
    allowed, reason = check_side_effect(conn, side_effect, approval_id)
    if not allowed:
        return {"ok": False, "status": "blocked", "blocked_reason": reason,
                "stdout": "", "stderr": "", "exit_code": None, "timed_out": False}
    if Path(cmd[0]).name.lower() in ("opencode", "agy", "codex"):
        return {"ok": False, "status": "blocked",
                "blocked_reason": "external agent runtimes are not product executors",
                "stdout": "", "stderr": "", "exit_code": None, "timed_out": False}
    workdir = Path(cwd) if cwd else root
    if not _cwd_ok(root, workdir):
        return {"ok": False, "status": "blocked",
                "blocked_reason": f"cwd outside workspace: {workdir}",
                "stdout": "", "stderr": "", "exit_code": None, "timed_out": False}
    if not (1 <= timeout <= 900):
        return {"ok": False, "status": "blocked", "blocked_reason": "timeout out of range 1..900",
                "stdout": "", "stderr": "", "exit_code": None, "timed_out": False}
    argv = _resolve_argv(cmd)
    try:
        proc = subprocess.run(argv, cwd=str(workdir), env=scrub_env(), shell=False,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, input=stdin_text)
        ok = proc.returncode == 0
        return {"ok": ok, "status": "completed" if ok else "failed", "blocked_reason": "",
                "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:],
                "exit_code": proc.returncode, "timed_out": False}
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        err = (e.stderr or "") if isinstance(e.stderr, str) else ""
        return {"ok": False, "status": "failed", "blocked_reason": f"timeout after {timeout}s",
                "stdout": out[-4000:], "stderr": err[-4000:],
                "exit_code": None, "timed_out": True}
    except Exception:
        return {"ok": False, "status": "failed", "blocked_reason": "process start failed",
                "stdout": "", "stderr": "", "exit_code": None, "timed_out": False}


def _is_langgraph_job(cmd: list[str], job_type: str = "", kind: str = "") -> bool:
    if job_type == "marketing_pm":
        return True
    if kind.startswith("marketing_pm:"):
        return True
    return bool(cmd) and cmd[0] in ("langgraph", "marketing_pm")


def _run_langgraph_worker(conn, root: str | Path, job: dict, spec: dict) -> dict:
    """In-process LangGraph marketing-PM worker (no subprocess, no OpenCode)."""
    try:
        from app.graphs.account_manager_graph import build_account_manager_graph
        from app.graphs.state import initial_state

        project_id = job.get("project_id") or DEFAULT_PROJECT_ID
        conversation_id = job.get("conversation_id") or ""
        turn_id = job.get("id") or uuid.uuid4().hex
        brief = spec.get("stdin_text") or job.get("brief_md") or ""
        user_request = brief if brief.strip() else (spec.get("goal") or "background research")
        graph = build_account_manager_graph()
        state = initial_state(project_id=project_id, conversation_id=conversation_id,
                              turn_id=turn_id, user_request=user_request)
        result = graph.invoke(state, config={"configurable": {"thread_id": f"job-{turn_id}"}})
        result = result or {}
        if result.get("errors"):
            return {"ok": False, "status": "failed",
                    "blocked_reason": "The background request could not be completed.",
                    "stdout": "", "stderr": "The background request could not be completed.",
                    "exit_code": 1, "timed_out": False}
        final = str(result.get("final_answer") or "").strip()
        stdout = final or "RESULT SUMMARY\n\n- background worker completed with empty final answer.\n- Recommended next step: review the job log."
        return {"ok": True, "status": "completed", "blocked_reason": "",
                "stdout": stdout[-4000:], "stderr": "", "exit_code": 0, "timed_out": False}
    except Exception:
        msg = "The background request could not be completed."
        return {"ok": False, "status": "failed", "blocked_reason": msg,
                "stdout": "", "stderr": msg, "exit_code": 1, "timed_out": False}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _worker_label(job: dict, phase: str) -> str:
    """Human worker/lane state from real job fields. No invented progress."""
    kind = (job.get("kind", "") or "").split(":")
    lane = kind[1].replace("_", " ").title() if len(kind) > 1 and kind[1] else "Background work"
    goal = ((job.get("brief_md", "") or "").split("\n")[0].removeprefix("GOAL:").strip())[:120]
    what = f"{lane} — {goal}" if goal else lane
    return {"started": f"{what} started",
            "completed": f"{what} completed",
            "failed": f"{what} needs attention"}.get(phase, what)


def log_path_for(root: Path, job_id: str) -> Path:
    d = Path(root) / "data" / "job-logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{job_id}.log"


def result_path_for(root: Path, project_id: str, job_id: str) -> Path:
    d = Path(root) / "data" / "projects" / project_id / "job-results"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{job_id}.json"


def sanitize_job_result(payload: dict | None) -> dict:
    source = payload if isinstance(payload, dict) else {}
    result = {
        "job_id": sanitize_user_text(source.get("job_id", ""), 128),
        "kind": sanitize_user_text(source.get("kind", ""), 120),
        "job_type": sanitize_user_text(source.get("job_type", ""), 120),
        "status": sanitize_user_text(source.get("status", ""), 40),
        "stdout_tail": sanitize_user_text(source.get("stdout_tail", ""), 2000),
        "finished_at": sanitize_user_text(source.get("finished_at", ""), 80),
    }
    try:
        result["exit_code"] = int(source.get("exit_code"))
    except (TypeError, ValueError):
        result["exit_code"] = None
    return result


def submit(conn, db_path, root, kind: str, cmd: list[str], side_effect: str = "green",
           approval_id: str | None = None, timeout: int = 120,
           project_id: str = DEFAULT_PROJECT_ID, conversation_id: str = "",
           job_type: str = "", brief_md: str = "",
           stdin_text: str | None = None, cwd: str | Path | None = None) -> dict:
    """Queue a job. Yellow/red without approval parks as waiting_approval."""
    root = Path(root)
    job_id = uuid.uuid4().hex
    allowed, reason = check_side_effect(conn, side_effect, approval_id)
    row = {"id": job_id, "kind": kind,
           "status": "queued" if allowed else "waiting_approval",
           "cmd_json": json.dumps({"cmd": cmd, "side_effect": side_effect,
                                   "approval_id": approval_id, "timeout": timeout,
                                   "stdin_text": stdin_text, "job_type": job_type,
                                   "blocked_reason": "" if allowed else reason}),
           "cwd": str(Path(cwd) if cwd else root), "exit_code": None,
           "log_path": str(log_path_for(root, job_id).relative_to(root)),
           "started_at": None, "finished_at": None}
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(background_jobs)").fetchall()}
        if "project_id" in cols:
            row["project_id"] = project_id
        if "conversation_id" in cols:
            row["conversation_id"] = conversation_id
        if "job_type" in cols:
            row["job_type"] = job_type
        if "brief_md" in cols:
            row["brief_md"] = brief_md or ""
        if "created_at" in cols:
            row["created_at"] = _now()
        if "updated_at" in cols:
            row["updated_at"] = _now()
    except Exception:
        pass
    repos.BackgroundJobs.upsert(conn, row)
    store._event(conn, "job_queued" if allowed else "job_waiting_approval",
                 job_id, {"kind": kind, "reason": reason})
    if conversation_id:
        try:
            from app.services import turns as turnsvc
            turnsvc.emit_event(str(db_path), project_id=project_id,
                               conversation_id=conversation_id, job_id=job_id,
                               event_type="worker_started" if allowed else "approval_required",
                               label=("Background work queued" if allowed
                                      else "Waiting for your approval"),
                               detail="" if allowed else reason,
                               metadata={"kind": kind, "status": row["status"]})
        except Exception:
            pass
    if allowed:
        _POOL.submit(_run, str(db_path), str(root), job_id)
    return row


def _run(db_path: str, root: str, job_id: str) -> None:
    from app.database.sqlite import connect
    from app.services import turns as turnsvc
    conn = connect(db_path)
    try:
        job = repos.BackgroundJobs.get(conn, job_id)
        if job is None or job["status"] != "queued":
            return
        job["status"] = "running"
        job["started_at"] = _now()
        repos.BackgroundJobs.upsert(conn, job)
        turnsvc.emit_event(db_path, project_id=job.get("project_id") or DEFAULT_PROJECT_ID,
                           conversation_id=job.get("conversation_id") or "",
                           job_id=job_id, event_type="worker_started",
                           label=_worker_label(job, "started"),
                           metadata={"kind": job.get("kind", ""), "status": "running"})
        turnsvc.emit_event(db_path, project_id=job.get("project_id") or DEFAULT_PROJECT_ID,
                           conversation_id=job.get("conversation_id") or "",
                           job_id=job_id, event_type="job_status_changed",
                           label="Background work running",
                           metadata={"kind": job.get("kind", ""), "status": "running"})
        spec = json.loads(job["cmd_json"])
        cmd = spec.get("cmd") or []
        job_type = job.get("job_type") or spec.get("job_type") or ""
        if _is_langgraph_job(cmd, job_type=job_type, kind=job.get("kind") or ""):
            result = _run_langgraph_worker(conn, root, job, spec)
        else:
            result = run_task(conn, root, cmd, spec.get("side_effect", "green"),
                              spec.get("approval_id"), spec.get("timeout", 120),
                              cwd=job.get("cwd") or None,
                              stdin_text=spec.get("stdin_text"))
        with open(Path(root) / job["log_path"], "w", encoding="utf-8") as f:
            f.write(f"--- stdout ---\n{result['stdout']}\n--- stderr ---\n{result['stderr']}\n")
        job["status"] = result["status"] if result["status"] in ("completed", "failed") else "failed"
        job["exit_code"] = result["exit_code"]
        job["finished_at"] = _now()
        try:
            job["updated_at"] = _now()
        except Exception:
            pass
        repos.BackgroundJobs.upsert(conn, job)
        done_ok = job["status"] == "completed"
        turnsvc.emit_event(db_path, project_id=job.get("project_id") or DEFAULT_PROJECT_ID,
                           conversation_id=job.get("conversation_id") or "",
                           job_id=job_id,
                           event_type="worker_completed" if done_ok else "worker_failed",
                           label=_worker_label(job, "completed" if done_ok else "failed"),
                           metadata={"kind": job.get("kind", ""), "status": job["status"]})
        turnsvc.emit_event(db_path, project_id=job.get("project_id") or DEFAULT_PROJECT_ID,
                           conversation_id=job.get("conversation_id") or "",
                           job_id=job_id, event_type="job_status_changed",
                           label=f"Background work {job['status']}",
                           metadata={"kind": job.get("kind", ""), "status": job["status"]})
        _write_result_json(root, job, result)
        store._event(conn, f"job_{job['status']}", job_id,
                     {"exit_code": result["exit_code"], "reason": result["blocked_reason"]})
    finally:
        conn.close()


def _write_result_json(root: str, job: dict, result: dict) -> None:
    """Persist a machine-readable result next to the log (best effort).

    Follow-ups synthesize from this + the log tail — never from chat text."""
    try:
        pid = job.get("project_id") or DEFAULT_PROJECT_ID
        clean_stdout = sanitize_user_text(
            result.get("stdout", "") or "", 1_000_000
        )
        payload = sanitize_job_result({
            "job_id": job["id"],
            "kind": job.get("kind", ""),
            "job_type": job.get("job_type", ""),
            "status": job.get("status", ""),
            "exit_code": result.get("exit_code"),
            "stdout_tail": clean_stdout[-2000:],
            "finished_at": job.get("finished_at", ""),
        })
        with open(result_path_for(Path(root), pid, job["id"]), "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


def read_result_json(root, project_id: str, job_id: str) -> dict | None:
    try:
        p = Path(root) / "data" / "projects" / project_id / "job-results" / f"{job_id}.json"
        if p.exists():
            return sanitize_job_result(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        pass
    return None


PM_BRIEF_TEMPLATE = """You are the Marketing PM worker for project '{project}'.
Goal: {goal}
Lane: {lane}
Brief:
{brief}

Rules: research/analysis only. No external sends, no spend, no publishing.
End your output with a RESULT SUMMARY section (findings + recommended next step).
Reply in the user's language from the brief (Arabic if the brief is Arabic)."""


def request_persistent(conn, db_path, root, project_id: str, conversation_id: str,
                       goal: str, brief_md: str = "", lane: str = "research",
                       timeout: int = 600) -> dict:
    """Create a REAL persistent application job (SOL hybrid model B).

    Called AFTER the Account Manager provider turn has ended — the job runner
    launches the in-process LangGraph worker later/outside that process
    (sequential, never nested). Raises ValueError on bad input (fail closed)."""
    goal = (goal or "").strip()
    if not project_id or not str(project_id).strip():
        raise ValueError("project_id is required (fail closed)")
    if not goal:
        raise ValueError("goal is required")
    lane = (lane or "research").strip() or "research"
    prompt = PM_BRIEF_TEMPLATE.format(project=project_id, goal=goal[:1000],
                                      lane=lane, brief=(brief_md or "")[:4000])
    stored_brief = f"GOAL: {goal[:1000]}" + (f"\n\n{brief_md[:4000]}" if (brief_md or "").strip() else "")
    workdir = Path(root) / "data" / "projects" / project_id
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        workdir = Path(root)
    row = submit(conn, db_path, root, kind=f"marketing_pm:{lane}",
                 cmd=["langgraph", "marketing_pm"],
                 side_effect="green", timeout=timeout, project_id=project_id,
                 conversation_id=conversation_id, job_type="marketing_pm",
                 brief_md=stored_brief, stdin_text=prompt, cwd=str(workdir))
    return row


def cancel(conn, job_id: str) -> dict:
    job = repos.BackgroundJobs.get(conn, job_id)
    if job is None:
        raise KeyError(job_id)
    if job["status"] in TERMINAL:
        raise ValueError(f"job {job_id} already {job['status']}")
    if job["status"] == "running":
        raise ValueError(f"job {job_id} is running and cannot be killed")
    job["status"] = "cancelled"
    job["finished_at"] = _now()
    try:
        job["updated_at"] = _now()
    except Exception:
        pass
    repos.BackgroundJobs.upsert(conn, job)
    store._event(conn, "job_cancelled", job_id, {})
    return job


def summary(conn, root, job_id: str, tail: int = 20) -> dict:
    """Job + log tail for the UI (result/failure summary without full log reads)."""
    job = repos.BackgroundJobs.get(conn, job_id)
    if job is None:
        raise KeyError(job_id)
    lines: list[str] = []
    try:
        text = (Path(root) / job["log_path"]).read_text(encoding="utf-8")
        lines = text.splitlines()[-tail:]
    except OSError:
        pass
    return {"job": job, "log_tail": lines}
