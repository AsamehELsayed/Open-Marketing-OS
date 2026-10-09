"""DEV-005 W6 — FastAPI graph runtime (thin adapter, additive).

Routes stay presentation-only: project scoping happens BEFORE any graph
invoke (fail-closed NO_PROJECT_SCOPE), graph orchestration lives in
app/graphs/* (W1), model telemetry in app/services/llm/model_router.py (W5).
This module only validates input, invokes the compiled StateGraph with a
thread_id, persists frozen W0 wire events, and exposes model visibility.

Endpoints (mounted ONLY when AI_RUNTIME=langgraph; legacy default untouched):
  POST /api/graph/threads                        create thread (turn)
  POST /api/graph/threads/{thread_id}/execute    invoke graph for the turn
  GET  /api/graph/threads/{thread_id}            thread state
  POST /api/graph/threads/{thread_id}/resume     approve/resume via Command
  GET  /api/graph/threads/{thread_id}/events     SSE (frozen wire types)

W1 passthrough: execute invokes build_account_manager_graph with
config={"configurable": {"thread_id": ...}}; resume continues it with
Command(resume={"approved": bool}); an isolated build_approval_graph
round-trip proves the same interrupt/Command primitive. SSE types come
exclusively from W1 NODE_EVENT_MAP via build_event, so every emitted type
is in frozen LEGACY_EVENT_TYPES.
"""
from __future__ import annotations

import json as _json
import re as _re
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import deps
from app.contracts.approvals import ApprovalResume
from app.contracts.events import (
    GraphExecutionEvent,
    project_event_boundary,
    safe_error_message,
    sanitize_metadata,
    sanitize_user_text,
)
from app.database import repos
from app.graphs.approvals import stable_key
from app.graphs.events import NODE_EVENT_MAP, build_event

router = APIRouter(prefix="/api/graph", tags=["graph-runtime"])

_GRAPH = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ok(data: dict, headers: dict | None = None,
        status_code: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data},
                        headers=headers or {}, status_code=status_code)


def _no_scope(detail: str, status: int = 400) -> HTTPException:
    safe_detail = sanitize_user_text(detail, 300)
    return HTTPException(status_code=status, detail=f"NO_PROJECT_SCOPE: {safe_detail}")


def _require_project(conn, project_id: str | None) -> dict:
    pid = (project_id or "").strip()
    if not pid:
        raise _no_scope("project_id is required (fail closed)")
    try:
        proj = repos.Projects.get(conn, pid)
    except Exception:
        proj = None
    if proj is None:
        raise _no_scope(f"unknown project {pid!r}", status=404)
    return proj


def _get_model_router():
    """Build ModelRouter with Local wired only to the verified managed server."""
    try:
        from app.services.llm.local_config import LocalLlamaConfig
        from app.services.llm.local_llama import LocalLlamaProvider
        from app.services.llm.model_router import (
            ModelRouter, get_managed_local_runtime_manager,
        )
        from app.services.config_service import ConfigService

        manager = get_managed_local_runtime_manager()
        status = manager.rediscover()
        enabled = str(ConfigService.get_setting(
            "local_model_enabled", "on") or "on").strip().lower() not in {
                "0", "off", "false", "no", "disabled"}
        expected = str(status.get("expected_model_id") or "")
        if (enabled and status.get("ready") is True and
                status.get("verification_state") == "verified" and
                status.get("activation_state") == "active" and expected and
                status.get("observed_model_id") == expected):
            cfg = LocalLlamaConfig(
                base_url=manager.base_url,
                model=expected,
                quantization=manager.catalog.model.quantization,
                adapter="",
            )
            provider = LocalLlamaProvider(config=cfg)
            return _make_model_router(local_provider=provider, local_model=expected)
    except Exception:
        pass
    return _make_model_router(local_provider=None, local_model=None)


def _make_model_router(*, local_provider, local_model: str | None):
    """ModelRouter with cloud providers wired when credentials exist (no
    AUTO change: escalation remains local-first and user-gated)."""
    from app.services.llm.model_router import ModelRouter
    openai_provider = None
    openrouter_provider = None
    try:
        from app.services.config_service import ConfigService
        if ConfigService.is_openai_configured():
            from app.services.llm.openai_provider import OpenAIProvider
            openai_provider = OpenAIProvider(
                model=str(ConfigService.get_setting("active_model", "gpt-4o-mini") or "gpt-4o-mini"),
                timeout_s=45,
            )
    except Exception:
        openai_provider = None
    default_openrouter_model = "openrouter/auto"
    try:
        from app.services.llm import openrouter_provider as orp
        if orp.is_configured():
            curated = []
            try:
                curated = [m.strip() for m in str(_get_setting(
                    "openrouter_curated_models", "")).split(",") if m.strip()]
            except Exception:
                curated = []
            saved_model = str(_get_setting("openrouter_default_model", "") or "").strip()
            default_openrouter_model = saved_model or (
                curated[0] if curated else "openrouter/auto")
            openrouter_provider = orp.OpenRouterProvider(
                model=default_openrouter_model, timeout_s=45)
    except Exception:
        openrouter_provider = None
    from app.services.config_service import ConfigService
    return ModelRouter(local_provider=local_provider,
                       openai_provider=openai_provider,
                       openrouter_provider=openrouter_provider,
                       default_local_model=local_model or "local-default",
                       default_openai_model=str(
                           ConfigService.get_setting("active_model", "gpt-4o-mini")
                           or "gpt-4o-mini"),
                       default_openrouter_model=default_openrouter_model)


def _generation_telemetry(response, model_call) -> dict:
    """Project persisted call data plus explicitly provider-reported extras."""
    if model_call is None:
        return {}
    telemetry = model_call.model_dump()
    # ModelCall.model is the router's resolved/actual model; keep its canonical
    # field and the explicit alias expected by the runtime telemetry surface.
    if telemetry.get("model"):
        telemetry["actual_model"] = telemetry["model"]
    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):
        for key in ("provider", "requested_model", "actual_model", "attempts",
                    "status", "status_code"):
            if key in usage:
                telemetry[key] = usage[key]
    for key in ("attempts", "status", "status_code"):
        value = getattr(response, key, None)
        if value is not None:
            telemetry[key] = value
    return telemetry


def _get_setting(key: str, default: str | None = None) -> str | None:
    try:
        from app.services.config_service import ConfigService
        return ConfigService.get_setting(key, default)
    except Exception:
        return default


def _cloud_provider(manager_provider: str) -> str:
    selected = str(manager_provider or "auto").strip().lower()
    if selected in {"openai", "openrouter"}:
        return selected
    try:
        from app.services.config_service import ConfigService
        openai_ready = ConfigService.is_openai_configured()
        openrouter_ready = ConfigService.is_openrouter_configured()
        selected_model = str(
            ConfigService.get_setting("openrouter_default_model", "") or ""
        ).strip()
    except Exception:
        openai_ready = False
        openrouter_ready = False
        selected_model = ""
    if openrouter_ready and (not openai_ready or selected_model):
        return "openrouter"
    return "openai"


# Frozen model-context contract (DEV-007R-HOTFIX-3 W1-6). Labels and the
# [CURRENT USER TURN] marker are normative — W2 relies on them.
_HONESTY_PROMPT = (
    "SOURCE PRIORITY: 1) the current user turn 2) the current conversation "
    "transcript 3) tool results from this turn 4) authoritative structured "
    "project state 5) project RAG knowledge 6) external research.\n"
    "PROJECT IDENTITY: The [PROJECT CONTEXT] block names the user's active "
    "project. Nicknames, shorthand, transliterations, or the project's name "
    "rendered in any language (e.g. an Arabic rendering of the company name) "
    "refer to this active project — the user never needs to re-explain their "
    "own project's name. هوية المشروع: أي ذكر من المستخدم لاسم مشروعه أو "
    "اسم مختصر منه أو ترجمة له بالعربية يعود دائماً إلى المشروع النشط في "
    "[PROJECT CONTEXT] — لا تسأل المستخدم ما معنى اسم مشروعه.\n"
    "HONESTY: If the provided context is insufficient to answer, ask one "
    "concise clarifying question instead of inventing project facts. Never "
    "claim a website was analyzed, an Instagram profile was fetched, or "
    "research was run unless TOOL RESULTS THIS TURN shows it. Do not treat "
    "project knowledge as conversation memory.\n"
)


