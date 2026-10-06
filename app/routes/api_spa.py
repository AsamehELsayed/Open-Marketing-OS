"""DEV-004 W1: additive JSON APIs for the React SPA.

New router, prefix /api. Read-only except rename/archive (which reuse
store.archive_conversation semantics for archive). All responses use the
{"ok": True, "data": ...} envelope. Rows are project-scoped — a
project_id filter never returns foreign-project rows, and single-resource
reads verify ownership before returning.

Existing Jinja routes, templates, and the streaming contract
(POST /chat/turn + GET /chat/turns/{id}/events) are untouched.
"""
import json as _json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app import deps
from app.contracts.events import (
    project_event_boundary,
    safe_error_message,
    sanitize_metadata,
    sanitize_user_text,
)
from app.database import repos
from app.services import state as store
from datetime import datetime, timezone

router = APIRouter(prefix="/api", tags=["spa"])


def _ok(data):
    return {"ok": True, "data": data}


def _project_or_404(conn, project_id: str | None):
    if not project_id:
        return None
    proj = repos.Projects.get(conn, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="unknown project")
    return proj


def _convo_or_404(conn, convo_id: str, project_id: str | None = None):
    try:
        convo = repos.Conversations.get(conn, convo_id, project_id)
    except ValueError as e:
        raise HTTPException(
            status_code=403,
            detail=safe_error_message(e, "The conversation is unavailable."),
        )
    if convo is None:
        raise HTTPException(status_code=404, detail="unknown conversation")
    return convo


def _citations_of(msg: dict) -> list:
    try:
        parsed = _json.loads(msg.get("citations_json") or "[]")
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [
        sanitize_metadata(item) if isinstance(item, dict)
        else sanitize_user_text(item, 2000)
        for item in parsed
    ]


# ---------- projects ----------

@router.get("/projects")
def spa_projects():
    with deps.get_db() as conn:
        rows = repos.Projects.list(conn)
    return _ok([
        {"id": r["id"], "name": r["name"],
         "website": r.get("website", ""), "goal": r.get("goal", "")}
        for r in rows if r.get("status") != "archived"
    ])


# ---------- chats ----------

@router.get("/chats")
def spa_chats(
    project_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    status: str = Query(default="open"),
):
    """List conversations. Default status=open (sidebar semantics);
    pass status=all to include archived."""
    with deps.get_db() as conn:
        if project_id:
            _project_or_404(conn, project_id)
            rows = repos.Conversations.list(conn, project_id)
        else:
            rows = repos.Conversations.list(conn)
    if status != "all":
        rows = [c for c in rows if c.get("status") == status]
    if q:
        needle = q.strip().lower()
        rows = [c for c in rows if needle in (c.get("title") or "").lower()]
    rows.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
    return _ok([
        {"id": c["id"], "title": c.get("title", ""),
         "project_id": c.get("project_id", ""),
         "updated_at": c.get("updated_at", "")}
        for c in rows
    ])


class RenameBody(BaseModel):
    title: str


@router.post("/chats/{convo_id}/rename")
def spa_rename_chat(convo_id: str, body: RenameBody):
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if len(title) > 120:
        raise HTTPException(status_code=400, detail="title too long (max 120)")
    with deps.get_db() as conn:
        convo = _convo_or_404(conn, convo_id)
        convo["title"] = title
        convo["updated_at"] = store.now_iso()
        repos.Conversations.upsert(conn, convo)
        out = _convo_or_404(conn, convo_id)
    return _ok({"id": out["id"], "title": out.get("title", ""),
                "project_id": out.get("project_id", ""),
                "updated_at": out.get("updated_at", "")})


@router.post("/chats/{convo_id}/archive")
def spa_archive_chat(convo_id: str):
    with deps.get_db() as conn:
        convo = _convo_or_404(conn, convo_id)
        try:
            out = store.archive_conversation(conn, convo_id, convo.get("project_id"))
        except (KeyError, ValueError) as e:
            raise HTTPException(
                status_code=400,
                detail=safe_error_message(e, "The conversation could not be updated."),
            )
    return _ok({"id": out["id"], "title": out.get("title", ""),
                "project_id": out.get("project_id", ""),
                "updated_at": out.get("updated_at", "")})


@router.get("/chats/{convo_id}/last_turn")
def spa_chat_last_turn(convo_id: str, project_id: str | None = Query(default=None)):
    """The conversation's latest turn with persisted execution events, so a
    reloaded chat can replay that turn's tree (DEV-008-SKILLS-OPS W12).

    Scoped fail-closed to the conversation's own project: the turn is resolved
    from the conversation's stored project_id (never from a global active
    value), and only matching events qualify - a foreign project's turn can
    never match. turn_id is empty when the conversation has no turn with
    events. Additive: the existing /messages response shape is unchanged.
    """
    with deps.get_db() as conn:
        convo = _convo_or_404(conn, convo_id, project_id)
        pid = convo.get("project_id", "")
        turn = repos.Turns.latest_with_events(conn, convo_id, project_id=pid)
    return _ok({
        "turn_id": (turn or {}).get("id", ""),
        "status": (turn or {}).get("status", ""),
    })


@router.get("/chats/{convo_id}/messages")
def spa_messages(convo_id: str, project_id: str | None = Query(default=None)):
    with deps.get_db() as conn:
        _convo_or_404(conn, convo_id, project_id)
        rows = repos.Messages.for_conversation(conn, convo_id)
    return _ok([
        {"role": m["role"],
         "body_md": sanitize_user_text(m.get("body_md", ""), 1_000_000),
         "citations": _citations_of(m)}
        for m in rows
    ])


# ---------- activity ----------

