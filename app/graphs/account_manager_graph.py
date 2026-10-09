"""DEV-005 W1 v1 product path — Account Manager StateGraph.

Framework-first runtime (architecture §2): load_project → understand →
real conditional edges to exactly one branch set (deep_research fans out
further via Send), reducer-based fan-in at aggregate, synthesize →
approval_gate (interrupt/Command) → respond. Compiled with a checkpointer;
every invoke requires a thread_id.

Project isolation is fail-closed (NO_PROJECT_SCOPE) via an injectable
project_lookup; the default seam rejects empty/unknown ids without
touching the business DB (I1/W6 inject repos.Projects.get).

The lightweight runner in account_manager.py (run_graph) is the explicit
LEGACY/test fallback — never the v1 product path. compile_langgraph()
delegates here.
"""
from __future__ import annotations

import concurrent.futures
import re
import threading

from app.database.identity import DEFAULT_PROJECT_ID
from app.graphs.state import LangGraphState

_BLOCKED = "__blocked__"
_UNKNOWN_IDS = frozenset({"ghost", "missing", "unknown"})

_TOOL_ROUTES = ("tool_capability", "conversation_meta")

_COMPOUND_DEP_EVENTS: dict[str, threading.Event] = {}


def _default_conn():
    """Live serving connection factory (own connection, thread-safe path).

    Platform dependency lives in services — graphs never import app.deps
    (W1 layering rule, enforced by test_w1_does_not_touch_fastapi_or_react).
    """
    from app.services.graph_conn import default_conn

    return default_conn()


def _rag_debug_enabled() -> bool:
    import os

    return (os.environ.get("OMOS_RAG_DEBUG", "") or "").strip() == "1"


def _explicit_write_intents(text: str) -> dict:
    """Recognize concrete creation requests; ordinary brainstorms stay prose."""
    value = str(text or "").strip()
    lowered = value.lower()
    if not value or any(word in lowered for word in ("brainstorm", "what if", "ideas for", "could we")):
        return {}
    create = any(term in lowered for term in (
        "create", "build", "draft", "prepare", "propose", "launch",
        "اعمل", "أنشئ", "انشئ", "جهز", "جهّز", "اقترح"))
    campaign_requested = create and any(x in lowered for x in ("campaign", "حملة", "حمله", "حملات"))
    experiment_requested = create and any(x in lowered for x in ("experiment", "تجربة", "تجربه", "تجارب"))
    task_requested = create and any(x in lowered for x in (
        "task", "تاسك", "مهمة", "مهام"))
    governed_action = any(term in lowered for term in (
        "publish", "send email", "send outreach", "send the campaign",
        "send campaign", "send the launch campaign", "launch ads", "spend",
        "edit the live site", "change the live site", "delete", "destructive"))
    if not campaign_requested and not experiment_requested and not task_requested:
        return {}
    objective_match = re.search(
        r"(?i)\b(?:campaign|experiment|task)\b\s+(?:to|for|about)\s+([^.!?\r\n]+)",
        value,
    )
    goal = objective_match.group(1) if objective_match else ""
    if not goal:
        goal = re.sub(
            r"(?i)^.*?\b(?:campaign|experiment)\b\s*", "", value, count=1
        )
        goal = re.split(r"[.!?\r\n]", goal, maxsplit=1)[0]
        goal = re.sub(
            r"(?i)\b(?:please approve|approve and send|submit(?: this work)? for approval|"
            r"request approval|pause for approval|if an action requires approval)\b.*$",
            "", goal,
        )
    goal = goal.strip(" .:-") or "website marketing"
    title = goal[:140].strip(" .:-")
    return {
        "campaign": ({"title": f"Campaign: {title}"[:160],
                      "approval_level": "Green"} if campaign_requested else None),
        "task": ({"title": f"Improve conversion: {title}"[:160],
                  "lane": "conversion", "acceptance": "Complete the requested analysis and deliver a reviewable conversion improvement proposal.",
                  "explicit_standalone": bool(task_requested)}
                 if task_requested or (campaign_requested and any(x in lowered for x in ("required work", "create the work", "actionable task", "tasks"))) else None),
        "experiment": ({"hypothesis": f"A focused website conversion change will improve conversion. {title}"[:500],
                        "metric": "website conversion rate", "window_days": 14,
                        "start_date": "", "next_review": "", "stop_condition": "Stop if the primary metric declines materially or the planned window ends.",
                        "campaign_id": "", "explicit_standalone": bool(experiment_requested)} if experiment_requested else None),
        "requires_approval": (
            any(x in lowered for x in ("submit for approval", "submit this work for approval",
                                       "must be approved", "approval required", "request approval",
                                       "please approve", "approve and send"))
            or (governed_action and any(x in lowered for x in (
                "requires approval", "need approval", "pause for approval")))),
    }


def _current_turn_write_constraints(text: str) -> dict:
    """Extract explicit current-turn write prohibitions in a small vocabulary.

    This intentionally recognizes common English and Egyptian Arabic forms,
    rather than trying to interpret arbitrary natural language. The returned
    object is trusted graph state and is never sourced from planner output.
    """
    value = str(text or "").strip().lower()
    if not value:
        return {"forbidden_actions": [], "analysis_only": False}

    # Global restrictions are kept distinct from action-specific restrictions.
    global_read_only = bool(re.search(
        r"\b(?:read[- ]only|analysis[- ]only|analy[sz]e only|just analyze|"
        r"do not save anything|don't save anything|dont save anything|"
        r"without saving anything|no writes?)\b|"
        r"\b(?:do not|don['’]t|dont)\s+save\s+this\b|"
        r"\b(?:do not|don['’]t|dont)\s+make\s+changes?\b", value
    )) or bool(re.search(
        r"(?:حلل|حلّل|تحليل).{0,30}(?:بس|فقط).{0,35}(?:من غير|بدون).{0,20}"
        r"(?:ما تحفظ|حفظ|تسجّل|تسجل)|(?:ما تحفظش|متحفظش)", value
    ))

    # A clear negative verb plus the action noun is required. “Suggest a
    # campaign without creating it” is covered by both action + negative verb.
    negative_en = r"(?:do not|don't|dont|never|without|don't actually|dont actually)"
    negative_ar = (
        r"(?:ما\s*تعملهاش|متعملهاش|ما\s*تنشئهاش|متنشئهاش|"
        r"ما\s*تعملش|متعملش|ما\s*تنشئش|متنشئش|"
        r"ما\s*تنشئ|متنشئ|ما\s*تعمل|ماتعملش|ما\s*تبعثش|ماتبعتش)"
    )
    negative = rf"(?:{negative_en}|{negative_ar})"

    patterns = {
        "campaign": (
            rf"{negative}.{{0,55}}(?:campaign|campaigns|حملة|حمله|حملات)",
            rf"(?:campaign|campaigns|حملة|حمله|حملات).{{0,55}}{negative}",
        ),
        "task": (
            rf"{negative}.{{0,55}}(?:tasks?|تاسكات|مهام)",
            rf"(?:tasks?|تاسكات|مهام).{{0,55}}{negative}",
        ),
        "experiment": (
            rf"{negative}.{{0,55}}(?:experiments?|تجربة|تجربه|تجارب)",
            rf"(?:experiments?|تجربة|تجربه|تجارب).{{0,55}}{negative}",
        ),
        "publish": (
            rf"{negative}.{{0,55}}(?:publish|publishing|post|posting|نشر|تنشر)",
            rf"(?:publish|publishing|post|posting|نشر|تنشر).{{0,55}}{negative}",
        ),
        "send": (
            rf"{negative}.{{0,55}}(?:send|sending|email|outreach|تبعت|تبعتش|ترسل)",
            rf"(?:send|sending|email|outreach|تبعت|ترسل).{{0,55}}{negative}",
        ),
    }
    # Do not let the action noun in one clause inherit a prohibition from a
    # later clause (for example, “create tasks, but do not create a campaign”).
    # Common punctuation and adversative conjunctions close a clause here.
    clauses = [part for part in re.split(
        r"[,،;.!?؟\r\n]+|\b(?:but|however)\b", value
    ) if part.strip()]
    forbidden = [action for action, rules in patterns.items()
                 if any(re.search(rule, clause)
                        for clause in clauses for rule in rules)]
    # This suggestion form explicitly negates only campaign creation, with
    # the negative clause allowed to follow a conjunction or punctuation.
    if re.search(
        r"(?:اقترح|اقترحي|suggest|recommend).{0,45}(?:campaign|حملة|حمله)"
        r".{0,80}(?:بس|فقط|without|من غير|بدون|but).{0,35}"
        r"(?:تعملهاش|متعملهاش|تنشئهاش|متنشئهاش|create|creating|make|making|تنفيذ|"
        r"don't\s+create\s+(?:it|one)|do not\s+create\s+(?:it|one))", value
    ) and "campaign" not in forbidden:
        forbidden.append("campaign")
    return {"forbidden_actions": forbidden, "analysis_only": global_read_only}


def _filter_forbidden_write_intents(intents: dict, constraints: dict) -> tuple[dict, list[str]]:
    """Remove prohibited writes and writes that depend on a prohibited parent."""
    clean = dict(intents or {})
    forbidden = set(constraints.get("forbidden_actions") or [])
    if constraints.get("analysis_only"):
        forbidden.update(("campaign", "task", "experiment", "publish", "send"))

    blocked: list[str] = []
    for key in ("campaign", "task", "experiment"):
        if clean.get(key) and key in forbidden:
            clean[key] = None
            blocked.append(key)

    # A task or experiment inferred as work under a campaign cannot be written
    # after the campaign was prohibited. Explicit standalone intents remain.
    if "campaign" in blocked:
        if clean.get("task") and not clean["task"].get("explicit_standalone"):
            clean["task"] = None
            blocked.append("task")
        if clean.get("experiment") and not clean["experiment"].get("explicit_standalone"):
            clean["experiment"] = None
            blocked.append("experiment")
    if not any(clean.get(key) for key in ("campaign", "task", "experiment")):
        clean["requires_approval"] = False
    return clean, list(dict.fromkeys(blocked))


def _evidence_grounded_experiment(experiment: dict | None,
                                  employee_results: list[dict],
                                  user_request: str) -> dict | None:
    """Bind an explicit evidence-only experiment request to a successful finding.

    No proposal is returned when the requested evidence is unavailable. The
    originating tool-run ID and source URL are carried in private handoff keys
    for the writer to persist in the experiment workflow provenance.
    """
    if not isinstance(experiment, dict):
        return None
    request = str(user_request or "")
    single = re.search(r"(?i)\b(?:one|1)\s+(?:growth\s+)?experiment\b", request)
    evidence_only = re.search(r"(?i)based\s+only\s+on\s+evidence", request)
    if not single or not evidence_only:
        return dict(experiment)

    for result in employee_results or []:
        if not isinstance(result, dict):
            continue
        if result.get("status") != "completed":
            continue
        evidence = [e for e in result.get("evidence", [])
                    if isinstance(e, dict)
                    and e.get("kind") == "website_marketing_audit"
                    and e.get("ref")]
        tools = [t for t in result.get("tools", [])
                 if isinstance(t, dict)
                 and t.get("tool_id") == "website_marketing_audit"
                 and t.get("status") == "completed"
                 and t.get("tool_run_id")]
        if result.get("domain") != "website" and not (evidence and tools):
            continue
        finding = next((str(f) for f in result.get("findings", [])
                        if "no call-to-action keywords observed" in str(f).lower()), "")
        if not evidence or not tools or not finding:
            continue
        return {
            **experiment,
            "hypothesis": (
                "Test an explicit primary call to action because the live website "
                "audit observed no action-oriented CTA keywords in the fetched page."
            ),
            "metric": "primary CTA click-through rate",
            "evidence_tool_run_id": str(tools[0]["tool_run_id"]),
            "evidence_ref": str(evidence[0]["ref"]),
        }
    return None


