"""DEV-008-SKILLS-OPS-HOTFIX W3 — employee-owned tool execution.

Implements the frozen contract sections this packet owns:

* **§4** the employee result dict, exactly the 13 keys, ``status`` restricted to
  ``"completed"`` / ``"failed"``;
* **§5** the registered-tool map -- ``website_marketing_audit``,
  ``instagram_audit``, ``web_search``. Nothing else is reachable from here: no
  new provider path, no second Instagram or website handler;
* **§6** the per-employee event order
  ``employee_started -> skill_selected/skill_loaded -> tool_started ->
  tool_completed|tool_failed -> evidence_added ->
  employee_completed|employee_failed``, every tool event carrying the owning
  ``employee_id`` so ``TreeEmitter._employee_parent`` nests it correctly;
* **§8** failure semantics -- one branch failing never cancels a sibling, a
  failed branch stays visible, and **nothing here writes a turn-level error
  list**: this module never receives one and never returns an ``errors`` key.

What is reused, never reimplemented
----------------------------------
============================  ==========================================
``app.graphs.adapters``      ``ToolRegistryAdapter.execute`` -- the single
                             registered-tool execution path, injected so a
                             test can substitute a fake registry and run with
                             no network.
``app.graphs.tool_capability``  the Instagram / website synthesis helpers and
                             the ``tool_runs`` write path.
``app.services.tools.registry``  the frozen nine-column ``tool_runs`` schema
                             and its writer.
``app.services.skills.router``  ``SkillRouteRequest`` / ``route`` -- the real
                             skill selection. There is no skill list in this
                             file.
``app.services.skills.roles``   ``role_for`` for the employee role fallback.
``app.services.skills.tree``    every emitter call goes through ``TreeEmitter``.
============================  ==========================================

Why the plan is a hard precondition
------------------------------------
Contract §6: *"No external research tool may run before ``task_planned``
exists."* A module that trusts its caller to order things correctly is an
ordering bug waiting to happen, so the precondition is enforced here and
:func:`PlanRequiredError` is raised **before any event and before any registry
call** when the plan is absent, when the employee is not in the plan, or when
the tree has not persisted ``task_planned`` yet. The test asserts zero tool
executions and zero ``tool_runs`` rows on that path.

One ``tool_runs`` row per execution
-----------------------------------
``Registry.execute`` telemeters its own row, and this module writes the
authoritative one through the same frozen schema
(``_TOOL_RUN_INSERT`` / ``_insert_tool_run``). The module's row is the one the
result and the tree events name, because it is the only one that can carry the
employee-visible outcome: the registry derives its row status from ``ok``, and
the Instagram handler reports provider unavailability as ``ok=True`` with
``status="NOT ACCESSIBLE"`` -- a *failed* research run that a row keyed on
``ok`` would record as a success. The module's row is also the only one whose
``started_at``/``completed_at`` bracket the real execution, which is what lets
``tool_started`` and ``tool_completed`` share one run id.
``app.graphs.tool_capability`` already writes its own row on top of the
registry's for exactly this reason.

Attribution
-----------
``TreeEmitter.tool_event`` is frozen at five metadata keys, so
``employee_id`` / ``tool_run_id`` / ``tool_id`` ride on the event and
``employee_role`` / ``capability`` / ``provider`` ride on the result's
``tools`` entry -- together every one of the six is present per execution. See
the W3 handoff for the W4 follow-up if the event itself must carry them.

Concurrency
-----------
:func:`run_employee` is the unit: it holds no shared mutable state, so the
graph can run one per ``Send`` branch for genuinely overlapping intervals
(§7). :func:`run_employees` is a **sequential** fan-in convenience used by
tests and by single-threaded callers; it is not a claim of parallelism.
"""
from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# --------------------------------------------------------------------- §5

@dataclass(frozen=True)
class ToolSpec:
    """One reachable external tool. ``tool_id`` is a registered registry name."""

    domain: str
    capability: str
    tool_id: str
    provider: str = ""
    arg_keys: tuple[str, ...] = ()


