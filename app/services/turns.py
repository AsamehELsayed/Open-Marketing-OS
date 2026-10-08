"""Turn orchestration for live chat UX (v0.2.1).

POST /chat/turn returns fast with turn/user-message ack; a background thread
runs the agentic turn and persists lifecycle events + the final message.
SSE streams persisted events (no fake deltas, no invented progress).

Only human-language labels/detail are persisted — never system prompts,
bundles, secrets, or tracebacks.
"""
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app.contracts.events import (
    LEGACY_EVENT_TYPES,
    GraphExecutionEvent,
    project_event_boundary,
    sanitize_metadata,
    sanitize_user_text,
)
from app.database import repos

_POOL = ThreadPoolExecutor(max_workers=4)


class AttachmentExecutionError(RuntimeError):
    """Selected attachments could not be safely included in this turn."""

# tool name -> (event_type, label, detail_fn)
_TOOL_LABELS = {
    "get_project": ("state_read", "Reading project information", lambda a, o: ""),
    "get_project_state": ("state_read", "Reading project information", lambda a, o: ""),
    "get_campaigns": ("state_read", "Reading project information", lambda a, o: "campaigns"),
    "get_tasks": ("state_read", "Reading project information", lambda a, o: "tasks"),
    "get_approvals": ("state_read", "Reading project information", lambda a, o: "approvals"),
    "get_measurements": ("state_read", "Reading project information", lambda a, o: "results"),
    "rag_search": ("rag_started", "Searching internal knowledge", lambda a, o: (a.get("query", "") or "")[:120]),
    "search_project_knowledge": ("rag_started", "Searching internal knowledge",
                                 lambda a, o: (a.get("query", "") or "")[:120]),
    "web_search": ("web_started", "Researching the web", lambda a, o: (a.get("query", "") or "")[:120]),
    "website_fetch": ("web_started", "Checking the website", lambda a, o: (a.get("url", "") or "")[:120]),
    "website_crawl": ("web_started", "Crawling the website", lambda a, o: (a.get("url", "") or "")[:120]),
    "website_marketing_audit": ("tool_completed", "Website audit ready", lambda a, o: (a.get("url", "") or "")[:120]),
    "instagram_audit": ("instagram_provider_started", "Checking Instagram",
                        lambda a, o: ("@" + str(a.get("username", "") or "").lstrip("@"))[:60] or "project account"),
    "delegate_to_marketing_pm": ("delegation_started", "Delegating to Marketing PM",
                                 lambda a, o: (a.get("goal", "") or "")[:160]),
    "get_job_status": ("state_read", "Checking background work", lambda a, o: ""),
    "propose_task": ("tool_completed", "Proposal prepared", lambda a, o: (a.get("title", "") or "")[:140]),
    "propose_campaign": ("tool_completed", "Proposal prepared", lambda a, o: (a.get("title", "") or "")[:140]),
    "propose_experiment": ("tool_completed", "Experiment proposed", lambda a, o: (a.get("hypothesis", "") or "")[:140]),
    "reject_task": ("tool_completed", "Proposed work rejected", lambda a, o: ""),
    "request_approval": ("approval_required", "Waiting for your approval",
                         lambda a, o: (a.get("title", "") or "")[:160]),
    "remember": ("tool_completed", "Saving an important decision", lambda a, o: ""),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event_row(*, project_id: str, conversation_id: str = "", turn_id: str = "",
               job_id: str = "", event_type: str, label: str = "", detail: str = "",
               metadata: dict | None = None) -> dict:
    clean_type = str(event_type or "")
    safe_event = project_event_boundary(
        {"event_type": clean_type, "label": label, "detail": detail,
         "metadata": metadata or {}}
    )
    clean_label = safe_event["label"]
    clean_detail = safe_event["detail"]
    clean_metadata = safe_event["meta"]
    if clean_type in LEGACY_EVENT_TYPES:
        event = GraphExecutionEvent(
            project_id=str(project_id or ""),
            conversation_id=str(conversation_id or ""),
            turn_id=str(turn_id or ""),
            job_id=str(job_id or ""),
            event_type=clean_type,
            label=clean_label,
            detail=clean_detail,
            metadata=clean_metadata,
            created_at=_now(),
        )
        return event.to_row()
    return {
        "project_id": project_id,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "job_id": job_id or "",
        "event_type": clean_type,
        "label": clean_label[:300],
        "detail": clean_detail[:1000],
        "metadata_json": json.dumps(clean_metadata, ensure_ascii=False)[:4000],
        "created_at": _now(),
    }


def _insert_uncommitted(conn, table: str, row: dict) -> None:
    columns = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})",
        list(row.values()),
    )