@router.get("/activity")
def spa_activity(
    conversation_id: str = Query(...),
    project_id: str | None = Query(default=None),
):
    """Persisted execution events for a conversation (job events + turn
    terminal events). Fields: id, event_type, label, detail."""
    with deps.get_db() as conn:
        convo = _convo_or_404(conn, conversation_id, project_id)
        pid = convo.get("project_id", "")
        job_events = repos.ExecutionEvents.for_conversation_jobs(conn, conversation_id)
        terminal = repos.ExecutionEvents.for_conversation_turn_terminal(conn, conversation_id)
        seen = {e["id"] for e in job_events}
        merged = sorted(job_events + [e for e in terminal if e["id"] not in seen],
                        key=lambda e: e["id"])
        # Never leak foreign-project events.
        merged = [e for e in merged if not pid or e.get("project_id") in ("", pid)]
    activity = []
    for event in merged:
        safe_event = project_event_boundary(event)
        activity.append({
            "id": event["id"],
            "event_type": sanitize_user_text(event.get("event_type", ""), 80),
            "label": safe_event["label"],
            "detail": safe_event["detail"],
        })
    return _ok(activity)


# ---------- jobs ----------

@router.get("/jobs")
def spa_jobs(project_id: str | None = Query(default=None)):
    with deps.get_db() as conn:
        if project_id:
            _project_or_404(conn, project_id)
            rows = repos.BackgroundJobs.list(conn, project_id)
        else:
            rows = repos.BackgroundJobs.list(conn)
    return _ok([
        {"id": j["id"], "kind": j.get("kind") or j.get("job_type", ""),
         "status": j.get("status", ""),
         "created_at": j.get("created_at", ""),
         "updated_at": j.get("updated_at", "")}
        for j in rows
    ])


# ---------- approvals ----------

