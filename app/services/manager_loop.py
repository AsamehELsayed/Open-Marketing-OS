"""Agentic Account Manager loop (v0.2 §5). Bounded: max iters, timeouts, failure-as-data.

run_manager_turn(conn, *, project_id, conversation_id, user_text, provider,
                 registry, root, budget) -> ManagerResult dict (§5.3).
"""
import time

SYSTEM_PROMPT = """You are the Agentic AI Account Manager for one project.
Reason about the business request; decide what information you need; use tools.
RULES:
- Structured state tools (get_*) are authoritative for current state. Prefer them over RAG for pending/blocked/measured questions.
- RAG (rag_search) is internal evidence only. NEVER paste chunk text verbatim; synthesize in your own words.
- Web (web_search) is for external/current facts. Use it when local tools return empty/weak, or the question needs outside info. Cite URLs.
- delegate_to_marketing_pm is for deep/multi-step work. Prefer it over doing large work inline. Jobs are async; tell the user to ask 'where are we?'.
- Planning/research/analysis run autonomously. External actions (publish, outreach, spend, pricing, mass sends, destructive) REQUIRE a human approval via request_approval + Approvals UI. Never claim an external action was executed without approval.
- Reply in the user's language (Arabic if they write Arabic, else English). Keep answers useful and concise.
- Evidence discipline: material external claims carry STATUS/SOURCE/CONFIDENCE.
- User-facing wording: say "project information" / "current project state" and
  "internal knowledge" / "sources". NEVER use implementation terms (state_digest,
  RAG, chunk, status tags, provider names) in normal replies.
"""

OBS_CAP = 2000


def _social_accounts_for_project(conn, project_id: str) -> list:
    """First-class social state (W1 contract C2), defensive pre-INT: [] when missing."""
    try:
        from app.database import repos
        sa = getattr(repos, "SocialAccounts", None)
        if sa is None:
            return []
        rows = sa.for_project(conn, project_id) or []
        out = [{"platform": str(r.get("platform", "")), "handle": str(r.get("handle", "")),
                "url": str(r.get("url", "")), "status": str(r.get("status", "LIKELY")),
                "source": str(r.get("source", "manual"))}
               for r in rows if isinstance(r, dict)]
        order = {"VERIFIED": 0, "LIKELY": 1, "UNVERIFIED": 2}
        out.sort(key=lambda a: order.get(str(a.get("status", "")).upper(), 3))
        return out[:12]
    except Exception:
        return []


def _social_identity_line(accounts: list) -> str:
    """C5 identity line shared by _compact_context and build_bundle."""
    if not accounts:
        return "Social: none recorded"
    parts = [f"{a.get('platform', '')}=@{str(a.get('handle', '')).lstrip('@')} ({a.get('status', 'LIKELY')})"
             for a in accounts[:12] if a.get("handle")]
    return f"Social: {', '.join(parts)}" if parts else "Social: none recorded"


def _compact_context(conn, project_id: str, conversation_id: str, user_text: str) -> tuple[str, list]:
    from app.database import repos
    proj = repos.Projects.get(conn, project_id)
    ident = f"Project: {project_id}"
    if proj:
        ident = f"Project: {proj.get('name', project_id)} (id={project_id}, website={proj.get('website', '')}, goal={proj.get('goal', '')})"
    ident += f"\n{_social_identity_line(_social_accounts_for_project(conn, project_id))}"
    try:
        camps = repos.Campaigns.list(conn, project_id)
        apprs = [a for a in repos.Approvals.list(conn, project_id=project_id) if a.get("status") == "pending"]
        tasks = repos.Tasks.list(conn, project_id)
        opens = [t for t in tasks if t.get("status") == "open"]
        blocked = [t for t in tasks if t.get("status") == "blocked"]
        jobs = repos.BackgroundJobs.list(conn, project_id)
        running = [j for j in jobs if j.get("status") in ("queued", "running")]
        digest = (f"State: {len(camps)} campaigns, {len(apprs)} pending approvals, "
                  f"{len(opens)} open / {len(blocked)} blocked tasks, {len(running)} active jobs.")
    except Exception:
        digest = "State: unavailable."
    try:
        mems = repos.Memories.list(conn, project_id)[:5]
        mem_txt = "; ".join(f"[{m.get('kind')}] {m.get('body_md', '')[:160]}" for m in mems) or "none"
    except Exception:
        mem_txt = "none"
    try:
        msgs = repos.Messages.for_conversation(conn, conversation_id)[-10:]
        hist = "\n".join(f"{m.get('role', '')}: {(m.get('body_md', '') or '')[:400]}" for m in msgs)
    except Exception:
        hist = ""
    system = (SYSTEM_PROMPT + f"\n{ident}\n{digest}\nDurable memory: {mem_txt}")
    messages = []
    if hist:
        messages.append({"role": "user", "content": f"[history]\n{hist}"})
    messages.append({"role": "user", "content": user_text})
    return system, messages


