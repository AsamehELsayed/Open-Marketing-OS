"""Application state operations. All UI mutations go through here —
repos for storage, adapters for file→DB sync. Never writes source files."""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.database import repos
from app.database.identity import DEFAULT_PROJECT_ID, company_id_for
from app.services.adapters import importer
from app.services.adapters.registry import ADAPTERS


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event(conn, kind: str, ref_id: str | None, detail: dict) -> None:
    repos.Events.log(conn, {
        "id": uuid.uuid4().hex, "kind": kind, "ref_id": ref_id,
        "detail_json": json.dumps(detail), "created_at": now_iso(),
    })


# ---- reads ----

class NoActiveProject(Exception):
    """No usable active project — callers must redirect to project selection."""


def require_active_project(conn) -> str:
    """Active project id or raise NoActiveProject (fail closed for UI scope)."""
    try:
        row = repos.Settings.get(conn, "active_project_id")
        pid = (row.get("value", "") if row else "").strip()
    except Exception:
        pid = ""
    if not pid:
        raise NoActiveProject("no active project selected")
    try:
        proj = repos.Projects.get(conn, pid)
    except Exception:
        proj = None
    if proj is None or proj.get("status") == "archived":
        raise NoActiveProject(f"active project {pid!r} unavailable")
    return pid


def indexed_documents(conn, project_id: str | None = None) -> list[dict]:
    """RAG corpus inventory (path + status tag). Read-only."""
    rows = [dict(r) for r in conn.execute(
        "SELECT path, project_id, status_tag, indexed_at FROM documents ORDER BY path").fetchall()]
    if project_id is not None:
        rows = [r for r in rows
                if r.get("project_id", DEFAULT_PROJECT_ID) == project_id]
    return rows


def all_measurements(conn, project_id: str | None = None) -> list[dict]:
    """Every observation, chronological. Read-only."""
    rows = [dict(r) for r in
            conn.execute("SELECT * FROM measurements ORDER BY observed_at").fetchall()]
    if project_id is not None:
        try:
            exp_ids = {e["id"] for e in repos.Experiments.list(conn, project_id)}
        except Exception:
            exp_ids = set()
        rows = [m for m in rows if m.get("experiment_id") in exp_ids]
    return rows


def settings_map(conn) -> dict:
    """Key-value settings for the Settings page. Read-only."""
    return {r["key"]: r["value"] for r in
            conn.execute("SELECT key, value FROM settings").fetchall()}


def dashboard(conn, project_id: str | None = None) -> dict:
    approvals = repos.Approvals.list(conn, "pending", project_id)
    tasks = [t for t in repos.Tasks.list(conn, project_id) if t["status"] in ("open", "READY_FOR_APPROVAL")]
    experiments = repos.Experiments.list(conn, project_id)
    due = [e for e in experiments if e["status"] in ("measuring", "drafted")]
    learnings = repos.Learnings.list(conn, project_id)[-3:]
    return {
        "pending_approvals": approvals,
        "open_tasks": tasks[:3],
        "due_measurements": due,
        "recent_learnings": learnings,
        "conflicts": list_conflicts(conn),
        "chroma": (repos.Settings.get(conn, "chroma_status") or {}).get("value", "unknown"),
    }


def list_conflicts(conn) -> list[dict]:
    """Unresolved import_conflict events (a later conflict_resolved clears them)."""
    resolved = {e["ref_id"] for e in repos.Events.list(conn) if e["kind"] == "conflict_resolved"}
    out = []
    for e in repos.Events.list(conn):
        if e["kind"] == "import_conflict" and e["ref_id"] not in resolved:
            detail = json.loads(e["detail_json"] or "{}")
            current = None
            for table in ("campaigns", "tasks", "approvals", "experiments", "companies"):
                row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (e["ref_id"],)).fetchone()
                if row:
                    current = {"table": table, **dict(row)}
                    break
            out.append({"event": e, "adapter": detail.get("adapter", ""),
                        "source": detail.get("source", ""), "current": current})
    return out


# ---- mutations (SQLite only, always audited) ----

