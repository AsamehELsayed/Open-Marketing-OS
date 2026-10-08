"""Proposal + approval + memory tools (v0.2 §6.5 / §13 / §15)."""
import json
import uuid
import hashlib
from datetime import datetime, timezone

from app.database import repos


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _context(project_id, args, tool_name):
    if not (project_id or "").strip():
        raise ValueError("project_id is required")
    workflow_source = args.get("workflow") or {}
    if not isinstance(workflow_source, dict):
        raise ValueError("workflow must be an object")
    workflow = {k: workflow_source.get(k, "") for k in (
        "conversation_id", "turn_id", "thread_id", "task_id", "campaign_id",
        "assigned_role", "requested_by")}
    for key in ("evidence_tool_run_id", "evidence_ref"):
        if key in workflow_source:
            workflow[key] = workflow_source[key]
    key = (args.get("idempotency_key") or "").strip()
    if not key:
        # Stable fallback for older callers; canonical args make a replay stable.
        seed = json.dumps({"tool": tool_name, "project_id": project_id,
                           "workflow": workflow, "args": args},
                          sort_keys=True, separators=(",", ":"), default=str)
        key = hashlib.sha256(seed.encode()).hexdigest()
    rid = hashlib.sha256(f"{project_id}:{tool_name}:{key}".encode()).hexdigest()[:32]
    return workflow, rid


def _linked_campaign(conn, project_id, cid):
    if not cid:
        return None
    row = repos.Campaigns.get(conn, cid, project_id)
    if row is None:
        raise ValueError(f"campaign {cid} not in project {project_id}")
    return row


def _linked_task(conn, project_id, task_id):
    if not task_id:
        return None
    row = repos.Tasks.get(conn, task_id, project_id)
    if row is None:
        raise ValueError(f"task {task_id} not in project {project_id}")
    return row