#: Contract §5 verbatim: the only three external tools an employee may run.
TOOL_MAP: dict[str, ToolSpec] = {
    "website": ToolSpec(
        domain="website",
        capability="website_marketing_audit",
        tool_id="website_marketing_audit",
        provider="website_fetcher",
        arg_keys=("url",),
    ),
    "instagram": ToolSpec(
        domain="instagram",
        capability="instagram_public_profile",
        tool_id="instagram_audit",
        provider="",
        arg_keys=("username", "owned"),
    ),
    "social": ToolSpec(
        domain="social",
        capability="instagram_public_profile",
        tool_id="instagram_audit",
        provider="",
        arg_keys=("username", "owned"),
    ),
    "competitor": ToolSpec(
        domain="competitor",
        capability="",
        tool_id="web_search",
        provider="web_transport",
        arg_keys=("query", "count"),
    ),
}

#: Contract §4: legitimate with ``tools: []``. Not a failure.
DOMAINS_WITHOUT_EXTERNAL_TOOL: tuple[str, ...] = (
    "seo", "cro", "positioning", "experiment",
)

#: §4 employee result keys, in contract order. The result carries exactly these.
RESULT_KEYS: tuple[str, ...] = (
    "employee_id", "employee_role", "task", "skills", "tools", "evidence",
    "findings", "limitations", "status", "started_at", "completed_at",
    "duration_ms",
)

STATUSES: tuple[str, ...] = ("completed", "failed")
TOOL_STATUSES: tuple[str, ...] = ("started", "completed", "failed")

#: A tool result whose ``status`` is one of these observed no usable external
#: result. ``ok=True`` is the handler saying "I returned gracefully", not "I
#: found something" -- ``t_instagram_audit`` and ``t_web_search`` both use it for
#: provider unavailability, and contract §5 classifies that as a failed run.
_UNUSABLE_STATUSES = frozenset({
    "NOT ACCESSIBLE", "NEEDS_HANDLE", "FAILED", "ERROR", "UNAVAILABLE",
})

_MAX_DETAIL = 400
_MAX_LIMITATION = 320
_MAX_FINDING = 240


class PlanRequiredError(RuntimeError):
    """An external tool was about to run before the plan existed (§6).

    Raised **before** any event is emitted and before the registry is called,
    so the refusal is provably side-effect free. A wiring bug rather than a
    provider failure, which is why it is not swallowed by
    :func:`run_employee` (and is re-raised by :func:`run_employees`).
    """


# ------------------------------------------------------------------ helpers

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utc_now_ms() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _str_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    try:
        return tuple(str(item).strip() for item in value if str(item).strip())
    except TypeError:
        return ()


def _safe_reason(value, fallback: str = "the tool returned no usable result") -> str:
    from app.contracts.events import safe_error_message

    try:
        return safe_error_message(value, fallback) or fallback
    except Exception:
        return fallback


def _footer(status: str = "", source: str = "", confidence: str = "") -> str:
    """The AGENTS.md evidence footer, omitting whatever the tool did not say."""
    parts = []
    if status:
        parts.append(f"STATUS: {status}")
    if source:
        parts.append(f"SOURCE: {source}")
    if confidence:
        parts.append(f"CONFIDENCE: {confidence}")
    return (" / ".join(parts)) if parts else ""


# ------------------------------------------------------------------- plan

def planned_tools(assignment: dict) -> tuple[ToolSpec, ...]:
    """The tools this assignment may run, from the domains in the plan.

    The plan's ``tools`` list is authoritative when it is non-empty: a domain
    whose tool is not named there is not run. A tool id in the plan that is not
    in :data:`TOOL_MAP` is reported by :func:`run_employee` as a limitation, so
    an unmapped request is visible rather than silently dropped.
    """
    assignment = assignment if isinstance(assignment, dict) else {}
    allowed = set(_str_tuple(assignment.get("tools")))
    specs: list[ToolSpec] = []
    seen: set[str] = set()
    for domain in _str_tuple(assignment.get("domains")):
        spec = TOOL_MAP.get(domain)
        if spec is None or spec.tool_id in seen:
            continue
        if allowed and spec.tool_id not in allowed:
            continue
        seen.add(spec.tool_id)
        specs.append(spec)
    return tuple(specs)