def build_context_text(context: dict) -> str:
    """Labeled context block from the structured context dict."""
    if not isinstance(context, dict):
        return ""
    sections: list[str] = []
    resolutions = [str(a).strip() for a in (context.get("identity_resolutions") or [])
                   if str(a).strip()]
    ps0 = context.get("project_state") or {}
    pname = str((ps0 or {}).get("name") or "").strip() if isinstance(ps0, dict) else ""
    if resolutions and pname:
        quoted = "، ".join(f"'{r}'" for r in resolutions)
        sections.append(
            "[IDENTITY RESOLVED] source: project_state\n"
            f"In the current user turn, {quoted} refer(s) to the user's "
            f"active project: {pname}. Answer about this project directly — "
            "do not ask what the name means.")
    ps = context.get("project_state") or {}
    if isinstance(ps, dict):
        name = str(ps.get("name") or "").strip()
        website = str(ps.get("website") or "").strip()
        goal = str(ps.get("goal") or "").strip()
        instagram = str(ps.get("instagram_handle") or "").strip()
        if name or website or goal or instagram:
            line = (f"[PROJECT CONTEXT] source: project_state\n"
                    f"ACTIVE PROJECT (the user's own business): {name or '(unnamed project)'}\n"
                    f"website: {website}; goal: {goal}; "
                    f"instagram: {instagram}\n"
                    "The user often refers to this project by shorthand, "
                    "nicknames, transliterations, or its name in Arabic — "
                    "any such reference means THIS project.")
            accounts = ps.get("social_accounts") or []
            acct_bits = []
            for acc in accounts:
                if not isinstance(acc, dict):
                    continue
                handle = str(acc.get("handle") or "").strip()
                if not handle:
                    continue
                acct_bits.append(
                    f"{acc.get('platform', '')} @{handle}"
                    f" ({acc.get('status', '')})")
            if acct_bits:
                line += "; accounts: " + ", ".join(acct_bits)
            memories = [str(m) for m in (ps.get("memories") or [])
                        if str(m).strip()]
            if memories:
                line += "\nmemories: " + "; ".join(m[:160] for m in memories[:5])
            sections.append(line)
    conv = context.get("conversation_context") or []
    if conv:
        lines = []
        for m in conv[-10:]:
            if not isinstance(m, dict):
                continue
            role = "Assistant" if str(m.get("role", "")) == "assistant" else "User"
            body = str(m.get("body_md", "") or "").strip().replace("\n", " ")
            if body:
                lines.append(f"{role}: {body[:400]}")
        if lines:
            sections.append("[CONVERSATION SO FAR] source: conversation_context\n"
                            + "\n".join(lines))
    tools = context.get("tool_results") or []
    if tools:
        lines = []
        for r in tools[:10]:
            text = ""
            if isinstance(r, dict):
                text = f"- {r.get('path', '')}#{r.get('chunk_id', '')}"
            else:
                text = f"- {str(r)[:400]}"
            if text.strip("- ").strip():
                lines.append(text)
        if lines:
            sections.append("[TOOL RESULTS THIS TURN] source: tool_results\n"
                            + "\n".join(lines))
    attachments = [h for h in (context.get("rag_evidence") or [])
                   if isinstance(h, dict) and h.get("source") == "turn_attachment"]
    if attachments:
        lines = []
        for h in attachments[:10]:
            filename = sanitize_user_text(h.get("filename", "attachment"), 160)
            file_id = sanitize_user_text(h.get("file_id", ""), 80)
            chunk = sanitize_user_text(h.get("chunk_id", ""), 120)
            snippet = sanitize_user_text(h.get("text", ""), 3000)
            lines.append(f"- {filename} (file_id: {file_id}; source: {chunk}): {snippet}")
        sections.append("[CURRENT TURN ATTACHMENTS] source: turn_attachment\n" + "\n".join(lines))
    rag = [h for h in (context.get("rag_evidence") or [])
           if isinstance(h, dict) and h.get("source") != "turn_attachment"]
    if rag:
        lines = []
        for h in rag[:5]:
            if not isinstance(h, dict):
                continue
            path = str(h.get("path", "") or "")
            cid = str(h.get("chunk_id", "") or "")
            text = str(h.get("text", "") or "").strip()[:300]
            if path:
                lines.append(f"- SOURCE: {path}#{cid} {text}".rstrip())
        if lines:
            sections.append("[PROJECT KNOWLEDGE] source: project_rag\n"
                            + "\n".join(lines))
    return "\n\n".join(sections)


