"""Structured state tools (v0.2 §6.1). Authoritative SQLite reads, GREEN, read-only."""
from app.database import repos
from app.database.identity import DEFAULT_PROJECT_ID
from app.services import state as store


def _limit(args, default=10, cap=50):
    try:
        n = int(args.get("limit", default))
    except (ValueError, TypeError):
        n = default
    return max(1, min(cap, n))


def t_get_project(conn, *, project_id, root, args):
    p = repos.Projects.get(conn, project_id)
    if p is None:
        company = repos.Companies.get(conn, project_id) or repos.Companies.get(conn, DEFAULT_PROJECT_ID)
        return {"ok": True, "project": {"id": project_id, "name": (company or {}).get("name", project_id),
                                        "website": (company or {}).get("website", ""),
                                        "goal": "", "status": "active"}}
    return {"ok": True, "project": p}


def t_get_project_state(conn, *, project_id, root, args):
    campaigns = repos.Campaigns.list(conn, project_id)
    approvals = repos.Approvals.list(conn, project_id=project_id)
    tasks = repos.Tasks.list(conn, project_id)
    experiments = repos.Experiments.list(conn, project_id)
    try:
        measurements = store.all_measurements(conn, project_id)
    except Exception:
        measurements = []
    try:
        conflicts = [c for c in store.list_conflicts(conn)
                     if ((c.get("current") or {}).get("project_id")
                         or ((c.get("current") or {}).get("id")
                             if (c.get("current") or {}).get("table") == "companies" else None))
                     == project_id]
    except Exception:
        conflicts = []
    pending = [a for a in approvals if a.get("status") == "pending"]
    active = [c for c in campaigns if c.get("status") in ("approved", "executing", "measuring")]
    digest = {
        "project_id": project_id,
        "campaigns_total": len(campaigns), "campaigns_active": len(active),
        "approvals_pending": len(pending), "approvals_pending_ids": [a["id"] for a in pending[:5]],
        "tasks_open": len([t for t in tasks if t.get("status") == "open"]),
        "tasks_blocked": len([t for t in tasks if t.get("status") == "blocked"]),
        "experiments": len(experiments), "measurements": len(measurements),
        "conflicts": len(conflicts),
        "chroma": ((repos.Settings.get(conn, "chroma_status") or {}).get("value", "unknown")),
    }
    sections = args.get("sections")
    if isinstance(sections, list) and sections:
        digest = {k: v for k, v in digest.items() if k in sections or k == "project_id"}
    return {"ok": True, "state": digest}


def t_get_campaigns(conn, *, project_id, root, args):
    rows = repos.Campaigns.list(conn, project_id)
    status = args.get("status")
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return {"ok": True, "campaigns": rows[:_limit(args)]}


def t_get_tasks(conn, *, project_id, root, args):
    rows = repos.Tasks.list(conn, project_id)
    status = args.get("status")
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return {"ok": True, "tasks": rows[:_limit(args)]}


def t_get_approvals(conn, *, project_id, root, args):
    status = args.get("status")
    rows = repos.Approvals.list(conn, status=status, project_id=project_id)
    return {"ok": True, "approvals": rows[:_limit(args, default=20)]}


def t_get_measurements(conn, *, project_id, root, args):
    exp_id = args.get("experiment_id")
    if exp_id:
        exp = repos.Experiments.get(conn, exp_id, project_id)
        if exp is None:
            return {"ok": False, "error": f"experiment {exp_id} not found", "status": "failed"}
        meas = repos.Measurements.for_experiment(conn, exp_id)
        return {"ok": True, "measurements": meas[:_limit(args)], "experiment": exp}
    experiments = repos.Experiments.list(conn, project_id)
    out = []
    for e in experiments[:_limit(args)]:
        out.extend(repos.Measurements.for_experiment(conn, e["id"]))
    learnings = repos.Learnings.list(conn, project_id)[:5]
    return {"ok": True, "measurements": out[:_limit(args)], "learnings": learnings}


STATE_TOOLS = [
    ("get_project", "Current project identity (name, website, goal).", {"type": "object", "properties": {}}),
    ("get_project_state", "Authoritative digest: counts of campaigns/approvals/tasks/experiments/measurements.",
     {"type": "object", "properties": {"sections": {"type": "array", "items": {"type": "string"}}}}),
    ("get_campaigns", "List project campaigns, optional status filter.",
     {"type": "object", "properties": {"status": {"type": "string"}, "limit": {"type": "integer"}}}),
    ("get_tasks", "List project tasks, optional status filter.",
     {"type": "object", "properties": {"status": {"type": "string"}, "limit": {"type": "integer"}}}),
    ("get_approvals", "List project approvals, optional status filter.",
     {"type": "object", "properties": {"status": {"type": "string"}, "limit": {"type": "integer"}}}),
    ("get_measurements", "Measurements and learnings; optional experiment_id.",
     {"type": "object", "properties": {"experiment_id": {"type": "string"}, "limit": {"type": "integer"}}}),
]

_HANDLERS = {"get_project": t_get_project, "get_project_state": t_get_project_state,
             "get_campaigns": t_get_campaigns, "get_tasks": t_get_tasks,
             "get_approvals": t_get_approvals, "get_measurements": t_get_measurements}


def register_state_tools(registry):
    from .registry import ToolDef
    for name, desc, params in STATE_TOOLS:
        registry.register(ToolDef(name=name, description=desc, parameters=params,
                                  side_effect="green", handler=_HANDLERS[name]))