def _format_latency(ms) -> str:
    """Pre-format wall-clock latency for display (backend owns formatting).

    <1000ms -> "took 0.4s" (one decimal); <60s -> "took 38s" (whole seconds);
    >=60s -> "took 1m 12s". Exact "took " prefix (frontend matches verbatim).
    """
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        ms = 0
    if ms < 0:
        ms = 0
    if ms < 1000:
        return f"took {ms / 1000:.1f}s"
    total_s = ms // 1000
    if total_s < 60:
        return f"took {total_s}s"
    return f"took {total_s // 60}m {total_s % 60}s"


def emit_event(db_path, *, project_id: str, conversation_id: str = "", turn_id: str = "",
               job_id: str = "", event_type: str, label: str = "", detail: str = "",
               metadata: dict | None = None) -> int | None:
    """Persist one lifecycle event. Opens its own connection (thread-safe)."""
    from app.database.sqlite import connect
    conn = connect(db_path)
    try:
        row = _event_row(
            project_id=project_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            job_id=job_id,
            event_type=event_type,
            label=label,
            detail=detail,
            metadata=metadata,
        )
        return repos.ExecutionEvents.insert(conn, row)
    except Exception:
        return None
    finally:
        conn.close()


def _new_id() -> str:
    return uuid.uuid4().hex


def create_turn(conn, db_path, *, conversation_id: str, project_id: str,
                text: str, client_message_id: str = "", attachment_ids=None) -> dict:
    """Idempotent turn creation. One (conversation, client_message_id) yields
    exactly one user message and one turn, even on retry/reconnect."""
    text = (text or "").strip()
    if not text:
        raise ValueError("message text is required")
    if not project_id or not str(project_id).strip():
        raise ValueError("project_id is required (fail closed)")
    cmid = (client_message_id or "").strip() or _new_id()
    from app.services.files import repo as files_repo
    from app.services.files.attachments import validate_attachment_ids
    selected_ids = validate_attachment_ids(attachment_ids)
    if selected_ids:
        rows = files_repo.list_turn_files(conn, selected_ids, project_id=project_id,
                                          conversation_id=conversation_id)
        if len(rows) != len(selected_ids):
            raise ValueError("one or more attachments are unavailable in this conversation")
    existing = repos.Turns.by_client_id(conn, conversation_id, cmid)
    if existing is not None:
        if selected_ids:
            files_repo.save_turn_selection(conn, turn_id=existing["id"], file_ids=selected_ids,
                                           project_id=project_id, conversation_id=conversation_id)
        return {"turn": existing, "created": False,
                "user_message": repos.Messages.by_client_id(conn, conversation_id, cmid)}
    user_msg = {"id": _new_id(), "conversation_id": conversation_id, "role": "user",
                "body_md": text, "citations_json": "[]", "client_message_id": cmid,
                "created_at": _now()}
    repos.Messages.insert(conn, user_msg)
    try:
        from app.services import state as _store
        _store.maybe_title_conversation(conn, conversation_id, text)
    except Exception:
        pass
    turn = {"id": _new_id(), "conversation_id": conversation_id, "project_id": project_id,
            "client_message_id": cmid, "user_message_id": user_msg["id"],
            "status": "running", "provider": "", "error": "",
            "created_at": _now(), "updated_at": _now()}
    repos.Turns.upsert(conn, turn)
    files_repo.save_turn_selection(conn, turn_id=turn["id"], file_ids=selected_ids,
                                   project_id=project_id, conversation_id=conversation_id)
    return {"turn": turn, "created": True, "user_message": user_msg}


