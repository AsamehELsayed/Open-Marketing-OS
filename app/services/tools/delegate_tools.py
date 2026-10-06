"""Delegation + job tools (v0.2 §6.4 / §8 / §14). GREEN to start, gated inside.

DEV-007 W1: Marketing PM work is a LangGraph-native persistent job
(in-process worker) — no external agent runtime in the product path.
"""
import json
from pathlib import Path


def t_delegate_to_marketing_pm(conn, *, project_id, root, args):
    goal = (args.get("goal") or "").strip()
    brief_md = (args.get("brief_md") or "").strip()
    if not goal:
        return {"ok": False, "error": "goal is required", "status": "failed"}
    lane = (args.get("lane") or "research").strip() or "research"
    from app.services import jobs as jobsvc
    from app import deps as _deps
    db_path = getattr(_deps, "DB_PATH", Path(root) / "data" / "marketing.db")
    cmd = ["langgraph", "marketing_pm"]
    conversation_id = args.get("conversation_id", "")
    try:
        row = jobsvc.submit(conn, db_path, root, kind=f"marketing_pm:{lane}", cmd=cmd,
                            side_effect="green", project_id=project_id,
                            conversation_id=conversation_id, job_type="marketing_pm",
                            brief_md=f"GOAL: {goal[:1000]}" +
                                    (f"\n\n{brief_md[:4000]}" if brief_md.strip() else ""),
                            stdin_text=goal[:500])
    except TypeError:
        row = jobsvc.submit(conn, db_path, root, kind=f"marketing_pm:{lane}", cmd=cmd, side_effect="green")
        row["project_id"] = project_id
        try:
            from app.database import repos
            full = repos.BackgroundJobs.get(conn, row["id"]) or {}
            full.update({"project_id": project_id, "conversation_id": conversation_id})
            repos.BackgroundJobs.upsert(conn, full)
            row = full
        except Exception:
            pass
    # Persist brief packet for the conversation
    brief_path = Path(root) / "data" / "projects" / project_id / "job-briefs"
    try:
        brief_path.mkdir(parents=True, exist_ok=True)
        (brief_path / f"{row['id']}.md").write_text(
            f"# {goal}\n\nLane: {lane}\nProject: {project_id}\n\n{brief_md}\n", encoding="utf-8")
    except OSError:
        pass
    return {"ok": True, "job_id": row["id"], "status": row.get("status", "queued"),
            "lane": lane, "note": "Ask 'where are we?' to check progress."}


def t_get_job_status(conn, *, project_id, root, args):
    job_id = (args.get("job_id") or "").strip()
    if not job_id:
        # convenience: latest job in this project
        from app.database import repos
        jobs = repos.BackgroundJobs.list(conn, project_id)
        if not jobs:
            return {"ok": True, "status": "none", "note": "no jobs in this project yet"}
        job = sorted(jobs, key=lambda j: j.get("started_at") or "")[-1]
    else:
        from app.database import repos
        try:
            job = repos.BackgroundJobs.get(conn, job_id, project_id)
        except ValueError as e:
            return {"ok": False, "error": str(e), "status": "failed"}
        if job is None:
            return {"ok": False, "error": f"job {job_id} not found", "status": "failed"}
    tail: list[str] = []
    try:
        lp = (Path(root) / job.get("log_path", "") ) if job.get("log_path") else None
        if lp and lp.exists():
            tail = lp.read_text(encoding="utf-8").splitlines()[-10:]
    except OSError:
        pass
    result_summary = None
    rp = Path(root) / "data" / "projects" / project_id / "job-results" / f"{job['id']}.json"
    if rp.exists():
        try:
            result_summary = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"ok": True, "job_id": job["id"], "status": job.get("status"),
            "kind": job.get("kind"), "log_tail": tail, "result_summary": result_summary}


def register_delegate_tools(registry):
    from .registry import ToolDef
    registry.register(ToolDef(name="delegate_to_marketing_pm",
                              description="Delegate deep/multi-step marketing work to Marketing PM (returns job_id).",
                              parameters={"type": "object", "required": ["goal"],
                                          "properties": {"goal": {"type": "string"},
                                                         "brief_md": {"type": "string"},
                                                         "lane": {"type": "string"},
                                                         "conversation_id": {"type": "string"}}},
                              side_effect="green", handler=t_delegate_to_marketing_pm))
    registry.register(ToolDef(name="get_job_status",
                              description="Check background job status and log tail (continuity).",
                              parameters={"type": "object",
                                          "properties": {"job_id": {"type": "string"}}},
                              side_effect="green", handler=t_get_job_status))