@router.get("/approvals")
def spa_approvals(
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    with deps.get_db() as conn:
        if project_id:
            _project_or_404(conn, project_id)
        rows = repos.Approvals.list(conn, status, project_id)
        result = []
        for a in rows:
            # Workflow is the only supported source for approval provenance.
            # Treat malformed or stale references as unavailable, and fail
            # closed on every relationship crossing a project boundary.
            try:
                fields = _json.loads(a.get("fields_json") or "{}")
            except (TypeError, ValueError):
                fields = {}
            workflow = fields.get("workflow") if isinstance(fields, dict) else None
            workflow = workflow if isinstance(workflow, dict) else {}
            pid = a.get("project_id")
            # Validate the persisted turn/conversation provenance as well as
            # the visible links. A stale or foreign source cannot lend
            # credibility to a requested_by value.
            conversation_id = workflow.get("conversation_id")
            try:
                conversation = (repos.Conversations.get(conn, conversation_id, pid)
                                if pid and isinstance(conversation_id, str) else None)
            except ValueError:
                # Repo scope violations mean this provenance is unavailable.
                conversation = None
            turn_id = workflow.get("turn_id")
            turn = repos.Turns.get(conn, turn_id) if isinstance(turn_id, str) else None
            if turn and (not pid or turn.get("project_id") != pid or
                         not conversation or turn.get("conversation_id") != conversation.get("id")):
                turn = None
            if conversation_id and not conversation:
                # All other workflow fields are unavailable if the source
                # conversation cannot be verified in this approval's project.
                workflow = {}
            elif turn_id and not turn:
                workflow = {k: v for k, v in workflow.items()
                            if k not in ("requested_by", "task_id", "campaign_id")}
            item = {"id": a["id"], "title": a.get("title", ""),
                    "kind": a.get("kind", ""), "status": a.get("status", ""),
                    "body_md": a.get("body_md", "")}
            if pid and repos.Projects.get(conn, pid):
                item["project_id"] = pid
            requested_by = workflow.get("requested_by")
            if isinstance(requested_by, str) and requested_by.strip():
                item["requested_by"] = requested_by.strip()
            task_id = workflow.get("task_id")
            task = repos.Tasks.get(conn, task_id, pid) if isinstance(task_id, str) and pid else None
            if task:
                item["task"] = {"id": task["id"], "title": task.get("title", "")}
            campaign_id = workflow.get("campaign_id")
            campaign = repos.Campaigns.get(conn, campaign_id, pid) if isinstance(campaign_id, str) and pid else None
            if campaign:
                item["campaign"] = {"id": campaign["id"], "title": campaign.get("title", "")}
            result.append(item)
    return _ok(result)


# ---------- campaigns ----------

def _campaign_row(conn, c: dict) -> dict:
    return {k: c.get(k) for k in (
        "id", "project_id", "title", "status", "impact", "confidence",
        "effort", "cost", "approval_level", "measurement_window",
        "result", "learning_ref", "updated_at")}


@router.get("/campaigns")
def spa_campaigns(project_id: str | None = Query(default=None)):
    with deps.get_db() as conn:
        if project_id:
            _project_or_404(conn, project_id)
            rows = repos.Campaigns.list(conn, project_id)
        else:
            rows = repos.Campaigns.list(conn)
    return _ok([_campaign_row(conn, c) for c in rows])


@router.get("/campaigns/{campaign_id}")
def spa_campaign_detail(campaign_id: str,
                        project_id: str | None = Query(default=None)):
    """Header + tabs data: campaign row passthrough plus its prospects,
    tasks, and experiments (all project-scoped)."""
    with deps.get_db() as conn:
        try:
            row = repos.Campaigns.get(conn, campaign_id, project_id)
        except ValueError as e:
            raise HTTPException(
                status_code=403,
                detail=safe_error_message(e, "The campaign is unavailable."),
            )
        if row is None:
            raise HTTPException(status_code=404, detail="unknown campaign")
        pid = row.get("project_id", "")
        prospects = [p for p in repos.Prospects.list(conn, campaign_id, pid)]
        tasks = [t for t in repos.Tasks.list(conn, pid)
                 if t.get("campaign_id") == campaign_id]
        experiments = [e for e in repos.Experiments.list(conn, pid)
                       if e.get("campaign_id") == campaign_id]
    return _ok({"campaign": _campaign_row(conn, row),
                "prospects": prospects, "tasks": tasks,
                "experiments": experiments})


# ---------- results ----------

@router.get("/results/summary")
def spa_results_summary(project_id: str | None = Query(default=None)):
    with deps.get_db() as conn:
        if project_id:
            _project_or_404(conn, project_id)
        experiments = repos.Experiments.list(conn, project_id)
        measurements = store.all_measurements(conn, project_id)
        learnings = repos.Learnings.list(conn, project_id)
    by_status: dict[str, int] = {}
    for e in experiments:
        by_status[e.get("status", "")] = by_status.get(e.get("status", ""), 0) + 1
    return _ok({
        "counts": {"experiments": len(experiments),
                   "experiments_by_status": by_status,
                   "measurements": len(measurements),
                   "learnings": len(learnings)},
        "learnings": learnings,
        # There is no persisted recommendation model yet. Generic labels
        # must not be presented as project-specific conclusions.
        "decisions": [],
    })


# ---------- knowledge ----------

@router.get("/knowledge")
def spa_knowledge(project_id: str | None = Query(default=None)):
    """User-facing knowledge names only. Never exposes chroma/fts internals
    (no chunk ids, embeddings, fts rows, or backend paths)."""
    with deps.get_db() as conn:
        if project_id:
            _project_or_404(conn, project_id)
            pid = project_id
        else:
            pid = None
        memories = repos.Memories.list(conn, pid)
        learnings = repos.Learnings.list(conn, pid)
        docs = store.indexed_documents(conn, pid) if pid else store.indexed_documents(conn)
        company = None
        if pid:
            proj = repos.Projects.get(conn, pid)
            if proj:
                company = {"name": proj.get("name", ""),
                           "website": proj.get("website", ""),
                           "goal": proj.get("goal", "")}
    by_kind: dict[str, list] = {}
    for m in memories:
        by_kind.setdefault(m.get("kind", ""), []).append(
            {"body_md": m.get("body_md", ""),
             "confidence": m.get("confidence", "")})
    files = [{"name": (d.get("path", "") or "").split("/")[-1],
              "status": d.get("status_tag", "")} for d in docs]
    return _ok({
        "company": company,
        "audience": by_kind.get("preference", []),
        "offers": by_kind.get("offer", []),
        "positioning": by_kind.get("strategy", []),
        "research": by_kind.get("decision", []),
        "learnings": [
            {"body_md": m.get("body_md", ""), "confidence": m.get("confidence", "")}
            for m in by_kind.get("learning", [])
        ] + [{"body_md": l.get("body_md", ""), "source": l.get("source", "")}
             for l in learnings],
        "files": files,
    })


# ---------- settings ----------

class GeneralSettingsBody(BaseModel):
    theme: str | None = None
    auto_load_project: bool | None = None
    language: str | None = None


class AiSettingsBody(BaseModel):
    ai_mode: str | None = None
    active_model: str | None = None
    marketing_adapter: str | None = None
    manager_provider: str | None = None
    openrouter_default_model: str | None = None
    cloud_escalation: str | None = None
    local_model_enabled: bool | None = None


class ConnectCredentialBody(BaseModel):
    target: str
    token: str = ""
    scope: str = "installation"
    project_id: str | None = None
    ig_account_id: str | None = None
    account_id: str | None = None
    code: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None
    dataset_id: str | None = None
    server_id: str | None = None
    endpoint: str | None = None
    config: dict | None = None


class TestCredentialBody(BaseModel):
    target: str
    scope: str = "installation"
    project_id: str | None = None
    ig_account_id: str | None = None
    account_id: str | None = None
    dataset_id: str | None = None
    server_id: str | None = None
    endpoint: str | None = None
    config: dict | None = None


class DisconnectCredentialBody(BaseModel):
    target: str
    scope: str = "installation"
    project_id: str | None = None
    server_id: str | None = None
    config: dict | None = None


class InstagramSettingsBody(BaseModel):
    public_provider: str | None = None
    handle: str | None = None
    project_id: str | None = None


class McpActionBody(BaseModel):
    server: str
    action: str
    tool: str | None = None


class WorkspaceActionBody(BaseModel):
    project_id: str | None = None


@router.get("/settings/config")
def spa_settings_config(project_id: str | None = Query(default=None)):
    """Current settings overview with protected credential status."""
    from app.services.config_service import ConfigService
    with deps.get_db() as conn:
        pid = project_id or store.active_project_id(conn)
        general = ConfigService.get_general_config(conn=conn)
        ai = ConfigService.get_ai_config(conn=conn)
        integrations = ConfigService.get_integrations_overview(project_id=pid, conn=conn)
        files_knowledge = ConfigService.get_files_and_knowledge_status(project_id=pid, conn=conn)
        privacy_security = ConfigService.get_privacy_and_security(conn=conn)
        system = ConfigService.get_system_health(conn=conn)

        return _ok({
            "project_id": pid,
            "general": general,
            "ai": ai,
            "integrations": integrations,
            "files_knowledge": files_knowledge,
            "privacy_security": privacy_security,
            "system": system,
        })


@router.post("/settings/general")
def spa_settings_update_general(body: GeneralSettingsBody):
    from app.services.config_service import ConfigService
    res = ConfigService.update_general_config(
        theme=body.theme,
        auto_load_project=body.auto_load_project,
        language=body.language,
    )
    return _ok(res)


@router.post("/settings/ai")
def spa_settings_update_ai(body: AiSettingsBody):
    from app.services.config_service import ConfigService
    res = ConfigService.update_ai_config(
        ai_mode=body.ai_mode,
        active_model=body.active_model,
        marketing_adapter=body.marketing_adapter,
        manager_provider=body.manager_provider,
        openrouter_default_model=body.openrouter_default_model,
        cloud_escalation=body.cloud_escalation,
        local_model_enabled=body.local_model_enabled,
    )
    return _ok(res)


class LocalModelDownloadBody(BaseModel):
    """Empty by design: POST itself is the user's explicit download action."""


def _local_runtime_manager():
    from app.services.llm.model_router import get_managed_local_runtime_manager
    return get_managed_local_runtime_manager()


def _local_runtime_wire(status: dict) -> dict:
    """Project the manager state onto the sanitized flat SPA contract."""
    import re
    state = dict(status or {})
    model = state.get("model") if isinstance(state.get("model"), dict) else state
    raw_shards = model.get("shards") if isinstance(model.get("shards"), list) else []
    shards = []
    for item in raw_shards:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename", ""))
        digest = str(item.get("sha256", "")).lower()
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", filename) or
                filename in {".", ".."} or
                not re.fullmatch(r"[0-9a-f]{64}", digest)):
            continue
        try:
            size = max(0, int(item.get("size_bytes", 0)))
        except (TypeError, ValueError):
            size = 0
        shards.append({"filename": filename, "size_bytes": size, "sha256": digest})
    shard_text = "\n".join(
        f"{item.get('filename', 'model shard')}: {item.get('sha256', '')}"
        for item in shards)
    legacy_hashes = state.get("sha256", [])
    if isinstance(legacy_hashes, str):
        legacy_hashes = [legacy_hashes]
    if not isinstance(legacy_hashes, list):
        legacy_hashes = []
    return {
        "model_id": str(model.get("model_id", "")),
        "display_name": str(model.get("display_name", "")),
        "source": str(model.get("source", "")),
        "source_url": str(model.get("source_url", "")),
        "publisher": str(model.get("publisher", "")),
        "license": str(model.get("license", "")),
        "license_url": str(model.get("license_url", "")),
        "model_bytes": int(model.get("model_bytes", 0) or 0),
        "temporary_disk_bytes": int(model.get("temporary_disk_bytes", 0) or 0),
        # A logical model can have multiple shard hashes. Keep the frontend's
        # string field explicit and readable, and retain structured provenance.
        "sha256": shard_text or ", ".join(str(value) for value in legacy_hashes
                                             if isinstance(value, str)),
        "shards": shards,
        "download_state": str(state.get("download_state", "not_started")),
        "downloaded_bytes": int(state.get("download_progress_bytes",
                                            state.get("downloaded_bytes", 0)) or 0),
        "download_total_bytes": int(model.get("model_bytes", 0) or 0),
        "verification_state": str(state.get("verification_state", "not_verified")),
        "activation_state": str(state.get("activation_state", "inactive")),
        "runtime_state": str(state.get("runtime_state", "stopped")),
        "runtime_version": str(state.get("runtime_version", "")),
        "runtime_revision": str(state.get("runtime_revision", "")),
        "observed_model_id": state.get("observed_model_id"),
        "expected_model_id": state.get("expected_model_id", model.get("model_id")),
        "ready": bool(state.get("ready", False)),
        "error_code": _local_runtime_error_code(state.get("error_code")),
    }