def _require_plan(plan: Any, assignment: dict, tree: Any) -> str:
    """Fail closed unless the plan is present, owns this employee, and is persisted."""
    employee_id = str((assignment or {}).get("employee_id") or "")
    if not isinstance(plan, dict) or not str(plan.get("plan_id") or "").strip():
        raise PlanRequiredError(
            "no task_plan was handed to the employee; an external tool may not "
            "run before the plan exists (contract section 6)"
        )
    known = {
        str(entry.get("employee_id") or "")
        for entry in (plan.get("employees") or [])
        if isinstance(entry, dict)
    }
    if employee_id and employee_id not in known:
        raise PlanRequiredError(
            f"employee {employee_id!r} is not listed in task_plan "
            f"{str(plan.get('plan_id'))!r}"
        )
    if tree is not None and hasattr(tree, "task_planned_id"):
        if tree.task_planned_id is None:
            raise PlanRequiredError(
                "task_planned has not been persisted to the event stream yet; "
                "an external tool may not run before it does (contract section 6)"
            )
    return str(plan.get("plan_id") or "")


# ------------------------------------------------------------------ skills

def _load_skill_registry():
    try:
        from app.services.skills.registry import load_registry

        return load_registry(disabled="")
    except Exception:
        return None


def _select_skills(skill_registry, *, intent: str, project_state, project_id: str):
    """Real ``SkillRouter`` selection. Never a hardcoded list; ``()`` is honest."""
    try:
        from app.services.skills import router as skill_router

        records = tuple(skill_registry.enabled_records()) if skill_registry is not None else ()
        request = skill_router.SkillRouteRequest(
            intent=str(intent or ""),
            project_state=dict(
                project_state
                if isinstance(project_state, dict) and project_state
                else {"project_id": str(project_id or "")}
            ),
            task={},
            available_skills=records,
        )
        return tuple(skill_router.route(request).selected or ())
    except Exception:
        return ()


def _skill_entries(selection) -> list[dict]:
    entries: list[dict] = []
    for item in selection or ():
        skill_id = str(getattr(item, "skill_id", "") or "")
        if not skill_id:
            continue
        try:
            score = float(getattr(item, "score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        entries.append({
            "skill_id": skill_id,
            "reason_code": str(getattr(item, "reason_code", "") or ""),
            "score": score,
        })
    return entries


def _resolve_role(assignment: dict, selection) -> str:
    """The plan's role, else ``role_for`` on the router's top selection."""
    from app.services.skills.roles import EMPLOYEE_ROLES, RoleTableError, role_for

    role = str((assignment or {}).get("role") or "").strip()
    if role in EMPLOYEE_ROLES:
        return role
    for item in selection or ():
        skill_id = str(getattr(item, "skill_id", "") or "")
        if not skill_id:
            continue
        try:
            return role_for(skill_id)
        except RoleTableError:
            continue
    return role


# ------------------------------------------------------------------ events

def _safe_call(tree, method: str, **kwargs):
    """A broken observer must never change what an employee does."""
    emitter = getattr(tree, method, None) if tree is not None else None
    if not callable(emitter):
        return None
    try:
        return emitter(**kwargs)
    except Exception:
        return None


def _task_payload(assignment: dict) -> dict:
    assignment = assignment if isinstance(assignment, dict) else {}
    return {
        "id": str(assignment.get("employee_id") or ""),
        "query": str(assignment.get("task") or ""),
        "domains": list(_str_tuple(assignment.get("domains"))),
        "role": str(assignment.get("role") or ""),
    }


def emit_employee_queued(tree, *, assignment: dict):
    """``employee_queued`` for one plan employee (the fan-out node's helper)."""
    assignment = assignment if isinstance(assignment, dict) else {}
    return _safe_call(
        tree, "employee_queued",
        employee_id=str(assignment.get("employee_id") or ""),
        role=str(assignment.get("role") or ""),
        task=_task_payload(assignment),
    )