def _make_complete_fn(router):
    if router is None:
        return None

    def _complete(*, turn_id: str, project_id: str, user_request: str, route: str,
                  conversation_id: str = "", context=None,
                  model_provider: str = "AUTO", model_id: str = ""):
        import uuid
        import inspect
        from app.services.marketing_guardrails import (
            MARKETING_NUMERIC_GUARDRAIL_PROMPT,
            enforce_numeric_guardrail,
        )
        context_text = ""
        try:
            context_text = build_context_text(context)
        except Exception:
            context_text = ""
        if context_text:
            user_content = (context_text + "\n\n"
                            "[CURRENT USER TURN] source: user_turn\n"
                            + user_request)
        else:
            user_content = user_request
        with deps.get_db() as conn:
            ai_mode = str(_get_setting("ai_mode", "AUTO") or "AUTO").strip().upper()
            manager_provider = str(
                _get_setting("manager_provider", "auto") or "auto").strip().lower()
            selected_provider = str(model_provider or "AUTO").strip().upper()
            model_override = str(model_id or "").strip()
            if selected_provider == "LOCAL":
                mode = "LOCAL"
            elif selected_provider == "OPENROUTER":
                mode = "OPENROUTER"
            elif ai_mode == "CLOUD":
                mode = _cloud_provider(manager_provider).upper()
            elif ai_mode in {
                "AUTO", "LOCAL", "OPENAI", "OPENROUTER", "BASE", "MARKETING_LORA",
            }:
                mode = ai_mode
            else:
                mode = "AUTO"
            adapter_to_pass = str(
                _get_setting("marketing_adapter", "OMOS-Qwen2.5-7B-Marketing-v1")
                or "")
            if mode == "BASE":
                adapter_to_pass = ""
            profile_to_pass = (adapter_to_pass
                               if mode in ("OPENAI", "OPENROUTER") else "")
            quant = str(_get_setting("llama_quantization", "") or "")
            system_prompt = (
                "You are the Marketing Account Manager for Open Marketing OS.\n"
                + MARKETING_NUMERIC_GUARDRAIL_PROMPT
            )
            if context_text:
                system_prompt = system_prompt + _HONESTY_PROMPT
            # Emit synthesize node lifecycle event before tokens stream
            _emit(conn, project_id=project_id, conversation_id=conversation_id, turn_id=turn_id,
                  node="synthesize", detail="Writing response", metadata={"route": route})

            cid = f"mc-{uuid.uuid4().hex[:12]}"
            import time as _time
            route_selector = getattr(router, "_route_decision", None)
            resolved_route = None
            if callable(route_selector):
                try:
                    # AUTO's concrete provider is chosen inside ModelRouter.
                    # Resolve the same pure route decision before wiring its
                    # optional observer so telemetry follows that provider.
                    import inspect
                    params = inspect.signature(route_selector).parameters.values()
                    accepts_override = any(p.name == "model_override" or
                                            p.kind == inspect.Parameter.VAR_KEYWORD
                                            for p in params)
                    resolved_route = (route_selector(
                        mode, None, model_override=model_override or None)
                        if accepts_override else route_selector(mode, None))
                except Exception:
                    resolved_route = None
            openrouter_invocation = (
                getattr(resolved_route, "provider", None) == "openrouter"
                if callable(route_selector) else mode == "OPENROUTER"
            )
            transport_events = []
            resolved_model = getattr(resolved_route, "model", None)
            requested_transport_model = str(
                model_override or resolved_model or _get_setting("openrouter_default_model", "openrouter/auto")
                or "openrouter/auto").strip()

            def _on_transport_event(event: dict):
                if not openrouter_invocation or len(transport_events) >= 32:
                    return
                safe = _safe_openrouter_transport_event(
                    event, call_id=cid, turn_id=turn_id,
                    requested_model=requested_transport_model)
                if safe:
                    transport_events.append(safe)
                    lifecycle_meta = {**safe, "transport_lifecycle": True}
                    try:
                        with deps.get_db() as c:
                            _emit(c, project_id=project_id,
                                  conversation_id=conversation_id, turn_id=turn_id,
                                  node="synthesize", detail="OpenRouter transport lifecycle",
                                  event_type="synthesis_completed", metadata=lifecycle_meta)
                    except Exception:
                        # Diagnostics must never trigger or repeat a provider request.
                        pass

            def _transport_kwargs(method):
                if not openrouter_invocation:
                    return {}
                try:
                    import inspect
                    params = inspect.signature(method).parameters.values()
                    accepts = any(p.name == "on_transport_event" or
                                  p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
                except (TypeError, ValueError):
                    accepts = False
                return {"on_transport_event": _on_transport_event} if accepts else {}

            invocation_started_at = _time.perf_counter()
            seq = 0
            accumulated = []

            def _on_token(delta: str = "", sequence: int = 0, call_id: str = ""):
                nonlocal seq
                text = delta
                if not text:
                    return
                seq += 1
                curr_seq = sequence or seq
                act_cid = call_id or cid
                accumulated.append(text)
                cum_text = "".join(accumulated)
                try:
                    with deps.get_db() as c:
                        # 1. Emit explicit model_delta with frozen contract
                        _emit(c, project_id=project_id, conversation_id=conversation_id, turn_id=turn_id,
                              node="synthesize", detail=text[:6000],
                              event_type="model_delta",
                              metadata={"turn_id": turn_id, "call_id": act_cid,
                                        "sequence": curr_seq, "delta": text, "route": route})
                        # 2. Also emit legacy assistant_delta for backward compatibility
                        _emit(c, project_id=project_id, conversation_id=conversation_id, turn_id=turn_id,
                              node="synthesize", detail=cum_text[:6000],
                              event_type="assistant_delta",
                              metadata={"cumulative": True, "route": route})
                except Exception:
                    pass

            try:
                if hasattr(router, "complete_streaming"):
                    streaming_method = router.complete_streaming
                    stream_kwargs = dict(
                        turn_id=turn_id, project_id=project_id,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_content}],
                        tools=[], mode=mode, adapter=adapter_to_pass,
                        model_override=model_override or None,
                        behavior_profile=profile_to_pass, quantization=quant,
                        call_id=cid, on_token=_on_token,
                    )
                    try:
                        params = inspect.signature(streaming_method).parameters.values()
                        accepts_override = any(p.name == "model_override" or
                                               p.kind == inspect.Parameter.VAR_KEYWORD
                                               for p in params)
                    except (TypeError, ValueError):
                        accepts_override = False
                    if model_override and not accepts_override:
                        raise RuntimeError("The active router cannot use the selected model.")
                    if not accepts_override:
                        stream_kwargs.pop("model_override", None)
                    stream_kwargs.update(_transport_kwargs(streaming_method))
                    resp, mcall = streaming_method(conn, **stream_kwargs)
                else:
                    complete_method = router.complete
                    complete_kwargs = dict(
                        turn_id=turn_id, project_id=project_id,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_content}],
                        tools=[], mode=mode, adapter=adapter_to_pass,
                        model_override=model_override or None,
                        behavior_profile=profile_to_pass, quantization=quant,
                        call_id=cid,
                    )
                    try:
                        params = inspect.signature(complete_method).parameters.values()
                        accepts_override = any(p.name == "model_override" or
                                               p.kind == inspect.Parameter.VAR_KEYWORD
                                               for p in params)
                    except (TypeError, ValueError):
                        accepts_override = False
                    if model_override and not accepts_override:
                        raise RuntimeError("The active router cannot use the selected model.")
                    if not accepts_override:
                        complete_kwargs.pop("model_override", None)
                    complete_kwargs.update(_transport_kwargs(complete_method))
                    resp, mcall = complete_method(conn, **complete_kwargs)
                    if resp and resp.text:
                        _on_token(delta=resp.text, sequence=1, call_id=cid)
            except Exception as exc:
                failure = {
                    "call_id": cid,
                    "invocation_started": getattr(exc, "invocation_started", None),
                    "provider": getattr(exc, "provider", None),
                    "requested_model": getattr(exc, "model", None),
                    "route_mode": getattr(exc, "route_mode", mode),
                    "route_reason": getattr(exc, "route_reason", None),
                    "error_type": type(exc).__name__,
                    "attempt_latency_ms": max(0, int((_time.perf_counter() - invocation_started_at) * 1000)),
                    "generation_source": "none",
                    "generation_status": "failed",
                }
                if openrouter_invocation:
                    transport = _openrouter_transport_telemetry(
                        transport_events, call_id=cid, turn_id=turn_id,
                        requested_model=requested_transport_model,
                        app_invocation_count=1)
                    failure["transport_telemetry"] = transport
                    failure.update({key: transport[key] for key in (
                        "app_invocation_count", "http_attempt_count", "http_response_count",
                        "network_attempted", "network_phase", "failure_code",
                        "failure_reason", "http_status") if key in transport})
                    failure["provider"] = "openrouter"
                    failure["requested_model"] = requested_transport_model
                # Only provider supplied, bounded classification fields are
                # allowed across the persistence/SSE boundary. Never persist
                # exception text, headers, or a traceback.
                for field in ("failure_code", "http_status", "failure_reason"):
                    if field in failure:
                        continue
                    value = getattr(exc, field, None)
                    if field == "failure_code" and value not in {
                            "authentication", "credit_limit", "rate_limit",
                            "request_configuration", "http_error", "timeout",
                            "parse_error", "connection", "request_failure"}:
                        continue
                    if field == "http_status" and not (
                            isinstance(value, int) and 100 <= value <= 599):
                        continue
                    if field == "failure_reason" and value not in {
                            "The provider rejected the credential.",
                            "The provider reported a credit or account limit.",
                            "The provider rate limit was reached.",
                            "The provider rejected the model or request configuration.",
                            "The provider returned an HTTP error.",
                            "The provider request timed out.",
                            "The provider returned an invalid response.",
                            "The provider could not be reached.",
                            "The provider request failed."}:
                        continue
                    if isinstance(value, (str, int)) and str(value).strip():
                        failure[field] = value
                try:
                    with deps.get_db() as c:
                        _emit(c, project_id=project_id, conversation_id=conversation_id,
                              turn_id=turn_id, node="synthesize",
                              detail="Provider generation failed",
                              event_type="synthesis_completed", metadata=failure)
                except Exception:
                    pass
                raise

            # Emit final model_completed event with authoritative telemetry once available
            final_text = resp.text if resp else "".join(accumulated)
            telemetry = _generation_telemetry(resp, mcall)
            telemetry["attempt_latency_ms"] = max(
                0, int((_time.perf_counter() - invocation_started_at) * 1000))
            transport = None
            if openrouter_invocation:
                transport = _openrouter_transport_telemetry(
                    transport_events, call_id=cid, turn_id=turn_id,
                    requested_model=requested_transport_model,
                    app_invocation_count=1)
                telemetry["transport_telemetry"] = transport
            try:
                with deps.get_db() as c:
                    success_meta = {
                        "turn_id": turn_id,
                        "call_id": mcall.call_id if mcall else cid,
                        "text": final_text,
                        "generation_source": "provider",
                        "generation_status": "completed",
                        "telemetry": telemetry,
                        "retrieval_telemetry": _safe_retrieval_telemetry(
                            context.get("retrieval_telemetry")
                            if isinstance(context, dict) else {}),
                        "route": route,
                    }
                    if transport is not None:
                        success_meta["transport_telemetry"] = transport
                        success_meta.update({key: transport[key] for key in (
                            "app_invocation_count", "http_attempt_count", "http_response_count",
                            "network_attempted", "network_phase", "http_status")
                            if key in transport})
                    _emit(c, project_id=project_id, conversation_id=conversation_id, turn_id=turn_id,
                          node="synthesize", detail=final_text[:6000],
                          event_type="model_completed",
                          metadata=success_meta)
            except Exception:
                pass

            raw = resp.text if resp else None
            return enforce_numeric_guardrail(raw) if raw else None

    return _complete


def _get_graph(model_router=None, complete_fn=None):
    """Module-singleton compiled Account Manager graph (shared checkpointer).

    Shares one InMemorySaver so an execute() interrupt and a later resume()
    Command on the same thread_id meet the same checkpoint in-process.
    """
    global _GRAPH
    if _GRAPH is not None and model_router is None and complete_fn is None:
        return _GRAPH
    try:
        from app.graphs.account_manager_graph import build_account_manager_graph
    except ImportError:
        raise RuntimeError("Graph runtime is unavailable") from None
    try:
        fn = complete_fn
        if fn is None:
            router = model_router or _get_model_router()
            fn = _make_complete_fn(router)
        compiled = build_account_manager_graph(complete_fn=fn)
        if model_router is None and complete_fn is None:
            _GRAPH = compiled
        return compiled
    except Exception:
        raise RuntimeError("Graph runtime is unavailable") from None


def _emit(conn, *, project_id: str, conversation_id: str, turn_id: str,
          node: str, detail: str = "", metadata: dict | None = None,
          event_type: str = "") -> int:
    """Persist one frozen wire event via W1 mapping (or explicit legacy type)."""
    if event_type:
        evt = GraphExecutionEvent(
            project_id=project_id, conversation_id=conversation_id,
            turn_id=turn_id, event_type=event_type,
            label=detail[:300] if detail else "", detail=detail[:6000],
            metadata=dict(metadata or {}),
        )
    else:
        evt = build_event(node, project_id=project_id, turn_id=turn_id,
                          conversation_id=conversation_id,
                          detail=detail, metadata=metadata or {})
    return repos.ExecutionEvents.insert(conn, evt.to_row())


