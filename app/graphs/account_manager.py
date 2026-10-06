"""Account Manager graph (docs/v1/architecture.md §2).

Normative node list:
load_project → load_conversation → understand → route
  → { state_only | conversation_meta | compound_marketing_task
     | tool_capability | knowledge | external_research | social_research
     | deep_research | campaign_operation | approval_operation | job_followup }
  → aggregate → route → synthesize → approval-interrupt → respond

Every branch wraps an existing capability (legacy account_manager,
tool registry, rag_service, instagram helpers). Nothing is rewritten.
Fail-closed NO_PROJECT_SCOPE preserves the manager_loop guard.
The lightweight runner below works without langgraph installed;
compile_langgraph() binds the same nodes to a real StateGraph when
the package is present (optional import, no new dependency).
"""
from __future__ import annotations

from app.contracts.routing import GraphRoute, RouteDecision
from app.database.identity import DEFAULT_PROJECT_ID
from app.contracts.runtime import RuntimeConfig
from app.graphs import approvals as _approvals
from app.graphs import deep_research as _deep
from app.graphs.adapters import (
    ConversationAdapter,
    ProjectAdapter,
    RetrievalAdapter,
    ToolRegistryAdapter,
)
from app.graphs.events import build_event
from app.graphs.state import initial_state

NODES = (
    "load_project",
    "load_conversation",
    "understand",
    "state_only",
    "conversation_meta",
    "compound_marketing_task",
    "tool_capability",
    "knowledge",
    "external_research",
    "social_research",
    "deep_research",
    "campaign_operation",
    "approval_operation",
    "job_followup",
    "aggregate",
    "synthesize",
    "approval-interrupt",
    "respond",
)

BRANCHES: tuple[str, ...] = (
    "state_only",
    "conversation_meta",
    "compound_marketing_task",
    "tool_capability",
    "knowledge",
    "external_research",
    "social_research",
    "deep_research",
    "campaign_operation",
    "approval_operation",
    "job_followup",
)

_HAS_LANGGRAPH = False
try:  # optional-import guard: W1 never hard-depends on langgraph
    import importlib.util as _ilu

    _HAS_LANGGRAPH = _ilu.find_spec("langgraph") is not None
except Exception:
    _HAS_LANGGRAPH = False


# ---------- routing ----------

_APPROVAL_CUES = ("please approve", "approve and send", "approve & send",
                  "send this", "publish this", "go ahead and send",
                  "sign off and send")
_JOB_CUES = ("job status", "job_status", "background work", "background job")
_CAMPAIGN_CUES = ("propose a campaign", "propose campaign", "create a campaign",
                  "create campaign", "new campaign", "propose a task",
                  "propose task", "create a task", "create task")
_SOCIAL_CUES = ("instagram", "انستجرام", "انستغرام", "tiktok", "تيك توك")
_DEEP_CUES = ("deep research", "deep_research", "in-depth research")
_WEB_CUES = ("search the web", "web search", "search live", "browse the web",
             "current events", "latest news", "live data")

_STATE_FLOWS = {"attention", "approvals", "blocked", "next",
                "company", "experiment", "changed"}


def _legacy_flow(text: str) -> str:
    try:
        from app.services import account_manager as legacy

        return legacy.classify(text or "")
    except Exception:
        return "documentary"


def _social_intent(text: str) -> bool:
    lowered = (text or "").lower()
    if any(c in lowered for c in _SOCIAL_CUES) or "@" in (text or ""):
        return True
    try:
        from app.graphs.adapters import SocialAdapter

        return SocialAdapter.detect_intent(text) is not None
    except Exception:
        return False


def classify_to_route(text: str, *, deep: bool = False,
                      project_evidence: bool = False) -> GraphRoute:
    lowered = (text or "").lower()
    if any(c in lowered for c in _APPROVAL_CUES):
        return "approval_operation"
    # DEV-007R-HOTFIX-2: explicit current-thread + tool-capability intents
    # take precedence over keyword routes (routing priority A/B).
    # DEV-008-SKILLS-OPS-HOTFIX §1 inserts compound_marketing_task between
    # them: a turn that asks for >= 2 marketing domains must orchestrate, never
    # collapse onto whichever single capability happened to match.
    from app.graphs.intent import classify_intent

    prior = classify_intent(text or "")
    if prior.get("route") == "conversation_meta":
        return "conversation_meta"
    if prior.get("route") == "compound_marketing_task":
        return "compound_marketing_task"
    if prior.get("route") == "tool_capability":
        return "tool_capability"
    if any(c in lowered for c in _JOB_CUES):
        return "job_followup"
    if any(c in lowered for c in _CAMPAIGN_CUES):
        return "campaign_operation"
    if _social_intent(text):
        # handle/identity questions and audits both ride social_research
        return "social_research"
    if deep or any(c in lowered for c in _DEEP_CUES):
        return "deep_research"
    if any(c in lowered for c in _WEB_CUES):
        return "external_research"
    flow = _legacy_flow(text)
    if flow in _STATE_FLOWS:
        return "state_only"
    return "knowledge"