def _local_runtime_error_code(value) -> str | None:
    if not isinstance(value, str):
        return None
    # Never reflect arbitrary exception text, paths, or command arguments.
    import re
    return value if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) else "runtime_error"


@router.get("/local/runtime")
def spa_local_runtime_status():
    manager = _local_runtime_manager()
    return _ok(_local_runtime_wire(manager.rediscover()))


@router.post("/local/model/download")
def spa_local_model_download(_body: LocalModelDownloadBody | None = None):
    manager = _local_runtime_manager()
    try:
        manager.download_model(explicit=True)
    except Exception as exc:
        code = _local_runtime_error_code(getattr(exc, "code", None)) or "download_failed"
        raise HTTPException(status_code=409, detail={"code": code}) from exc
    return _ok(_local_runtime_wire(manager.status()))


@router.post("/local/runtime/start")
def spa_local_runtime_start():
    manager = _local_runtime_manager()
    try:
        # This lifecycle operation is intentionally separate from download.
        return _ok(_local_runtime_wire(manager.start()))
    except Exception as exc:
        code = _local_runtime_error_code(getattr(exc, "code", None)) or "runtime_start_failed"
        raise HTTPException(status_code=409, detail={"code": code}) from exc


@router.post("/local/runtime/stop")
def spa_local_runtime_stop():
    manager = _local_runtime_manager()
    try:
        return _ok(_local_runtime_wire(manager.stop()))
    except Exception as exc:
        code = _local_runtime_error_code(getattr(exc, "code", None)) or "runtime_stop_failed"
        raise HTTPException(status_code=409, detail={"code": code}) from exc


def _selected_project(conn, project_id: str | None = None) -> str:
    pid = (project_id or "").strip() or store.active_project_id(conn)
    _project_or_404(conn, pid)
    return pid