def _events_for(conn, turn_id: str) -> list:
    try:
        return repos.ExecutionEvents.for_turn(conn, turn_id)
    except Exception:
        return []


def _meta_of(row: dict) -> dict:
    return project_event_boundary(row)["meta"]


def _find_replay(conn, turn_id: str, key: str) -> dict | None:
    key = (key or "").strip()
    if not key:
        return None
    for row in _events_for(conn, turn_id):
        if _meta_of(row).get("idempotency_key") == key:
            return row
    return None


def _final_answer_for(conn, turn_id: str) -> str:
    answer = ""
    for row in _events_for(conn, turn_id):
        if row.get("event_type") in ("assistant_completed", "turn_completed"):
            if row.get("detail"):
                answer = project_event_boundary(row)["detail"]
    return answer


def _citations_for_turn(conn, turn_id: str) -> list[dict]:
    """Read the assistant citation projection following this turn's user row."""
    turn = repos.Turns.get(conn, turn_id)
    if turn is None:
        return []
    convo_id = str(turn.get("conversation_id") or "")
    user = repos.Messages.by_client_id(
        conn, convo_id, str(turn.get("client_message_id") or ""))
    if user is None:
        return []
    rows = repos.Messages.for_conversation(conn, convo_id)
    try:
        index = next(i for i, row in enumerate(rows) if row.get("id") == user.get("id"))
    except StopIteration:
        return []
    for row in rows[index + 1:]:
        if row.get("role") == "user":
            break
        if row.get("role") != "assistant":
            continue
        try:
            value = _json.loads(row.get("citations_json") or "[]")
        except (TypeError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        return [sanitize_metadata(item) for item in value
                if isinstance(item, dict)]
    return []


def _route_for(conn, turn_id: str) -> str:
    for row in reversed(_events_for(conn, turn_id)):
        route = _meta_of(row).get("route")
        if route:
            return sanitize_user_text(route, 300)
    return ""


def _visibility(conn, turn_id: str, project_id: str,
                fallback_route: str = "") -> dict:
    """Model-visibility snapshot. Unknown provider legs stay unknown."""
    turn_events = _events_for(conn, turn_id)
    calls: list = []
    try:
        calls = repos.ModelCalls.for_turn(conn, turn_id, project_id)
    except Exception:
        calls = []
    if calls:
        last = calls[-1]
        provider = last.get("provider", "")
        model = last.get("model", "")
        adapter = last.get("adapter", "")
        quant = last.get("quantization", "")
        reason = last.get("route_reason", "") or fallback_route
        mode = last.get("route_mode", "AUTO")
    else:
        provider, model, adapter, quant, mode = ("", "", "", "", "AUTO")
        reason = fallback_route or ""
    attempt = _generation_attempt_projection(turn_events)
    # A failed provider call has no ModelCalls row. Preserve the attempted
    # route identity from the event while keeping actual model unknown.
    if not calls and attempt:
        provider = attempt.get("provider") or provider
        mode = attempt.get("route_mode") or mode
        reason = attempt.get("route_reason") or reason
    try:
        totals = repos.ModelCalls.turn_totals(conn, turn_id, project_id)
    except Exception:
        totals = {"input_tokens": None, "output_tokens": None,
                  "total_tokens": None}
    return {
        "provider": sanitize_user_text(provider, 120),
        "model": sanitize_user_text(model, 200),
        "adapter": sanitize_user_text(adapter, 120),
        "quantization": sanitize_user_text(quant, 120),
        "route_reason": sanitize_user_text(reason, 300),
        "route_mode": sanitize_user_text(mode, 80),
        "generation_source": next((str(_meta_of(row).get("generation_source"))
                                    for row in reversed(turn_events)
                                    if _meta_of(row).get("generation_source")), "unknown"),
        "generation_status": next((str(_meta_of(row).get("generation_status"))
                                    for row in reversed(turn_events)
                                    if _meta_of(row).get("generation_status")), "unknown"),
        "generation_attempt": attempt,
        "retrieval_telemetry": _retrieval_telemetry_projection(turn_events),
        "calls": [sanitize_metadata(call) for call in calls],
        "totals": totals,
    }


def _generation_attempt_projection(events: list[dict]) -> dict:
    """Merge persisted attempt identity without treating requested as actual."""
    result: dict = {}
    lifecycle_events: list[dict] = []
    latest_transport: dict = {}
    for row in events:
        meta = _meta_of(row)
        telemetry = meta.get("telemetry")
        if not isinstance(telemetry, dict):
            telemetry = {}
        if meta.get("transport_lifecycle") is True:
            lifecycle = _safe_openrouter_transport_event(
                meta, call_id=str(meta.get("call_id") or ""),
                turn_id=str(meta.get("turn_id") or ""),
                requested_model=str(meta.get("requested_model") or ""))
            if lifecycle:
                lifecycle_events.append(lifecycle)
        for source, target in (
            ("generation_source", "generation_source"),
            ("generation_status", "generation_status"),
            ("call_id", "call_id"),
            ("provider", "provider"),
            ("requested_model", "requested_model"),
            ("route_mode", "route_mode"),
            ("route_reason", "route_reason"),
            ("invocation_started", "invocation_started"),
            ("error_type", "error_type"),
            ("failure_code", "failure_code"),
            ("http_status", "http_status"),
            ("failure_reason", "failure_reason"),
            ("attempt_latency_ms", "attempt_latency_ms"),
        ):
            if meta.get(source) not in (None, ""):
                result[target] = meta[source]
        if telemetry:
            result.setdefault("call_id", telemetry.get("call_id") or meta.get("call_id"))
            result.setdefault("provider", telemetry.get("provider"))
            result.setdefault("actual_model", telemetry.get("model"))
            result.setdefault("route_mode", telemetry.get("route_mode"))
            result.setdefault("route_reason", telemetry.get("route_reason"))
        transport = meta.get("transport_telemetry") or telemetry.get("transport_telemetry")
        if isinstance(transport, dict):
            latest_transport = transport
            for key in ("app_invocation_count", "http_attempt_count",
                        "http_response_count", "network_attempted", "http_status",
                        "network_phase", "failure_code", "failure_reason"):
                if key in transport:
                    result[key] = transport[key]
            result["transport_attempts"] = transport.get("attempts", [])
            result["transport_telemetry"] = transport
            result.setdefault("call_id", transport.get("call_id"))
            result.setdefault("provider", transport.get("provider"))
            result.setdefault("requested_model", transport.get("requested_model"))
    if lifecycle_events:
        call_id = str(lifecycle_events[-1].get("call_id") or result.get("call_id") or "")
        turn_id = str(lifecycle_events[-1].get("turn_id") or "")
        requested_model = str(lifecycle_events[-1].get("requested_model") or
                              result.get("requested_model") or "")
        aggregate = _openrouter_transport_telemetry(
            lifecycle_events, call_id=call_id, turn_id=turn_id,
            requested_model=requested_model,
            app_invocation_count=int(latest_transport.get("app_invocation_count", 1)))
        result.update({key: value for key, value in aggregate.items()
                       if key in {"app_invocation_count", "http_attempt_count",
                                  "http_response_count", "network_attempted", "http_status",
                                  "network_phase", "failure_code", "failure_reason",
                                  "actual_model", "usage", "provider_request_id",
                                  "provider_error_code", "provider_error_type",
                                  "provider_safe_error_message", "attempts"}})
        result["transport_telemetry"] = aggregate
    return sanitize_metadata(result)


_TRANSPORT_STATES = frozenset({
    "provider_request_prepared", "provider_request_started",
    "provider_response_headers", "provider_response_completed",
    "provider_request_failed",
})
_TRANSPORT_PHASES = frozenset({
    "credential", "vault", "credential_vault", "client_initialization",
    "serialization", "request_serialization", "transport", "transport_dispatch",
    "dns", "connection_refused", "connection", "tls", "proxy", "timeout",
    "http_response", "response_parse", "other",
})
_TRANSPORT_REASONS = frozenset({
    "credential_failure", "vault_failure", "credential_unavailable",
    "client_initialization_failure", "client_initialization_error", "client_unavailable",
    "request_serialization_failure", "serialization_error", "dns_failure", "dns_error",
    "connection_refused", "connection_error", "tls_failure", "tls_error",
    "proxy_failure", "proxy_error", "timeout", "request_timeout", "http_401",
    "http_402", "http_403", "http_404", "http_408", "http_429", "http_5xx",
    "http_error", "response_parse_failure", "other", "transport_error",
})


def _safe_openrouter_transport_event(event: dict, *, call_id: str,
                                      turn_id: str, requested_model: str) -> dict:
    """Rebuild lifecycle telemetry from an allowlist; never trust adapter text."""
    import re
    if not isinstance(event, dict) or event.get("state") not in _TRANSPORT_STATES:
        return {}
    model = (requested_model if isinstance(requested_model, str) else "")
    if not (1 <= len(model) <= 160 and _re.fullmatch(r"[A-Za-z0-9_./:-]+", model)):
        model = "unknown"
    safe = {
        "state": event["state"], "call_id": call_id, "turn_id": turn_id,
        "provider": "openrouter", "requested_model": model,
    }
    for key, limit in (("endpoint_host", 253), ("endpoint_path", 256),
                       ("http_method", 8), ("content_type", 96)):
        value = event.get(key)
        if isinstance(value, str) and value and len(value) <= limit \
                and re.fullmatch(r"[A-Za-z0-9._:/+;=-]+", value):
            safe[key] = value
    for key in ("attempt_index", "elapsed_ms", "http_status"):
        value = event.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            safe[key] = value
    for key in ("provider_request_id",):
        value = event.get(key)
        if isinstance(value, str) and len(value) <= 128 \
                and re.fullmatch(r"[A-Za-z0-9._:-]+", value):
            safe[key] = value
    for key in ("exception_class",):
        value = event.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", value):
            safe[key] = value
    phase = event.get("network_phase")
    if phase in _TRANSPORT_PHASES:
        safe["network_phase"] = phase
    reason = event.get("reason")
    if reason in _TRANSPORT_REASONS:
        safe["reason"] = reason
    checkpoints = event.get("checkpoints")
    if not isinstance(checkpoints, dict):
        checkpoints = {
            key: event[key] for key in (
                "credential_reference_resolved", "credential_decrypted",
                "client_initialized", "request_serialized", "transport_started")
            if key in event
        }
    if isinstance(checkpoints, dict):
        allowed_checkpoints = {
            "credential_reference_resolved", "credential_decrypted",
            "client_initialized", "request_serialized", "transport_started",
        }
        safe["checkpoints"] = {
            key: value for key, value in checkpoints.items()
            if key in allowed_checkpoints and isinstance(value, (bool, str))
            and (isinstance(value, bool) or value in {"complete", "failed", "not_reached"})
        }
    usage = event.get("usage")
    if isinstance(usage, dict):
        safe["usage"] = {
            key: value for key, value in usage.items()
            if key in {"input", "output", "cached", "reasoning", "total_tokens", "cost_usd"}
            and isinstance(value, (int, float)) and not isinstance(value, bool)
            and value >= 0
        }
    for key in ("provider_error_code", "provider_error_type"):
        value = event.get(key)
        if isinstance(value, str) and len(value) <= 64 \
                and re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            safe[key] = value
    actual_model = event.get("actual_model")
    if isinstance(actual_model, str) and len(actual_model) <= 160 \
            and re.fullmatch(r"[A-Za-z0-9_./:-]+", actual_model):
        safe["actual_model"] = actual_model
    return safe


def _openrouter_transport_telemetry(events: list[dict], *, call_id: str,
                                     turn_id: str, requested_model: str,
                                     app_invocation_count: int) -> dict:
    safe_events = [
        item for item in (
            _safe_openrouter_transport_event(
                event, call_id=call_id, turn_id=turn_id,
                requested_model=requested_model)
            for event in events[:32]
        ) if item
    ]
    http_attempt_count = sum(
        event["state"] == "provider_request_started" for event in safe_events)
    http_response_count = sum(
        event["state"] == "provider_response_headers" for event in safe_events)
    statuses = [event["http_status"] for event in safe_events
                if isinstance(event.get("http_status"), int)]
    completed = next((event for event in reversed(safe_events)
                      if event["state"] == "provider_response_completed"), {})
    failed = (safe_events[-1] if safe_events and
              safe_events[-1]["state"] == "provider_request_failed" else {})
    attempts: dict[int, dict] = {}
    for event in safe_events:
        index = event.get("attempt_index")
        if not isinstance(index, int) or index <= 0:
            continue
        summary = attempts.setdefault(index, {"attempt_index": index})
        if event.get("http_status") is not None:
            summary["http_status"] = event["http_status"]
        for key in ("network_phase", "reason", "exception_class"):
            if event.get(key) is not None:
                summary[key] = event[key]
    checkpoints = {}
    for event in safe_events:
        if isinstance(event.get("checkpoints"), dict):
            checkpoints.update(event["checkpoints"])
    result = {
        "call_id": call_id, "turn_id": turn_id, "provider": "openrouter",
        "requested_model": requested_model,
        "app_invocation_count": max(0, int(app_invocation_count)),
        "http_attempt_count": http_attempt_count,
        "http_response_count": http_response_count,
        "network_attempted": http_attempt_count > 0,
        "attempts": list(attempts.values()),
        "checkpoints": checkpoints,
    }
    if statuses:
        result["http_status"] = statuses[-1]
    if failed.get("network_phase"):
        phase_map = {
            "credential_vault": "vault", "request_serialization": "serialization",
            "transport_dispatch": "transport",
        }
        result["network_phase"] = phase_map.get(failed["network_phase"], failed["network_phase"])
    elif http_response_count:
        result["network_phase"] = "http_response"
    if failed.get("reason"):
        status = failed.get("http_status")
        code = ("response_parse_failure" if result.get("network_phase") == "response_parse"
                else f"http_{status}" if status in {401, 402, 403, 404, 408, 429}
                else "http_5xx" if isinstance(status, int) and 500 <= status <= 599
                else failed["reason"])
        if code in _TRANSPORT_REASONS:
            result["failure_code"] = code
            result["failure_reason"] = code
        if isinstance(status, int):
            result["provider_safe_error_message"] = f"OpenRouter returned HTTP {status}."
    if failed.get("provider_error_code"):
        result["provider_error_code"] = failed["provider_error_code"]
    if failed.get("provider_error_type"):
        result["provider_error_type"] = failed["provider_error_type"]
    if completed.get("actual_model"):
        result["actual_model"] = completed["actual_model"]
    if completed.get("usage"):
        result["usage"] = completed["usage"]
    if completed.get("provider_request_id"):
        result["provider_request_id"] = completed["provider_request_id"]
    if completed.get("provider_error_code"):
        result["provider_error_code"] = completed["provider_error_code"]
    if completed.get("provider_error_type"):
        result["provider_error_type"] = completed["provider_error_type"]
    return result


def _retrieval_telemetry_projection(events: list[dict]) -> dict:
    """Return the latest exact-turn, content-free retrieval snapshot."""
    for row in reversed(events):
        meta = _meta_of(row)
        telemetry = meta.get("retrieval_telemetry")
        if isinstance(telemetry, dict):
            return _safe_retrieval_telemetry(telemetry)
    return {}


def _safe_retrieval_telemetry(value: dict | None) -> dict:
    """Allowlist counts and source identities; never carry retrieved text."""
    if not isinstance(value, dict):
        return {}
    from app.contracts.events import compact_retrieval_telemetry
    result = compact_retrieval_telemetry(value)
    # Keep the previous wire aliases for clients that consume the earlier
    # telemetry shape; all aliases derive from the same known values.
    mode = result.get("retrieval_mode")
    if mode is not None:
        result.setdefault("mode", mode)
        result.setdefault("search_mode", mode)
    if "selected_chunk_ids" in result or "source_file_ids" in result:
        chunks = result.get("selected_chunk_ids", [])
        files = result.get("source_file_ids", [])
        result["selected_chunks"] = [
            {**({"chunk_id": chunks[i]} if i < len(chunks) else {}),
             **({"file_id": files[i]} if i < len(files) else {})}
            for i in range(max(len(chunks), len(files)))
        ]
    return result


def _citations_for_result(result: dict, *, project_id: str,
                          attachment_evidence=None) -> list:
    """Keep the exact evidence snapshot even when answer generation failed."""
    citations = result.get("citation_evidence")
    if isinstance(citations, list) and citations:
        return citations
    from app.graphs.state import citation_projection
    return citation_projection(
        project_id=project_id,
        rag_hits=(result.get("rag_hits") or result.get("retrieved_evidence")),
        attachment_evidence=attachment_evidence)


def _visibility_headers(vis: dict) -> dict:
    return {
        "X-Model-Provider": str(vis.get("provider", "")),
        "X-Model-Name": str(vis.get("model", "")),
        "X-Model-Adapter": str(vis.get("adapter", "")),
        "X-Model-Quantization": str(vis.get("quantization", "")),
        "X-Route-Reason": str(vis.get("route_reason", "")),
        "X-Route-Mode": str(vis.get("route_mode", "")),
    }


def _approval_flow_passthrough(*, approval_id: str, project_id: str,
                               approved: bool, key: str) -> bool:
    """Real Command-resume round-trip on W1 approval_flow (isolated thread).

    Proves the interrupt()/Command(resume=...) primitive the resume endpoint
    relies on. Best-effort: never fails the caller; returns executed flag.
    """
    try:
        from langgraph.types import Command

        from app.graphs.approval_flow import build_approval_graph

        flow = build_approval_graph()
        cfg = {"configurable": {"thread_id": f"w6-{approval_id}"}}
        paused = flow.invoke(
            {"approval_id": approval_id, "project_id": project_id,
             "title": "Resume verification", "action_class": "yellow",
             "idempotency_key": key},
            config=cfg,
        )
        if not isinstance(paused, dict) or "__interrupt__" not in paused:
            return False
        resumed = flow.invoke(Command(resume={"approved": approved}), config=cfg)
        return bool((resumed or {}).get("executed"))
    except Exception:
        return False


# ---------- request models (permissive strings; fail-closed in handlers) ----------

class ThreadCreate(BaseModel):
    project_id: str = ""
    conversation_id: str = ""
    text: str = ""
    client_message_id: str = ""
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)