def _obs_text(obs: dict) -> str:
    import json
    try:
        s = json.dumps(obs, ensure_ascii=False, default=str)
    except Exception:
        s = str(obs)
    return s[:OBS_CAP]


def _emit(on_event, event_type: str, payload: dict | None = None):
    if callable(on_event):
        try:
            on_event(event_type, dict(payload or {}))
        except Exception:
            pass


def run_manager_turn(conn, *, project_id: str, conversation_id: str, user_text: str,
                     provider, registry, root, budget: dict | None = None,
                     on_event=None) -> dict:
    # Fail closed: never run a user-facing turn without an explicit project scope.
    if not project_id or not str(project_id).strip():
        return {"reply_md": "No active project is selected. Pick a project first.",
                "provenance": [], "tools_used": [], "retrieval_used": False,
                "web_used": False, "delegated": False, "job_ids": [],
                "approval_ids": [], "usage": {}, "mode": "NO_PROJECT_SCOPE"}
    budget = budget or {}
    max_iters = int(budget.get("max_tool_iters", 6))
    deadline = time.time() + float(budget.get("turn_timeout_s", 120))
    _emit(on_event, "context_started", {})
    system, messages = _compact_context(conn, project_id, conversation_id, user_text)
    _emit(on_event, "context_completed", {})
    specs = registry.specs()

    def _forward(kind, text):
        if kind == "delta":
            _emit(on_event, "assistant_delta", {"text": text})
        elif kind in ("tool_started", "tool_completed") and isinstance(text, dict):
            # provider-side prefetch (e.g. instagram) reuses loop tool hooks
            _emit(on_event, kind, text)
    tools_used: list[str] = []
    provenance: list[dict] = []
    job_ids: list[str] = []
    approval_ids: list[str] = []
    retrieval_used = web_used = delegated = False
    consec_fail: dict[str, int] = {}
    final_text: str | None = None
    last_resp = None
    iters = 0
    while True:
        if time.time() > deadline:
            final_text = final_text or "I ran out of time gathering data — here's what I found so far."
            break
        try:
            from app.services.llm.base import stream_complete
            resp = stream_complete(provider, system=system, messages=messages,
                                   tools=specs, opts={"event_sink": _forward},
                                   on_event=_forward)
            last_resp = resp
        except Exception:
            # R5: never promise state we don't attach — include a real snapshot.
            snapshot = _failure_snapshot(conn, project_id)
            return {"reply_md": ("The AI reasoning provider is unavailable. "
                                 f"{snapshot}"),
                    "provenance": provenance, "tools_used": tools_used, "retrieval_used": retrieval_used,
                    "web_used": web_used, "delegated": delegated, "job_ids": job_ids,
                    "approval_ids": approval_ids, "usage": {}, "mode": "PROVIDER_ERROR"}
        if not resp.tool_calls or iters >= max_iters:
            final_text = resp.text or final_text or "I don't have enough to answer that yet."
            if resp.tool_calls and iters >= max_iters:
                final_text += " (Note: I hit my tool budget; ask me to continue.)"
            _emit(on_event, "synthesis_started", {})
            break
        for call in resp.tool_calls:
            name = call.name if hasattr(call, "name") else call.get("name", "")
            args = call.arguments if hasattr(call, "arguments") else call.get("arguments", {})
            if name in ("rag_search", "search_project_knowledge"):
                retrieval_used = True
            if name == "web_search":
                web_used = True
            if name == "delegate_to_marketing_pm":
                delegated = True
            _emit(on_event, "tool_started", {"tool": name, "args": args or {}})
            obs = registry.execute(conn, project_id=project_id, root=root, name=name, args=args or {})
            tools_used.append(name)
            if not obs.get("ok"):
                consec_fail[name] = consec_fail.get(name, 0) + 1
                _emit(on_event, "tool_failed",
                      {"tool": name, "error": str(obs.get("error", ""))[:300]})
            else:
                consec_fail[name] = 0
                _emit(on_event, "tool_completed", {"tool": name, "obs": obs})
            for h in (obs.get("provenance") or obs.get("hits") or []):
                if isinstance(h, dict) and h.get("path"):
                    provenance.append({"path": h.get("path"), "chunk_id": h.get("chunk_id", ""),
                                       "status_tag": h.get("status_tag", "UNKNOWN")})
            for r in obs.get("results", []) or []:
                if isinstance(r, dict) and r.get("url"):
                    provenance.append({"path": r["url"], "chunk_id": "",
                                       "status_tag": obs.get("status", "PARTIALLY VERIFIED")})
            if obs.get("job_id"):
                job_ids.append(obs["job_id"])
            if obs.get("approval_id"):
                approval_ids.append(obs["approval_id"])
            hint = ""
            if consec_fail.get(name, 0) >= 2:
                hint = f" (Note: {name} failed twice — try another source or ask the user.)"
            messages.append({"role": "assistant", "content": f"[tool:{name}]{hint}"})
            messages.append({"role": "user", "content": f"[observation:{name}] {_obs_text(obs)}"})
        iters += 1
        if iters > max_iters + 1:
            break
    reply = (final_text or "").strip() or "I don't have enough to answer that yet."
    _merge_agent_contract(last_resp, provenance, job_ids, approval_ids)
    flags = _agent_evidence_flags(last_resp)
    created = _create_persistent_jobs(conn, project_id, conversation_id, last_resp,
                                      budget, root, user_text)
    for jid in created:
        if jid not in job_ids:
            job_ids.append(jid)
    if created:
        reply += _persistent_note(user_text, created)
        delegated = True
    else:
        _contract = _agent_contract(last_resp)
        _reqs = (_contract.get("delegate_requests", []) or []) if _contract else []
        if _reqs:
            import re as _re
            _ar = bool(_re.search(r"[\u0600-\u06FF]", user_text or ""))
            if _ar:
                reply += "\n\nتم تجهيز طلب العمل في الخلفية لكن تعذر بدء التتبع هذه المرة، لذا لا شيء يعمل بعد."
            else:
                reply += "\n\nThe background request was prepared but tracking could not start this turn, so nothing is running yet."
            provenance.append({"path": "", "chunk_id": "", "status_tag": "UNKNOWN"})
    return {"reply_md": reply, "provenance": provenance, "tools_used": sorted(set(tools_used)),
            "retrieval_used": retrieval_used or flags["retrieval"],
            "web_used": web_used or flags["web"],
            "delegated": delegated or flags["delegated"],
            "job_ids": job_ids, "approval_ids": approval_ids, "usage": {}, "mode": "AGENTIC"}