def _body_value(body, *keys: str) -> str:
    config = body.config if isinstance(body.config, dict) else {}
    for key in keys:
        value = getattr(body, key, None)
        if value is not None and str(value).strip():
            return str(value).strip()
        value = config.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _set_credential_state(target: str, credential: str, conn) -> None:
    from app.services.config_service import ConfigService
    ConfigService.set_setting(f"health_{target}_credential", credential, conn=conn)
    ConfigService.set_setting(f"health_{target}_provider", "unknown", conn=conn)
    ConfigService.set_setting(
        f"health_{target}_capability", "not_verified", conn=conn)
    ConfigService.set_setting(f"health_{target}_last_checked", "", conn=conn)


def _register_public_integration(
    target: str,
    secret_ref: str | None,
    conn,
    dataset_id: str | None = None,
) -> None:
    if target not in ("apify", "brightdata"):
        return
    from app.services.integrations import registry
    config = {}
    if target == "apify":
        config = {"actor_id": "apify/instagram-profile-scraper"}
    if target == "brightdata":
        clean_dataset = str(dataset_id or "").strip()
        if len(clean_dataset) > 128:
            raise ValueError("Bright Data dataset id is too long")
        if clean_dataset:
            config = {"dataset_id": clean_dataset}
    configured = bool(secret_ref) and (
        target == "apify" or bool(config.get("dataset_id"))
    )
    registry.register_integration(
        registry.IntegrationRecord(
            integration_id=f"{target}-integration",
            capability="instagram_public_profile",
            provider=target,
            scope="installation",
            status="connected" if configured else "not_configured",
            config=config,
            secret_ref=secret_ref,
            last_health=None,
        ),
        conn=conn,
    )


@router.post("/settings/credentials/connect")
def spa_settings_connect_credential(body: ConnectCredentialBody):
    from app.services.config_service import ConfigService
    target = (body.target or "").strip().lower()
    token = (body.token or "").strip()

    with deps.get_db() as conn:
        if target == "meta":
            from app.services.social.instagram import oauth as meta_oauth
            pid = _selected_project(conn, body.project_id)
            account_id = _body_value(body, "ig_account_id", "account_id")
            if not account_id:
                raise HTTPException(
                    status_code=400,
                    detail="Instagram account id is required for Meta setup",
                )
            meta_config = {"ig_account_id": account_id}
            if body.code:
                result = meta_oauth.exchange_code(
                    "meta",
                    client_id=str(body.client_id or "").strip(),
                    client_secret=str(body.client_secret or "").strip(),
                    code=str(body.code).strip(),
                    redirect_uri=str(body.redirect_uri or "").strip(),
                    project_id=pid,
                    config=meta_config,
                )
            elif token:
                result = meta_oauth.connect_access_token(
                    "meta", token, project_id=pid, config=meta_config)
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Meta authorization code or access token is required",
                )
            if not result.get("ok"):
                reason = str(result.get("reason") or "connection_failed")
                safe_reason = reason if reason in {
                    "missing_client_credentials", "missing_code_or_project",
                    "missing_token_or_project", "no_access_token",
                    "vault_unavailable", "register_failed",
                } else "connection_failed"
                raise HTTPException(
                    status_code=400,
                    detail=f"Meta connection could not be completed ({safe_reason})",
                )
            _set_credential_state(target, "connected", conn)
            result_payload = {
                "target": target,
                "status": "connected",
                "project_id": pid,
                "message": "Meta connection saved securely.",
            }
        elif target == "mcp":
            from app.services import mcp
            from app.services.credentials import vault
            server_id = _body_value(body, "server_id")
            endpoint = _body_value(body, "endpoint")
            if (
                not server_id
                or len(server_id) > 64
                or any(not (ch.isalnum() or ch in "._-") for ch in server_id)
                or not endpoint
                or len(endpoint) > 512
                or "\r" in endpoint
                or "\n" in endpoint
            ):
                raise HTTPException(
                    status_code=400,
                    detail="MCP server id and endpoint are required",
                )
            scope_value = _body_value(body, "scope") or "project"
            scope_project = ""
            if scope_value.startswith("project:"):
                scope = "project"
                scope_project = scope_value.split(":", 1)[1].strip()
            else:
                scope = scope_value
            if scope not in {"project", "installation"}:
                raise HTTPException(
                    status_code=400,
                    detail="MCP scope must be project or installation",
                )
            project = None
            gateway_scope = "installation"
            if scope == "project":
                project = _selected_project(conn, body.project_id or scope_project)
                gateway_scope = f"project:{project}"
            existing = repos.McpServers.get(conn, server_id)
            secret_ref = str((existing or {}).get("secret_ref") or "")
            if secret_ref:
                try:
                    ref_row = vault.get_ref_row(secret_ref, conn=conn)
                    if not ref_row or ref_row.get("status") != vault.ACTIVE:
                        secret_ref = ""
                except Exception:
                    secret_ref = ""
            if token:
                label = ("mcp_" + server_id)[:64]
                try:
                    secret_ref = vault.store(
                        scope, label, token, project_id=project, conn=conn,
                    )
                except Exception:
                    raise HTTPException(
                        status_code=400,
                        detail="MCP credential could not be stored",
                    )
            try:
                mcp.register_mcp_server(
                    server_id, endpoint, gateway_scope, secret_ref or None,
                )
            except ValueError:
                if token and secret_ref:
                    try:
                        vault.revoke(secret_ref, conn=conn)
                    except Exception:
                        pass
                raise HTTPException(
                    status_code=400,
                    detail="MCP server configuration is invalid",
                )
            result_payload = {
                "target": target,
                "status": "registered",
                "server_id": server_id,
                "message": "MCP server registered securely.",
            }
        else:
            if not token and target != "brightdata":
                raise HTTPException(status_code=400, detail="Token or key is required")
            if target == "openai":
                ConfigService.store_openai_api_key(
                    token, scope=body.scope or "installation", conn=conn)
            elif target == "apify":
                ref = ConfigService.store_apify_token(token, conn=conn)
                _register_public_integration(target, ref, conn)
            elif target == "brightdata":
                from app.services.credentials import vault
                if token:
                    ref = ConfigService.store_brightdata_key(token, conn=conn)
                else:
                    ref = vault.get_secret_ref(
                        "installation", "integration_brightdata", conn=conn,
                    )
                    if not ref:
                        raise HTTPException(
                            status_code=400,
                            detail="Token or key is required",
                        )
                dataset_id = body.dataset_id
                if dataset_id is None and isinstance(body.config, dict):
                    dataset_id = body.config.get("dataset_id")
                try:
                    _register_public_integration(target, ref, conn, dataset_id)
                except ValueError:
                    raise HTTPException(
                        status_code=400, detail="Bright Data configuration is invalid")
            elif target == "openrouter":
                ConfigService.store_openrouter_key(token, conn=conn)
            else:

                raise HTTPException(status_code=400, detail="Unknown integration target")
            _set_credential_state(target, "connected", conn)
            result_payload = {
                "target": target,
                "status": "connected",
                "message": "Saved securely in Credential Vault.",
            }

    return _ok(result_payload)