class ResumeRequest(BaseModel):
    approved: bool = False
    decided_by: str = ""
    idempotency_key: str = ""
    approval_id: str = ""


# ---------- threads ----------

@router.post("/threads", status_code=201)
def create_thread(body: ThreadCreate):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="message text is required")
    with deps.get_db() as conn:
        proj = _require_project(conn, body.project_id)
        pid = proj["id"]
        cid = (body.conversation_id or "").strip()
        convo = repos.Conversations.get(conn, cid) if cid else None
        if convo is None:
            raise HTTPException(status_code=404, detail="unknown conversation")
        if (convo.get("project_id") or "") != pid:
            raise _no_scope("conversation belongs to another project")
        from app.services import turns as turnsvc
        try:
            made = turnsvc.create_turn(
                conn, str(deps.DB_PATH), conversation_id=cid,
                project_id=pid, text=text,
                client_message_id=(body.client_message_id or "").strip(),
                attachment_ids=body.attachment_ids)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=safe_error_message(e, "The request could not be prepared."),
            )
        turn = made["turn"]
        _emit(conn, project_id=pid, conversation_id=cid, turn_id=turn["id"],
              node="load_conversation", detail="Project context loaded",
              metadata={"client_message_id": turn.get("client_message_id", "")})
    return _ok({"thread_id": turn["id"], "turn_id": turn["id"],
                "conversation_id": cid, "project_id": pid,
                "created": made["created"]}, status_code=201)


