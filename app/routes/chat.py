import json as _json
import html as _html
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from app import deps
from app.contracts.events import safe_error_message, sanitize_user_text
from app.database import repos
from app.database.identity import DEFAULT_PROJECT_ID
from app.main import templates
from app.services import account_manager, state as store
from app.services import turns as turnsvc

router = APIRouter()


def _active_project(conn) -> str:
    try:
        return store.active_project_id(conn)
    except Exception:
        return DEFAULT_PROJECT_ID


def _provider_selection(conn) -> str:
    from app.services.config_service import ConfigService
    return str(ConfigService.get_setting(
        "manager_provider", "auto", conn=conn) or "auto").strip().lower()


def _project_or_404(conn, pid: str):
    try:
        proj = repos.Projects.get(conn, pid)
    except Exception:
        proj = None
    return proj


# ---------- project-first new chat (A) ----------

@router.get("/chat/new", response_class=HTMLResponse)
def chat_new(request: Request):
    return RedirectResponse("/app/chat/new", status_code=303)


@router.post("/chat/new")
def chat_create(request: Request, project_id: str = Form(...),
                new_name: str = Form(""), website: str = Form(""),
                goal: str = Form("")):
    with deps.get_db() as conn:
        pid = (project_id or "").strip()
        if pid == "__new__":
            try:
                proj = store.create_project(conn, new_name, website, goal)
            except (KeyError, ValueError) as e:
                return HTMLResponse(
                    f'<div class="error">{safe_error_message(e, "The project could not be created.")}</div>',
                    status_code=400,
                )
            pid = proj["id"]
        if _project_or_404(conn, pid) is None:
            return HTMLResponse('<div class="error">unknown project</div>', status_code=400)
        convo = store.create_conversation(conn, project_id=pid)
    return RedirectResponse(f"/chat/{convo['id']}", status_code=303)


@router.get("/chat/{convo_id}", response_class=HTMLResponse)
def chat_conversation(request: Request, convo_id: str):
    return RedirectResponse(f"/app/chat/{convo_id}", status_code=303)


def chat_archive(request: Request, convo_id: str):
    raise RuntimeError("legacy chat archive route is not registered")


def _open_turn(conn, convo_id):
    rows = [t for t in repos._list(conn, "turns", "conversation_id = ? ORDER BY id DESC",
                                   (convo_id,))[:5] if t["status"] not in ("completed", "failed")]
    return rows[0] if rows else None


# ---------- live turn transport (B/C/D) ----------

@router.post("/chat/turn", response_class=HTMLResponse)
def chat_turn(request: Request, conversation_id: str = Form(...),
              text: str = Form(...), client_message_id: str = Form(""),
              attachment_ids: list[str] = Form(default=[]),
              model_provider: str = Form(default=""),
              model_id: str = Form(default="")):
    """Fast ack: persists the user message + turn, starts background work,
    returns immediately with the user bubble + live assistant shell."""
    with deps.get_db() as conn:
        convo = repos.Conversations.get(conn, conversation_id)
        if convo is None:
            return HTMLResponse('<div class="error">unknown conversation</div>', status_code=404)
        pid = convo.get("project_id") or DEFAULT_PROJECT_ID  # immutable: never global active
        if _project_or_404(conn, pid) is None:
            return HTMLResponse('<div class="error">project unavailable</div>', status_code=400)
        from app.services.chat_models import normalize_chat_selection
        submitted_selection = bool(str(model_provider or "").strip())
        requested_provider = model_provider if submitted_selection else (
            convo.get("model_provider") or "AUTO")
        requested_model = model_id if submitted_selection else convo.get("model_id", "")
        try:
            selection = normalize_chat_selection(requested_provider, requested_model)
        except ValueError as e:
            message = _html.escape(safe_error_message(
                str(e), "The selected model is unavailable."))
            return HTMLResponse(f'<div class="error">{message}</div>', status_code=400)
        if selection["model_provider"] != "AUTO":
            from app.contracts.runtime import get_ai_runtime
            if get_ai_runtime() != "langgraph":
                return HTMLResponse(
                    '<div class="error">Selected models require the graph runtime.</div>',
                    status_code=503)
        if submitted_selection:
            convo["model_provider"] = selection["model_provider"]
            convo["model_id"] = selection["model_id"]
            convo["updated_at"] = store.now_iso()
            repos.Conversations.upsert(conn, convo)
        try:
            made = turnsvc.create_turn(conn, str(deps.DB_PATH), conversation_id=conversation_id,
                                       project_id=pid, text=text,
                                       client_message_id=client_message_id,
                                       attachment_ids=attachment_ids,
                                       model_provider=selection["model_provider"],
                                       model_id=selection["model_id"])
        except ValueError as e:
            return HTMLResponse(
                f'<div class="error">{safe_error_message(e, "The message could not be accepted.")}</div>',
                status_code=400,
            )
        turn = made["turn"]
        if made["created"]:
            turnsvc.emit_event(str(deps.DB_PATH), project_id=pid,
                               conversation_id=conversation_id, turn_id=turn["id"],
                               event_type="turn_started", label="Starting your request")
            turnsvc.start_turn_bg(str(deps.DB_PATH), str(deps.ROOT), turn["id"])
    return templates.TemplateResponse(request, "_partials/turn_ack.html",
                                      {"user_body": made["user_message"]["body_md"],
                                       "turn": turn, "conversation_id": conversation_id})