def _failure_snapshot(conn, project_id: str) -> str:
    """Compact real state for the failure path (R5: attach, don't just promise)."""
    try:
        from app.database import repos
        camps = repos.Campaigns.list(conn, project_id)
        pending = [a for a in repos.Approvals.list(conn, project_id=project_id)
                   if a.get("status") == "pending"]
        tasks = repos.Tasks.list(conn, project_id)
        opened = [t for t in tasks if t.get("status") == "open"]
        return (f"Current project snapshot: {len(camps)} campaign(s), "
                f"{len(pending)} pending approval(s), {len(opened)} open task(s). "
                f"Open the Campaigns / Approvals pages for details.")
    except Exception:
        return "Project state is currently unavailable."


def _create_persistent_jobs(conn, project_id: str, conversation_id: str,
                            resp, budget: dict, root, user_text: str) -> list:
    """Hybrid model B: agent `delegate_requests` become REAL background_jobs rows
    AFTER the provider turn ended (sequential — never nested inside the run).
    Bounded: at most 2 per turn; requires budget['db_path'] or skips safely."""
    contract = _agent_contract(resp)
    reqs = (contract.get("delegate_requests", []) or []) if contract else []
    db_path = (budget or {}).get("db_path")
    if not reqs or not db_path:
        return []
    from app.services import jobs as jobsvc
    created = []
    for r in reqs[:2]:
        try:
            row = jobsvc.request_persistent(
                conn, db_path, root, project_id, conversation_id,
                goal=r.get("goal", ""), brief_md=r.get("brief_md", ""),
                lane=r.get("lane", "research"))
            created.append(row["id"])
        except (ValueError, KeyError):
            continue
        except Exception:
            continue
    return created