def decide_approval(conn, approval_id: str, decision: str, decided_by: str = "founder",
                    project_id: str | None = None) -> dict:
    if decision not in ("approved", "returned", "rejected"):
        raise ValueError(f"invalid decision: {decision}")
    row = repos.Approvals.get(conn, approval_id, project_id)
    if row is None:
        raise KeyError(approval_id)
    if row["status"] != "pending":
        raise ValueError(f"approval {approval_id} already decided ({row['status']})")
    row.update({"status": decision, "decided_by": decided_by,
                "decided_at": now_iso(), "updated_at": now_iso()})
    repos.Approvals.upsert(conn, row)
    _event(conn, "approval_decision", approval_id, {"decision": decision, "by": decided_by})
    return row


def set_task_status(conn, task_id: str, status: str, project_id: str | None = None) -> dict:
    if status not in ("open", "in_progress", "done", "blocked"):
        raise ValueError(f"invalid task status: {status}")
    row = repos.Tasks.get(conn, task_id, project_id)
    if row is None:
        raise KeyError(task_id)
    row.update({"status": status, "updated_at": now_iso()})
    repos.Tasks.upsert(conn, row)
    _event(conn, "task_status", task_id, {"status": status})
    return row


def set_campaign_status(conn, campaign_id: str, status: str,
                        project_id: str | None = None) -> dict:
    allowed = {"proposed", "drafted", "approved", "executing", "measuring",
               "learned", "rejected", "blocked"}
    if status not in allowed:
        raise ValueError(f"invalid campaign status: {status}")
    row = repos.Campaigns.get(conn, campaign_id, project_id)
    if row is None:
        raise KeyError(campaign_id)
    row.update({"status": status, "updated_at": now_iso()})
    repos.Campaigns.upsert(conn, row)
    _event(conn, "campaign_status", campaign_id, {"status": status})
    return row


def add_measurement(conn, experiment_id: str, evidence_md: str, decision: str = "none",
                    project_id: str | None = None) -> dict:
    if repos.Experiments.get(conn, experiment_id, project_id) is None:
        raise KeyError(experiment_id)
    row = {"id": uuid.uuid4().hex, "experiment_id": experiment_id,
           "observed_at": now_iso(), "evidence_md": evidence_md, "decision": decision}
    repos.Measurements.insert(conn, row)
    _event(conn, "measurement", experiment_id, {"decision": decision})
    return row


MEASUREMENT_DECISIONS = ("CONTINUE", "MODIFY", "MEASURE LONGER", "FOLLOW-UP TEST", "STOP", "SCALE")


def record_measurement_decision(conn, experiment_id: str, evidence_md: str, decision: str,
                                project_id: str | None = None) -> dict:
    """Validated measurement write path for the Results UI (Gate 3 Required 1)."""
    if decision not in MEASUREMENT_DECISIONS:
        raise ValueError(f"invalid decision: {decision}")
    if not (evidence_md or "").strip():
        raise ValueError("evidence note is required")
    return add_measurement(conn, experiment_id, evidence_md.strip(), decision, project_id)


# ---- imports + conflict resolution (file→DB only) ----

def run_imports(conn, root: str | Path) -> dict:
    root = Path(root)
    stats = {}
    for name, spec in ADAPTERS.items():
        parse = spec["parse"]
        # Only adapters that derive a row id from the database need the
        # connection; the rest read their row ids straight out of the file.
        rows = parse(root, conn) if spec.get("wants_conn") else parse(root)
        stats[name] = importer.import_rows(
            conn, name, root / spec["source"], rows, spec["upsert"], spec["get"])
    return stats


def resolve_conflict(conn, root: str | Path, adapter: str, row_id: str, keep: str) -> dict:
    """keep=db → row stays, import marker advances. keep=file → row refreshed from file."""
    if keep not in ("db", "file"):
        raise ValueError("keep must be 'db' or 'file'")
    spec = ADAPTERS.get(adapter)
    if spec is None:
        raise KeyError(adapter)
    root = Path(root)
    if keep == "file":
        parse = spec["parse"]
        rows = (parse(root, conn) if spec.get("wants_conn") else parse(root))
        rows = [r for r in rows if r["id"] == row_id]
        if not rows:
            raise KeyError(f"{row_id} not in {spec['source']}")
        importer.resolve_conflict_keep_file(conn, adapter, root / spec["source"], rows, spec["upsert"])
    else:
        importer.resolve_conflict_keep_db(conn, adapter, root / spec["source"])
    _event(conn, "conflict_resolved", row_id, {"adapter": adapter, "keep": keep})
    return {"adapter": adapter, "row_id": row_id, "keep": keep}