def decide_route(text: str, *, project_id: str,
                 deep: bool = False) -> RouteDecision:
    route = classify_to_route(text, deep=deep)
    confidence = {
        "approval_operation": 0.9,
        "campaign_operation": 0.85,
        "job_followup": 0.9,
        "conversation_meta": 0.95,
        "compound_marketing_task": 0.9,
        "tool_capability": 0.85,
        "social_research": 0.85,
        "deep_research": 0.85,
        "external_research": 0.85,
        "state_only": 0.8,
        "knowledge": 0.75,
    }.get(route, 0.6)
    return RouteDecision(route=route, confidence=confidence,
                         reason=f"deterministic classifier → {route}",
                         project_id=(project_id or "").strip() or DEFAULT_PROJECT_ID)


def route_targets(route: str) -> list[str]:
    """Conditional-edge fan-out: single branch, deep_research included.

    deep_research fans out *inside* its sub-graph (Send/workers), so the
    top-level edge stays single-target; multi-worker parallelism lives
    in app/graphs/deep_research.py.
    """
    if route in BRANCHES:
        return [route]
    return ["state_only"]


def graph_definition() -> dict:
    return {
        "nodes": list(NODES),
        "entry": "load_project",
        "branches": list(BRANCHES),
        "edges": [
            ["load_project", "load_conversation"],
            ["load_conversation", "understand"],
            ["understand", "route"],
            ["route", "branches"],
            ["branches", "aggregate"],
            ["aggregate", "route"],
            ["route", "synthesize"],
            ["synthesize", "approval-interrupt"],
            ["approval-interrupt", "respond"],
        ],
    }


def compile_langgraph(checkpointer=None, store=None, **kwargs):
    """v1 product path: real StateGraph (Send, reducers, conditional edges,
    checkpointer/thread_id, interrupt/Command). Requires langgraph installed.

    Delegates to app.graphs.account_manager_graph.build_account_manager_graph.
    """
    from app.graphs.account_manager_graph import build_account_manager_graph

    return build_account_manager_graph(checkpointer=checkpointer, **kwargs)


# ---------- branch implementations (wraps, never rewrites) ----------

def _legacy_reply(conn, root, text: str, retriever=None) -> dict:
    from app.services import account_manager as legacy

    out = legacy.handle_turn(conn, root, text, retriever=retriever)
    return {
        "reply_md": out.get("reply_md", ""),
        "provenance": out.get("provenance", []) or [],
        "job_ids": [],
        "approval_ids": [],
        "evidence": out.get("evidence", []) or [],
    }


def _branch_external(conn, root, text: str, tools: ToolRegistryAdapter,
                     project_id: str) -> dict:
    try:
        from app.services import account_manager as legacy

        lang = legacy.detect_language(text or "")
    except Exception:
        lang = "en"
    obs = tools.execute(conn, project_id=project_id, root=root,
                        name="web_search", args={"query": text, "count": 5})
    results = (obs or {}).get("results", []) or []
    provenance = [{"path": r.get("url", ""), "chunk_id": "?",
                   "file_sha": "", "status_tag": "PARTIALLY VERIFIED"}
                  for r in results[:5] if r.get("url")]
    if not results:
        reply = ("البحث الخارجي مش متاح دلوقتي — أقدر أجاوب من البيانات المسجلة بدل كده. "
                 "(STATUS: NOT ACCESSIBLE / SOURCE: web search / CONFIDENCE: HIGH)"
                 if lang == "ar" else
                 "Live web search is not accessible right now — "
                 "here is what the recorded data says instead. "
                 "(STATUS: NOT ACCESSIBLE / SOURCE: web search / CONFIDENCE: HIGH)")
        return {"reply_md": reply, "provenance": [], "job_ids": [],
                "approval_ids": [], "evidence": []}
    lines = [f"- {r.get('title', r.get('url', ''))} — {r.get('url', '')}"[:220]
             for r in results[:5]]
    head = ("نتائج الويب:" if lang == "ar" else "Web findings:")
    tail = ("(STATUS: PARTIALLY VERIFIED / SOURCE: web search / CONFIDENCE: MEDIUM)"
            if lang != "ar" else
            "(STATUS: PARTIALLY VERIFIED / SOURCE: web search / CONFIDENCE: MEDIUM)")
    return {"reply_md": head + "\n" + "\n".join(lines) + "\n" + tail,
            "provenance": provenance, "job_ids": [], "approval_ids": [],
            "evidence": []}