def _emit_tool(tree, event_type: str, spec: ToolSpec, tool_run_id: str,
               employee_id: str, status: str, duration_ms: int):
    return _safe_call(
        tree, "tool_event",
        event_type=event_type,
        tool_id=spec.tool_id,
        tool_run_id=tool_run_id,
        employee_id=employee_id,
        status=status,
        duration_ms=duration_ms,
    )


# -------------------------------------------------------------- tool runs

def _persist_tool_run(conn, *, project_id: str, tool_id: str, provider: str,
                      tool_run_id: str, started_at: str, completed_at: str,
                      status: str, latency_ms: int, turn_id: str,
                      employee_id: str, employee_role: str,
                      evidence_count: int) -> bool:
    """One authoritative ``tool_runs`` row for one execution.

    Reuses the frozen schema and writer from
    ``app.services.tools.registry`` (the same tuple
    ``app.graphs.tool_capability.persist_tool_run`` writes) with the caller's
    run id and the real start/end of the execution. Never raises; returns
    whether the row landed, so a caller can record the miss as a limitation
    rather than reporting an unbacked run id.
    """
    try:
        from app.services.tools.registry import _insert_tool_run

        _insert_tool_run(conn, (
            tool_run_id, project_id or "", tool_id, provider,
            started_at, completed_at, status, int(latency_ms or 0), "",
            turn_id or None, employee_id or None, employee_role or None,
            max(0, int(evidence_count or 0)),
        ))
        return True
    except Exception as exc:
        try:
            print(f"[tool_run] persist failed: {exc}", file=sys.stderr, flush=True)
        except Exception:
            pass
        return False


# ------------------------------------------------------------- tool driving

def _project_website(conn, project_id: str) -> str:
    """The project's own URL, from the canonical resolver. Not reimplemented."""
    try:
        from app.graphs.tool_capability import _website_project_url

        return str(_website_project_url(conn, project_id) or "")
    except Exception:
        return ""


def _tool_args(spec: ToolSpec, conn, project_id: str, assignment: dict,
               intent: str) -> dict:
    given = assignment.get("args") if isinstance(assignment.get("args"), dict) else {}
    args: dict[str, Any] = {k: v for k, v in given.items() if k in spec.arg_keys}
    if spec.tool_id == "website_marketing_audit":
        url = str(args.get("url") or "").strip() or _project_website(conn, project_id)
        return {"url": url}
    if spec.tool_id == "instagram_audit":
        return {k: args[k] for k in ("username", "owned") if k in args}
    if spec.tool_id == "web_search":
        query = (str(args.get("query") or "").strip()
                 or str(intent or "").strip()[:200] or spec.domain)
        try:
            count = max(1, min(10, int(args.get("count") or 5)))
        except (TypeError, ValueError):
            count = 5
        return {"query": query, "count": count}
    return dict(args)


def _provider_for(spec: ToolSpec, result: dict) -> str:
    """Return provider attribution supplied by the capability/tool layer."""
    declared = str(result.get("provider") or "").strip()
    if declared:
        return declared
    if spec.provider:
        return spec.provider
    audit = result.get("audit") if isinstance(result.get("audit"), dict) else {}
    return str(audit.get("provider") or "").strip()


def _classify(spec: ToolSpec, result: dict) -> tuple[bool, str, str]:
    """``(usable, reason, provider)`` from a real tool result. Never optimistic."""
    provider = _provider_for(spec, result)
    if spec.tool_id == "web_search":
        rows = result.get("results")
        if not isinstance(rows, list) or not any(isinstance(row, dict) for row in rows):
            return False, "web search returned no usable results", provider
    status = str(result.get("status") or "").strip().upper()
    if not bool(result.get("ok")) or status in _UNUSABLE_STATUSES:
        reason = _safe_reason(
            result.get("error") or result.get("note") or result.get("status"),
            f"the tool reported no usable result (status {status or 'unknown'})",
        )
        return False, reason, provider
    return True, "", provider


# ---------------------------------------------------------------- evidence