@router.post("/threads/{thread_id}/execute")
def execute_thread(thread_id: str, request: Request,
                   idempotency_key: str = Header(default="", alias="Idempotency-Key")):
    tid = (thread_id or "").strip()
    if not tid:
        raise HTTPException(status_code=404, detail="unknown thread")
    key = ((idempotency_key or "") or
           (request.headers.get("x-idempotency-key", "") or "")).strip()
    with deps.get_db() as conn:
        turn = repos.Turns.get(conn, tid)
        if turn is None:
            raise HTTPException(status_code=404, detail="unknown thread")
        pid = (turn.get("project_id") or "").strip()
        cid = (turn.get("conversation_id") or "").strip()
        _require_project(conn, pid)  # BEFORE any graph invoke
        if key:
            replay = _find_replay(conn, tid, key)
            if replay is not None:
                vis = _visibility(conn, tid, pid, _route_for(conn, tid))
                return _ok(
                    {"thread_id": tid, "turn_id": tid, "status": "completed",
                     "route": _route_for(conn, tid),
                     "final_answer": _final_answer_for(conn, tid),
                     "citations": _citations_for_turn(conn, tid),
                     "model_visibility": vis, "idempotency_key": key,
                     "replayed": True},
                    headers={**_visibility_headers(vis),
                             "X-Idempotent-Replay": "true"})
        try:
            graph = _get_graph()
        except RuntimeError:
            raise HTTPException(status_code=503, detail="Graph runtime is unavailable")
        from app.services import turns as turnsvc
        user_msg = repos.Messages.by_client_id(
            conn, cid, turn.get("client_message_id", ""))
        text = ((user_msg or {}).get("body_md", "") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="nothing to execute")
        from app.graphs.state import initial_state
        from app.services.files import repo as files_repo
        from app.services.files.attachments import assemble_attachment_evidence
        selected_ids = files_repo.selected_for_turn(conn, turn_id=tid, project_id=pid,
                                                   conversation_id=cid)
        try:
            attachment_evidence = assemble_attachment_evidence(
                conn, root=deps.ROOT, project_id=pid, conversation_id=cid,
                file_ids=selected_ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        state = initial_state(project_id=pid, conversation_id=cid,
                              turn_id=tid, user_request=text,
                              model_provider=str(turn.get("model_provider") or "AUTO"),
                              model_id=str(turn.get("model_id") or ""))
        state["attachment_evidence"] = attachment_evidence
        try:
            from app.services.skills.tree import TreeEmitter
            _tree = TreeEmitter(
                db_path=str(deps.DB_PATH), project_id=pid,
                conversation_id=cid, turn_id=tid, thread_id=tid)
        except Exception:
            _tree = None
        _invoke_config: dict = {"thread_id": tid}
        if _tree is not None:
            # Same tree surface as turns._run_graph_turn: a route that streams
            # the tree on one surface and not the other would be a lie the E2E
            # would expose (plan §3.4 W4).
            _invoke_config["tree"] = _tree
        try:
            result = graph.invoke(state, config={"configurable": _invoke_config})
        except Exception as exc:
            msg = safe_error_message(exc, "")
            if "NO_PROJECT_SCOPE" in msg:
                raise _no_scope("The project scope is unavailable")
            _emit(conn, project_id=pid, conversation_id=cid, turn_id=tid,
                  node="respond_failed", detail="Something interrupted this run.",
                  metadata={"idempotency_key": key} if key else {})
            repos.Turns.set_status(
                conn, tid, "failed",
                {"error": "The request could not be completed."})
            raise HTTPException(status_code=500, detail="graph execution failed")
        result = result or {}
        raw_errors = result.get("errors", []) or []
        if isinstance(raw_errors, (str, bytes)):
            raw_errors = [raw_errors]
        graph_errors = [sanitize_user_text(item, 300) for item in raw_errors]
        if any("NO_PROJECT_SCOPE" in item for item in graph_errors):
            raise _no_scope("unknown project")
        final = sanitize_user_text(result.get("final_answer", "") or "", 1_000_000)
        if graph_errors:
            final = "The request could not be completed."
        # Keep the retained evidence snapshot even if generation failed;
        # post-answer re-retrieval could create citations synthesis never saw.
        citations = _citations_for_result(
            result, project_id=pid, attachment_evidence=attachment_evidence)
        route = sanitize_user_text(
            result.get("route", "") or result.get("model_route", "") or "state_only",
            300,
        )
        usage_meta = result.get("model_usage") if isinstance(result.get("model_usage"), dict) else {}
        meta = {
            "route": route,
            "generation_source": str(usage_meta.get("generation_source") or "unknown"),
            "generation_status": str(usage_meta.get("generation_status") or "unknown"),
        }
        if key:
            meta["idempotency_key"] = key
        generation_meta = {
            "generation_source": meta["generation_source"],
            "generation_status": meta["generation_status"],
            "route": route,
        }
        for field in ("call_id", "provider", "requested_model", "route_mode",
                      "route_reason", "invocation_started", "error_type",
                      "failure_code", "http_status", "failure_reason",
                      "attempt_latency_ms"):
            if usage_meta.get(field) not in (None, ""):
                generation_meta[field] = usage_meta[field]
        previous_attempt = _generation_attempt_projection(_events_for(conn, tid))
        if previous_attempt.get("provider") == "openrouter":
            for field in ("app_invocation_count", "http_attempt_count",
                          "http_response_count", "network_attempted", "http_status",
                          "network_phase", "failure_code", "failure_reason",
                          "transport_telemetry"):
                if field in previous_attempt:
                    generation_meta[field] = previous_attempt[field]
        retrieval_telemetry = result.get("retrieval_telemetry")
        if isinstance(retrieval_telemetry, dict):
            generation_meta["retrieval_telemetry"] = _safe_retrieval_telemetry(
                retrieval_telemetry)
        _emit(conn, project_id=pid, conversation_id=cid, turn_id=tid,
              node="synthesize", detail="Generation provenance",
              event_type="synthesis_completed", metadata=generation_meta)
        if "__interrupt__" in result:
            try:
                payload = result["__interrupt__"][0].value
            except Exception:
                payload = {}
            aid = str((payload or {}).get("approval_id", "") or f"turn-{tid}")
            akey = str((payload or {}).get("idempotency_key", "")
                       or stable_key(aid))
            _emit(conn, project_id=pid, conversation_id=cid, turn_id=tid,
                  node="approval_operation",
                  detail=sanitize_user_text(
                      (payload or {}).get("title", "") or final, 300),
                  metadata={**meta, "approval_id": aid,
                            "action_class": (payload or {}).get("action_class", "yellow"),
                            "idempotency_key": akey})
            vis = _visibility(conn, tid, pid, route)
            return _ok(
                {"thread_id": tid, "turn_id": tid, "status": "interrupted",
                 "route": route, "approval_id": aid, "idempotency_key": akey,
                 "final_answer": final,
                 "model_visibility": vis},
                headers=_visibility_headers(vis),
            )

        branch_node = route if route in NODE_EVENT_MAP else "state_only"
        existing_nodes = {r.get("event_type") for r in _events_for(conn, tid)}
        if "state_read" not in existing_nodes:
            _emit(conn, project_id=pid, conversation_id=cid, turn_id=tid,
                  node="load_project", metadata=dict(meta))
        if "tool_completed" not in existing_nodes:
            _emit(conn, project_id=pid, conversation_id=cid, turn_id=tid,
                  node="understand", metadata=dict(meta))
        _emit(conn, project_id=pid, conversation_id=cid, turn_id=tid,
              node=branch_node, detail=final[:300], metadata=dict(meta))
        _emit(conn, project_id=pid, conversation_id=cid, turn_id=tid,
              node="aggregate", metadata=dict(meta))
        if "synthesis_started" not in existing_nodes:
            _emit(conn, project_id=pid, conversation_id=cid, turn_id=tid,
                  node="synthesize", detail=final[:6000], metadata=dict(meta))
        asst = {"id": turnsvc._new_id(), "conversation_id": cid,
                "turn_id": tid,
                "role": "assistant", "body_md": final,
                "citations_json": _json.dumps(citations, ensure_ascii=False),
                "client_message_id": "",
                "created_at": _now()}
        repos.Messages.insert(conn, asst)
        _emit(conn, project_id=pid, conversation_id=cid, turn_id=tid,
              node="respond", detail=final[:6000], metadata=dict(meta))
        repos.Turns.set_status(conn, tid, "completed",
                               {"provider": "graph",
                                "updated_at": _now()})
        vis = _visibility(conn, tid, pid, route)
        return _ok({"thread_id": tid, "turn_id": tid, "status": "completed",
                    "route": route, "final_answer": final,
                    "citations": citations,
                    "model_visibility": vis,
                    "idempotency_key": key or stable_key(f"exec:{tid}")},
                   headers=_visibility_headers(vis))


@router.get("/threads/{thread_id}")
def thread_state(thread_id: str, project_id: str = Query(default="")):
    tid = (thread_id or "").strip()
    with deps.get_db() as conn:
        turn = repos.Turns.get(conn, tid) if tid else None
        if turn is None:
            raise HTTPException(status_code=404, detail="unknown thread")
        pid = (turn.get("project_id") or "").strip()
        if (project_id or "").strip() and project_id.strip() != pid:
            raise _no_scope("thread belongs to another project", status=403)
        _require_project(conn, pid)
        vis = _visibility(conn, tid, pid, _route_for(conn, tid))
        return _ok({"thread_id": tid, "turn_id": tid,
                    "conversation_id": turn.get("conversation_id", ""),
                    "project_id": pid, "status": turn.get("status", ""),
                    "route": _route_for(conn, tid),
                    "final_answer": _final_answer_for(conn, tid),
                    "citations": _citations_for_turn(conn, tid),
                    "model_visibility": vis},
                   headers=_visibility_headers(vis))


@router.post("/threads/{thread_id}/resume")
def resume_thread(thread_id: str, body: ResumeRequest, request: Request,
                  header_key: str = Header(default="", alias="Idempotency-Key")):
    tid = (thread_id or "").strip()
    key = ((body.idempotency_key or "").strip() or header_key.strip()
           or request.headers.get("x-idempotency-key", "").strip())
    if not tid:
        raise HTTPException(status_code=404, detail="unknown thread")
    if not key:
        raise HTTPException(status_code=422, detail="idempotency_key is required")
    if not (body.decided_by or "").strip():
        raise HTTPException(status_code=422, detail="decided_by is required")
    with deps.get_db() as conn:
        turn = repos.Turns.get(conn, tid)
        if turn is None:
            raise HTTPException(status_code=404, detail="unknown thread")
        pid = (turn.get("project_id") or "").strip()
        cid = (turn.get("conversation_id") or "").strip()
        _require_project(conn, pid)  # BEFORE any graph resume
        approval_row = None
        requested_aid = (body.approval_id or "").strip()
        if requested_aid and not requested_aid.startswith("turn-"):
            approval_row = repos.Approvals.get(conn, requested_aid, pid)
            if approval_row is None:
                raise HTTPException(status_code=404, detail="unknown approval")
            try:
                approval_fields = _json.loads(approval_row.get("fields_json") or "{}")
            except (TypeError, ValueError):
                approval_fields = {}
            workflow = approval_fields.get("workflow") if isinstance(approval_fields, dict) else {}
            workflow = workflow if isinstance(workflow, dict) else {}
            if (workflow.get("thread_id") != tid or workflow.get("turn_id") != tid
                    or workflow.get("conversation_id") != cid):
                raise HTTPException(status_code=403, detail="approval is not linked to this thread")
            if (approval_row.get("status") or "") != "pending":
                prior = _find_replay(conn, tid, key)
                if prior is not None:
                    pmeta = _meta_of(prior)
                    return _ok({"thread_id": tid, "turn_id": tid,
                                "approved": bool(pmeta.get("approved", body.approved)),
                                "executed": bool(pmeta.get("executed", False)),
                                "idempotency_key": key, "replayed": True})
                raise HTTPException(status_code=409, detail="approval is no longer pending")
        prior = _find_replay(conn, tid, key)
        if prior is not None:
            pmeta = _meta_of(prior)
            return _ok({"thread_id": tid, "turn_id": tid,
                        "approved": bool(pmeta.get("approved", body.approved)),
                        "executed": bool(pmeta.get("executed", False)),
                        "idempotency_key": key, "replayed": True})
        resume = ApprovalResume(
            approval_id=((body.approval_id or "").strip() or f"turn-{tid}"),
            project_id=pid,
            decision="approved" if body.approved else "rejected",
            decided_by=body.decided_by.strip(),
            idempotency_key=key,
        )
        try:
            graph = _get_graph()
        except RuntimeError:
            raise HTTPException(status_code=503, detail="Graph runtime is unavailable")
        from langgraph.types import Command

        from app.services import turns as turnsvc
        try:
            result = graph.invoke(
                Command(resume={"approved": resume.decision == "approved"}),
                config={"configurable": {"thread_id": tid}})
        except Exception:
            if approval_row is not None:
                raise HTTPException(status_code=409,
                                    detail="the linked approval workflow could not resume")
            # No pending interrupt in this saver (e.g. green fast-path):
            # record the decision without duplicating side effects.
            result = {"approval_state": {"approved": body.approved},
                      "final_answer": _final_answer_for(conn, tid),
                      "resume_note": "The graph resumed without a pending approval."}
        result = result or {}
        executed = bool(body.approved)
        if isinstance(result, dict) and "__interrupt__" in result:
            executed = False
        if approval_row is not None and not (isinstance(result, dict) and "__interrupt__" in result):
            try:
                from app.services import state as statesvc
                statesvc.decide_approval(
                    conn, resume.approval_id,
                    "approved" if body.approved else "rejected",
                    decided_by=resume.decided_by, project_id=pid)
            except (KeyError, ValueError) as exc:
                raise HTTPException(status_code=409,
                                    detail=safe_error_message(exc, "The approval could not be updated."))
        final = sanitize_user_text(
            result.get("final_answer", "") or _final_answer_for(conn, tid) or "",
            1_000_000,
        )
        if (executed or approval_row is not None) and final and final != _final_answer_for(conn, tid):
            try:
                repos.Messages.insert(conn, {
                    "id": turnsvc._new_id(), "conversation_id": cid,
                    "turn_id": tid,
                    "role": "assistant", "body_md": final,
                    "citations_json": "[]", "client_message_id": "",
                    "created_at": _now()})
            except Exception:
                pass
        _emit(conn, project_id=pid, conversation_id=cid, turn_id=tid,
              node="approval-interrupt",
              detail=("Approved — continuing" if body.approved
                      else "Rejected — holding"),
              metadata={"route": _route_for(conn, tid) or "approval_operation",
                        "approval_id": resume.approval_id,
                        "approved": body.approved, "executed": executed,
                        "idempotency_key": key,
                        "decided_by": resume.decided_by})
        if executed:
            _emit(conn, project_id=pid, conversation_id=cid, turn_id=tid,
                  node="respond", detail=final[:6000],
                  metadata={"route": _route_for(conn, tid) or "approval_operation",
                            "approved": True, "executed": True,
                            "idempotency_key": f"{key}:respond"})
            repos.Turns.set_status(conn, tid, "completed",
                                   {"provider": "graph", "updated_at": _now()})
        elif approval_row is not None:
            repos.Turns.set_status(conn, tid, "completed",
                                   {"provider": "graph", "updated_at": _now()})
        flow_executed = _approval_flow_passthrough(
            approval_id=resume.approval_id, project_id=pid,
            approved=body.approved, key=key)
        vis = _visibility(conn, tid, pid,
                          _route_for(conn, tid) or "approval_operation")
        return _ok({"thread_id": tid, "turn_id": tid,
                    "approved": body.approved, "executed": executed,
                    "approval_flow_executed": flow_executed,
                    "idempotency_key": key, "final_answer": final,
                    "model_visibility": vis},
                   headers=_visibility_headers(vis))


@router.get("/threads/{thread_id}/events")
def thread_events(thread_id: str, request: Request,
                  after: int = Query(default=0),
                  project_id: str = Query(default="")):
    tid = (thread_id or "").strip()
    last_hdr = request.headers.get("last-event-id", "")
    try:
        after = int(last_hdr or after)
    except (ValueError, TypeError):
        after = 0
    with deps.get_db() as conn:
        turn = repos.Turns.get(conn, tid) if tid else None
        if turn is None:
            raise HTTPException(status_code=404, detail="unknown thread")
        pid = (turn.get("project_id") or "").strip()
        if (project_id or "").strip() and project_id.strip() != pid:
            raise _no_scope("thread belongs to another project", status=403)
        _require_project(conn, pid)
        rows = [r for r in _events_for(conn, tid) if r["id"] > after]
        rows = [r for r in rows if (r.get("project_id") or "") == pid]
        vis = _visibility(conn, tid, pid, _route_for(conn, tid))

    def _gen():
        import time as _time
        yield ": connected\n\n"
        last_id = after
        deadline = _time.time() + 600
        while _time.time() < deadline:
            with deps.get_db() as conn2:
                cur_turn = repos.Turns.get(conn2, tid)
                cur_rows = [r for r in _events_for(conn2, tid) if r["id"] > last_id]
                cur_rows = [r for r in cur_rows if (r.get("project_id") or "") == pid]
            for row in cur_rows:
                last_id = max(last_id, row["id"])
                try:
                    evt = GraphExecutionEvent.from_row(row)
                    sse = evt.to_sse(id=row["id"])
                except Exception:
                    continue  # never emit non-frozen wire types
                yield f"id: {sse['id']}\nevent: {sse['event']}\n"
                yield f"data: {_json.dumps(sse['data'], ensure_ascii=False)}\n\n"
                if evt.is_terminal:
                    yield "event: stream_end\ndata: {}\n\n"
                    return
            status = (cur_turn or {}).get("status")
            if status in ("completed", "failed", "interrupted"):
                break
            _time.sleep(0.05)
        yield "event: stream_end\ndata: {}\n\n"

    return StreamingResponse(
        _gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 **_visibility_headers(vis)})