# ---- retrieval index (W4 wiring) ----

def build_index(conn, root: str | Path, chroma_dir: str | Path | None = None,
                project_id: str | None = None) -> dict:
    """Rebuild workspace and persisted-upload knowledge for the selected project.

    The third positional argument remains the legacy custom Chroma directory.
    Omitted scope follows the active project, failing closed if none is set.
    """
    from app.services.knowledge_index import rebuild_project_knowledge, retrieval_runtime

    root = Path(root)
    pid = (project_id or active_project_id(conn, "")).strip()
    if not pid:
        raise ValueError("project_id is required for knowledge rebuild")
    if chroma_dir is None:
        store, provider = retrieval_runtime(root, pid)
    else:
        from app.services.rag.chroma_store import ChromaStore
        from app.services.rag.embeddings import LocalHashEmbeddings
        provider = LocalHashEmbeddings()
        store = ChromaStore(chroma_dir, provider, project_id=pid)
    report = rebuild_project_knowledge(conn, root, pid, store=store, provider=provider)
    mode = getattr(store, "mode", "FTS_ONLY")
    repos.Settings.set(conn, "chroma_status", "hybrid" if mode == "HYBRID" else "fts_only", now_iso())
    _event(conn, "reindex", None, {k: v for k, v in report.items() if k not in ("workspace", "uploads", "errors")})
    report["mode"] = mode
    if mode != "HYBRID":
        report["offline_reason"] = report.get("vector_reason", "")
    return report


# ---- chat transcript (W6) ----

def active_project_id(conn, default: str = DEFAULT_PROJECT_ID) -> str:
    try:
        row = repos.Settings.get(conn, "active_project_id")
        if row and (row.get("value") or "").strip():
            return row["value"]
    except Exception:
        pass
    return default


def set_active_project(conn, project_id: str) -> dict:
    if repos.Projects.get(conn, project_id) is None:
        raise KeyError(project_id)
    repos.Settings.set(conn, "active_project_id", project_id, now_iso())
    return repos.Projects.get(conn, project_id)