def _website_evidence(args: dict, result: dict) -> tuple[list[dict], list[str]]:
    from app.graphs.tool_capability import synthesize_website_audit

    url = str(args.get("url") or "")
    audit = result.get("audit") if isinstance(result.get("audit"), dict) else {}
    checklist = audit.get("checklist") if isinstance(audit.get("checklist"), dict) else {}
    detail = ""
    try:
        detail = synthesize_website_audit(url, result)
    except Exception:
        detail = ""
    if not detail:
        detail = _footer(status=str(result.get("status") or "UNKNOWN"),
                         source="website_fetcher")
    evidence = [{"kind": "website_marketing_audit", "ref": url,
                 "detail": detail[:_MAX_DETAIL]}]
    findings: list[str] = []
    for key, label in (
        ("has_title", "page title"),
        ("has_meta_description", "meta description"),
        ("has_h1", "H1 heading"),
        ("has_cta_keywords", "call-to-action keywords"),
        ("has_email", "contact email"),
        ("has_phone", "phone number"),
    ):
        if checklist.get(key) is False:
            findings.append(f"website: no {label} observed in the fetched pages.")
    checked = checklist.get("pages_checked")
    if checked:
        findings.append(f"website: {int(checked)} page(s) fetched for the audit.")
    return evidence, findings


def _instagram_evidence(args: dict, result: dict) -> tuple[list[dict], list[str]]:
    from app.graphs.tool_capability import synthesize_success

    audit = result.get("audit") if isinstance(result.get("audit"), dict) else {}
    account = audit.get("account") if isinstance(audit.get("account"), dict) else {}
    handle = str(account.get("username") or account.get("handle")
                 or args.get("username") or "").strip().lstrip("@")
    detail = ""
    if handle:
        try:
            detail = synthesize_success(handle, audit)
        except Exception:
            detail = ""
    if not detail:
        detail = _footer(status=str(result.get("status") or "UNKNOWN"),
                         source=str((audit.get("evidence") or {}).get("source") or ""))
    evidence = [{"kind": "instagram_audit", "ref": f"@{handle}" if handle else "",
                 "detail": detail[:_MAX_DETAIL]}]
    findings: list[str] = []
    source = str((audit.get("evidence") or {}).get("source") or "")
    followers = account.get("followers")
    if handle and followers is not None:
        findings.append(
            f"instagram @{handle}: {followers} followers observed"
            + (f" via {source}." if source else ".")
        )
    for unknown in (audit.get("unknowns") or [])[:3]:
        text = str(unknown or "").strip()
        if text:
            findings.append(f"instagram @{handle or 'unknown'}: {text}"[:_MAX_FINDING])
    return evidence, findings


def _web_evidence(args: dict, result: dict) -> tuple[list[dict], list[str]]:
    query = str(args.get("query") or "")
    rows = [r for r in (result.get("results") or []) if isinstance(r, dict)]
    if not rows:
        return [], []
    lines = [
        f"- {str(r.get('title') or '').strip() or '(untitled)'} — {str(r.get('url') or '').strip()}"
        for r in rows[:5]
    ]
    detail = (f"web_search for {query!r} returned {len(rows)} result(s) from the "
              f"web transport.\n" + "\n".join(lines))
    footer = _footer(status=str(result.get("status") or ""),
                     source="web_transport",
                     confidence=str(result.get("confidence") or ""))
    if footer:
        detail = f"{detail}\n{footer}"
    evidence = [{"kind": "web_search", "ref": query, "detail": detail[:_MAX_DETAIL]}]
    findings = [f"competitor research: {len(rows)} web source(s) observed for {query!r}."]
    return evidence, findings