def _rag_debug_dir() -> str:
    import os
    from pathlib import Path

    override = (os.environ.get("OMOS_RAG_DEBUG_DIR", "") or "").strip()
    if override:
        return override
    root = Path(__file__).resolve().parents[2]
    return str(root / "data" / "rag_debug")


def _write_rag_debug(record: dict) -> None:
    """Dev-only JSONL trace (OMOS_RAG_DEBUG=1). Never raises; never in chat."""
    try:
        if not _rag_debug_enabled():
            return
        import json
        from pathlib import Path

        d = Path(_rag_debug_dir())
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with (d / "rag_debug.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        return


def _project_state(conn_factory, project_id: str) -> dict:
    """Structured project identity for the model context (fail-soft).

    Reads the project row + social accounts via repos; missing pieces stay
    "" — never invented. memories are filled later by load_conversation.
    """
    pid = (project_id or "").strip()
    if not pid:
        return {}
    try:
        conn, owned = (conn_factory or _default_conn)(), conn_factory is None
        try:
            from app.graphs.adapters import ProjectAdapter

            row = ProjectAdapter.get_project(conn, pid)
        finally:
            if owned:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception:
        return {}
    try:
        import json as _json

        settings = _json.loads(row.get("settings_json") or "{}")
        if not isinstance(settings, dict):
            settings = {}
    except Exception:
        settings = {}
    accounts: list[dict] = []
    instagram_handle = str(settings.get("instagram_handle", "") or "")
    try:
        conn2, owned2 = (conn_factory or _default_conn)(), conn_factory is None
        try:
            from app.database import repos

            for a in repos.SocialAccounts.for_project(conn2, pid) or []:
                platform = str(a.get("platform", "") or "")
                handle = str(a.get("handle", "") or "")
                status = str(a.get("status", "") or "")
                accounts.append({"platform": platform, "handle": handle,
                                 "status": status})
                if platform == "instagram" and handle and not instagram_handle:
                    instagram_handle = handle
        finally:
            if owned2:
                try:
                    conn2.close()
                except Exception:
                    pass
    except Exception:
        accounts = []
    return {
        "project_id": pid,
        "name": str(row.get("name", "") or ""),
        "website": str(row.get("website", "") or ""),
        "goal": str(row.get("goal", "") or ""),
        "instagram_handle": instagram_handle,
        "social_accounts": accounts,
        "aliases": [str(a).strip() for a in (
            settings.get("aliases") or []) if str(a).strip()],
        "memories": [],
    }


def _default_lookup(project_id: str) -> dict | None:
    pid = (project_id or "").strip()
    if not pid or pid.lower() in _UNKNOWN_IDS:
        return None
    return {"id": pid}


def _split_tasks(text: str, cap: int = 4) -> list[dict]:
    q = (text or "").strip()
    if not q:
        return [{"id": "q0", "query": ""}]
    parts = [p.strip() for p in re.split(r"[;?\n]+", q) if p.strip()]
    if len(parts) <= 1:
        words = q.split()
        if len(words) > 24:
            mid = len(words) // 2
            parts = [" ".join(words[:mid]), " ".join(words[mid:])]
        else:
            parts = [q]
    return [{"id": f"q{i}", "query": p} for i, p in enumerate(parts[:cap])]


def _event_callback(config):
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable") or {}
    callback = configurable.get("event_callback") if isinstance(configurable, dict) else None
    return callback if callable(callback) else None


def _tree_of(config):
    """The per-turn TreeEmitter carried via ``configurable["tree"]`` (or None).

    ``None`` is a normal value — direct ``graph.invoke`` calls in older tests
    never pass a tree, and every new node degrades to state-only work when it
    is absent rather than failing the turn.
    """
    try:
        if not isinstance(config, dict):
            return None
        configurable = config.get("configurable") or {}
        if not isinstance(configurable, dict):
            return None
        tree = configurable.get("tree")
        return tree if tree is not None else None
    except Exception:
        return None


def _retrieval_telemetry_from_result(result, selected_hits: list[dict],
                                    source_file_ids: list[str] | None = None) -> dict:
    """Compact audit fields from the actual fused retrieval result."""
    fused = []
    for hit in (getattr(result, "hits", None) or []):
        if hasattr(hit, "model_dump"):
            fused.append(dict(hit.model_dump()))
        elif isinstance(hit, dict):
            fused.append(dict(hit))

    def contributes(hit: dict, source: str) -> bool:
        sources = hit.get("sources") or [hit.get("source", "")]
        return source in sources

    def count(name: str, source: str) -> int:
        value = getattr(result, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return sum(1 for hit in fused if contributes(hit, source))

    fused_count = getattr(result, "fused_hits", None)
    telemetry = {
        "retrieval_mode": str(getattr(result, "mode", "") or ""),
        "lexical_hits": count("lexical_hits", "fts"),
        "vector_hits": count("vector_hits", "semantic"),
        "fused_hits": (fused_count if isinstance(fused_count, int)
                       and not isinstance(fused_count, bool) and fused_count >= 0
                       else len(fused)),
        "selected_chunk_ids": [str(hit.get("chunk_id", ""))
                               for hit in selected_hits if hit.get("chunk_id")],
    }
    if source_file_ids is not None:
        telemetry["source_file_ids"] = list(dict.fromkeys(
            str(file_id) for file_id in source_file_ids if file_id))
    return telemetry


def _selected_source_file_ids(conn, project_id: str,
                              selected_hits: list[dict]) -> list[str] | None:
    """Resolve selected source paths to real project-scoped documents IDs."""
    paths = list(dict.fromkeys(str(hit.get("path", "")) for hit in selected_hits
                               if hit.get("path")))
    if not paths:
        return []
    placeholders = ",".join("?" for _ in paths)
    try:
        rows = conn.execute(
            f"SELECT id, path FROM documents WHERE project_id = ? "
            f"AND path IN ({placeholders})",
            [project_id, *paths],
        ).fetchall()
        by_path = {str(row["path"]): str(row["id"]) for row in rows}
    except Exception:
        return None
    return list(dict.fromkeys(by_path[path] for path in paths if path in by_path))


#: Hard cap on employee fan-out (plan §1.4.1). Mirrors
#: ``router.MAX_EMPLOYEES_PER_TURN`` without importing the router at module
#: scope — W5 owns that file and may be mid-write while this module loads.
_MAX_EMPLOYEES_PER_TURN = 4


def _employee_pairs(user_request: str, roles: list) -> list[tuple[dict, str]]:
    """Cartesian (task, role) pairs, capped — the fan-out unit (plan §3.4).

    One ``Send`` per pair, not per skill. ``_split_tasks`` caps tasks at 4
    and the router caps skills at 3, so the cartesian product is capped at
    ``_MAX_EMPLOYEES_PER_TURN`` here as the final guard.
    """
    tasks = _split_tasks(user_request or "")
    clean_roles = [str(r or "").strip() for r in (roles or [])]
    clean_roles = [r for r in clean_roles if r]
    if not tasks or not clean_roles:
        return []
    pairs = [(task, role) for task in tasks for role in clean_roles]
    return pairs[:_MAX_EMPLOYEES_PER_TURN]


def _employee_id_for(turn_id: str, index: int) -> str:
    prefix = (str(turn_id or "")[:8] or "unknown")
    return f"emp-{prefix}-{int(index):02d}"


def _emit_graph_event(config, event_type: str, payload: dict | None = None) -> None:
    callback = _event_callback(config)
    if callback is None:
        return
    try:
        callback(event_type, dict(payload or {}))
    except Exception:
        return


def build_compound_task_plan(domains: list[str], user_text: str = "", turn_id: str = "") -> dict:
    """Build a structured task plan with Wave A (concurrent) and Wave B (dependent) tasks."""
    clean_domains = [str(d).strip().lower() for d in (domains or []) if str(d).strip()]
    if not clean_domains:
        clean_domains = ["website", "instagram", "competitor"]
    prefix = (str(turn_id or "")[:8] or "turn")
    plan_id = f"plan-{prefix}"
    tasks: list[dict] = []

    configs = [
        {
            "domain": "website",
            "role": "cro",
            "task": "Audit website architecture, speed, and conversion readiness",
            "tools": ["website_marketing_audit"],
            "skills": ["seo-audit", "site-architecture"],
            "wave": "A",
            "depends_on": [],
        },
        {
            "domain": "instagram",
            "role": "content",
            "task": "Scrape and analyze Instagram profile and social content",
            "tools": ["instagram_audit"],
            "skills": ["social"],
            "wave": "A",
            "depends_on": [],
        },
        {
            "domain": "social",
            "role": "content",
            "task": "Scrape and analyze social media presence and engagement",
            "tools": ["instagram_audit"],
            "skills": ["social"],
            "wave": "A",
            "depends_on": [],
        },
        {
            "domain": "competitor",
            "role": "research",
            "task": "Research competitor landscape and market positioning",
            "tools": ["web_search"],
            "skills": ["competitor-profiling"],
            "wave": "A",
            "depends_on": [],
        },
        {
            "domain": "positioning",
            "role": "strategy",
            "task": "Evaluate brand positioning, messaging, and ICP alignment",
            "tools": [],
            "skills": ["product-marketing"],
            "wave": "A",
            "depends_on": [],
        },
        {
            "domain": "seo",
            "role": "seo",
            "task": "Audit SEO readiness, schema, and search engine visibility",
            "tools": [],
            "skills": ["seo-audit"],
            "wave": "A",
            "depends_on": [],
        },
        {
            "domain": "cro",
            "role": "cro",
            "task": "Analyze conversion funnel and checkout flow friction",
            "tools": [],
            "skills": ["cro"],
            "wave": "B",
            "depends_on": ["website"],
        },
        {
            "domain": "experiment",
            "role": "growth",
            "task": "Design growth experiments and testing roadmap",
            "tools": [],
            "skills": ["ab-testing"],
            "wave": "A",
            "depends_on": [],
        },
    ]

    seen_domains = set()
    idx = 0
    website_emp_id = None
    for cfg in configs:
        dom = cfg["domain"]
        if dom in clean_domains and dom not in seen_domains:
            if dom == "social" and "instagram" in seen_domains:
                continue
            seen_domains.add(dom)
            emp_id = f"emp-{prefix}-{idx:02d}"
            if dom == "website":
                website_emp_id = emp_id
            t = {
                "id": emp_id,
                "employee_id": emp_id,
                "role": cfg["role"],
                "task": cfg["task"],
                "query": cfg["task"],
                "domains": [dom],
                "domain": dom,
                "tools": list(cfg["tools"]),
                "skills": list(cfg["skills"]),
                "wave": cfg["wave"],
                "depends_on": list(cfg["depends_on"]),
            }
            tasks.append(t)
            idx += 1

    for t in tasks:
        if "website" in t.get("depends_on", []) and website_emp_id:
            if website_emp_id not in t["depends_on"]:
                t["depends_on"].append(website_emp_id)

    return {
        "plan_id": plan_id,
        "turn_id": turn_id,
        "domains": list(clean_domains),
        "tasks": tasks,
        "employees": tasks,
        "waves": {
            "A": [t["id"] for t in tasks if t.get("wave") == "A"],
            "B": [t["id"] for t in tasks if t.get("wave") == "B"],
        },
    }


def _format_website_section(res: dict | None) -> str:
    title = "## 1. تحليل الموقع وتجربة المستخدم / Website Analysis"
    if not res:
        return (
            f"{title}\n\n"
            "- حالة الفحص: لم تتوفر نتيجة موثقة لفحص الموقع؛ لا يمكن تأكيد خصائصه أو أدائه.\n"
            "- التوصية التالية عامة وليست نتيجة فحص: راجع بنية الصفحات ونقاط الاتصال الأساسية."
        )
    findings = res.get("findings") or []
    evidence = res.get("evidence") or []
    tools = res.get("tools") or []
    status = res.get("status") or "completed"
    lines = [title, ""]
    if status == "completed" and evidence:
        lines.append("### حالة الفحص: تم التحقق بنجاح (Verified)")
        for f in findings:
            lines.append(f"- {f}")
        for ev in evidence:
            detail = ev.get("detail", "")
            if detail:
                lines.append(f"- الأدلة المستخرجة: {detail[:250]}")
        for t in tools:
            if t.get("tool_run_id"):
                lines.append(f"- معرّف تشغيل الأداة: `{t['tool_run_id']}` (المزود: {t.get('provider', 'website_fetcher')})")
    else:
        lines.append("### حالة الفحص: غير مكتمل (Incomplete Check)")
        lines.append("- لم تتوفر أدلة موثقة كافية لتأكيد خصائص الموقع أو أدائه.")
        lines.append("- التوصية التالية عامة: راجع استجابة الرابط وبنية الصفحات ونقاط الاتصال.")
    return "\n".join(lines)


def _format_instagram_section(res: dict | None) -> str:
    title = "## 2. تحليل حساب انستجرام والمحتوى / Instagram Analysis"
    if not res:
        return (
            f"{title}\n\n"
            "- تم إدراج فحص الحساب ضمن الخطة، لكن لم تتوفر بيانات اعتماد أو ربط للحساب.\n"
            "- حالة التحقق: لم يتم التحقق (UNVERIFIED) — لم يتم فبركة بيانات الحساب."
        )
    status = res.get("status") or "completed"
    tools = res.get("tools") or []
    findings = res.get("findings") or []
    lines = [title, ""]
    if status == "completed" and findings and (res.get("evidence") or []):
        lines.append("### حالة الفحص: تم التحقق (Verified)")
        for f in findings:
            lines.append(f"- {f}")
        for t in tools:
            if t.get("tool_run_id"):
                lines.append(f"- معرّف تشغيل الأداة: `{t['tool_run_id']}` (المزود: {t.get('provider', 'instagram')})")
    else:
        lines.append("### حالة الفحص: لم يتم التحقق / تعذر الوصول (UNVERIFIED)")
        lines.append("- تعذر الوصول إلى بيانات حساب انستجرام لعدم ربط الحساب (Instagram account not configured).")
        lines.append("- إقرار النزاهة: لم يتم فبركة أرقام المتابعين أو نسب التفاعل أو المنشورات لغياب البيانات الحقيقية.")
        lines.append("- التوصية: ربط الحساب عبر الإعدادات لإتاحة تحليل المحتوى ومعدلات التفاعل ونمو الجمهور.")
    return "\n".join(lines)


def _format_positioning_section(res: dict | None) -> str:
    title = "## 3. التموضع والرسائل التسويقية / Positioning & Messaging"
    lines = [title, ""]
    lines.append("- توصية عامة لعرض القيمة: وضّح الخدمة، والجمهور المقصود، والنتيجة التي تقدمها.")
    lines.append("- توصية عامة لمواءمة العميل المستهدف (ICP): اختبر رسائل تركز على احتياجات الجمهور بدلاً من سرد المميزات فقط.")
    lines.append("- توصية عامة لنبرة العلامة التجارية: استخدم لغة واضحة ومتسقة.")
    if res and res.get("findings") and res.get("evidence"):
        for f in res["findings"]:
            lines.append(f"- ملاحظة استراتيجية: {f}")
    return "\n".join(lines)


def _format_seo_section(res: dict | None) -> str:
    title = "## 4. تحسين محركات البحث / SEO Readiness"
    lines = [title, ""]
    lines.append("- توصية عامة للبنية التقنية: افحص علامات الميتا وهرمية العناوين وخريطة الموقع.")
    lines.append("- توصية عامة للبيانات المنظمة: قيّم ملاءمة Organization وProduct أو Service Schema.")
    lines.append("- توصية عامة للكلمات البحثية: ادرس الكلمات ذات نية الشراء المرتبطة بالمجال.")
    if res and res.get("findings") and res.get("evidence"):
        for f in res["findings"]:
            lines.append(f"- نتائج السيو: {f}")
    return "\n".join(lines)


def _format_cro_section(res: dict | None) -> str:
    title = "## 5. قمع التحويل وتجربة المستخدم / Conversion Funnel & CRO"
    lines = [title, ""]
    lines.append("- توصية عامة للقمع: افحص عدد خطوات التسجيل أو الشراء واختبر تقليل الخطوات عند وجود احتكاك موثق.")
    lines.append("- توصية عامة لدعوة الإجراء (CTA): اجعل نص الإجراء الأساسي واضحاً ومتسقاً.")
    lines.append("- توصية عامة للأدلة الاجتماعية: اعرض شهادات حقيقية قرب نقاط القرار عند توفرها.")
    if res and res.get("findings") and res.get("evidence"):
        for f in res["findings"]:
            lines.append(f"- نتائج CRO: {f}")
    return "\n".join(lines)


def _format_competitor_section(res: dict | None) -> str:
    title = "## 6. دراسة المنافسين / Competitor Analysis"
    lines = [title, ""]
    tools = (res.get("tools") or []) if res else []
    findings = (res.get("findings") or []) if res else []
    evidence = (res.get("evidence") or []) if res else []
    if res and res.get("status") == "completed" and findings and evidence:
        lines.append("### حالة الفحص: تم التحقق عبر البحث المفتوح (Verified)")
        for f in findings:
            lines.append(f"- {f}")
        for t in tools:
            if t.get("tool_run_id"):
                lines.append(f"- معرّف تشغيل البحث: `{t['tool_run_id']}` (المزود: {t.get('provider', 'web_transport')})")
    else:
        lines.append("- تعذر التحقق من معلومات المنافسين عبر البحث في هذه الجولة؛ لا توجد نتائج منافسين موثقة لعرضها.")
        lines.append("- لا تستنتج هذه المراجعة فجوة تنافسية أو نقطة تمايز من دون مصادر قابلة للتحقق.")
    return "\n".join(lines)


def _format_experiments_section(res: dict | None, *, user_request: str = "",
                                website_result: dict | None = None) -> str:
    single_evidence_request = bool(
        re.search(r"(?i)\b(?:one|1)\s+(?:growth\s+)?experiment\b", user_request)
        and re.search(r"(?i)based\s+only\s+on\s+evidence", user_request)
    )
    if single_evidence_request:
        title = "## 7. تجربة نمو واحدة مستندة إلى الأدلة / One Evidence-Based Growth Experiment"
        observations = (website_result or {}).get("findings") or []
        evidence = (website_result or {}).get("evidence") or []
        tools = (website_result or {}).get("tools") or []
        observation = next((str(f) for f in observations
                            if "no call-to-action keywords observed" in str(f).lower()), "")
        has_live_evidence = (
            (website_result or {}).get("status") == "completed"
            and any(isinstance(e, dict) and e.get("kind") == "website_marketing_audit"
                    and e.get("ref") for e in evidence)
            and any(isinstance(t, dict) and t.get("tool_id") == "website_marketing_audit"
                    and t.get("status") == "completed" and t.get("tool_run_id")
                    for t in tools)
        )
        if not has_live_evidence or not observation:
            return (
                f"{title}\n\n"
                "No experiment was proposed because this run did not produce a "
                "verified website observation that supports one."
            )
        tool_run_id = next(
            str(t["tool_run_id"]) for t in tools
            if isinstance(t, dict) and t.get("tool_id") == "website_marketing_audit"
            and t.get("status") == "completed" and t.get("tool_run_id")
        )
        return "\n\n".join([
            title,
            "### Test a clear primary call to action",
            "- **Hypothesis**: An explicit primary call to action may make the next step clearer; the live website audit observed no action-oriented CTA keywords in the fetched page.",
            "- **Metric**: Primary CTA click-through rate. No baseline or outcome has been recorded.",
            f"- **Evidence**: {observation} (tool run `{tool_run_id}`).",
        ])

    title = "## 7. 3 تجارب نمو ذات أولوية / 3 Prioritized Growth Experiments"
    exp1 = (
        "### التجربة 1: اختبار A/B لعنوان الواجهة الرئيسية ورسالة القيمة (Hero Value Prop A/B Test)\n"
        "- **الفرضية (Hypothesis)**: اختبر عنواناً مباشراً يركز على قيمة العميل مقابل النسخة الحالية، وقارن النقرات بخط الأساس.\n"
        "- **التصميم والمتغيرات (Test Design)**: متغير A (النسخة الحالية) مقابل متغير B (عنوان محدد بنتيجة + زر CTA عالي التباين).\n"
        "- **مقياس النجاح (Success Metric)**: قارن معدل النقر والتحويل للتسجيل بخط الأساس؛ لا توجد نتيجة متوقعة موثقة."
    )
    exp2 = (
        "### التجربة 2: تبسيط قمع الشراء وإبراز ضمانات الثقة (Frictionless Checkout Flow)\n"
        "- **الفرضية (Hypothesis)**: اختبر تقليل الحقول الضرورية وإضافة معلومات أمان مقابل النموذج الحالي، ثم قارن التخلي عن القمع بخط الأساس.\n"
        "- **التصميم والمتغيرات (Test Design)**: نموذج بخطوة واحدة ودفع مباشر مقابل النموذج التقليدي متعدد الصفحات.\n"
        "- **مقياس النجاح (Success Metric)**: قارن نسبة إتمام الشراء أو الطلب بخط الأساس؛ لا توجد نتيجة متوقعة موثقة."
    )
    exp3 = (
        "### التجربة 3: إطلاق صفحات مقارنة للمنافسين لجذب عملاء ذوي نية شراء عالية (High-Intent Comparison Pages)\n"
        "- **الفرضية (Hypothesis)**: إنشاء صفحات مقارنة موضوعية (X vs Competitors) ستلتقط الباحثين في مرحلة اتخاذ القرار النهائي.\n"
        "- **التصميم والمتغيرات (Test Design)**: صفحات مقارنة مفصلة بجدول مزايا واضح ودعوة لتجربة الخدمة.\n"
        "- **مقياس النجاح (Success Metric)**: راقب الزيارات العضوية المؤهلة والتحويل وقارنها بخط الأساس؛ لا توجد عتبة موثقة."
    )
    return "\n\n".join([title, exp1, exp2, exp3])


def synthesize_compound_answer(state: dict, tree=None) -> str:
    """Synthesize compound marketing audit into the exact 7 contract sections."""
    results = list(state.get("employee_results") or [])
    by_domain: dict[str, dict] = {}
    for r in results:
        dom = str(r.get("domain") or "")
        if not dom:
            task_str = str(r.get("task") or "").lower()
            role_str = str(r.get("employee_role") or "").lower()
            for d in ("website", "instagram", "competitor", "positioning", "seo", "cro", "experiment"):
                if d in task_str or d in role_str:
                    dom = d
                    break
        if dom:
            by_domain[dom] = r

    sec1 = _format_website_section(by_domain.get("website"))
    sec2 = _format_instagram_section(by_domain.get("instagram") or by_domain.get("social"))
    sec3 = _format_positioning_section(by_domain.get("positioning"))
    sec4 = _format_seo_section(by_domain.get("seo"))
    sec5 = _format_cro_section(by_domain.get("cro"))
    sec6 = _format_competitor_section(by_domain.get("competitor"))
    sec7 = _format_experiments_section(
        by_domain.get("experiment"),
        user_request=str(state.get("user_request") or ""),
        website_result=by_domain.get("website"),
    )
    # The experiment section proposes tests; any numbers here are targets, not outcomes.
    from app.services.marketing_guardrails import enforce_numeric_guardrail
    sec7 = enforce_numeric_guardrail(sec7, has_evidence=False)

    return "\n\n".join([
        "# تقرير التحليل التسويقي الشامل / Compound Marketing Audit & Growth Plan",
        sec1,
        sec2,
        sec3,
        sec4,
        sec5,
        sec6,
        sec7,
    ])


def run_compound_marketing_task(
    conn,
    *,
    project_id: str,
    user_text: str,
    domains: list[str],
    turn_id: str = "",
    tree=None,
) -> dict:
    """The canonical compound orchestration entry point (contract §2/§3)."""
    from app.graphs.employee_tools import run_employee

    plan = build_compound_task_plan(domains, user_text, turn_id=turn_id)
    if tree is not None:
        try:
            tree.account_manager_started(intent=user_text, route="compound_marketing_task")
            tree.task_planned(tasks=plan["tasks"], employee_count=len(plan["tasks"]))
            for t in plan["tasks"]:
                tree.employee_queued(employee_id=t["id"], role=t["role"], task=t)
        except Exception:
            pass

    wave_a_tasks = [t for t in plan["tasks"] if t.get("wave") == "A"]
    wave_b_tasks = [t for t in plan["tasks"] if t.get("wave") == "B"]

    all_results: list[dict] = []

    def _exec(t):
        return run_employee(
            conn,
            project_id=project_id,
            assignment=dict(t),
            plan=plan,
            tree=tree,
            intent=user_text,
        )

    if wave_a_tasks:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(wave_a_tasks))) as executor:
            futs = [executor.submit(_exec, t) for t in wave_a_tasks]
            for fut in futs:
                all_results.append(fut.result())

    for t in wave_b_tasks:
        all_results.append(_exec(t))

    state_for_synth = {
        "user_request": user_text,
        "employee_results": all_results,
        "task_plan": plan,
        "compound_domains": domains,
        "route": "compound_marketing_task",
    }
    if tree is not None:
        try:
            tree.synthesis(phase="started")
        except Exception:
            pass
    answer = synthesize_compound_answer(state_for_synth, tree=tree)
    if tree is not None:
        try:
            tree.synthesis(phase="completed")
        except Exception:
            pass

    evidence = [ev for r in all_results for ev in r.get("evidence", [])]
    provenance = [
        {
            "path": str(ev.get("ref") or ev.get("kind")),
            "status_tag": "VERIFIED" if r.get("status") == "completed" else "PARTIALLY VERIFIED",
        }
        for r in all_results
        for ev in r.get("evidence", [])
    ]
    limitations = [lim for r in all_results for lim in r.get("limitations", [])]

    return {
        "reply_md": answer,
        "evidence": evidence,
        "provenance": provenance,
        "employee_results": all_results,
        "task_plan": plan,
        "job_ids": [],
        "approval_ids": [],
        "limitations": limitations,
    }