def _persist_health(provider_key: str, credential: str, provider: str,
                    capability: str, last_checked_at: str | None) -> None:
    """Write the outcome of a REAL health check into product settings.
    Only this function may mark a capability verified."""
    from app.services.config_service import ConfigService
    now = last_checked_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with deps.get_db() as c:
        ConfigService.set_setting(f"health_{provider_key}_credential", credential, conn=c)
        ConfigService.set_setting(f"health_{provider_key}_provider", provider, conn=c)
        ConfigService.set_setting(f"health_{provider_key}_capability", capability, conn=c)
        ConfigService.set_setting(f"health_{provider_key}_last_checked", now, conn=c)


@router.post("/settings/credentials/test")
def spa_settings_test_credential(body: TestCredentialBody):
    from app.services.config_service import ConfigService
    target = (body.target or "").strip().lower()
    configured = False
    detail = "Not configured"
    credential_status = "not_configured"
    capability_health = "not_configured"
    provider_health = "unknown"
    last_error_code = None
    actor_id = None
    last_checked_at = None

    if target == "openai":
        configured = ConfigService.is_openai_configured()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if configured:
            # Real capability verification (models list) — never presence-only.
            key = ConfigService.get_openai_api_key()
            cred = "connected"
            provider_health = "error"
            capability_health = "unknown"
            detail = "OpenAI verification failed (network error)"
            try:
                from openai import OpenAI
                client = OpenAI(api_key=key, timeout=10)
                models = list(client.models.list())
                provider_health = "healthy"
                capability_health = "verified"
                detail = "Authenticated; model catalog verified"
            except Exception as e:
                status = getattr(e, "status_code", None) or getattr(e, "status", None)
                provider_health = "error"
                capability_health = "unknown"
                detail = "OpenAI verification failed (auth or network)"
                if status == 401:
                    cred = "auth_error"
                    detail = "OpenAI rejected the stored credential"
            _persist_health("openai", cred, provider_health, capability_health, now)
            credential_status = cred
        else:
            detail = "Not connected (no key stored)"
            _persist_health("openai", "not_configured", "unknown", "unknown", None)
            credential_status = "not_configured"
            capability_health = "unknown"
            provider_health = "unknown"
    elif target == "apify":
        from app.services.social.instagram.apify_provider import ApifyProvider
        provider = ApifyProvider()
        test_res = provider.test_connection()
        credential_status = test_res.get("credential_status", "not_configured")
        capability_health = test_res.get("capability_health", "not_configured")
        provider_health = test_res.get("provider_health", "unknown")
        last_error_code = test_res.get("last_error_code")
        actor_id = test_res.get("actor_id")
        detail = test_res.get("detail", "")
        configured = credential_status == "connected"
        status_label = "Verified" if capability_health == "verified" else ("Connected" if configured else ("Auth error" if credential_status == "auth_error" else "Not connected"))
        # Persist the authoritative tri-state for config payloads.
        _persist_health("apify",
                        test_res.get("credential_status", "not_configured"),
                        test_res.get("provider_health", "unknown"),
                        test_res.get("capability_health", "unknown"),
                        test_res.get("last_checked_at"))
        return _ok({
            "target": target,
            "configured": configured,
            "status": status_label,
            "credential_status": credential_status,
            "capability_health": capability_health,
            "provider_health": provider_health,
            "last_error_code": last_error_code,
            "actor_id": actor_id,
            "cost_type": "metered",
            "detail": detail,
            "last_checked_at": test_res.get("last_checked_at"),
        })
    elif target == "brightdata":
        from app.services.social.instagram.brightdata_provider import BrightDataProvider
        with deps.get_db() as c:
            pid = _selected_project(c, body.project_id)
        result = BrightDataProvider().test_connection(project_id=pid)
        credential_status = result.get("credential_status", "not_configured")
        capability_health = result.get("capability_health", "unknown")
        provider_health = result.get("provider_health", "unknown")
        last_checked_at = result.get("last_checked_at")
        detail = result.get("detail", "Provider test completed")
        configured = credential_status == "connected"
        _persist_health(
            "brightdata", credential_status, provider_health,
            capability_health, last_checked_at)
    elif target == "mcp":
        from app.services import mcp
        server_id = _body_value(body, "server_id")
        if not server_id:
            raise HTTPException(status_code=400, detail="MCP server id is required")
        with deps.get_db() as c:
            row = repos.McpServers.get(c, server_id)
        if row is None:
            return _ok({
                "target": target,
                "server_id": server_id,
                "configured": False,
                "status": "unknown",
                "credential_status": "not_configured",
                "capability_health": "unknown",
                "provider_health": "unknown",
                "health_status": "unknown",
                "connected": False,
                "last_checked_at": None,
                "checked_at": None,
                "detail": "MCP server is not registered",
            })
        try:
            health = mcp.check_mcp_health(server_id)
        except Exception:
            health = {
                "status": "unhealthy",
                "connected": False,
                "checked_at": None,
            }
        health_status = str(health.get("status") or "unknown")
        connected = bool(health.get("connected"))
        checked_at = health.get("checked_at") or None
        return _ok({
            "target": target,
            "server_id": server_id,
            "configured": True,
            "status": health_status,
            "credential_status": "configured",
            "capability_health": "unknown",
            "provider_health": health_status,
            "health_status": health_status,
            "connected": connected,
            "last_checked_at": checked_at,
            "checked_at": checked_at,
            "detail": "MCP health check completed",
        })
    elif target == "openrouter":
        try:
            import app.services.llm.openrouter_provider as orp
            if not ConfigService.is_openrouter_configured():
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                _persist_health("openrouter", "not_configured", "unknown", "unknown", now)
                credential_status = "not_configured"
                capability_health = "unknown"
                provider_health = "unknown"
                detail = "Not connected (no token stored)"
            else:
                res = orp.test_connection()
                credential_status = res.get("credential_status", "not_configured")
                capability_health = res.get("capability_health", "unknown")
                provider_health = res.get("provider_health", "unknown")
                detail = res.get("detail", "")
                _persist_health("openrouter", credential_status, provider_health,
                                capability_health, res.get("last_checked_at"))
                configured = credential_status == "connected"
                return _ok({
                    "target": target,
                    "configured": configured,
                    "status": ("Verified" if capability_health == "verified"
                               else ("Connected" if configured
                                     else ("Auth error" if credential_status == "auth_error"
                                           else "Not connected"))),
                    "credential_status": credential_status,
                    "capability_health": capability_health,
                    "provider_health": provider_health,
                    "model_count": res.get("model_count"),
                    "actual_model": res.get("actual_model"),
                    "detail": detail,
                    "last_checked_at": res.get("last_checked_at"),
                })
        except HTTPException:
            raise
        except Exception:
            credential_status = "connected" if configured else "not_configured"
            detail = "OpenRouter health check failed"
            provider_health = "error"
            capability_health = "unknown"
            _persist_health("openrouter", credential_status, provider_health,
                            capability_health,
                            datetime.now(timezone.utc).isoformat(timespec="seconds"))
    elif target == "meta":
        from app.services.social.instagram import capabilities as instagram_caps
        from app.services.social.instagram import registry_bridge
        from app.services.social.instagram.meta_provider import MetaProvider
        with deps.get_db() as c:
            pid = _selected_project(c, body.project_id)
            view = registry_bridge.resolve(
                instagram_caps.CAP_INSTAGRAM_OWNED_INSIGHTS, pid)
        configured = bool(
            view is not None
            and view.provider == "meta"
            and view.status == "connected"
            and view.secret_ref
            and (view.config or {}).get("ig_account_id")
        )
        if not configured:
            credential_status = "not_configured"
            capability_health = "unknown"
            provider_health = "unknown"
            detail = "Not connected"
        else:
            try:
                result = MetaProvider().audit_owned("", project_id=pid)
                evidence = result.get("evidence", {}) if isinstance(result, dict) else {}
                evidence_status = str(evidence.get("status") or "unavailable")
                if evidence_status == "verified":
                    credential_status = "connected"
                    provider_health = "healthy"
                    capability_health = "verified"
                    detail = "Meta capability verified"
                elif evidence_status == "partial":
                    credential_status = "connected"
                    provider_health = "healthy"
                    capability_health = "not_verified"
                    detail = "Meta provider responded; capability is incomplete"
                else:
                    credential_status = "connected"
                    provider_health = "error"
                    capability_health = "unknown"
                    detail = "Meta capability test failed"
            except Exception:
                credential_status = "connected"
                provider_health = "error"
                capability_health = "unknown"
                detail = "Meta capability test failed"
            last_checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            _persist_health(
                "meta", credential_status, provider_health,
                capability_health, last_checked_at)
    elif target == "browser":
        configured = False
        detail = "Browser review has not been verified"
        credential_status = "not_configured"
        capability_health = "unknown"
        provider_health = "unknown"
    else:
        raise HTTPException(status_code=400, detail="Unknown test target")

    return _ok({
        "target": target,
        "configured": configured,
        "status": "Connected" if configured else "Not connected",
        "credential_status": credential_status,
        "capability_health": capability_health,
        "provider_health": provider_health,
        "detail": detail,
        "last_checked_at": last_checked_at,
    })