def _evidence_for(spec: ToolSpec, args: dict, result: dict) -> tuple[list[dict], list[str]]:
    if spec.tool_id == "website_marketing_audit":
        return _website_evidence(args, result)
    if spec.tool_id == "instagram_audit":
        return _instagram_evidence(args, result)
    if spec.tool_id == "web_search":
        return _web_evidence(args, result)
    audit = result.get("audit") if isinstance(result.get("audit"), dict) else {}
    unknowns = [str(u).strip() for u in (audit.get("unknowns") or []) if str(u).strip()]
    detail = (f"{spec.tool_id} returned status {str(result.get('status') or 'UNKNOWN')}."
              + (f" Observed unknowns: {'; '.join(unknowns[:3])}" if unknowns else ""))
    return ([{"kind": spec.tool_id, "ref": "", "detail": detail[:_MAX_DETAIL]}], [])


def _failure_limitation(spec: ToolSpec, provider: str, reason: str) -> str:
    return (
        f"{spec.tool_id} (capability {spec.capability or 'n/a'}, provider "
        f"{provider or 'none reachable'}) could not complete: {reason}. "
        f"No substitute source was used for this domain."
    )[:_MAX_LIMITATION]


# ----------------------------------------------------------------- running

def _run_one_tool(conn, *, project_id: str, adapter, spec: ToolSpec,
                  assignment: dict, employee_id: str, employee_role: str,
                  tree, intent: str):
    args = _tool_args(spec, conn, project_id, assignment, intent)
    tool_run_id = uuid.uuid4().hex
    wall_now = datetime.now(timezone.utc)
    started_dt = wall_now.replace(microsecond=(wall_now.microsecond // 1000) * 1000)
    started_at = started_dt.isoformat(timespec="milliseconds")
    _emit_tool(tree, "tool_started", spec, tool_run_id, employee_id, "RUNNING", 0)

    clock = time.perf_counter()
    try:
        from app.services.tools.registry import employee_tool_telemetry_context
        turn_id = str(getattr(tree, "turn_id", "") or "")
        with employee_tool_telemetry_context(
                tool_run_id=tool_run_id, turn_id=turn_id,
                employee_id=employee_id, employee_role=employee_role):
            raw = adapter.execute(conn, project_id=project_id, root="",
                                  name=spec.tool_id, args=args)
    except Exception:
        raw = None
    duration_ms = max(0, int((time.perf_counter() - clock) * 1000))
    completed_at = (started_dt + timedelta(milliseconds=duration_ms)).isoformat(
        timespec="milliseconds")

    if isinstance(raw, dict):
        result = dict(raw)
    else:
        result = {"ok": False, "status": "failed",
                  "error": "The tool could not be completed."}
    usable, reason, provider = _classify(spec, result)
    status = "completed" if usable else "failed"

    entry = {
        "tool_id": spec.tool_id,
        "tool_run_id": tool_run_id,
        "provider": provider,
        "capability": spec.capability,
        "status": status,
        "duration_ms": duration_ms,
    }
    limitations: list[str] = []
    if usable:
        evidence, findings = _evidence_for(spec, args, result)
    else:
        evidence, findings = [], []
        limitations.append(_failure_limitation(spec, provider, reason))
    persisted = _persist_tool_run(
        conn, project_id=project_id, tool_id=spec.tool_id, provider=provider,
        tool_run_id=tool_run_id, started_at=started_at, completed_at=completed_at,
        status="success" if usable else "failed", latency_ms=duration_ms,
        turn_id=str(getattr(tree, "turn_id", "") or ""),
        employee_id=employee_id, employee_role=employee_role,
        evidence_count=len(evidence))
    if not persisted:
        limitations.append(
            f"{spec.tool_id}: the tool run could not be written to tool_runs, "
            f"so run {tool_run_id} is unverified telemetry."
        )
    _emit_tool(tree, "tool_completed" if usable else "tool_failed", spec,
               tool_run_id, employee_id, "COMPLETE" if usable else "FAILED",
               duration_ms)
    return entry, evidence, findings, limitations


def _run_planned_tools(conn, *, project_id: str, adapter, assignment: dict,
                       employee_id: str, employee_role: str, tree, intent: str,
                       skills: list[dict]):
    tools: list[dict] = []
    evidence: list[dict] = []
    findings: list[str] = []
    limitations: list[str] = []
    specs = planned_tools(assignment)
    for spec in specs:
        entry, item_evidence, item_findings, item_limitations = _run_one_tool(
            conn, project_id=project_id, adapter=adapter, spec=spec,
            assignment=assignment, employee_id=employee_id,
            employee_role=employee_role, tree=tree, intent=intent)
        tools.append(entry)
        evidence.extend(item_evidence)
        findings.extend(item_findings)
        limitations.extend(item_limitations)

    ran = {entry["tool_id"] for entry in tools}
    played = skills[0]["skill_id"] if skills else ""
    for domain in _str_tuple(assignment.get("domains")):
        spec = TOOL_MAP.get(domain)
        if spec is not None and spec.tool_id not in ran:
            limitations.append(
                f"{domain}: {spec.tool_id} is not in the plan's tool list, so no "
                f"external run was made and no substitute was used."
            )
            continue
        if domain in DOMAINS_WITHOUT_EXTERNAL_TOOL:
            findings.append(
                f"{domain}: no external tool is registered for this domain; the "
                f"playbook is the method"
                + (f" ({played})." if played else " and no playbook was selected.")
            )
            continue
        if spec is None:
            limitations.append(
                f"{domain}: no registered tool in the contract tool map, so "
                f"nothing was run and no substitute was used."
            )
    for tool_id in _str_tuple(assignment.get("tools")):
        if tool_id in ran:
            continue
        if not any(item.startswith(f"{tool_id}:") for item in limitations):
            limitations.append(
                f"{tool_id}: named in the plan but not a registered tool in "
                f"the contract tool map; it was not run and nothing replaced it."
            )
    return tools, evidence, findings, limitations


def _employee_body(conn, *, project_id: str, adapter, assignment: dict,
                   employee_id: str, role: str, tree, intent: str,
                   skills: list[dict], clock: float):
    tools, evidence, findings, limitations = _run_planned_tools(
        conn, project_id=project_id, adapter=adapter, assignment=assignment,
        employee_id=employee_id, employee_role=role, tree=tree, intent=intent,
        skills=skills)
    if evidence:
        kinds = sorted({str(item.get("kind") or "") for item in evidence})
        _safe_call(tree, "evidence_added", count=len(evidence),
                   kind=", ".join(kinds)[:64], employee_id=employee_id)
    failed = [entry["tool_id"] for entry in tools if entry["status"] != "completed"]
    status = "failed" if failed else "completed"
    duration_ms = max(0, int((time.perf_counter() - clock) * 1000))
    if status == "completed":
        _safe_call(tree, "employee_completed", employee_id=employee_id, role=role,
                   duration_ms=duration_ms)
    else:
        _safe_call(tree, "employee_failed", employee_id=employee_id, role=role,
                   exc=RuntimeError("tool run failed: " + ", ".join(failed)),
                   duration_ms=duration_ms)
    return {
        "skills": skills,
        "tools": tools,
        "evidence": evidence,
        "findings": findings,
        "limitations": limitations,
        "status": status,
    }


def _emit_skill_events(tree, skill_entries: list[dict], employee_id: str,
                       skill_registry) -> None:
    for entry in skill_entries:
        _safe_call(tree, "skill_selected", skill_id=entry["skill_id"],
                   reason_code=entry["reason_code"], score=entry["score"],
                   employee_id=employee_id)
        record = None
        if skill_registry is not None:
            try:
                record = skill_registry.get(entry["skill_id"])
            except Exception:
                record = None
        if record is not None:
            _safe_call(tree, "skill_loaded", record=record, employee_id=employee_id)


# ------------------------------------------------------------------- public

def run_employee(
    conn,
    *,
    project_id: str,
    assignment: dict,
    plan: dict,
    tree=None,
    registry=None,
    skill_registry=None,
    project_state: dict | None = None,
    intent: str = "",
) -> dict:
    """Run one employee: plan, skills, its registered tools, evidence, result.

    Returns exactly the contract §4 result dict. ``status`` is only
    ``"completed"`` or ``"failed"``. Raises :class:`PlanRequiredError` — and
    executes nothing — when a tool would run before the plan exists.

    ``registry`` accepts anything with the ``ToolRegistryAdapter.execute``
    signature (a fake in tests, the real adapter in production); ``tree``
    accepts a :class:`~app.services.skills.tree.TreeEmitter` or a fake.
    """
    assignment = dict(assignment or {})
    employee_id = str(assignment.get("employee_id") or "")
    task_text = str(assignment.get("task") or "")
    if planned_tools(assignment):
        _require_plan(plan, assignment, tree)

    started_at = _utc_now_ms()
    clock = time.perf_counter()
    if skill_registry is None:
        skill_registry = _load_skill_registry()
    selection = _select_skills(skill_registry, intent=task_text or intent,
                               project_state=project_state, project_id=project_id)
    skills = _skill_entries(selection)
    role = _resolve_role(assignment, selection)
    adapter = registry if registry is not None else _default_adapter()

    _safe_call(tree, "employee_started", employee_id=employee_id, role=role,
               task=_task_payload(assignment), started_at=started_at)
    _emit_skill_events(tree, skills, employee_id, skill_registry)

    try:
        body = _employee_body(
            conn, project_id=project_id, adapter=adapter, assignment=assignment,
            employee_id=employee_id, role=role, tree=tree,
            intent=task_text or intent, skills=skills, clock=clock)
    except Exception as exc:
        body = {
            "skills": skills,
            "tools": [],
            "evidence": [],
            "findings": [],
            "limitations": [
                f"the employee run stopped early: {_safe_reason(exc)}"
            ],
            "status": "failed",
        }
        _safe_call(tree, "employee_failed", employee_id=employee_id, role=role,
                   exc=exc, duration_ms=max(0, int((time.perf_counter() - clock) * 1000)))

    return {
        "employee_id": employee_id,
        "employee_role": role,
        "task": task_text,
        "skills": body["skills"],
        "tools": body["tools"],
        "evidence": body["evidence"],
        "findings": [str(item)[:_MAX_FINDING] for item in body["findings"]],
        "limitations": [str(item)[:_MAX_LIMITATION] for item in body["limitations"]],
        "status": body["status"],
        "started_at": started_at,
        "completed_at": _utc_now_ms(),
        "duration_ms": max(0, int((time.perf_counter() - clock) * 1000)),
    }


def run_employees(
    conn,
    *,
    project_id: str,
    assignments,
    plan: dict,
    tree=None,
    registry=None,
    skill_registry=None,
    project_state: dict | None = None,
    intent: str = "",
) -> list[dict]:
    """Run several plan employees in order, one result each.

    Sequential by design: it is a fan-in convenience, not the concurrency model.
    A branch that fails still returns its own failed result and its siblings
    still run (contract §8). :class:`PlanRequiredError` is re-raised, because a
    missing plan is a wiring fault for the whole turn, not a branch failure.
    """
    results: list[dict] = []
    for assignment in list(assignments or []):
        try:
            results.append(run_employee(
                conn, project_id=project_id, assignment=assignment, plan=plan,
                tree=tree, registry=registry, skill_registry=skill_registry,
                project_state=project_state, intent=intent))
        except PlanRequiredError:
            raise
        except Exception as exc:
            assignment = dict(assignment or {})
            now = _utc_now()
            results.append({
                "employee_id": str(assignment.get("employee_id") or ""),
                "employee_role": str(assignment.get("role") or ""),
                "task": str(assignment.get("task") or ""),
                "skills": [], "tools": [], "evidence": [], "findings": [],
                "limitations": [f"the employee run stopped early: {_safe_reason(exc)}"],
                "status": "failed",
                "started_at": now, "completed_at": now, "duration_ms": 0,
            })
    return results


def _default_adapter():
    from app.graphs.adapters import ToolRegistryAdapter

    return ToolRegistryAdapter()


__all__ = [
    "TOOL_MAP", "ToolSpec",
    "DOMAINS_WITHOUT_EXTERNAL_TOOL", "RESULT_KEYS", "STATUSES", "TOOL_STATUSES",
    "PlanRequiredError", "planned_tools", "emit_employee_queued",
    "run_employee", "run_employees",
]