def _branch_social(conn, project_id: str, text: str) -> dict:
    try:
        from app.services import account_manager as legacy

        lang = legacy.detect_language(text or "")
        early = legacy._instagram_fallback_reply(conn, project_id, text, lang)
    except Exception:
        early, lang = None, "en"
    if early is not None:
        return {"reply_md": early.get("reply_md", ""),
                "provenance": early.get("provenance", []) or [],
                "job_ids": [], "approval_ids": [], "evidence": []}
    reply = ("لسه معنديش إنستجرام المشروع ده — إيه الـ@handle؟" if lang == "ar"
             else "I don't have this project's Instagram yet — what's the @handle?")
    return {"reply_md": reply, "provenance": [], "job_ids": [],
            "approval_ids": [], "evidence": []}


def _branch_deep(conn, root, text: str, project_id: str,
                 config: RuntimeConfig, tools: ToolRegistryAdapter) -> dict:
    tasks = _deep.decompose(text, max_tasks=4, config=config)

    def _worker(task: dict, idx: int) -> dict:
        # RAG and/or web per worker, then a synthesis chunk. No sub-delegation.
        chunk, sources = "", []
        try:
            adapter = RetrievalAdapter(conn, project_id)
            res = adapter.retrieve(task.get("query", ""))
            hits = res.hits[:3]
            if hits:
                chunk = "; ".join(h.get("text", "")[:200] for h in hits)[:600]
                sources += [h.path for h in hits if h.path]
        except Exception:
            pass
        try:
            obs = tools.execute(conn, project_id=project_id, root=root,
                                name="web_search",
                                args={"query": task.get("query", ""), "count": 3})
            for r in (obs or {}).get("results", []) or []:
                if r.get("url") and r["url"] not in sources:
                    sources.append(r["url"])
                if not chunk and r.get("snippet"):
                    chunk = str(r["snippet"])[:300]
        except Exception:
            pass
        return {"task_id": task.get("id", f"q{idx}"),
                "chunk": chunk or f"no direct evidence for {task.get('query', '')}"[:300],
                "sources": sources[:5], "ok": True}

    results = _deep.fan_out_and_collect(tasks, _worker, config=config)
    fused = _deep.fuse_results(results)
    try:
        from app.services import account_manager as legacy

        lang = legacy.detect_language(text or "")
    except Exception:
        lang = "en"
    if fused["chunks"]:
        body = "\n".join(f"- {c}"[:300] for c in fused["chunks"][:4])
        cites = "\n".join(f"- {c}" for c in fused["citations"][:5])
        head = "ملخص البحث المعمق:" if lang == "ar" else "Deep-research summary:"
        src_head = "المصادر:" if lang == "ar" else "Sources:"
        reply = head + "\n" + body + ("\n" + src_head + "\n" + cites if cites else "")
    else:
        reply = ("مفيش أدلة كفاية من البحث المعمق." if lang == "ar"
                 else "Deep research found no usable evidence.")
    provenance = [{"path": c, "chunk_id": "?", "file_sha": "",
                   "status_tag": "PARTIALLY VERIFIED"}
                  for c in fused["citations"][:5]]
    return {"reply_md": reply, "provenance": provenance, "job_ids": [],
            "approval_ids": [], "evidence": [],
            "research_tasks": tasks, "worker_results": results}


def _branch_campaign(conn, project_id: str, text: str) -> dict:
    try:
        from app.services import account_manager as legacy

        lang = legacy.detect_language(text or "")
    except Exception:
        lang = "en"
    tools = ToolRegistryAdapter()
    digest: dict = {}
    try:
        obs = tools.execute(conn, project_id=project_id, root="",
                            name="get_project_state", args={})
        digest = (obs or {}).get("state", {}) or {}
    except Exception:
        pass
    pending = digest.get("approvals_pending", 0)
    reply = (f"جهزت مقترح مبدئي — {pending} موافقة معلقة قبل التنفيذ. "
             "قول موافق عشان أطلب الموافقة الرسمية."
             if lang == "ar" else
             f"Draft proposal ready — {pending} pending approval(s) before execution. "
             "Say approve and I will request formal approval.")
    void = {"reply_md": reply, "provenance": [], "job_ids": [],
            "approval_ids": [], "evidence": []}
    void["state_results"] = digest
    return void