def start_turn_bg(db_path, root, turn_id: str) -> None:
    _POOL.submit(_run_turn, str(db_path), str(root), turn_id)


def _set_turn(db_path, turn_id, status, extra=None):
    from app.database.sqlite import connect
    conn = connect(db_path)
    try:
        repos.Turns.set_status(conn, turn_id, status, extra)
    except Exception:
        pass
    finally:
        conn.close()


def _has_turn_event(db_path, turn_id, event_type: str) -> bool:
    from app.database.sqlite import connect
    conn = connect(db_path)
    try:
        return any(
            row.get("event_type") == event_type
            for row in repos.ExecutionEvents.for_turn(conn, turn_id)
        )
    except Exception:
        return False
    finally:
        conn.close()


def _run_turn(db_path: str, root: str, turn_id: str) -> None:
    """Background turn execution. Own connection; never raises to the pool."""
    from app.database.sqlite import connect
    conn = connect(db_path)
    # Wall-clock t0 is captured inside _run_turn right after the turn_started
    # emit, so ThreadPool (_POOL, max_workers=4) queue wait is EXCLUDED:
    # this measures engine time, not total founder-perceived wait.
    t0 = None
    try:
        turn = repos.Turns.get(conn, turn_id)
        if turn is None or turn["status"] in repos.Turns.TERMINAL:
            return
        pid, convo_id = turn["project_id"], turn["conversation_id"]
        user_msg = repos.Messages.by_client_id(conn, convo_id, turn["client_message_id"])
        text = (user_msg or {}).get("body_md", "")
        if not _has_turn_event(db_path, turn_id, "turn_started"):
            emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                       event_type="turn_started", label="Starting your request")
        t0 = time.monotonic()
        from app.contracts.runtime import get_ai_runtime
        from app.services import manager_loop
        from app.services.llm.config import config_from_env
        from app.services.llm.router import (normalize_selection,
                                             openai_configured, resolve_candidates)
        from app.services.tools import build_default_registry
        cfg = config_from_env()
        selection = _provider_selection(conn)
        from app.services.files import repo as files_repo
        selected_attachment_ids = files_repo.selected_for_turn(
            conn, turn_id=turn_id, project_id=pid, conversation_id=convo_id)
        res, used = None, "deterministic"
        if get_ai_runtime() == "langgraph":
            graph_res = _run_graph_turn(
                conn, db_path, pid, convo_id, turn_id, text,
                on_event=lambda et, pl: _on_loop_event(
                    db_path, pid, convo_id, turn_id, et, pl))
            if graph_res is not None:
                res, used = graph_res, "langgraph"
            elif selected_attachment_ids:
                raise AttachmentExecutionError(
                    "The selected attachment could not be processed safely. "
                    "Please upload it again or retry without the attachment.")
        elif selected_attachment_ids:
            raise AttachmentExecutionError(
                "Attachments require the graph runtime, which is unavailable. "
                "Please retry when it is available or remove the attachment.")
        if res is None:
            candidates = resolve_candidates(selection, cfg.provider_priority,
                                            openai_ok=openai_configured(),
                                            legacy_agentic=False)
            for candidate in candidates:
                if candidate == "deterministic":
                    break
                try:
                    if not _has_turn_event(db_path, turn_id, "provider_selected"):
                        emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                                   event_type="provider_selected",
                                   label=f"Working with {candidate.title()}",
                                   metadata={"provider": candidate})
                    provider = _build_provider(candidate, conn, pid, cfg)
                    res = manager_loop.run_manager_turn(
                        conn, project_id=pid, conversation_id=convo_id, user_text=text,
                        provider=provider, registry=build_default_registry(), root=root,
                        budget={"max_tool_iters": cfg.max_tool_iters,
                                "turn_timeout_s": cfg.turn_timeout_s,
                                "db_path": db_path},
                        on_event=lambda et, pl: _on_loop_event(
                            db_path, pid, convo_id, turn_id, et, pl))
                    used = candidate
                    break
                except Exception:
                    break
        if res is None:
            from app.services import account_manager, state as store
            if not _has_turn_event(db_path, turn_id, "provider_selected"):
                emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                           event_type="provider_selected", label="Answering directly",
                           metadata={"provider": "deterministic"})
            if not _has_turn_event(db_path, turn_id, "context_completed"):
                emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                           event_type="context_completed", label="Project context loaded")
            prior = _prior_provenance(conn, convo_id)
            if not _has_turn_event(db_path, turn_id, "synthesis_started"):
                emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                           event_type="synthesis_started", label="Writing response")
            turn_out = account_manager.handle_turn(conn, root, text, prior_provenance=prior)
            res = {"reply_md": turn_out["reply_md"], "provenance": turn_out.get("provenance", []),
                   "job_ids": [], "approval_ids": []}
            used = "deterministic"
        _finish_turn(db_path, root, turn, res, used, text,
                     elapsed_ms=max(0, int((time.monotonic() - t0) * 1000)) if t0 is not None else 0)
    except Exception as e:
        try:
            turn = repos.Turns.get(conn, turn_id)
            pid = (turn or {}).get("project_id", "")
            convo_id = (turn or {}).get("conversation_id", "")
            elapsed_ms = max(0, int((time.monotonic() - t0) * 1000)) if t0 is not None else 0
            used_failed = locals().get("used", "deterministic")
            attachment_failure = isinstance(e, AttachmentExecutionError)
            if not (_has_turn_event(db_path, turn_id, "turn_completed")
                    or _has_turn_event(db_path, turn_id, "turn_failed")):
                emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                           event_type="turn_failed",
                           label=(str(e) if attachment_failure
                                  else "Something interrupted this run."),
                           metadata={"provider": used_failed, "latency_ms": elapsed_ms,
                                     "latency_display": _format_latency(elapsed_ms)})
            _set_turn(
                db_path, turn_id, "failed",
                {"error": (str(e) if attachment_failure
                           else "The request could not be completed.")})
        except Exception:
            pass
    finally:
        conn.close()