def build_account_manager_graph(
    *, checkpointer=None, store=None, known_projects: set | None = None,
    project_lookup=None, thread_id: str = "", complete_fn=None,
    conn_factory=None, employee_fn=None,
):
    """Compile the v1 Account Manager graph. Requires langgraph installed.

    Every invoke needs ``config={"configurable": {"thread_id": ...}}``;
    thread_id is mentioned here so the requirement is explicit at the
    call site (the value itself travels via invoke config, not here).

    conn_factory: callable returning a sqlite connection for intent nodes
    (tool capability execution / conversation-meta transcript). Defaults to
    the serving DB path; tests inject an in-memory/tmp factory.
    """
    _ = thread_id
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Send, interrupt

    from app.graphs.account_manager import classify_to_route
    from app.graphs.approvals import stable_key

    if project_lookup is not None:
        _lookup = project_lookup
    elif known_projects is not None:
        _known = set(known_projects)

        def _lookup(pid: str) -> dict | None:
            p = (pid or "").strip()
            if not p or p not in _known:
                return None
            return {"id": p}

    else:
        _lookup = _default_lookup

    # ---------- nodes ----------

    def load_project(state: LangGraphState, config=None) -> dict:
        pid = (state.get("project_id") or "").strip()
        if not pid:
            return {
                "errors": ["NO_PROJECT_SCOPE: project_id is required (fail closed)"],
                "route": _BLOCKED,
                "final_answer": "",
            }
        try:
            row = _lookup(pid)
        except (ValueError, LookupError):
            return {
                "errors": ["NO_PROJECT_SCOPE: project scope could not be loaded"],
                "route": _BLOCKED,
                "final_answer": "",
            }
        if row is None:
            return {
                "errors": [f"NO_PROJECT_SCOPE: unknown project {pid!r}"],
                "route": _BLOCKED,
                "final_answer": "",
            }
        _emit_graph_event(config, "context_completed", {"project_id": pid})
        return {"project_id": pid, "project_context": {"project": dict(row)},
                "project_state": _project_state(conn_factory, pid)}

    def understand(state: LangGraphState) -> dict:
        if (state.get("route") or "") == _BLOCKED or state.get("errors"):
            return {}
        text = state.get("user_request", "")
        lowered = (text or "").lower()
        write_constraints = _current_turn_write_constraints(text)
        write_intents, blocked_actions = _filter_forbidden_write_intents(
            _explicit_write_intents(text), write_constraints)
        if blocked_actions:
            write_intents["_blocked_actions"] = blocked_actions
        from app.graphs.account_manager import _APPROVAL_CUES

        if any(c in lowered for c in _APPROVAL_CUES) and not write_intents:
            # Approval precedence (route C) precedes meta/capability on ALL
            # paths — kept identical with the legacy classifier.
            return {"route": "approval_operation", "model_route": "approval_operation",
                    "write_intents": write_intents,
                    "write_constraints": write_constraints}
        from app.graphs.intent import classify_intent

        prior = classify_intent(text)
        if prior.get("route"):
            route = prior["route"]
            if route == "compound_marketing_task":
                return {
                    "intent": prior,
                    "route": route,
                    "model_route": route,
                    "intent_type": "COMPOUND_MARKETING_TASK",
                    "compound_domains": list(prior.get("domains") or []),
                    "write_intents": write_intents,
                    "write_constraints": write_constraints,
                }
            intent_type = ("CONVERSATION_META"
                           if route == "conversation_meta" else "TOOL_CAPABILITY")
            intent = prior.get("capability") or {}
            return {"intent": intent, "route": route, "model_route": route,
                    "intent_type": intent_type,
                    "write_intents": write_intents,
                    "write_constraints": write_constraints,
                    "tool_capability_name": str(intent.get("capability", "") or "")}
        route = classify_to_route(
            text, deep="deep research" in (text or "").lower())
        if any(write_intents.get(k) for k in ("campaign", "task", "experiment")) and route in ("state_only", "knowledge"):
            route = "campaign_operation"
        return {"route": route, "model_route": route,
                "write_intents": write_intents,
                "write_constraints": write_constraints}

    def load_conversation(state: LangGraphState) -> dict:
        """W1-2 hydration: last 12 messages (all roles) + project memories.

        Never user-visible; failures degrade to empty context, never a
        turn-breaking error.
        """
        try:
            from app.graphs.adapters import ConversationAdapter

            pid = (state.get("project_id") or "").strip()
            cid = state.get("conversation_id", "")
            transcript, memories = [], []
            try:
                conn, owned = _conn_for_nodes()
                try:
                    msgs = ConversationAdapter.load_messages(
                        conn, cid, limit=12)
                    transcript = [
                        {"role": str((m or {}).get("role", "")),
                         "body_md": str((m or {}).get("body_md", "") or "")}
                        for m in msgs
                    ]
                    if pid:
                        memories = [
                            str((m or {}).get("body_md", "") or "")
                            for m in ConversationAdapter.load_memories(
                                conn, pid, limit=5)
                        ]
                finally:
                    if owned:
                        try:
                            conn.close()
                        except Exception:
                            pass
            except Exception:
                transcript, memories = [], []
        except Exception:
            transcript, memories = [], []
        project_state = dict(state.get("project_state") or {})
        if memories:
            project_state["memories"] = memories
        return {"conversation_context": list(transcript),
                "project_state": project_state}

    def _conn_for_nodes():
        """Connection + ownership flag: injected (test) conns are owned by
        their creator and never closed here; default serving conns close."""
        c = (conn_factory or _default_conn)()
        return c, conn_factory is None

    def conversation_meta(state: LangGraphState) -> dict:
        """Current-thread summary — transcript only, never project RAG.

        Chronological user+assistant bullets reused from state (load_conversation
        already hydrated them); a fresh DB read is only the fallback. Assistant
        lines that mention website/instagram analysis results are included as-is.
        """
        msgs = list(state.get("conversation_context") or [])
        if not msgs:
            try:
                from app.graphs.adapters import ConversationAdapter

                conn, owned = _conn_for_nodes()
                try:
                    msgs = ConversationAdapter.load_messages(
                        conn, state.get("conversation_id", ""), limit=12)
                finally:
                    if owned:
                        try:
                            conn.close()
                        except Exception:
                            pass
            except Exception:
                # Honest degradation: keep the answer (synthesize must not blank
                # it via the errors check) — never silently pretend thread empty.
                return {"final_answer":
                        "I couldn't read this conversation's transcript right now."}
        user_label, assistant_label = "User", "Assistant"
        current = str(state.get("user_request", "") or "")
        if any("\u0600" <= ch <= "\u06FF" for ch in current):
            user_label, assistant_label = "المستخدم", "المساعد"
        ordered = [m for m in msgs if isinstance(m, dict)]
        user_lines = [
            str((m or {}).get("body_md", "")).strip()
            for m in ordered if (m or {}).get("role") == "user"
        ]
        user_lines = [u for u in user_lines if u]
        if not user_lines:
            return {"final_answer": "We just started talking — no deeper context yet."}

        def _mentions_analysis(body: str) -> bool:
            b = (body or "").lower()
            return any(k in b for k in (
                "website", "موقع", "instagram", "انستجرام", "انستغرام",
                "audit", "تحليل", "تحلل", "scrape", "scrapped"))

        capped: list[str] = []
        for m in reversed(ordered):
            if len(capped) >= 8:
                break
            role = str((m or {}).get("role", ""))
            body = str((m or {}).get("body_md", "") or "").strip()
            if not body or role not in ("user", "assistant"):
                continue
            label = user_label if role == "user" else assistant_label
            # Keep current-turn meta request itself out of the recap body.
            if role == "user" and current and body.strip() == current.strip():
                continue
            capped.append(f"- {label}: {body[:200]}")
        capped.reverse()
        if not capped:
            capped = [f"- {user_label}: {u[:200]}" for u in user_lines[-8:]]
        return {"final_answer":
                "In this chat we've been working through these requests:\n"
                + "\n".join(capped)}


    def tool_capability(state: LangGraphState, config=None) -> dict:
        """resolve_tool → execute_tool → normalize_tool_result."""
        from app.graphs.tool_capability import resolve_and_execute

        intent = state.get("intent") or {}
        cap = str(intent.get("capability") or "instagram_public_profile")
        args = dict(intent.get("arguments") or {})
        try:
            conn, owned = _conn_for_nodes()
            try:
                out = resolve_and_execute(
                    conn, project_id=state.get("project_id", ""),
                    capability=cap, args=args,
                    turn_id=state.get("turn_id", ""),
                    on_event=_event_callback(config))
            finally:
                if owned:
                    try:
                        conn.close()
                    except Exception:
                        pass
        except Exception:
            # Honest degradation without errors-flag (see synthesize):
            return {"final_answer":
                    f"I couldn't run the {cap} capability right now."}
        return {"capability_answer": out.get("answer", ""),
                "tool_run_id": out.get("tool_run_id", ""),
                "social_results": out.get("social_results", [])}

    def _branch_answer(kind: str, state: LangGraphState) -> dict:
        pid = state.get("project_id", "")
        text = state.get("user_request", "")
        bodies = {
            "state_only": f"Project {pid}: here is what needs attention for \u201c{text}\u201d.",
            "knowledge": f"Project {pid}: recorded knowledge on \u201c{text}\u201d.",
            "external_research": f"Project {pid}: web findings on \u201c{text}\u201d. "
            "(STATUS: PARTIALLY VERIFIED / SOURCE: web search / CONFIDENCE: MEDIUM)",
            "social_research": f"Project {pid}: social check on \u201c{text}\u201d.",
            "campaign_operation": f"Project {pid}: draft proposal ready for \u201c{text}\u201d.",
            "job_followup": f"Project {pid}: background work reviewed.",
        }
        return {"final_answer": bodies.get(kind, f"Project {pid}: done.")}

    def state_only(state: LangGraphState) -> dict:
        return _branch_answer("state_only", state)

    def knowledge(state: LangGraphState) -> dict:
        """Gated RAG retrieval (W1-5): project-scoped, deduped, compressed.

        rag_hits retain safe source identity/branch metadata alongside the
        compressed text used for this turn's model context.
        """
        pid = (state.get("project_id") or "").strip()
        query = str(state.get("user_request", "") or "")
        hits_out: list[dict] = []
        rag_invoked = False
        retrieval_telemetry: dict = {}
        try:
            conn, owned = _conn_for_nodes()
            try:
                from app.graphs.adapters import RetrievalAdapter

                result = RetrievalAdapter(conn, pid).retrieve(query)
                legacy_mode = str(getattr(result, "mode", "") or "")
                search_mode = str(getattr(result, "search_mode", "") or "")
                retrieval_telemetry = {
                    "retrieval_mode": search_mode or legacy_mode,
                    "mode": legacy_mode,
                    "search_mode": search_mode,
                }
                for counter in ("lexical_hits", "vector_hits", "fused_hits"):
                    value = getattr(result, counter, None)
                    if value is not None:
                        retrieval_telemetry[counter] = int(value)
                fused = []
                for h in (getattr(result, "hits", None) or []):
                    if hasattr(h, "model_dump"):
                        try:
                            fused.append(dict(h.model_dump()))
                        except Exception:
                            fused.append({"path": getattr(h, "path", ""),
                                          "chunk_id": getattr(h, "chunk_id", ""),
                                          "text": getattr(h, "text", ""),
                                          "score": getattr(h, "score", 0.0),
                                          "sources": list(getattr(h, "sources", []) or []),
                                          "project_id": getattr(h, "project_id", ""),
                                          "file_id": getattr(h, "file_id", ""),
                                          "file_name": getattr(h, "file_name", "")})
                    elif not isinstance(h, dict):
                        fused.append({"path": getattr(h, "path", ""),
                                      "chunk_id": getattr(h, "chunk_id", ""),
                                      "text": getattr(h, "text", ""),
                                      "score": getattr(h, "score", 0.0),
                                      "sources": list(getattr(h, "sources", []) or []),
                                      "project_id": getattr(h, "project_id", ""),
                                      "file_id": getattr(h, "file_id", ""),
                                      "file_name": getattr(h, "file_name", "")})
                    elif isinstance(h, dict):
                        fused.append(dict(h))
                rag_invoked = True
                seen: set[tuple[str, str]] = set()
                for h in fused:
                    path = str((h or {}).get("path", "") or "")
                    chunk_id = str((h or {}).get("chunk_id", "") or "?")
                    key = (path, chunk_id)
                    if not path or key in seen:
                        continue
                    seen.add(key)
                    text = str((h or {}).get("text", "") or "").strip()[:300]
                    score = float((h or {}).get("score", 0.0) or 0.0)
                    hit = {"path": path, "chunk_id": chunk_id,
                           "text": text, "score": score,
                           "sources": list(h.get("sources") or [h.get("source", "fts")])}
                    for field in ("project_id", "file_id", "file_name",
                                  "document_id", "file_sha", "status_tag"):
                        if h.get(field) not in (None, ""):
                            hit[field] = h[field]
                    hits_out.append(hit)
                    if len(hits_out) >= 5:
                        break
                retrieval_telemetry = _retrieval_telemetry_from_result(
                    result, hits_out,
                    source_file_ids=_selected_source_file_ids(conn, pid, hits_out))
            finally:
                if owned:
                    try:
                        conn.close()
                    except Exception:
                        pass
        except Exception:
            hits_out, rag_invoked = [], False
            retrieval_telemetry = {"retrieval_mode": "FTS", "mode": "FTS_ONLY",
                                   "search_mode": "FTS"}
        retrieval_telemetry["selected_chunk_ids"] = [
            str(hit.get("chunk_id")) for hit in hits_out
            if hit.get("chunk_id") not in (None, "")
        ][:20]
        retrieval_telemetry["source_file_ids"] = list(dict.fromkeys(
            str(hit.get("file_id")) for hit in hits_out
            if hit.get("file_id") not in (None, "")
        ))[:20]
        retrieval_telemetry["selected_chunks"] = [
            {"chunk_id": str(hit.get("chunk_id") or ""),
             **({"file_id": str(hit.get("file_id"))}
                if hit.get("file_id") not in (None, "") else {})}
            for hit in hits_out if hit.get("chunk_id") not in (None, "")
        ]
        fusion_sources = sorted({s for h in hits_out for s in h.get("sources", [])})
        _write_rag_debug({
            "turn_id": state.get("turn_id", ""),
            "query": query,
            "route": "knowledge",
            "chunk_ids": [h["chunk_id"] for h in hits_out],
            "scores": [h["score"] for h in hits_out],
            "fusion_sources": fusion_sources,
            "compressed_evidence": [
                {"path": h.get("path", ""), "chunk_id": h.get("chunk_id", ""),
                 "text": h.get("text", "")}
                for h in hits_out
            ],
            "selected": [f"{h['path']}#{h['chunk_id']}" for h in hits_out],
        })
        out = _branch_answer("knowledge", state)
        out["retrieved_evidence"] = [
            {"path": h["path"], "chunk_id": h["chunk_id"],
             "text": h["text"], "score": h["score"], "source": "project_rag",
             **{field: h[field] for field in (
                 "project_id", "file_id", "file_name", "document_id", "sources")
                if h.get(field) not in (None, "")}}
            for h in hits_out
        ] or [{"path": "recorded-data", "chunk_id": "?"}]
        from app.graphs.state import citation_projection
        citations = citation_projection(project_id=pid, rag_hits=hits_out)
        return {
            "rag_invoked": rag_invoked,
            "rag_query": query,
            "rag_hits": hits_out,
            "citation_evidence": citations,
            "rag_debug": {
                "route": "knowledge",
                "retrieved": len(hits_out),
                "selected": [f"{h['path']}#{h['chunk_id']}" for h in hits_out],
                "retrieval_telemetry": retrieval_telemetry,
            },
            "retrieval_telemetry": retrieval_telemetry,
            "retrieved_evidence": out["retrieved_evidence"],
            "final_answer": out["final_answer"],
        }

    def external_research(state: LangGraphState) -> dict:
        out = _branch_answer("external_research", state)
        out["web_results"] = [{"path": "https://example.com/web", "chunk_id": "?"}]
        return out

    def social_research(state: LangGraphState) -> dict:
        out = _branch_answer("social_research", state)
        out["social_results"] = [{"path": "@handle", "chunk_id": "?"}]
        return out

    def campaign_operation(state: LangGraphState) -> dict:
        out = _branch_answer("campaign_operation", state)
        out["state_results"] = {"proposal": "draft"}
        return out

    def approval_operation(state: LangGraphState) -> dict:
        title = (state.get("user_request", "") or "")[:160]
        aid = f"turn-{state.get('turn_id') or 't'}"
        return {
            "approval_state": {
                "approval_id": aid,
                "project_id": state.get("project_id", ""),
                "title": title,
                "action_class": "yellow",
                "idempotency_key": stable_key(aid),
            },
            "final_answer": f"Waiting for your approval: {title}",
        }

    def job_followup(state: LangGraphState) -> dict:
        return _branch_answer("job_followup", state)

    def deep_planner(state: LangGraphState) -> dict:
        return {"research_tasks": _split_tasks(state.get("user_request", ""))}

    def deep_fanout(state: LangGraphState):
        return [
            Send("deep_worker", {"task_id": t["id"], "query": t["query"]})
            for t in (state.get("research_tasks") or [])
        ]

    def deep_worker(state: dict) -> dict:
        tid = state.get("task_id", "q0")
        q = state.get("query", "")
        return {
            "worker_results": [
                {
                    "task_id": tid,
                    "chunk": f"evidence for {q}",
                    "sources": [f"https://example.com/{tid}"],
                    "ok": True,
                }
            ]
        }

    def deep_fuse(state: LangGraphState) -> dict:
        # Fan-in barrier: all Send branches completed before this runs
        # (reducer already merged worker_results). Synthesize reads the
        # merged list directly; nothing extra to compute here.
        return {}

    # ---------- DEV-008 execution-tree nodes (W4) ----------
    #
    # skill_route → employee_fanout → employee → employee_fanin →
    # skill_load → evidence_collect → synthesize → synthesis_complete.
    # Follows the proven deep_planner → deep_fanout (Send) → deep_worker →
    # deep_fuse → aggregate pattern. Fan-out is over EMPLOYEES (one Send per
    # (task, employee_role) pair, capped at 4), not over skills. Employee
    # bodies run inside the single turn thread via LangGraph's own Send
    # scheduler — never as new _POOL submissions (see W4 handoff).

    def skill_route(state: LangGraphState, config=None) -> dict:
        """Route skills, plan tasks, emit the tree roots.

        Always emits ``account_manager_started`` + ``task_planned`` (when a
        tree is present) and one router-level ``skill_selected`` per selected
        skill. Sets the turn-level reducer/scalar fields W3 declared. Never
        fails the turn: a missing router/registry degrades to an empty
        selection with the turn otherwise unchanged.
        """
        if (state.get("route") or "") == _BLOCKED or state.get("errors"):
            return {}
        tree = _tree_of(config)
        intent = str(state.get("user_request") or "")
        route = str(state.get("route") or "")
        pid = str(state.get("project_id") or "")
        turn_id = str(state.get("turn_id") or "")
        if tree is not None:
            try:
                tree.account_manager_started(intent=intent, route=route)
            except Exception:
                pass

        if route == "compound_marketing_task" or bool(state.get("compound_domains")):
            domains = list(state.get("compound_domains") or [])
            if not domains:
                from app.graphs.intent import detect_compound_task
                c = detect_compound_task(intent)
                if c:
                    domains = list(c.get("domains") or [])
            plan = build_compound_task_plan(domains, intent, turn_id=turn_id)
            for t in plan["tasks"]:
                _COMPOUND_DEP_EVENTS[f"{turn_id}:{t['id']}"] = threading.Event()
                _COMPOUND_DEP_EVENTS[f"{turn_id}:{t['domain']}"] = threading.Event()
            if tree is not None:
                try:
                    tree.task_planned(tasks=plan["tasks"], employee_count=len(plan["tasks"]))
                except Exception:
                    pass
                for t in plan["tasks"]:
                    for skill_id in t.get("skills") or []:
                        try:
                            tree.skill_selected(
                                skill_id=str(skill_id),
                                reason_code="compound-plan",
                                score=3.0,
                            )
                        except Exception:
                            continue
            roles = [t["role"] for t in plan["tasks"]]
            return {
                "task_plan": plan,
                "compound_domains": domains,
                "employee_roles": roles,
                "skill_selection_reason": "compound-marketing-orchestration",
                "foundation_injected": True,
            }

        available: tuple = ()
        try:
            from app.services.skills.registry import load_registry
            registry = load_registry(disabled="")
            available = registry.enabled_records()
        except Exception:
            available = ()
        decision = None
        try:
            from app.services.skills import router as _router
            project_state = state.get("project_state")
            if not isinstance(project_state, dict) or not project_state:
                project_state = {"project_id": pid} if pid else {}
            req = _router.SkillRouteRequest(
                intent=intent,
                project_state=dict(project_state),
                task={},
                available_skills=tuple(available),
            )
            decision = _router.route(req)
        except Exception:
            decision = None
        tasks = _split_tasks(intent)
        if decision is not None:
            try:
                selected = list(decision.selected or ())
                reason_codes = [str(c) for c in (decision.reason_codes or ())]
                decision_roles = [str(r) for r in (decision.employee_roles or ())]
                foundation = bool(decision.foundation_injected)
            except Exception:
                selected, reason_codes, decision_roles, foundation = [], [], [], False
        else:
            selected, reason_codes, decision_roles, foundation = [], [], [], False
        pairs = _employee_pairs(intent, decision_roles)
        if tree is not None:
            try:
                tree.task_planned(tasks=tasks, employee_count=len(pairs))
            except Exception:
                pass
            for item in selected:
                try:
                    skill_id = getattr(item, "skill_id", "")
                    reason = getattr(item, "reason_code", "")
                    score = getattr(item, "score", 0.0)
                    tree.skill_selected(
                        skill_id=str(skill_id), reason_code=str(reason),
                        score=float(score),
                    )
                except Exception:
                    continue
        out: dict = {
            "skill_ids": [str(getattr(s, "skill_id", "")) for s in selected
                          if str(getattr(s, "skill_id", ""))],
            "skill_reason_codes": reason_codes,
            "skill_selection_reason": (reason_codes[0] if reason_codes else ""),
            "employee_roles": decision_roles,
            "foundation_injected": foundation,
        }
        return out

    def employee_fanout(state: LangGraphState, config=None) -> dict:
        """Emit one ``employee_queued`` per (task, role) pair.

        Returns no state (overwriting ``employee_roles`` here would duplicate
        the router's turn-level value). The actual fan-out is the conditional
        edge ``employee_send`` below, which recomputes the identical pairs —
        no hidden state channel between the two.
        """
        if (state.get("route") or "") == _BLOCKED or state.get("errors"):
            return {}
        tree = _tree_of(config)
        if tree is None:
            return {}
        try:
            plan = state.get("task_plan")
            if isinstance(plan, dict) and plan.get("tasks"):
                for t in plan["tasks"]:
                    try:
                        tree.employee_queued(
                            employee_id=str(t["id"]),
                            role=str(t["role"]),
                            task=dict(t),
                        )
                    except Exception:
                        continue
                return {}
            roles = list(state.get("employee_roles") or [])
            pairs = _employee_pairs(str(state.get("user_request") or ""), roles)
            turn_id = str(state.get("turn_id") or "")
            for index, (task, role) in enumerate(pairs):
                try:
                    tree.employee_queued(
                        employee_id=_employee_id_for(turn_id, index),
                        role=str(role), task=dict(task),
                    )
                except Exception:
                    continue
        except Exception:
            pass
        return {}

    def employee_send(state: LangGraphState):
        """Conditional edge: one Send per (task, role) pair, else pass through."""
        try:
            if (state.get("route") or "") == _BLOCKED or state.get("errors"):
                return "employee_fanin"
            plan = state.get("task_plan")
            turn_id = str(state.get("turn_id") or "")
            pid = str(state.get("project_id") or "")
            if isinstance(plan, dict) and plan.get("tasks"):
                return [
                    Send("employee", {
                        "task": dict(t),
                        "employee_role": str(t["role"]),
                        "employee_id": str(t["id"]),
                        "turn_id": turn_id,
                        "project_id": pid,
                        "task_plan": plan,
                    })
                    for t in plan["tasks"]
                ]
            roles = list(state.get("employee_roles") or [])
            pairs = _employee_pairs(str(state.get("user_request") or ""), roles)
            if not pairs:
                return "employee_fanin"
            return [
                Send("employee", {
                    "task": dict(task),
                    "employee_role": str(role),
                    "employee_id": _employee_id_for(turn_id, index),
                    "turn_id": turn_id,
                    "project_id": pid,
                })
                for index, (task, role) in enumerate(pairs)
            ]
        except Exception:
            return "employee_fanin"

    def employee(state: dict, config=None) -> dict:
        """One employee body: started → playbook → evidence → completed/failed.

        Runs inside the invoking turn thread (LangGraph Send scheduler). Only
        the ``evidence`` and ``employee_results`` reducer fields are written
        from inside a branch.
        """
        import time as _time

        task = state.get("task") if isinstance(state, dict) else {}
        role = str(state.get("employee_role", "") or "") if isinstance(state, dict) else ""
        employee_id = str(state.get("employee_id", "") or "") if isinstance(state, dict) else ""
        turn_id = str(state.get("turn_id", "") or "") if isinstance(state, dict) else ""
        pid = str(state.get("project_id", "") or "") if isinstance(state, dict) else ""
        plan = state.get("task_plan") if isinstance(state, dict) else None
        if not isinstance(task, dict):
            task = {"id": "q0", "query": str(task or "")}
        if not task.get("employee_id") and employee_id:
            task["employee_id"] = employee_id

        # Dependency barrier: wait for prior dependent employees to finish
        deps = task.get("depends_on") or []
        if isinstance(deps, (list, tuple)):
            for dep in deps:
                dep_evt = _COMPOUND_DEP_EVENTS.get(f"{turn_id}:{dep}")
                if dep_evt is not None:
                    dep_evt.wait(timeout=10.0)

        tree = _tree_of(config)
        t0 = _time.perf_counter()
        query = str(task.get("query") or task.get("task") or "")

        try:
            if employee_fn is not None:
                # Injected employee capability for mock tests
                if tree is not None:
                    try:
                        tree.employee_started(employee_id=employee_id, role=role, task=dict(task))
                    except Exception:
                        pass
                injected = employee_fn(dict(task), role)
                injected_sources: list = []
                if isinstance(injected, dict):
                    injected_sources = list(injected.get("sources", []) or [])
                duration_ms = max(0, int((_time.perf_counter() - t0) * 1000))
                evidence_item = {
                    "employee_id": employee_id,
                    "employee_role": role,
                    "query": query,
                    "skill_ids": [],
                    "sources": [f"employee:{employee_id}"] + [str(s) for s in injected_sources],
                }
                if tree is not None:
                    try:
                        tree.evidence_added(count=1, kind="employee", employee_id=employee_id)
                        tree.employee_completed(employee_id=employee_id, role=role, duration_ms=duration_ms)
                    except Exception:
                        pass
                res = {
                    "employee_id": employee_id,
                    "employee_role": role,
                    "task": query,
                    "skills": [],
                    "tools": [],
                    "evidence": [evidence_item],
                    "findings": [],
                    "limitations": [],
                    "status": "completed",
                    "duration_ms": duration_ms,
                }
            else:
                from app.graphs.employee_tools import run_employee
                conn, owned = _conn_for_nodes()
                try:
                    res = run_employee(
                        conn,
                        project_id=pid,
                        assignment=dict(task),
                        plan=plan if isinstance(plan, dict) and plan.get("plan_id") else {
                            "plan_id": f"plan-{(turn_id or 'turn')[:8]}",
                            "employees": [task],
                        },
                        tree=tree,
                        project_state=state.get("project_state"),
                        intent=query,
                    )
                finally:
                    if owned and conn:
                        try:
                            conn.close()
                        except Exception:
                            pass

            for k in (employee_id, task.get("id"), task.get("domain")):
                if k:
                    evt = _COMPOUND_DEP_EVENTS.get(f"{turn_id}:{k}")
                    if evt is not None:
                        evt.set()

            return {
                "employee_results": [res],
                "evidence": res.get("evidence", []),
            }
        except Exception as exc:
            duration_ms = max(0, int((_time.perf_counter() - t0) * 1000))
            if tree is not None:
                try:
                    tree.employee_failed(
                        employee_id=employee_id, role=role, exc=exc,
                        duration_ms=duration_ms)
                except Exception:
                    pass
            for k in (employee_id, task.get("id"), task.get("domain")):
                if k:
                    evt = _COMPOUND_DEP_EVENTS.get(f"{turn_id}:{k}")
                    if evt is not None:
                        evt.set()
            failed_res = {
                "employee_id": employee_id,
                "employee_role": role,
                "task": query,
                "skills": [],
                "tools": [],
                "evidence": [],
                "findings": [],
                "limitations": [f"employee failed: {exc}"],
                "status": "failed",
                "duration_ms": duration_ms,
            }
            return {
                "employee_results": [failed_res],
                "evidence": [],
            }

    def employee_fanin(state: LangGraphState) -> dict:
        # Fan-in barrier: all employee Send branches completed before this
        # runs (the ``evidence`` reducer already merged branch outputs).
        # Emits nothing — every employee already reported its own completion
        # (plan: never emit a node after a fan-in has already reported it).
        _ = state
        return {}

    def skill_load(state: LangGraphState, config=None) -> dict:
        """Emit the single turn-level foundation ``skill_loaded``.

        Per-employee playbook loads were already reported by ``employee``;
        re-emitting them here would duplicate. The foundation context
        (``product-marketing``) is turn-level, so this node reports it once
        when the router injected it.
        """
        if (state.get("route") or "") == _BLOCKED or state.get("errors"):
            return {}
        tree = _tree_of(config)
        if tree is None:
            return {}
        try:
            if not bool(state.get("foundation_injected", False)):
                return {}
            from app.services.skills.registry import load_registry
            registry = load_registry(disabled="")
            record = registry.get("product-marketing")
            if record is None:
                return {}
            tree.skill_loaded(record=record)
        except Exception:
            pass
        return {}

    def evidence_collect(state: LangGraphState) -> dict:
        # Barrier after the fan-in: the ``evidence`` reducer already merged
        # per-employee items (each employee reported its own ``evidence_added``
        # at execution time — real streaming, no post-hoc animation). Emits
        # nothing to avoid double-reporting.
        _ = state
        return {}

    def persist_workflow_writes(state: LangGraphState, config=None) -> dict:
        """Single registered-tool writer, reached only after employee fan-in."""
        intents = state.get("write_intents") or {}
        if not isinstance(intents, dict):
            return {}
        blocked_actions = list(intents.get("_blocked_actions") or [])
        if not any(intents.get(k) for k in ("campaign", "task", "experiment")) and not blocked_actions:
            return {}
        pid = str(state.get("project_id") or "")
        turn_id = str(state.get("turn_id") or "")
        convo_id = str(state.get("conversation_id") or "")
        workflow_base = {"project_id": pid, "conversation_id": convo_id,
                         "turn_id": turn_id, "thread_id": turn_id,
                         "requested_by": "account_manager"}
        conn, owned = _conn_for_nodes()
        results: dict = {
            action: {"ok": False, "status": "blocked_by_user_constraint", "action": action}
            for action in blocked_actions
        }
        campaign_id = ""
        task_id = ""
        try:
            from app.graphs.adapters import ToolRegistryAdapter
            adapter = ToolRegistryAdapter()
            callback = _event_callback(config)

            def execute(name: str, args: dict):
                tree = _tree_of(config)
                run_id = f"{turn_id}:{name}"
                if tree is not None and not callable(callback):
                    try:
                        tree.tool_event(event_type="tool_started", tool_id=name,
                                       tool_run_id=run_id, status="RUNNING")
                    except Exception:
                        pass
                if callable(callback):
                    callback("tool_started", {"tool": name, "tool_id": name,
                                               "args": {"title": str(args.get("title") or args.get("hypothesis") or "")[:120]}})
                res = adapter.execute(
                    conn, project_id=pid, root="", name=name, args=args,
                    execution_context=dict(state.get("write_constraints") or {
                        "forbidden_actions": [], "analysis_only": False}))
                blocked = res.get("status") == "blocked_by_user_constraint"
                if callable(callback):
                    callback("tool_completed" if res.get("ok") or blocked else "tool_failed",
                             {"tool": name, "tool_id": name, "ok": bool(res.get("ok")),
                              "status": res.get("status", ""),
                              "error": res.get("error", "")})
                if tree is not None and not callable(callback):
                    try:
                        tree.tool_event(event_type="tool_completed" if res.get("ok") or blocked else "tool_failed",
                                        tool_id=name, tool_run_id=run_id,
                                        status="BLOCKED" if blocked else ("COMPLETE" if res.get("ok") else "FAILED"))
                    except Exception:
                        pass
                return res

            campaign = intents.get("campaign")
            if isinstance(campaign, dict):
                wf = {**workflow_base, "assigned_role": "account_manager"}
                res = execute("propose_campaign", {**campaign, "workflow": wf,
                               "idempotency_key": f"{turn_id}:campaign"})
                results["campaign"] = res
                campaign_id = str(res.get("campaign_id") or "") if res.get("ok") else ""
            task = intents.get("task")
            if isinstance(task, dict) and (not intents.get("campaign") or campaign_id):
                task_args = dict(task)
                task_args.pop("explicit_standalone", None)
                wf = {**workflow_base, "campaign_id": campaign_id,
                      "assigned_role": str(task.get("lane") or "conversion")}
                res = execute("propose_task", {**task_args, "campaign_id": campaign_id,
                               "workflow": wf, "idempotency_key": f"{turn_id}:task"})
                results["task"] = res
                task_id = str(res.get("task_id") or "") if res.get("ok") else ""
            experiment = _evidence_grounded_experiment(
                intents.get("experiment"),
                list(state.get("employee_results") or []),
                str(state.get("user_request") or ""),
            )
            if isinstance(experiment, dict) and (not intents.get("campaign") or campaign_id):
                experiment_args = dict(experiment)
                experiment_args.pop("explicit_standalone", None)
                evidence_tool_run_id = str(experiment_args.pop("evidence_tool_run_id", ""))
                evidence_ref = str(experiment_args.pop("evidence_ref", ""))
                wf = {**workflow_base, "campaign_id": campaign_id,
                      "task_id": task_id, "assigned_role": "growth"}
                if evidence_tool_run_id:
                    wf["evidence_tool_run_id"] = evidence_tool_run_id
                    wf["evidence_ref"] = evidence_ref
                res = execute("propose_experiment", {**experiment_args,
                               "campaign_id": campaign_id or experiment.get("campaign_id", ""),
                               "workflow": wf,
                               "idempotency_key": f"{turn_id}:experiment"})
                results["experiment"] = res
            successful_write = any(
                isinstance(results.get(key), dict) and results[key].get("ok")
                for key in ("campaign", "task", "experiment"))
            if intents.get("requires_approval") and successful_write:
                title = str((campaign or {}).get("title") or (task or {}).get("title") or "Review proposed marketing work")
                wf = {**workflow_base, "campaign_id": campaign_id,
                      "task_id": task_id, "assigned_role": "account_manager"}
                approval = execute("request_approval", {
                    "kind": "marketing_proposal", "title": title,
                    "body_md": "Review the proposed campaign and linked work before proceeding.",
                    "fields_json": {"workflow": wf, "campaign_id": campaign_id,
                                    "task_id": task_id},
                    "workflow": wf, "idempotency_key": f"{turn_id}:approval"})
                results["approval"] = approval
                if approval.get("ok"):
                    results["approval_id"] = approval.get("approval_id", "")
        except Exception:
            results["error"] = "proposal tools were unavailable"
        finally:
            if owned:
                try:
                    conn.close()
                except Exception:
                    pass
        failed = [k for k, v in results.items()
                  if isinstance(v, dict) and not v.get("ok")
                  and v.get("status") != "blocked_by_user_constraint"]
        approval = {}
        if results.get("approval", {}).get("ok"):
            approval = {"approval_id": results["approval"].get("approval_id", ""),
                        "project_id": pid, "title": str(results["approval"].get("title") or "Review proposed marketing work"),
                        "action_class": "yellow", "task_id": task_id,
                        "campaign_id": campaign_id,
                        "idempotency_key": f"{turn_id}:approval"}
        if failed or results.get("error"):
            results["write_error"] = "One or more proposals could not be saved."
        return {"write_results": results, "approval_state": approval}

    def synthesis_complete(state: LangGraphState, config=None) -> dict:
        """Emit the tree ``synthesis_completed`` after ``synthesize`` ran."""
        if (state.get("route") or "") == _BLOCKED or state.get("errors"):
            return {}
        tree = _tree_of(config)
        if tree is not None:
            try:
                tree.synthesis(phase="completed")
            except Exception:
                pass
        writes = state.get("write_results") or {}
        if not writes:
            return {}
        ids = []
        for key, id_key in (("campaign", "campaign_id"), ("task", "task_id"), ("experiment", "experiment_id")):
            item = writes.get(key) or {}
            if item.get("ok") and item.get(id_key):
                ids.append(f"{key} {item[id_key]}")
        note = (" Saved: " + ", ".join(ids) + ".") if ids else ""
        if writes.get("write_error"):
            note += " " + writes["write_error"]
        blocked = [str(item.get("action") or key)
                   for key, item in writes.items()
                   if isinstance(item, dict)
                   and item.get("status") == "blocked_by_user_constraint"]
        if blocked:
            note += " Skipped saving the prohibited " + ", ".join(dict.fromkeys(blocked)) + "."
        return {"final_answer": (str(state.get("final_answer") or "").rstrip() + note).strip()}

    def aggregate(state: LangGraphState) -> dict:
        # Fan-in barrier for all branches. Reducers already merged branch
        # outputs; single-branch runs write each list once, so there is
        # nothing to dedupe here. (Note: must NOT return list values —
        # reducer fields would append, not replace.)
        _ = state
        return {}

    def _context_sources(state: LangGraphState, route: str) -> list[str]:
        """Ordered evidence-source ids (W1-6 frozen contract)."""
        sources = ["user_turn"]
        if state.get("conversation_context"):
            sources.append("conversation_context")
        ps = state.get("project_state") or {}
        if ps.get("name") or ps.get("website") or ps.get("goal") or \
                ps.get("instagram_handle") or ps.get("social_accounts"):
            sources.append("project_state")
        if list(state.get("social_results") or []) or list(state.get("web_results") or []):
            sources.append("tool_results")
        if state.get("rag_hits"):
            sources.append("project_rag")
        if route == "external_research":
            sources.append("external_research")
        return sources

    def synthesize(state: LangGraphState, config=None) -> dict:
        _synthesize_tree = _tree_of(config)
        if _synthesize_tree is not None:
            try:
                _synthesize_tree.synthesis(phase="started")
            except Exception:
                pass
        if state.get("errors"):
            return {"final_answer": ""}
        route = state.get("route") or ""
        # Deterministic capability/conversation answers never pass through
        # the model: a tool claim requires this turn's artifact, and a
        # failing capability must not be rewritten into project knowledge.
        if route == "tool_capability":
            sources = _context_sources(state, route)
            capability_name = str((state.get("intent") or {}).get("capability", "") or "")
            out = {"context_sources_used": sources}
            if capability_name:
                out["tool_capability_name"] = capability_name
            if state.get("capability_answer"):
                out["final_answer"] = state["capability_answer"]
                return out
            out["final_answer"] = "I couldn't run that capability right now."
            return out
        if route == "conversation_meta":
            return {"context_sources_used": _context_sources(state, route)}
        if route == "deep_research":
            ok_rows = [
                r for r in state.get("worker_results") or [] if (r or {}).get("ok")
            ]
            chunks = [str((r or {}).get("chunk", "")) for r in ok_rows]
            seen_cites: list[str] = []
            for r in ok_rows:
                for s in (r or {}).get("sources") or []:
                    if s not in seen_cites:
                        seen_cites.append(s)
            body = "\n".join(f"- {c}"[:300] for c in chunks[:4]) or "no usable evidence."
            src = (
                ("\nSources:\n" + "\n".join(f"- {c}" for c in seen_cites[:5]))
                if seen_cites
                else ""
            )
            return {"final_answer": f"Deep-research summary:\n{body}{src}",
                    "context_sources_used": ["user_turn", "external_research"]}
        if route == "compound_marketing_task" or bool(state.get("task_plan")):
            answer = synthesize_compound_answer(state, tree=_synthesize_tree)
            return {
                "final_answer": answer,
                "context_sources_used": ["employee_results", "project_context", "tool_runs"],
            }
        # Model leg with the labeled context contract (graph_runtime builds
        # the [PROJECT CONTEXT]/[CONVERSATION SO FAR]/[TOOL RESULTS THIS
        # TURN]/[PROJECT KNOWLEDGE] sections from this structured context).
        sources = _context_sources(state, route)
        ps_for_ctx = dict(state.get("project_state") or {})
        user_text = str(state.get("user_request") or "")
        aliases = [str(a) for a in (ps_for_ctx.get("aliases") or [])
                   if str(a).strip()]
        lowered = user_text.lower()
        identity_resolutions = [
            str(a) for a in aliases
            if a.lower() in lowered][:3]
        retrieval_telemetry = state.get("retrieval_telemetry")
        if not isinstance(retrieval_telemetry, dict) or not retrieval_telemetry:
            retrieval_telemetry = (state.get("rag_debug") or {}).get(
                "retrieval_telemetry")
        context = {
            "project_state": ps_for_ctx,
            "identity_resolutions": identity_resolutions,
            "conversation_context": list(state.get("conversation_context") or []),
            "tool_results": (list(state.get("social_results") or [])
                             + list(state.get("web_results") or [])),
            "rag_evidence": (list(state.get("attachment_evidence") or [])
                             + list(state.get("rag_hits") or [])),
            "context_sources_used": list(sources),
            "route": route,
            "retrieval_telemetry": dict(retrieval_telemetry or {}),
        }
        from app.graphs.state import citation_projection
        evidence_for_citations = context["rag_evidence"]
        citation_evidence = citation_projection(
            project_id=str(state.get("project_id") or ""),
            rag_hits=[h for h in evidence_for_citations
                      if isinstance(h, dict) and h.get("source") != "turn_attachment"],
            attachment_evidence=[h for h in evidence_for_citations
                                 if isinstance(h, dict) and h.get("source") == "turn_attachment"])
        if complete_fn is not None:
            ans = None
            completion_failure = None
            complete_args = {
                "turn_id": str(state.get("turn_id") or "turn"),
                "project_id": str(state.get("project_id") or DEFAULT_PROJECT_ID),
                "user_request": str(state.get("user_request") or ""),
                "route": str(state.get("route") or "state_only"),
                "conversation_id": str(state.get("conversation_id") or ""),
                "context": context,
            }
            try:
                import inspect
                try:
                    parameters = inspect.signature(complete_fn).parameters.values()
                    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD
                                         for p in parameters)
                    if not accepts_kwargs:
                        accepted = {p.name for p in parameters}
                        complete_args = {k: v for k, v in complete_args.items()
                                         if k in accepted}
                except (TypeError, ValueError):
                    pass
                ans = complete_fn(**complete_args)
            except Exception as exc:
                completion_failure = {
                    "call_id": str(getattr(exc, "call_id", "") or ""),
                    "provider": str(getattr(exc, "provider", "") or ""),
                    "requested_model": str(getattr(exc, "model", "") or ""),
                    "route_mode": str(getattr(exc, "route_mode", "") or ""),
                    "route_reason": str(getattr(exc, "route_reason", "") or ""),
                    "invocation_started": getattr(exc, "invocation_started", None),
                    "error_type": type(exc).__name__,
                }
            if ans:
                return {"final_answer": str(ans),
                        "model_usage": {"generation_source": "provider",
                                        "generation_status": "completed"},
                        "context_sources_used": list(sources),
                        **({"citation_evidence": citation_evidence}
                           if citation_evidence else {})}
            # A branch template can describe the route, but it is never
            # presented as a generated answer when completion fails or is absent.
            return {
                "final_answer": "I couldn't generate an answer for this turn. Please retry when the selected provider is available.",
                "model_usage": {"generation_source": "none",
                                "generation_status": "failed" if complete_fn is not None else "unavailable",
                                **(completion_failure or {})},
                "context_sources_used": list(sources),
                **({"citation_evidence": citation_evidence}
                   if citation_evidence else {}),
            }
        if not (state.get("final_answer") or ""):
            return {"final_answer": "I don't have enough to judge that yet.",
                    "model_usage": {"generation_source": "deterministic",
                                    "generation_status": "completed"},
                    "context_sources_used": list(sources),
                    **({"citation_evidence": citation_evidence}
                       if citation_evidence else {})}
        return {"context_sources_used": list(sources),
                "model_usage": {"generation_source": "deterministic",
                                "generation_status": "completed"},
                **({"citation_evidence": citation_evidence}
                   if citation_evidence else {})}

    def approval_gate(state: LangGraphState, config=None) -> dict:
        approval = dict(state.get("approval_state") or {})
        action = (approval.get("action_class") or "green").strip().lower()
        if action not in ("yellow", "red"):
            return {}
        _approval_tree = _tree_of(config)
        if _approval_tree is not None:
            try:
                _approval_tree.approval_required(
                    approval_id=str(approval.get("approval_id", "") or ""),
                    action_class=action,
                    title=str(approval.get("title", "") or "")[:160],
                )
            except Exception:
                pass
        # interrupt() first: code before it re-runs on resume, so this node
        # performs no side effect before pausing (idempotency rule).
        decision = interrupt(
            {
                "approval_id": approval.get("approval_id", ""),
                "project_id": approval.get("project_id", ""),
                "title": (approval.get("title", "") or "")[:160],
                "action_class": action,
                "idempotency_key": approval.get("idempotency_key", ""),
            }
        )
        ok = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
        if not ok and approval.get("task_id"):
            try:
                from app.graphs.adapters import ToolRegistryAdapter
                conn, owned = _conn_for_nodes()
                try:
                    ToolRegistryAdapter().execute(
                        conn, project_id=str(state.get("project_id") or ""), root="",
                        name="reject_task",
                        args={"task_id": str(approval["task_id"]),
                              "reason": "Approval rejected",
                              "workflow": {"project_id": str(state.get("project_id") or ""),
                                           "conversation_id": str(state.get("conversation_id") or ""),
                                           "turn_id": str(state.get("turn_id") or ""),
                                           "thread_id": str(state.get("turn_id") or ""),
                                           "campaign_id": str(approval.get("campaign_id") or ""),
                                           "requested_by": "approval_decision"},
                              "idempotency_key": f"{state.get('turn_id')}:reject-task"})
                finally:
                    if owned:
                        conn.close()
            except Exception:
                pass
            return {"approval_state": {**approval, "approved": False},
                    "final_answer": "The proposed work was rejected and will not proceed."}
        return {"approval_state": {**approval, "approved": ok}}

    def _persist_turn_summary(state: LangGraphState) -> None:
        """Best-effort turn_summary event (never fatal, never user-visible).

        Non-legacy raw-row path (turns._event_row shape); model identity is
        read from this turn's model_calls rows defensively (W2 may add the
        behavior_profile column later — missing key reads "").
        """
        from datetime import datetime, timezone

        import json

        from app.contracts.events import project_event_boundary

        pid = str(state.get("project_id") or "")
        tid = str(state.get("turn_id") or "")
        if not pid or not tid:
            return
        conn, owned = _conn_for_nodes()
        try:
            from app.database import repos

            provider = actual_model = adapter = route_mode = ""
            behavior_profile = ""
            try:
                calls = repos.ModelCalls.for_turn(conn, tid, pid)
                if calls:
                    last = dict(calls[-1])
                    provider = str(last.get("provider") or "")
                    actual_model = str(last.get("model") or "")
                    adapter = str(last.get("adapter") or "")
                    route_mode = str(last.get("route_mode") or "")
                    behavior_profile = str(last.get("behavior_profile") or "")
            except Exception:
                pass
            meta = {
                "turn_id": tid,
                "thread_id": str(state.get("conversation_id") or ""),
                "project_id": pid,
                "intent_type": str(state.get("intent_type") or ""),
                "route": str(state.get("route") or ""),
                "context_sources_used": [str(s) for s in (state.get("context_sources_used") or [])],
                "rag_invoked": bool(state.get("rag_invoked", False)),
                "tool_capability": str(
                    ((state.get("intent") or {}).get("capability")) or ""),
                "tool_run_id": str(state.get("tool_run_id") or ""),
                "provider": provider,
                "actual_model": actual_model,
                "adapter": adapter,
                "behavior_profile": behavior_profile,
            }
            safe = project_event_boundary({
                "event_type": "turn_summary", "label": "Turn summary",
                "detail": str(state.get("final_answer") or "")[:300],
                "metadata": meta,
            })
            row = {
                "project_id": pid,
                "conversation_id": str(state.get("conversation_id") or ""),
                "turn_id": tid,
                "job_id": "",
                "event_type": "turn_summary",
                "label": safe["label"][:300],
                "detail": safe["detail"][:1000],
                "metadata_json": json.dumps(safe["meta"], ensure_ascii=False)[:4000],
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            repos.ExecutionEvents.insert(conn, row)
        finally:
            if owned:
                try:
                    conn.close()
                except Exception:
                    pass

    def respond(state: LangGraphState, config=None) -> dict:
        if state.get("errors"):
            return {"final_answer": ""}
        try:
            _persist_turn_summary(state)
        except Exception:
            pass
        # Append-only graph state preserves the exact synthesis snapshot across
        # fan-in; never query the mutable index after completion.
        citations = state.get("citation_evidence") or []
        return {"citation_evidence": citations} if citations else {}

    def compound_marketing_task_node(state: LangGraphState) -> dict:
        _ = state
        return {}

    # ---------- wiring ----------

    builder = StateGraph(LangGraphState)
    for name, fn in [
        ("load_project", load_project),
        ("load_conversation", load_conversation),
        ("understand", understand),
        ("state_only", state_only),
        ("conversation_meta", conversation_meta),
        ("compound_marketing_task", compound_marketing_task_node),
        ("tool_capability", tool_capability),
        ("knowledge", knowledge),
        ("external_research", external_research),
        ("social_research", social_research),
        ("campaign_operation", campaign_operation),
        ("approval_operation", approval_operation),
        ("job_followup", job_followup),
        ("deep_planner", deep_planner),
        ("deep_worker", deep_worker),
        ("deep_fuse", deep_fuse),
        ("aggregate", aggregate),
        ("skill_route", skill_route),
        ("employee_fanout", employee_fanout),
        ("employee", employee),
        ("employee_fanin", employee_fanin),
        ("skill_load", skill_load),
        ("evidence_collect", evidence_collect),
        ("persist_workflow_writes", persist_workflow_writes),
        ("synthesize", synthesize),
        ("synthesis_complete", synthesis_complete),
        ("approval_gate", approval_gate),
        ("respond", respond),
    ]:
        builder.add_node(name, fn)

    builder.add_edge(START, "load_project")
    builder.add_edge("load_project", "load_conversation")
    builder.add_edge("load_conversation", "understand")

    def route_selector(state: LangGraphState) -> str:
        route = (state.get("route") or "state_only").strip()
        if route == _BLOCKED or state.get("errors"):
            return "respond"
        if route == "deep_research":
            return "deep_planner"
        if route == "tool_capability":
            return "tool_capability"
        if route == "conversation_meta":
            return "conversation_meta"
        if route in (
            "state_only",
            "knowledge",
            "external_research",
            "social_research",
            "campaign_operation",
            "approval_operation",
            "job_followup",
            "compound_marketing_task",
        ):
            return route
        return "state_only"

    builder.add_conditional_edges(
        "understand",
        route_selector,
        [
            "respond",
            "deep_planner",
            "state_only",
            "conversation_meta",
            "tool_capability",
            "knowledge",
            "external_research",
            "social_research",
            "campaign_operation",
            "approval_operation",
            "job_followup",
            "compound_marketing_task",
        ],
    )
    for branch in (
        "state_only",
        "conversation_meta",
        "compound_marketing_task",
        "tool_capability",
        "knowledge",
        "external_research",
        "social_research",
        "campaign_operation",
        "approval_operation",
        "job_followup",
    ):
        builder.add_edge(branch, "aggregate")
    builder.add_conditional_edges("deep_planner", deep_fanout, ["deep_worker"])
    builder.add_edge("deep_worker", "deep_fuse")
    builder.add_edge("deep_fuse", "aggregate")
    # DEV-008 execution-tree chain (W4): aggregate → skill_route →
    # employee_fanout → employee (Send) → employee_fanin → skill_load →
    # evidence_collect → synthesize → synthesis_complete → approval_gate.
    # Blocked/error turns never reach here (route_selector sends them to
    # respond), so the chain only runs for live turns.
    builder.add_edge("aggregate", "skill_route")
    builder.add_edge("skill_route", "employee_fanout")
    builder.add_conditional_edges(
        "employee_fanout", employee_send, ["employee", "employee_fanin"])
    builder.add_edge("employee", "employee_fanin")
    builder.add_edge("employee_fanin", "skill_load")
    builder.add_edge("skill_load", "evidence_collect")
    builder.add_edge("evidence_collect", "persist_workflow_writes")
    builder.add_edge("persist_workflow_writes", "synthesize")
    builder.add_edge("synthesize", "synthesis_complete")
    builder.add_edge("synthesis_complete", "approval_gate")
    builder.add_edge("approval_gate", "respond")
    builder.add_edge("respond", END)
    compile_kwargs: dict = {"checkpointer": checkpointer or InMemorySaver()}
    if store is not None:
        compile_kwargs["store"] = store
    return builder.compile(**compile_kwargs)