def _branch_job(conn, project_id: str) -> dict:
    try:
        from app.database import repos

        jobs = repos.BackgroundJobs.list(conn, project_id)
    except Exception:
        jobs = []
    open_jobs = [j for j in jobs if (j.get("status") or "") not in ("done", "failed")]
    reply = (f"الشغل الخلفي: {len(open_jobs)} مفتوح من {len(jobs)}."
             if any(ord(c) > 127 for c in str(jobs)) else
             f"Background work: {len(open_jobs)} open of {len(jobs)} tracked.")
    return {"reply_md": reply, "provenance": [],
            "job_ids": [j.get("id", "") for j in open_jobs[:5]],
            "approval_ids": [], "evidence": []}


# ---- compound marketing task (DEV-008-SKILLS-OPS-HOTFIX §2/§3) -------------
# The orchestration itself lives in the compiled graph
# (account_manager_graph.build_account_manager_graph, node
# `compound_marketing_task`). This module only dispatches: it never builds a
# second orchestration path, so there is exactly one plan/employee/fan-in
# implementation in the system.

#: Probed in order on the graph module; any callable whose name announces
#: compound orchestration is then accepted. The expected signature is
#: ``fn(conn=, project_id=, user_text=, domains=, turn_id=) -> dict`` with
#: ``reply_md`` in the result; anything else degrades to the limitation reply.
_COMPOUND_RUNNER_NAMES = (
    "run_compound_marketing_task",
    "compound_marketing_task_node",
    "run_compound_orchestration",
)


def resolve_compound_runner():
    """The graph-side compound entry point, or None when it has not landed.

    Defensive on purpose: W4 owns `account_manager_graph.py` and this module
    must not guess its internals, so a missing entry point is a *recorded
    limitation*, not a reason to build a fallback orchestrator here.
    """
    try:
        from app.graphs import account_manager_graph as amg
    except Exception:
        return None
    for name in _COMPOUND_RUNNER_NAMES:
        fn = getattr(amg, name, None)
        if callable(fn):
            return fn
    for name in dir(amg):
        if "compound" in name.lower():
            fn = getattr(amg, name, None)
            if callable(fn):
                return fn
    return None


def _compound_unavailable(user_text: str, domains: list[str]) -> dict:
    """Honest reply when the compound graph node is not reachable from here."""
    listed = ", ".join(domains) if domains else "none"
    reply = ("I classified this as a compound marketing task ("
             f"{listed}) but this offline runner has no compound "
             "orchestration path wired yet — the compiled graph owns it. "
             "Nothing was researched; re-run the turn on the graph runtime.")
    return {"reply_md": reply, "provenance": [], "job_ids": [],
            "approval_ids": [], "evidence": [],
            "limitations": [
                "compound_marketing_task branch unavailable on the legacy "
                "runner: no callable compound entry point on "
                "app.graphs.account_manager_graph"]}


# ---------- runner ----------