@router.get("/ai/models")
def spa_ai_models(provider: str = "openrouter", refresh: str = "0"):
    """Backend-proxied model catalog (OpenRouter /models via API), TTL cached.
    Curated allow-list applied separately to AUTO routing in ModelRouter."""
    provider = (provider or "").strip().lower()
    if provider != "openrouter":
        raise HTTPException(status_code=400, detail=f"Unknown AI provider: {provider}")
    from app.services.config_service import ConfigService
    if not ConfigService.is_openrouter_configured():
        return _ok({"provider": provider, "connected": False, "models": [],
                    "curated": [], "detail": "OpenRouter is not connected"})
    from app.services.llm import openrouter_provider as orp
    try:
        models = orp.model_catalog(refresh=(refresh or "0") in ("1", "true", "yes"))
    except Exception:
        models = []
    curated_raw = ConfigService.get_setting("openrouter_curated_models", "", conn=None)
    curated = [m.strip() for m in str(curated_raw).split(",") if m.strip()]
    return _ok({"provider": provider, "connected": True, "models": models,
                "curated": curated, "cached_at": orp._CATALOG_TS})


@router.post("/settings/credentials/disconnect")
def spa_settings_disconnect_credential(body: DisconnectCredentialBody):
    from app.services.config_service import ConfigService
    target = (body.target or "").strip().lower()
    with deps.get_db() as c:
        if target == "openai":
            ConfigService.clear_openai_api_key(conn=c)
        elif target == "apify":
            ConfigService.clear_apify_token(conn=c)
            _register_public_integration(target, None, c)
        elif target == "brightdata":
            ConfigService.clear_brightdata_key(conn=c)
            _register_public_integration(target, None, c)
        elif target == "openrouter":
            ConfigService.clear_openrouter_key(conn=c)
        elif target == "meta":
            from app.services.social.instagram import oauth as meta_oauth
            pid = _selected_project(c, body.project_id)
            meta_oauth.disconnect_project("meta", pid)
        elif target == "mcp":
            from app.services import mcp
            from app.services.credentials import vault
            server_id = _body_value(body, "server_id")
            if not server_id:
                raise HTTPException(status_code=400, detail="MCP server id is required")
            row = repos.McpServers.get(c, server_id)
            if row and row.get("secret_ref"):
                try:
                    vault.revoke(str(row["secret_ref"]), conn=c)
                except Exception:
                    pass
            repos.McpServers.delete(c, server_id)
            mcp.reset_gateway()
        else:
            raise HTTPException(status_code=400, detail="Unknown disconnect target")

    # Disconnect resets the persisted tri-state (no stale Verified survives).
    from app.services.config_service import ConfigService
    with deps.get_db() as c:
        for field, val in (("credential", "not_configured"), ("provider", "unknown"),
                           ("capability", "unknown"), ("last_checked", "")):
            ConfigService.set_setting(f"health_{target}_{field}", val, conn=c)

    return _ok({"target": target, "status": "disconnected", "message": "Credential removed from vault."})