def _finish_turn(db_path, root, turn, res, used, text, elapsed_ms=None):
    from app.database.sqlite import connect
    conn = connect(db_path)
    try:
        try:
            ms = int(elapsed_ms) if elapsed_ms is not None else 0
        except (TypeError, ValueError):
            ms = 0
        if ms < 0:
            ms = 0
        latency_display = _format_latency(ms)
        pid, convo_id, turn_id = turn["project_id"], turn["conversation_id"], turn["id"]
        existing_types = {
            row.get("event_type")
            for row in repos.ExecutionEvents.for_turn(conn, turn_id)
        }
        if "turn_completed" in existing_types or "turn_failed" in existing_types:
            return
        reply = sanitize_user_text(res.get("reply_md", "") or "", 1_000_000)
        provenance = []
        for item in res.get("provenance", []) or []:
            if isinstance(item, dict):
                provenance.append(sanitize_metadata(item))
            else:
                provenance.append({"path": sanitize_user_text(item, 1000)})
        for jid in res.get("job_ids", []) or []:
            title = _job_title(conn, jid)
            emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                       job_id=jid, event_type="job_created",
                       label="Background work started",
                       detail=title, metadata={"job_id": jid})
        for aid in res.get("approval_ids", []) or []:
            emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                       event_type="approval_required", label="Waiting for your approval",
                       detail=_approval_title(conn, aid), metadata={"approval_id": aid})
        for prov in provenance:
            path = (prov or {}).get("path", "")
            if str(path).startswith("http"):
                emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                           event_type="web_source", label="Found a source", detail=path[:300],
                           metadata={"url": path})
        assistant = {
            "id": _new_id(),
            "conversation_id": convo_id,
            "role": "assistant",
            "body_md": reply,
            "citations_json": json.dumps(provenance, ensure_ascii=False),
            "client_message_id": "",
            "created_at": _now(),
        }
        message_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "client_message_id" not in message_columns:
            assistant.pop("client_message_id", None)
        event_rows = []
        if "assistant_completed" not in existing_types:
            event_rows.append(_event_row(
                project_id=pid,
                conversation_id=convo_id,
                turn_id=turn_id,
                event_type="assistant_completed",
                label="Response ready",
                detail=reply,
                metadata={"provider": used, "cumulative": True,
                          "latency_ms": ms, "latency_display": latency_display},
            ))
        event_rows.append(_event_row(
            project_id=pid,
            conversation_id=convo_id,
            turn_id=turn_id,
            event_type="turn_completed",
            label="Response ready",
            metadata={"provider": used, "latency_ms": ms,
                      "latency_display": latency_display},
        ))
        _insert_uncommitted(conn, "messages", assistant)
        for event_row in event_rows:
            _insert_uncommitted(conn, "execution_events", event_row)
        conn.execute(
            "UPDATE turns SET status = ?, provider = ?, updated_at = ? WHERE id = ?",
            ("completed", str(used or ""), _now(), turn_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


#: Tree event types bridged to the TreeEmitter (DEV-008-SKILLS-OPS W4).
#: When a tree is present these delegate to it; otherwise the generic
#: branches below apply unchanged.
_TREE_BRIDGE_TYPES = frozenset({
    "account_manager_started", "task_planned",
    "employee_queued", "employee_started", "employee_completed", "employee_failed",
    "skill_selected", "skill_loaded",
    "tool_started", "tool_completed", "tool_failed",
    "evidence_added", "synthesis_started", "synthesis_completed",
    "approval_required",
})


def _bridge_tree_event(tree, et, pl) -> bool:
    """Delegate one hook event to the TreeEmitter. True if handled.

    Never raises: a broken payload must not change what the turn does. Returns
    False when there is nothing to delegate (no tree, unknown type, or a
    skill record that cannot be resolved) so the caller falls through to the
    generic branches.
    """
    if tree is None or et not in _TREE_BRIDGE_TYPES:
        return False
    try:
        pl = pl or {}
        if et == "account_manager_started":
            tree.account_manager_started(
                intent=str(pl.get("intent", "") or ""),
                route=str(pl.get("route", "") or ""))
        elif et == "task_planned":
            tasks = pl.get("tasks", []) or []
            if not isinstance(tasks, list):
                tasks = []
            tree.task_planned(tasks=tasks,
                              employee_count=pl.get("employee_count", len(tasks)))
        elif et in ("employee_queued", "employee_started"):
            task = pl.get("task", {}) or {}
            if not isinstance(task, dict):
                task = {"query": str(task)}
            fn = tree.employee_queued if et == "employee_queued" else tree.employee_started
            fn(employee_id=str(pl.get("employee_id", "") or ""),
               role=str(pl.get("role", "") or ""), task=task)
        elif et == "employee_completed":
            tree.employee_completed(
                employee_id=str(pl.get("employee_id", "") or ""),
                role=str(pl.get("role", "") or ""),
                duration_ms=pl.get("duration_ms", 0))
        elif et == "employee_failed":
            exc = pl.get("exc")
            if not isinstance(exc, BaseException):
                exc = RuntimeError(str(pl.get("error", "") or "employee failed"))
            tree.employee_failed(
                employee_id=str(pl.get("employee_id", "") or ""),
                role=str(pl.get("role", "") or ""), exc=exc,
                duration_ms=pl.get("duration_ms", 0))
        elif et == "skill_selected":
            tree.skill_selected(
                skill_id=str(pl.get("skill_id", "") or ""),
                reason_code=str(pl.get("reason_code", "") or ""),
                score=pl.get("score", 0.0),
                employee_id=str(pl.get("employee_id", "") or ""))
        elif et == "skill_loaded":
            record = pl.get("record")
            if record is None and str(pl.get("skill_id", "") or ""):
                try:
                    from app.services.skills.registry import load_registry
                    record = load_registry(disabled="").get(
                        str(pl.get("skill_id", "") or ""))
                except Exception:
                    record = None
            if record is None:
                return False
            tree.skill_loaded(record=record,
                              employee_id=str(pl.get("employee_id", "") or ""))
        elif et in ("tool_started", "tool_completed", "tool_failed"):
            tree.tool_event(
                event_type=et,
                tool_id=str(pl.get("tool_id", "") or pl.get("tool", "") or ""),
                tool_run_id=str(pl.get("tool_run_id", "") or ""),
                employee_id=str(pl.get("employee_id", "") or ""),
                status=str(pl.get("status", "") or (
                    "RUNNING" if et == "tool_started"
                    else "COMPLETE" if et == "tool_completed" else "FAILED")),
                duration_ms=pl.get("duration_ms", 0))
        elif et == "evidence_added":
            tree.evidence_added(
                count=pl.get("count", pl.get("evidence_count", 0)),
                kind=str(pl.get("kind", "") or pl.get("evidence_kind", "") or ""),
                employee_id=str(pl.get("employee_id", "") or ""))
        elif et == "synthesis_started":
            tree.synthesis(phase="started")
        elif et == "synthesis_completed":
            tree.synthesis(phase="completed",
                           duration_ms=pl.get("duration_ms", 0))
        elif et == "approval_required":
            tree.approval_required(
                approval_id=str(pl.get("approval_id", "") or ""),
                action_class=str(pl.get("action_class", "") or "yellow"),
                title=str(pl.get("title", "") or pl.get("detail", "") or ""))
        else:
            return False
        return True
    except Exception:
        return False


def _on_loop_event(db_path, pid, convo_id, turn_id, et, pl, tree=None):
    """Map loop hook events to persisted human-language lifecycle events.

    When ``tree`` (a ``TreeEmitter``) is supplied, tree event types delegate
    to it first so both public surfaces (``_run_graph_turn`` here and
    ``execute_thread`` in ``graph_runtime.py``) stream the same tree. Generic
    types fall through to the pre-existing branches unchanged.
    """
    pl = pl or {}
    if _bridge_tree_event(tree, et, pl):
        return
    if et == "context_started":
        emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                   event_type="context_started", label="Loading project context")
    elif et == "context_completed":
        emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                   event_type="context_completed", label="Project context loaded")
    elif et == "assistant_delta":
        text = str(pl.get("text", ""))[:6000]
        if text.strip():
            emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                       event_type="assistant_delta", label="Writing response",
                       detail=text, metadata={"cumulative": True})
    elif et == "synthesis_started":
        emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                   event_type="synthesis_started", label="Writing response")
    elif et == "tool_started":
        name = pl.get("tool", "")
        spec = _TOOL_LABELS.get(name)
        label = (spec[1] if spec else str(name or "tool").replace("_", " "))
        if label in ("Website audit ready", "Proposal prepared", "Experiment proposed"):
            label = str(name or "tool").replace("_", " ")
        try:
            detail = spec[2](pl.get("args", {}) or {}, {}) if spec else ""
        except Exception:
            detail = ""
        emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                   event_type="tool_started", label=f"Running {label}", detail=detail,
                   metadata={"tool": str(name or "")[:128], "status": "RUNNING"})
    elif et == "tool_completed":
        name, obs = pl.get("tool", ""), pl.get("obs", {}) or {}
        status = str(pl.get("status") or "").lower()
        blocked = "block" in status
        succeeded = bool(pl.get("ok", obs.get("ok", True))) and not blocked
        if name not in ("rag_search", "search_project_knowledge", "web_search",
                        "instagram_audit", "delegate_to_marketing_pm",
                        "request_approval"):
            label = str(name or "Tool").replace("_", " ")
            detail = str(pl.get("error") or "")[:300]
            emit_event(
                db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                event_type=("tool_failed" if blocked or not succeeded else "tool_completed"),
                label=(f"Blocked: {label}" if blocked else
                       f"Failed: {label}" if not succeeded else f"Completed: {label}"),
                detail=detail,
                metadata={"tool": str(name or "")[:128],
                          "status": "BLOCKED" if blocked else
                                   "FAILED" if not succeeded else "COMPLETED"},
            )
        if name in ("rag_search", "search_project_knowledge"):
            n = len(obs.get("hits", []) or [])
            emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                       event_type="rag_completed", label="Internal knowledge searched",
                       detail=f"{n} relevant item{'s' if n != 1 else ''}" if n else "nothing directly relevant",
                       metadata={"hits": n})
        elif name == "web_search":
            n = len(obs.get("results", []) or [])
            emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                       event_type="web_completed", label="Web research finished",
                       detail=f"{n} source{'s' if n != 1 else ''}",
                       metadata={"sources": n})
            for r in (obs.get("results", []) or [])[:8]:
                url = (r or {}).get("url", "")
                if url:
                    emit_event(db_path, project_id=pid, conversation_id=convo_id,
                               turn_id=turn_id, event_type="web_source",
                               label="Found a source", detail=url[:300],
                               metadata={"url": url})
        elif name == "instagram_audit":
            audit = (obs.get("audit") or {}) if isinstance(obs, dict) else {}
            ev = audit.get("evidence", {}) or {}
            src = ev.get("source", "none")
            n = len(audit.get("posts", []) or [])
            emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                       event_type="instagram_provider_completed",
                       label="Instagram checked" if ev.get("status") in ("verified", "partial")
                       else "Instagram unavailable",
                       detail=f"{src} · {n} post{'s' if n != 1 else ''}" if n
                       else f"{src} · {'; '.join(audit.get('unknowns', [])[:1])}"[:300],
                        metadata={"source": src, "posts": n,
                                  "status": ev.get("status", "unavailable"),
                                  "tool_run_id": str(pl.get("tool_run_id", "") or "")})

        elif name == "delegate_to_marketing_pm" and obs.get("job_id"):
            emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                       job_id=obs["job_id"], event_type="job_created",
                       label="Background work started",
                       detail=_job_title_db(db_path, obs["job_id"]),
                       metadata={"job_id": obs["job_id"]})
        elif name == "request_approval" and obs.get("approval_id"):
            emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                       event_type="approval_required", label="Waiting for your approval",
                       detail=str((obs.get("title", "") or ""))[:160],
                       metadata={"approval_id": obs["approval_id"]})
    elif et == "tool_failed":
        name = pl.get("tool", "")
        status = str(pl.get("status") or "").lower()
        blocked = "block" in status
        label = str(name or "Tool").replace("_", " ")
        emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                   event_type="tool_failed",
                   label=(f"Blocked: {label}" if blocked else f"Failed: {label}"),
                   detail=str(pl.get("error") or "")[:300],
                   metadata={"tool": str(name or "")[:128],
                             "status": "BLOCKED" if blocked else "FAILED"})