# ---------- I2 W6/W7 compat aliases (thin, same-file ownership) ----------
#
# W7's runtimeClient targets exactly these two JSON shapes; W6's canonical
# /api/graph/* surface did not include them, so the UI degraded to 404
# fallback. These aliases reuse the same helpers/repos (ModelCalls.for_turn
# project-scoped reads; legacy approvals decide + W1 approval_flow
# Command-resume passthrough). Mounted under the same AI_RUNTIME=langgraph
# gate in app/main.py. No new business semantics.

compat_router = APIRouter(tags=["graph-runtime-compat"])


class CompatResumeRequest(BaseModel):
    decision: str = ""
    decided_by: str = ""
    idempotency_key: str = ""


@compat_router.get("/turns/{turn_id}/model-calls")
def compat_turn_model_calls(turn_id: str,
                            project_id: str = Query(default="")):
    tid = (turn_id or "").strip()
    with deps.get_db() as conn:
        turn = repos.Turns.get(conn, tid) if tid else None
        if turn is None:
            raise HTTPException(status_code=404, detail="unknown turn")
        pid = (turn.get("project_id") or "").strip()
        if (project_id or "").strip() and project_id.strip() != pid:
            raise _no_scope("turn belongs to another project", status=403)
        _require_project(conn, pid)  # fail-closed BEFORE telemetry read
        try:
            rows = repos.ModelCalls.for_turn(conn, tid, pid)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=safe_error_message(e, "The telemetry is unavailable."),
            )
    return _ok([dict(r) for r in rows])