@router.post("/settings/integrations/instagram")
def spa_settings_instagram(body: InstagramSettingsBody):
    from app.services.config_service import ConfigService
    with deps.get_db() as conn:
        pid = body.project_id or store.active_project_id(conn)
        if body.public_provider:
            ConfigService.set_setting("instagram_public_provider", body.public_provider.lower(), conn=conn)
        if body.handle is not None:
            clean_handle = body.handle.strip().lstrip("@")
            if clean_handle:
                repos.SocialAccounts.set_handle(
                    conn, pid, "instagram", clean_handle,
                    status="LIKELY", source="manual")
            else:
                repos.SocialAccounts.remove(conn, pid, "instagram")
    return _ok({"status": "updated", "project_id": pid})


@router.post("/settings/workspace/reimport")
def spa_settings_reimport(body: WorkspaceActionBody):
    with deps.get_db() as conn:
        pid = body.project_id or store.active_project_id(conn)
        _project_or_404(conn, pid)
        stats = store.run_imports(conn, deps.ROOT)
    return _ok({"status": "synced", "scope": "workspace_data", "project_id": pid,
                "message": "Structured workspace data imported. Project knowledge files were not reindexed.",
                "stats": sanitize_metadata(stats)})


@router.post("/settings/workspace/reindex")
def spa_settings_reindex(body: WorkspaceActionBody):
    with deps.get_db() as conn:
        pid = body.project_id or store.active_project_id(conn)
        _project_or_404(conn, pid)
        try:
            report = store.build_index(conn, deps.ROOT, project_id=pid)
        except Exception as exc:
            report = {"project_id": pid, "failed": 1, "indexed": 0,
                      "errors": [{"code": "rebuild_failed"}],
                      "error": safe_error_message(exc, "Knowledge index rebuild failed.",
                                                  force_generic=True)}
        report = report if isinstance(report, dict) else {}
        report.setdefault("project_id", pid)
        report.setdefault("discovered", 0)
        report.setdefault("indexed", 0)
        report.setdefault("skipped", 0)
        report.setdefault("deleted", 0)
        report.setdefault("quarantined", 0)
        report.setdefault("failed", 0)
        report.setdefault("not_searchable", 0)
        report.setdefault("chunks", 0)
        report.setdefault("search_mode", "FTS")
        report.setdefault("vector_status", "NOT_AVAILABLE")
        report.setdefault("vector_reason", "runtime_status_unavailable")
        if report["failed"] or report["quarantined"] or report["not_searchable"]:
            status = "partial" if report["indexed"] or report["skipped"] else "failed"
            status = "partial" if status != "failed" or report["not_searchable"] else "failed"
            message = (f"{report['indexed']} indexed, {report['failed']} failed, "
                       f"{report['quarantined']} quarantined, "
                       f"{report['not_searchable']} not searchable.")
        elif not report["discovered"] and not report["indexed"] and not report["skipped"]:
            status, message = "empty", "No knowledge files found. Nothing was indexed."
        else:
            status = "indexed"
            message = f"{report['indexed']} indexed, {report['skipped']} unchanged."
        report = sanitize_metadata(report)
    return _ok({"status": status, "project_id": pid, "message": message,
                "report": report})


@router.get("/settings/system/health")
def spa_settings_system_health():
    from app.services.config_service import ConfigService
    return _ok(ConfigService.get_system_health())