def _job_title(conn, job_id):
    try:
        job = repos.BackgroundJobs.get(conn, job_id)
        if not job:
            return ""
        brief = (job.get("brief_md", "") or "").split("\n")[0]
        return (brief or job.get("kind", ""))[:160]
    except Exception:
        return ""


def _job_title_db(db_path, job_id):
    from app.database.sqlite import connect
    conn = connect(db_path)
    try:
        return _job_title(conn, job_id)
    finally:
        conn.close()


def _approval_title(conn, approval_id):
    try:
        row = repos.Approvals.get(conn, approval_id)
        return ((row or {}).get("title", "") or "")[:160]
    except Exception:
        return ""


def _prior_provenance(conn, convo_id):
    import json as _json
    prior = []
    for m in repos.Messages.for_conversation(conn, convo_id):
        if m["role"] == "assistant":
            try:
                prior = _json.loads(m["citations_json"] or "[]")
            except ValueError:
                prior = []
    return prior


def _provider_selection(conn):
    from app.services.config_service import ConfigService
    return str(ConfigService.get_setting(
        "manager_provider", "auto", conn=conn) or "auto").strip().lower()


def _run_graph_turn(conn, db_path, pid, convo_id, turn_id, text, on_event=None):
    """In-process LangGraph turn for AI_RUNTIME=langgraph (default). None on failure."""
    try:
        from app.routes.graph_runtime import _get_graph
        from app.graphs.state import initial_state
        if not _has_turn_event(db_path, turn_id, "provider_selected"):
            emit_event(db_path, project_id=pid, conversation_id=convo_id, turn_id=turn_id,
                       event_type="provider_selected", label="Working with LangGraph",
                       metadata={"provider": "langgraph"})
        graph = _get_graph()
        from app.services.files import repo as files_repo
        from app.services.files.attachments import assemble_attachment_evidence
        from app import deps
        selected_ids = files_repo.selected_for_turn(conn, turn_id=turn_id, project_id=pid,
                                                   conversation_id=convo_id)
        attachment_evidence = assemble_attachment_evidence(
            conn, root=deps.ROOT, project_id=pid,
            conversation_id=convo_id, file_ids=selected_ids)
        state = initial_state(project_id=pid, conversation_id=convo_id,
                              turn_id=turn_id, user_request=text)
        state["attachment_evidence"] = attachment_evidence
        try:
            from app.services.skills.tree import TreeEmitter
            _tree = TreeEmitter(
                db_path=db_path, project_id=pid, conversation_id=convo_id,
                turn_id=turn_id, thread_id=turn_id)
        except Exception:
            _tree = None
        configurable = {"thread_id": turn_id}
        if _tree is not None:
            configurable["tree"] = _tree
        if callable(on_event):
            # Rebuild the loop bridge around the same _on_loop_event so tree
            # types delegate to the per-turn emitter while every generic type
            # keeps its pre-existing branch. The original callback is the same
            # function without a tree, so nothing is lost by replacing it.
            configurable["event_callback"] = (
                lambda et, pl, _t=_tree: _on_loop_event(
                    db_path, pid, convo_id, turn_id, et, pl, tree=_t))
        result = graph.invoke(
            state, config={"configurable": configurable})
        result = result or {}
        final = str(result.get("final_answer") or "")
        if not final:
            return None
        citations = result.get("citation_evidence")
        if not isinstance(citations, list) or not citations:
            # Project sources may only be projected from the hits retained in
            # the graph result (the same snapshot used to build completion
            # context). Never run a second retrieval after the answer exists.
            from app.graphs.state import citation_projection
            citations = citation_projection(
                project_id=pid,
                rag_hits=(result.get("rag_hits") or result.get("retrieved_evidence")),
                attachment_evidence=attachment_evidence)
        return {"reply_md": final, "provenance": citations,
                "job_ids": [], "approval_ids": []}
    except Exception:
        return None


def _build_provider(candidate, conn, pid, cfg):
    if candidate == "openai":
        from app.services.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(model=cfg.model, timeout_s=cfg.per_call_timeout_s)
    if candidate == "openrouter":
        from app.services.config_service import ConfigService
        from app.services.llm.openrouter_provider import OpenRouterProvider
        model = ConfigService.get_setting(
            "openrouter_default_model", "", conn=conn) or "openrouter/auto"
        return OpenRouterProvider(
            model=str(model), timeout_s=cfg.per_call_timeout_s)
    if candidate == "fake":
        from app.services.llm.fake_provider import FakeProvider
        return FakeProvider()
    raise ValueError(f"unknown provider candidate: {candidate}")