def run_graph(conn, *, root, project_id: str, conversation_id: str,
              turn_id: str, user_text: str, config: RuntimeConfig | None = None,
              registry=None, retriever=None, checkpointer=None) -> dict:
    """LEGACY/test fallback runner (no langgraph dependency).

    Kept for unit tests and offline fallback only — never the v1 product
    path. v1 traffic uses compile_langgraph() →
    account_manager_graph.build_account_manager_graph() (real StateGraph
    with Send/reducers/conditional edges/checkpointer/interrupt).

    Pure with respect to business SQLite: reads via repos/adapters only,
    persists nothing except an optional checkpoint snapshot keyed by
    turn_id (dedicated checkpoint DB, never marketing.db).
    """
    cfg = config or RuntimeConfig()
    tools = registry if isinstance(registry, ToolRegistryAdapter) else ToolRegistryAdapter(registry)
    state = initial_state(project_id=(project_id or "").strip(),
                          conversation_id=conversation_id or "",
                          turn_id=turn_id or "",
                          user_request=user_text or "")
    events = []
    errors: list[str] = []

    def _emit(node: str, **kw) -> None:
        events.append(build_event(node, project_id=state["project_id"] or DEFAULT_PROJECT_ID,
                                  turn_id=state["turn_id"],
                                  conversation_id=state["conversation_id"], **kw))

    # load_project (fail-closed)
    try:
        project = ProjectAdapter.get_project(conn, project_id)
        state["project_context"] = {"project": project}
        _emit("load_project")
    except (ValueError, LookupError):
        errors.append("NO_PROJECT_SCOPE: Project could not be loaded.")
        _emit("respond_failed", detail="Project could not be loaded.")
        out = {"route": "state_only", "final_answer": "", "events": events,
               "errors": errors, "provenance": [], "job_ids": [],
               "approval_ids": [], "evidence": [], "limitations": [],
               "approval_state": {}, "state": state}
        return out


    # load_conversation
    try:
        state["project_context"]["messages"] = ConversationAdapter.load_messages(
            conn, conversation_id)
        state["project_context"]["memories"] = ConversationAdapter.load_memories(
            conn, state["project_id"])
        _emit("load_conversation")
    except Exception:
        errors.append("Conversation context could not be loaded.")

    # understand
    decision = decide_route(user_text or "", project_id=state["project_id"])
    state["model_route"] = decision.route
    _emit("understand", detail=f"route={decision.route} conf={decision.confidence:.2f}",
          metadata={"route": decision.route})

    route = decision.route
    branch_out: dict = {}
    try:
        if route == "state_only":
            _emit("state_only")
            branch_out = _legacy_reply(conn, root, user_text or "", retriever)
        elif route == "knowledge":
            _emit("knowledge")
            branch_out = _legacy_reply(conn, root, user_text or "", retriever)
        elif route == "tool_capability":
            _emit("tool_capability")
            from app.graphs.tool_capability import resolve_and_execute
            from app.graphs.intent import detect_capability_intent

            intent = detect_capability_intent(user_text or "") or {
                "capability": "instagram_public_profile", "arguments": {}}
            out = resolve_and_execute(
                conn, project_id=state["project_id"],
                capability=str(intent.get("capability") or "instagram_public_profile"),
                args=dict(intent.get("arguments") or {}))
            branch_out = {"reply_md": out.get("answer", ""), "provenance": [],
                          "job_ids": [], "approval_ids": [], "evidence": []}
            state["tool_run_id"] = out.get("tool_run_id", "")
            state["social_results"] = out.get("social_results", []) or []
        elif route == "compound_marketing_task":
            # Must never fall through to tool_capability: a multi-domain turn
            # orchestrates. The plan/employee/fan-in implementation is the
            # graph's; this branch only dispatches to it.
            from app.graphs.intent import detect_compound_task

            compound = detect_compound_task(user_text or "") or {}
            domains = [str(d) for d in (compound.get("domains") or [])]
            _emit("compound_marketing_task",
                  label="Compound marketing task",
                  detail=compound.get("reason", "")[:300],
                  metadata={"domains": domains})
            state["compound_domains"] = list(domains)
            runner = resolve_compound_runner()
            branch_out = None
            if runner is not None:
                try:
                    out = runner(conn=conn, project_id=state["project_id"],
                                 user_text=user_text or "", domains=domains,
                                 turn_id=state["turn_id"])
                    branch_out = {"reply_md": str((out or {}).get("reply_md", "")),
                                  "provenance": (out or {}).get("provenance", []) or [],
                                  "job_ids": (out or {}).get("job_ids", []) or [],
                                  "approval_ids": (out or {}).get("approval_ids", []) or [],
                                  "evidence": (out or {}).get("evidence", []) or []}
                    if isinstance(out, dict) and out.get("limitations"):
                        branch_out["limitations"] = list(out["limitations"])
                except Exception as exc:  # never a duplicate path, never a raise
                    branch_out = _compound_unavailable(user_text or "", domains)
                    branch_out["limitations"].append(
                        f"compound runner raised: {type(exc).__name__}")
            if branch_out is None:
                branch_out = _compound_unavailable(user_text or "", domains)
        elif route == "conversation_meta":
            _emit("conversation_meta")
            msgs = state.get("project_context", {}).get("messages") or []
            user_lines = [
                str((m or {}).get("body_md", "")).strip()
                for m in msgs if (m or {}).get("role") == "user"]
            user_lines = [u for u in user_lines if u][-6:]
            if not user_lines:
                branch_out = {"reply_md":
                              "We just started talking — no deeper context yet.",
                              "provenance": [], "job_ids": [],
                              "approval_ids": [], "evidence": []}
            else:
                bullets = "\n".join(f"- {u[:200]}" for u in user_lines)
                branch_out = {"reply_md":
                              "In this chat we've been working through these "
                              "requests:\n" + bullets,
                              "provenance": [], "job_ids": [],
                              "approval_ids": [], "evidence": []}
        elif route == "external_research":
            _emit("external_research")
            branch_out = _branch_external(conn, root, user_text or "", tools,
                                          state["project_id"])
        elif route == "social_research":
            _emit("social_research")
            branch_out = _branch_social(conn, state["project_id"], user_text or "")
        elif route == "deep_research":
            _emit("deep_research")
            branch_out = _branch_deep(conn, root, user_text or "",
                                      state["project_id"], cfg, tools)
            state["research_tasks"] = branch_out.get("research_tasks", [])
            state["worker_results"] = branch_out.get("worker_results", [])
        elif route == "campaign_operation":
            _emit("campaign_operation")
            branch_out = _branch_campaign(conn, state["project_id"], user_text or "")
            if branch_out.get("state_results"):
                state["state_results"] = branch_out["state_results"]
        elif route == "approval_operation":
            _emit("approval_operation")
            req = _approvals.build_approval_request(
                approval_id=f"turn-{state['turn_id'] or 't'}",
                project_id=state["project_id"], title=(user_text or "")[:160],
                action_class="yellow", conversation_id=state["conversation_id"],
                turn_id=state["turn_id"])
            state["approval_state"] = {"approval_id": req.approval_id,
                                       "project_id": req.project_id,
                                       "title": req.title,
                                       "action_class": req.action_class,
                                       "idempotency_key": req.idempotency_key}
            branch_out = {"reply_md": f"Waiting for your approval: {req.title}",
                          "provenance": [], "job_ids": [],
                          "approval_ids": [req.approval_id], "evidence": []}
        else:  # job_followup
            _emit("job_followup")
            branch_out = _branch_job(conn, state["project_id"])
    except Exception:
        errors.append(f"branch {route} failed")
        branch_out = {"reply_md": "", "provenance": [], "job_ids": [],
                      "approval_ids": [], "evidence": []}

    # aggregate (provenance union, ids, evidence flags)
    _emit("aggregate")
    seen: set[tuple[str, str]] = set()
    provenance: list[dict] = []
    for p in branch_out.get("provenance", []) or []:
        key = (str((p or {}).get("path", "")), str((p or {}).get("chunk_id", "?")))
        if key not in seen and key[0]:
            seen.add(key)
            provenance.append(p)
    if route in ("state_only", "knowledge"):
        state["retrieved_evidence"] = branch_out.get("evidence", []) or []
    if route == "external_research":
        state["web_results"] = provenance
    if route == "social_research":
        state["social_results"] = provenance

    # synthesize (branch reply is the composed answer; deep already fused)
    _emit("synthesize")
    final_answer = (branch_out.get("reply_md", "") or "").strip()
    if not final_answer and not errors:
        final_answer = "I don't have enough to judge that yet."
    state["final_answer"] = final_answer

    # approval-interrupt (yellow/red pause; green passes)
    if state.get("approval_state"):
        interrupt = _approvals.approval_interrupt(
            {"approval_state": state["approval_state"]})
        if interrupt.get("status") == "pending":
            _emit("approval-interrupt",
                  detail=str(state["approval_state"].get("title", ""))[:160],
                  metadata={"approval_id": state["approval_state"].get("approval_id", "")})

    # respond (terminal)
    if errors:
        _emit("respond_failed", detail="; ".join(errors)[:300])
    else:
        _emit("respond", detail=final_answer[:300],
              metadata={"route": route})
    if checkpointer is not None:
        try:
            checkpointer.save(state["turn_id"] or "turn",
                              {"turn_id": state["turn_id"],
                               "route": route,
                               "final_answer": final_answer,
                               "errors": errors})
        except Exception:
            pass

    return {"route": route, "final_answer": final_answer, "events": events,
            "errors": errors, "provenance": provenance,
            "job_ids": branch_out.get("job_ids", []) or [],
            "approval_ids": branch_out.get("approval_ids", []) or [],
            "approval_state": state.get("approval_state", {}) or {},
            "research_tasks": state.get("research_tasks", []) or [],
            "worker_results": state.get("worker_results", []) or [],
            "limitations": [str(x) for x in (branch_out.get("limitations", []) or [])],
            "state": state}