@router.post("/chat/turn/{turn_id}/retry", response_class=HTMLResponse)
def chat_retry(request: Request, turn_id: str):
    """Retry a failed turn: brand-new turn reusing the same user text."""
    with deps.get_db() as conn:
        turn = repos.Turns.get(conn, turn_id)
        if turn is None:
            return HTMLResponse('<div class="error">unknown turn</div>', status_code=404)
        convo = repos.Conversations.get(conn, turn["conversation_id"])
        if convo is None:
            return HTMLResponse('<div class="error">unknown conversation</div>', status_code=404)
        user_msg = repos.Messages.by_client_id(conn, turn["conversation_id"],
                                               turn["client_message_id"])
        text = (user_msg or {}).get("body_md", "")
        if not text:
            return HTMLResponse('<div class="error">nothing to retry</div>', status_code=400)
        import uuid as _uuid
        made = turnsvc.create_turn(conn, str(deps.DB_PATH),
                                   conversation_id=turn["conversation_id"],
                                   project_id=convo.get("project_id") or DEFAULT_PROJECT_ID,
                                   text=text, client_message_id=_uuid.uuid4().hex,
                                   model_provider=str(turn.get("model_provider") or "AUTO"),
                                   model_id=str(turn.get("model_id") or ""),
                                   attachment_ids=__import__("app.services.files.repo", fromlist=["selected_for_turn"]).selected_for_turn(
                                       conn, turn_id=turn_id, project_id=turn["project_id"],
                                       conversation_id=turn["conversation_id"]))
        turnsvc.start_turn_bg(str(deps.DB_PATH), str(deps.ROOT), made["turn"]["id"])
    return templates.TemplateResponse(request, "_partials/turn_ack.html",
                                      {"user_body": "", "turn": made["turn"],
                                       "conversation_id": turn["conversation_id"],
                                       "retry": True})


@router.get("/chat/turns/{turn_id}/events")
def chat_turn_events(request: Request, turn_id: str, after: int = 0):
    """SSE: persisted lifecycle events for this turn (+ its conversation's job
    events). Scoped by the turn's own conversation/project — a turn id from
    another project yields nothing foreign. Supports Last-Event-ID resume."""
    last_hdr = request.headers.get("last-event-id", "")
    try:
        after = int(last_hdr or after)
    except (ValueError, TypeError):
        after = 0
    with deps.get_db() as conn:
        turn = repos.Turns.get(conn, turn_id)
        if turn is None:
            return HTMLResponse("unknown turn", status_code=404)
        convo_id, pid = turn["conversation_id"], turn["project_id"]
        since = turn.get("created_at", "")

    def _gen():
        yield ": connected\n\n"
        last_id = after
        deadline = time.time() + 600
        terminal_seen = False
        while time.time() < deadline:
            with deps.get_db() as conn2:
                turn_events = repos.ExecutionEvents.for_turn(conn2, turn_id, after_id=last_id)
                job_events = [e for e in repos.ExecutionEvents.for_conversation_jobs(
                    conn2, convo_id, since=since) if e["id"] > last_id]
                seen = {e["id"] for e in turn_events}
                merged = list(turn_events) + [e for e in job_events if e["id"] not in seen]
                merged.sort(key=lambda e: e["id"])
                cur = repos.Turns.get(conn2, turn_id)
            for e in merged:
                last_id = max(last_id, e["id"])
                if e.get("project_id") != pid:
                    continue  # never leak foreign-project events
                from app.contracts.events import LEGACY_EVENT_TYPES

                if e.get("event_type") not in LEGACY_EVENT_TYPES:
                    last_id = last_id  # non-frozen types never reach the wire
                    continue
                safe_event = _safe_event(e)
                event_type = sanitize_user_text(e["event_type"], 80)
                yield f"id: {e['id']}\nevent: {event_type}\n"
                yield f"data: {_json.dumps(safe_event, ensure_ascii=False)}\n\n"
                if e["event_type"] in ("turn_completed", "turn_failed"):
                    terminal_seen = True
            if terminal_seen and (cur or {}).get("status") in ("completed", "failed"):
                break
            if (cur or {}).get("status") in ("completed", "failed") and not merged:
                # terminal but events already drained: confirm once then close
                yield f"event: turn_closed\ndata: {_json.dumps({'status': cur['status']})}\n\n"
                break
            time.sleep(0.5)
        yield "event: stream_end\ndata: {}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _safe_event(e):
    from app.contracts.events import project_event_boundary
    return project_event_boundary(e)