def t_propose_task(conn, *, project_id, root, args):
    title = (args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title is required", "status": "failed"}
    try:
        workflow, rid = _context(project_id, args, "propose_task")
        cid = args.get("campaign_id") or workflow.get("campaign_id") or None
        _linked_campaign(conn, project_id, cid)
        _linked_task(conn, project_id, workflow.get("task_id"))
    except ValueError as e:
        return {"ok": False, "error": str(e), "status": "failed"}
    row = {"id": rid, "project_id": project_id, "campaign_id": cid,
           "title": title, "lane": args.get("lane", ""), "status": "proposed",
           "acceptance": args.get("acceptance", ""), "job_id": None,
           "due_at": None, "workflow_json": json.dumps(workflow, sort_keys=True), "updated_at": _now()}
    try:
        row = repos.Tasks.insert_proposal(conn, row)
    except ValueError as e:
        return {"ok": False, "error": str(e), "status": "failed"}
    return {"ok": True, "task_id": row["id"], "status": "proposed"}


def t_propose_campaign(conn, *, project_id, root, args):
    title = (args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title is required", "status": "failed"}
    level = (args.get("approval_level") or "Green")
    if level not in ("Green", "Yellow", "Red", "green", "yellow", "red"):
        return {"ok": False, "error": f"invalid approval_level: {level}", "status": "failed"}
    try:
        workflow, rid = _context(project_id, args, "propose_campaign")
        _linked_campaign(conn, project_id, workflow.get("campaign_id"))
        _linked_task(conn, project_id, workflow.get("task_id"))
    except ValueError as e:
        return {"ok": False, "error": str(e), "status": "failed"}
    row = {"id": rid, "project_id": project_id, "title": title,
           "status": "drafted", "impact": 0, "confidence": 0, "effort": 0, "cost": 0,
           "approval_level": level, "measurement_window": None, "result": None,
           "learning_ref": None, "workflow_json": json.dumps(workflow, sort_keys=True), "updated_at": _now()}
    try:
        existing = repos.Campaigns.get(conn, rid, project_id)
        if existing and existing.get("status") == "proposed":
            old_content = {k: v for k, v in existing.items()
                           if k not in ("status", "updated_at")}
            new_content = {k: v for k, v in row.items()
                           if k not in ("status", "updated_at")}
            if old_content == new_content:
                existing.update({"status": "drafted", "updated_at": row["updated_at"]})
                repos.Campaigns.upsert(conn, existing)
                row = existing
            else:
                row = repos.Campaigns.insert_proposal(conn, row)
        else:
            row = repos.Campaigns.insert_proposal(conn, row)
    except ValueError as e:
        return {"ok": False, "error": str(e), "status": "failed"}
    return {"ok": True, "campaign_id": row["id"], "status": row.get("status", "drafted")}


def t_request_approval(conn, *, project_id, root, args):
    title = (args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title is required", "status": "failed"}
    try:
        workflow, rid = _context(project_id, args, "request_approval")
        _linked_campaign(conn, project_id, workflow.get("campaign_id"))
        _linked_task(conn, project_id, workflow.get("task_id"))
    except ValueError as e:
        return {"ok": False, "error": str(e), "status": "failed"}
    fields = args.get("fields_json", {})
    fields = dict(fields) if isinstance(fields, dict) else {"legacy_fields": str(fields)}
    fields["workflow"] = workflow
    row = {"id": rid, "project_id": project_id,
           "kind": args.get("kind", ""), "title": title,
           "body_md": args.get("body_md", ""), "status": "pending",
           "fields_json": json.dumps(fields) if isinstance(fields, dict) else str(fields),
           "decided_by": None, "decided_at": None, "updated_at": _now()}
    try:
        row = repos.Approvals.insert_proposal(conn, row)
    except ValueError as e:
        return {"ok": False, "error": str(e), "status": "failed"}
    return {"ok": True, "approval_id": row["id"], "status": "pending",
            "note": "Human approval required in Approvals UI; tools cannot approve."}


def t_propose_experiment(conn, *, project_id, root, args):
    hypothesis = (args.get("hypothesis") or "").strip()
    metric = (args.get("metric") or "").strip()
    if not hypothesis or not metric:
        return {"ok": False, "error": "hypothesis and metric are required", "status": "failed"}
    try:
        workflow, rid = _context(project_id, args, "propose_experiment")
        cid = args.get("campaign_id") or workflow.get("campaign_id") or None
        _linked_campaign(conn, project_id, cid)
        _linked_task(conn, project_id, workflow.get("task_id"))
    except ValueError as e:
        return {"ok": False, "error": str(e), "status": "failed"}
    try:
        days = max(0, int(args.get("window_days", 0)))
    except (TypeError, ValueError):
        return {"ok": False, "error": "window_days must be an integer", "status": "failed"}
    row = {"id": rid, "project_id": project_id, "campaign_id": cid,
           "hypothesis": hypothesis, "metric": metric, "window_days": days,
           "start_date": args.get("start_date"), "next_review": args.get("next_review"),
           "status": "proposed", "stop_condition": args.get("stop_condition", ""),
           "workflow_json": json.dumps(workflow, sort_keys=True), "updated_at": _now()}
    try:
        row = repos.Experiments.insert_proposal(conn, row)
    except ValueError as e:
        return {"ok": False, "error": str(e), "status": "failed"}
    return {"ok": True, "experiment_id": row["id"], "status": "proposed"}


def t_reject_task(conn, *, project_id, root, args):
    task_id = (args.get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "error": "task_id is required", "status": "failed"}
    try:
        if not (project_id or "").strip():
            raise ValueError("project_id is required")
        row = repos.Tasks.reject_proposal(conn, task_id, project_id,
                                          (args.get("reason") or "").strip())
        return {"ok": True, "task_id": row["id"], "status": row["status"]}
    except ValueError as e:
        return {"ok": False, "error": str(e), "status": "failed"}


ALLOWED_MEMORY = ("decision", "offer", "preference", "strategy", "learning")


def t_remember(conn, *, project_id, root, args):
    kind = (args.get("kind") or "").strip()
    body = (args.get("body_md") or "").strip()
    if kind not in ALLOWED_MEMORY:
        return {"ok": False, "error": f"kind must be one of {ALLOWED_MEMORY}", "status": "failed"}
    if len(body) < 10:
        return {"ok": False, "error": "body_md too short; only durable business knowledge", "status": "failed"}
    row = {"id": uuid.uuid4().hex, "project_id": project_id, "kind": kind,
           "body_md": body, "confidence": args.get("confidence", "MEDIUM"),
           "source_ref": args.get("source_ref", ""), "created_at": _now(), "updated_at": _now()}
    repos.Memories.insert(conn, row)
    return {"ok": True, "memory_id": row["id"]}


def register_propose_tools(registry):
    from .registry import ToolDef
    workflow_props = {"workflow": {"type": "object"}, "idempotency_key": {"type": "string"}}
    registry.register(ToolDef(name="propose_task", description="Draft a task (status proposed, GREEN).",
                              parameters={"type": "object", "required": ["title"],
                                          "properties": {"title": {"type": "string"},
                                                         "lane": {"type": "string"},
                                                         "acceptance": {"type": "string"},
                                                         "campaign_id": {"type": "string"}, **workflow_props}},
                              side_effect="green", handler=t_propose_task))
    registry.register(ToolDef(name="propose_campaign", description="Create a local campaign draft (status drafted, GREEN).",
                              parameters={"type": "object", "required": ["title"],
                                          "properties": {"title": {"type": "string"},
                                                         "rationale_md": {"type": "string"},
                                                         "approval_level": {"type": "string"}, **workflow_props}},
                              side_effect="green", handler=t_propose_campaign))
    registry.register(ToolDef(name="propose_experiment", description="Draft an experiment in proposed status; records no measurements or outcome.",
                              parameters={"type": "object", "required": ["hypothesis", "metric"],
                                          "properties": {"hypothesis": {"type": "string"},
                                                         "metric": {"type": "string"},
                                                         "window_days": {"type": "integer"},
                                                         "start_date": {"type": "string"},
                                                         "next_review": {"type": "string"},
                                                         "stop_condition": {"type": "string"},
                                                         "campaign_id": {"type": "string"}, **workflow_props}},
                              side_effect="green", handler=t_propose_experiment))
    registry.register(ToolDef(name="reject_task", description="Mark a proposed project task rejected after approval rejection.",
                              parameters={"type": "object", "required": ["task_id"],
                                          "properties": {"task_id": {"type": "string"},
                                                         "reason": {"type": "string"}, **workflow_props}},
                              side_effect="green", handler=t_reject_task))
    registry.register(ToolDef(name="request_approval", description="Create a pending approval (human decides).",
                              parameters={"type": "object", "required": ["title"],
                                          "properties": {"kind": {"type": "string"},
                                                         "title": {"type": "string"},
                                                         "body_md": {"type": "string"},
                                                         "fields_json": {"type": "object"}, **workflow_props}},
                              side_effect="green", handler=t_request_approval))
    registry.register(ToolDef(name="remember", description="Store durable project memory (decision/offer/preference/strategy/learning only).",
                              parameters={"type": "object", "required": ["kind", "body_md"],
                                          "properties": {"kind": {"type": "string"},
                                                         "body_md": {"type": "string"},
                                                         "confidence": {"type": "string"},
                                                         "source_ref": {"type": "string"}}},
                              side_effect="green", handler=t_remember))