def _persistent_note(user_text: str, job_ids: list) -> str:
    import re
    ar = bool(re.search(r"[\u0600-\u06FF]", user_text or ""))
    ids = ", ".join(f"`{j[:8]}`" for j in job_ids)
    if ar:
        return (f"\n\nبدأت بحثًا أعمق في الخلفية ({ids}). "
                f"اسألني «وصلت لفين؟» لمتابعة التقدم.")
    return (f"\n\nI've started deeper background research ({ids}). "
            f"Ask 'where are we?' for progress.")


def parse_agent_contract(stdout: str) -> dict:
    """Extract + validate the agent JSON contract. Raises RuntimeError if malformed."""
    import json as _json
    text = (stdout or "").strip()
    if not text:
        raise RuntimeError("agent returned empty output")
    obj = None
    try:
        obj = _json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = _json.loads(text[start:end + 1])
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        raise RuntimeError("agent output is not a JSON object")
    reply = obj.get("reply", "")
    if not isinstance(reply, str) or not reply.strip():
        raise RuntimeError("agent contract missing non-empty 'reply'")
    contract = {"reply": reply.strip()}
    for key in ("sources", "unknowns", "actions", "jobs_started", "approvals_required"):
        val = obj.get(key, [])
        if not isinstance(val, list):
            raise RuntimeError(f"agent contract field '{key}' must be a list")
        contract[key] = val
    ev = obj.get("evidence_used", [])
    if isinstance(ev, list):
        contract["evidence_used"] = [str(e) for e in ev]
    reqs = obj.get("delegate_requests", [])
    if isinstance(reqs, list):
        valid = []
        for r in reqs:
            if isinstance(r, dict) and isinstance(r.get("goal"), str) and r["goal"].strip():
                valid.append({"goal": r["goal"].strip()[:1000],
                              "brief_md": str(r.get("brief_md", ""))[:4000],
                              "lane": str(r.get("lane", "research") or "research")[:40]})
        contract["delegate_requests"] = valid
    return contract


def build_project_bundle(conn, project_id: str, messages: list, root=None) -> dict:
    """Active-project-only context bundle (isolation fail-closed)."""
    import json as _json
    from app.database import repos
    proj = repos.Projects.get(conn, project_id) or {}
    last_user = ""
    for m in reversed(messages or []):
        if (m.get("role") or "") == "user" and not str(m.get("content", "")).startswith("["):
            last_user = str(m.get("content", ""))[:2000]
            break
    try:
        campaigns = repos.Campaigns.list(conn, project_id)
        approvals = repos.Approvals.list(conn, project_id=project_id)
        tasks = repos.Tasks.list(conn, project_id)
        jobs = repos.BackgroundJobs.list(conn, project_id)
        memories = repos.Memories.list(conn, project_id)[:5]
    except Exception as e:
        raise RuntimeError(f"cannot load project scope: {e}")
    rag_evidence: list = []
    if last_user:
        try:
            from app.services.rag import rag_service
            res = rag_service.retrieve(conn, last_user, project_id=project_id)
            for h in res.get("hits", [])[:5]:
                rag_evidence.append({"path": h.get("path", ""),
                                     "header": h.get("header", ""),
                                     "snippet": (h.get("text", "") or h.get("snippet", ""))[:600],
                                     "status_tag": h.get("status_tag", "UNKNOWN")})
        except Exception:
            rag_evidence = []
    jobs_recent = _recent_job_summaries(jobs, project_id, root)
    social_accounts = _social_accounts_for_project(conn, project_id)
    return {
        "project": {"id": project_id, "name": proj.get("name", project_id),
                    "website": proj.get("website", ""), "goal": proj.get("goal", ""),
                    "workspace": f"data/projects/{project_id}"},
        "social_accounts": social_accounts,
        "social_identity_line": _social_identity_line(social_accounts),
        "state_digest": {
            "campaigns": [{"id": c["id"], "title": c.get("title", ""),
                           "status": c.get("status", "")} for c in campaigns[:20]],
            "approvals_pending": [{"id": a["id"], "title": a.get("title", "")}
                                  for a in approvals if a.get("status") == "pending"][:10],
            "tasks_open": len([t for t in tasks if t.get("status") == "open"]),
            "tasks_blocked": len([t for t in tasks if t.get("status") == "blocked"]),
            "jobs_active": [{"id": j["id"], "kind": j.get("kind", ""),
                             "status": j.get("status", "")} for j in jobs
                            if j.get("status") in ("queued", "running", "waiting_approval")][:10],
        },
        "memories": [{"kind": m.get("kind"), "body": m.get("body_md", "")[:300]}
                     for m in memories],
        "rag_evidence": rag_evidence,
        "jobs_recent": jobs_recent,
        "user_request": last_user,
    }