def _safe_label(e):
    return _safe_event(e)["label"]


def _safe_detail(e):
    return _safe_event(e)["detail"]


def _safe_meta(e):
    return _safe_event(e)["meta"]


# ---------- legacy compat (existing tests / bookmarks) ----------

@router.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    return RedirectResponse("/app/chat/new", status_code=303)


def _active_project_or_none(conn):
    try:
        return store.require_active_project(conn)
    except Exception:
        return None


def _run_legacy(conn, pid, convo_id, text, prior):
    from app.contracts.runtime import get_ai_runtime
    if get_ai_runtime() == "langgraph":
        graph_res = _run_graph_serving(conn, pid, convo_id, text)
        if graph_res is not None:
            return graph_res
    from app.services.llm.router import (normalize_selection,
                                         openai_configured, resolve_candidates)
    cfg = _cfg()
    selection = normalize_selection(_provider_selection(conn))
    priority = cfg.provider_priority
    try:
        candidates = resolve_candidates(selection, priority,
                                        openai_ok=openai_configured(),
                                        legacy_agentic=False)
    except Exception:
        candidates = ["deterministic"]
    for candidate in candidates:
        if candidate == "deterministic":
            break
        try:
            res, _ = _run_agentic(conn, pid, convo_id, text, candidate)
            return {"reply_md": res["reply_md"], "provenance": res.get("provenance", [])}
        except Exception:
            break
    return account_manager.handle_turn(conn, deps.ROOT, text, prior_provenance=prior)


def _run_graph_serving(conn, pid, convo_id, text):
    """Default AI_RUNTIME=langgraph serving path (in-process). None on failure."""
    try:
        from app.routes.graph_runtime import _get_graph
        from app.graphs.state import initial_state
        graph = _get_graph()
        import uuid as _uuid
        turn_id = _uuid.uuid4().hex
        state = initial_state(project_id=pid, conversation_id=convo_id,
                              turn_id=turn_id, user_request=text)
        result = graph.invoke(state, config={"configurable": {"thread_id": turn_id}})
        result = result or {}
        final = str(result.get("final_answer") or "")
        if not final:
            return None
        return {"reply_md": final, "provenance": []}
    except Exception:
        return None


def _cfg():
    from app.services.llm.config import config_from_env
    return config_from_env()


def _run_agentic(conn, pid: str, convo_id: str, text: str, candidate: str):
    """Run one candidate provider for the legacy fallback path."""
    from app.services import manager_loop
    from app.services.tools import build_default_registry
    cfg = _cfg()
    budget = {"max_tool_iters": cfg.max_tool_iters, "turn_timeout_s": cfg.turn_timeout_s,
              "db_path": str(deps.DB_PATH)}
    registry = build_default_registry()
    if candidate == "openai":
        from app.services.llm.openai_provider import OpenAIProvider
        provider = OpenAIProvider(model=cfg.model, timeout_s=cfg.per_call_timeout_s)
    elif candidate == "openrouter":
        from app.services.llm import openrouter_provider as orp
        model_override = None
        try:
            from app.services.config_service import ConfigService
            model_override = ConfigService.get_setting("openrouter_default_model") or None
        except Exception:
            model_override = None
        provider = orp.OpenRouterProvider(model=model_override or "openrouter/auto",
                                          timeout_s=cfg.per_call_timeout_s)
    elif candidate == "fake":
        from app.services.llm.fake_provider import FakeProvider
        provider = FakeProvider()
    else:
        raise ValueError(f"unknown provider candidate: {candidate}")
    res = manager_loop.run_manager_turn(
        conn, project_id=pid, conversation_id=convo_id, user_text=text,
        provider=provider, registry=registry, root=deps.ROOT, budget=budget)
    return res, candidate