@compat_router.post("/approvals/{approval_id}/resume")
def compat_approval_resume(approval_id: str, body: CompatResumeRequest):
    aid = (approval_id or "").strip()
    if not aid:
        raise HTTPException(status_code=404, detail="unknown approval")
    decision = (body.decision or "").strip().lower()
    if decision not in ("approved", "rejected"):
        raise HTTPException(
            status_code=422,
            detail="decision must be 'approved' or 'rejected'")
    if not (body.decided_by or "").strip():
        raise HTTPException(status_code=422, detail="decided_by is required")
    key = (body.idempotency_key or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="idempotency_key is required")
    with deps.get_db() as conn:
        row = repos.Approvals.get(conn, aid)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown approval")
        pid = (row.get("project_id") or "").strip()
        _require_project(conn, pid)  # fail-closed BEFORE any decision write
        if (row.get("status") or "") != "pending":
            return _ok({"approval_id": aid, "status": row.get("status"),
                        "decided_by": row.get("decided_by", ""),
                        "idempotency_key": key, "replayed": True,
                        "executed": row.get("status") == "approved"})
        try:
            fields = _json.loads(row.get("fields_json") or "{}")
        except (TypeError, ValueError):
            fields = {}
        workflow = fields.get("workflow") if isinstance(fields, dict) else {}
        workflow = workflow if isinstance(workflow, dict) else {}
        workflow_thread = str(workflow.get("thread_id") or "").strip()
        if workflow_thread:
            tid = str(workflow.get("turn_id") or "").strip()
            turn = repos.Turns.get(conn, tid) if tid else None
            if (workflow_thread != tid or turn is None
                    or turn.get("project_id") != pid
                    or turn.get("conversation_id") != workflow.get("conversation_id")):
                raise HTTPException(status_code=403,
                                    detail="approval workflow provenance is invalid")
            try:
                graph = _get_graph()
                from langgraph.types import Command
                result = graph.invoke(
                    Command(resume={"approved": decision == "approved"}),
                    config={"configurable": {"thread_id": workflow_thread}}) or {}
            except Exception:
                raise HTTPException(status_code=409,
                                    detail="the linked approval workflow could not resume")
            if "__interrupt__" in result:
                raise HTTPException(status_code=409,
                                    detail="the linked workflow is still waiting for approval")
            from app.services import state as statesvc
            try:
                decided = statesvc.decide_approval(
                    conn, aid, decision, decided_by=body.decided_by.strip(),
                    project_id=pid)
            except (KeyError, ValueError) as e:
                raise HTTPException(status_code=409,
                                    detail=safe_error_message(e, "The approval could not be updated."))
            final = sanitize_user_text(result.get("final_answer", "") or "", 100000)
            if final:
                from app.services import turns as turnsvc
                repos.Messages.insert(conn, {
                    "id": turnsvc._new_id(),
                    "conversation_id": str(workflow.get("conversation_id") or ""),
                    "turn_id": tid,
                    "role": "assistant", "body_md": final,
                    "citations_json": "[]", "client_message_id": "",
                    "created_at": _now()})
            repos.Turns.set_status(conn, tid, "completed",
                                   {"provider": "graph", "updated_at": _now()})
            _emit(conn, project_id=pid,
                  conversation_id=str(workflow.get("conversation_id") or ""),
                  turn_id=tid, node="approval-interrupt",
                  detail=("Approved — continuing" if decision == "approved"
                          else "Rejected — holding"),
                  metadata={"approval_id": aid, "approved": decision == "approved",
                            "executed": decision == "approved",
                            "idempotency_key": key,
                            "decided_by": body.decided_by.strip()})
            return _ok({"approval_id": aid, "status": decided.get("status"),
                        "decided_by": decided.get("decided_by", ""),
                        "thread_id": workflow_thread, "turn_id": tid,
                        "final_answer": final, "idempotency_key": key,
                        "executed": decision == "approved"})
        from app.services import state as statesvc
        try:
            decided = statesvc.decide_approval(
                conn, aid, decision,
                decided_by=body.decided_by.strip(), project_id=pid)
        except (KeyError, ValueError) as e:
            raise HTTPException(
                status_code=409,
                detail=safe_error_message(e, "The approval could not be updated."),
            )
        flow_executed = _approval_flow_passthrough(
            approval_id=aid, project_id=pid,
            approved=(decision == "approved"), key=key)
    return _ok({"approval_id": aid, "status": decided.get("status"),
                "decided_by": decided.get("decided_by", ""),
                "approval_flow_executed": flow_executed,
                "idempotency_key": key, "executed": decision == "approved"})