def _recent_job_summaries(jobs: list, project_id: str, root=None) -> list:
    """Last terminal jobs with real result state (hybrid follow-ups read these,
    never chat-text inference). Best effort; empty list on any failure."""
    out = []
    try:
        terminal = [j for j in jobs
                    if j.get("status") in ("completed", "failed", "cancelled")]
        terminal.sort(key=lambda j: j.get("finished_at") or "", reverse=True)
        from app.services import jobs as jobsvc
        for j in terminal[:3]:
            summary = ""
            if root:
                res = jobsvc.read_result_json(root, project_id, j["id"])
                if res:
                    summary = (res.get("stdout_tail", "") or "")[-800:]
            out.append({"job_id": j["id"], "kind": j.get("kind", ""),
                        "status": j.get("status", ""),
                        "finished_at": j.get("finished_at", ""),
                        "result_summary": summary})
    except Exception:
        return []
    return out


def _agent_contract(resp) -> dict | None:
    try:
        usage = getattr(resp, "usage", None) or {}
        contract = usage.get("agent_contract")
        return contract if isinstance(contract, dict) else None
    except Exception:
        return None


def _merge_agent_contract(resp, provenance: list, job_ids: list, approval_ids: list) -> None:
    """Single-shot agent providers report work via the contract;
    surface it as provenance/job/approval ids so the UI treats them uniformly."""
    contract = _agent_contract(resp)
    if not contract:
        return
    for s in contract.get("sources", []) or []:
        if isinstance(s, dict) and s.get("url"):
            provenance.append({"path": s["url"], "chunk_id": "",
                               "status_tag": s.get("status", "PARTIALLY VERIFIED")})
        elif isinstance(s, dict) and s.get("path"):
            provenance.append({"path": s["path"], "chunk_id": s.get("chunk_id", ""),
                               "status_tag": s.get("status_tag", "UNKNOWN")})
        elif isinstance(s, str) and s.strip():
            provenance.append({"path": s.strip(), "chunk_id": "", "status_tag": "UNKNOWN"})
    for j in contract.get("jobs_started", []) or []:
        jid = j.get("job_id") if isinstance(j, dict) else (j if isinstance(j, str) else "")
        if jid and jid not in job_ids:
            job_ids.append(jid)
    for a in contract.get("approvals_required", []) or []:
        aid = a.get("approval_id") if isinstance(a, dict) else (a if isinstance(a, str) else "")
        if aid and aid not in approval_ids:
            approval_ids.append(aid)


def _agent_evidence_flags(resp) -> dict:
    contract = _agent_contract(resp)
    used = set()
    if contract:
        for e in contract.get("evidence_used", []) or []:
            if isinstance(e, str):
                used.add(e.strip().lower())
    return {"retrieval": "rag" in used,
            "web": "web" in used,
            "delegated": bool(contract and contract.get("jobs_started"))}