def create_project(conn, name: str, website: str = "", goal: str = "") -> dict:
    """Minimal onboarding: name-only works (v0.2 §12)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("project name is required")
    pid = name.strip().lower().replace(" ", "-")[:40] or uuid.uuid4().hex[:8]
    base, suffix = pid, 1
    while repos.Projects.get(conn, pid) is not None:
        suffix += 1
        pid = f"{base}-{suffix}"
    row = {"id": pid, "name": name.strip(), "website": (website or "").strip(),
           "goal": (goal or "").strip(), "status": "active",
           "settings_json": json.dumps({}), "created_at": now_iso(), "updated_at": now_iso()}
    repos.Projects.upsert(conn, row)
    return row


def create_conversation(conn, project_id: str, company_id: str | None = None,
                        title: str = "Account Manager") -> dict:
    """Always-new conversation permanently bound to one project (v0.2.1: immutable)."""
    if not project_id or not str(project_id).strip():
        raise ValueError("project_id is required (fail closed)")
    if repos.Projects.get(conn, project_id) is None:
        raise KeyError(project_id)
    # The schema has always conflated company and project ids, so an omitted
    # company_id follows the project rather than defaulting to a fixed literal.
    # That keeps a legacy workspace (project_id="njm") writing to company "njm"
    # exactly as before, while a fresh install writes company "starter".
    company_id = company_id or company_id_for(project_id)
    if repos.Companies.get(conn, company_id) is None:
        repos.Companies.upsert(conn, {"id": company_id, "name": company_id,
                                      "website": "", "markets_json": "{}",
                                      "languages_json": "[]", "services_json": "[]",
                                      "created_at": now_iso()})
    row = {"id": uuid.uuid4().hex, "company_id": company_id, "project_id": project_id,
           "title": title, "status": "open", "created_at": now_iso(), "updated_at": now_iso()}
    repos.Conversations.upsert(conn, row)
    return row


def get_or_create_conversation(conn, company_id: str | None = None,
                               title: str = "Account Manager",
                               project_id: str | None = None) -> dict:
    if project_id is None:
        project_id = active_project_id(conn, company_id or DEFAULT_PROJECT_ID)
    # Resolved after the project so an omitted company_id follows the *actual*
    # project, including a legacy one. See create_conversation for why.
    company_id = company_id or company_id_for(project_id)
    try:
        if repos.Projects.get(conn, project_id) is None:
            create_project(conn, project_id)
    except Exception:
        pass
    if repos.Companies.get(conn, company_id) is None:
        repos.Companies.upsert(conn, {"id": company_id, "name": company_id,
                                      "website": "", "markets_json": "{}",
                                      "languages_json": "[]", "services_json": "[]",
                                      "created_at": now_iso()})
    open_convos = [c for c in repos.Conversations.list(conn, project_id)
                   if c.get("company_id", company_id) == company_id and c["status"] == "open"]
    if not open_convos:
        # fallback: legacy rows without project filter
        open_convos = [c for c in repos.Conversations.list(conn)
                       if c["company_id"] == company_id and c["status"] == "open"
                       and c.get("project_id", project_id) == project_id]
    if open_convos:
        return open_convos[0]
    row = {"id": uuid.uuid4().hex, "company_id": company_id, "project_id": project_id,
           "title": title, "status": "open", "created_at": now_iso(), "updated_at": now_iso()}
    try:
        repos.Conversations.upsert(conn, row)
    except Exception:
        row = {"id": uuid.uuid4().hex, "company_id": company_id, "title": title,
               "status": "open", "created_at": now_iso(), "updated_at": now_iso()}
        repos.Conversations.upsert(conn, row)
    return row


def add_message(conn, conversation_id: str, role: str, body_md: str, citations=None) -> dict:
    row = {"id": uuid.uuid4().hex, "conversation_id": conversation_id, "role": role,
           "body_md": body_md, "citations_json": json.dumps(citations or []),
           "created_at": now_iso()}
    try:
        repos.Messages.insert(conn, row)
    except Exception:
        row = {k: v for k, v in row.items()}
        repos.Messages.insert(conn, row)
    if role == "user":
        maybe_title_conversation(conn, conversation_id, body_md)
    return row


_TITLE_RULES = (
    (("instagram", "انستجرام", "انستغرام"), "Instagram Review"),
    (("competitor", "منافس", "منافسة"), "Competitor Research"),
    (("outreach", "agency", "agencies", "وكال"), "Agency Outreach"),
    (("week", "weekly", "الأسبوع", "اسبوع"), "Weekly Marketing Focus"),
    (("approval", "approve", "موافق"), "Approvals Review"),
    (("campaign", "حمل"), "Campaign Review"),
    (("plan", "خطة", "خطه"), "Marketing Plan"),
    (("customer", "client", "عميل", "عملاء"), "Customer Growth"),
)


def generate_chat_title(text: str) -> str:
    """Deterministic short title from the first user message. No LLM call."""
    lowered = (text or "").lower()
    for keywords, title in _TITLE_RULES:
        if any(k in lowered for k in keywords):
            return title
    first_line = (text or "").strip().split("\n")[0].strip()
    return first_line[:42] if first_line else "New conversation"


def maybe_title_conversation(conn, conversation_id: str, user_text: str) -> None:
    """Title an untitled conversation once, from its first user message."""
    try:
        convo = repos.Conversations.get(conn, conversation_id)
        if convo is None or (convo.get("title") or "") not in ("", "Account Manager", "New conversation"):
            return
        convo["title"] = generate_chat_title(user_text)
        convo["updated_at"] = now_iso()
        repos.Conversations.upsert(conn, convo)
    except Exception:
        pass


def archive_conversation(conn, conversation_id: str, project_id: str | None = None) -> dict:
    """Archive (never delete) a conversation. Project-scoped when given."""
    convo = repos.Conversations.get(conn, conversation_id, project_id)
    if convo is None:
        raise KeyError(conversation_id)
    convo["status"] = "archived"
    try:
        convo["archived_at"] = now_iso()
    except Exception:
        pass
    convo["updated_at"] = now_iso()
    repos.Conversations.upsert(conn, convo)
    return convo


def project_conversations(conn, project_id: str) -> list[dict]:
    """Open, non-archived conversations for the sidebar, newest first."""
    rows = [c for c in repos.Conversations.list(conn, project_id)
            if c.get("status") == "open" and not c.get("archived_at")]
    rows.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
    return rows
