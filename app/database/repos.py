"""Thin per-table repos. Routes/services use these — never raw SQL elsewhere."""
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.database.identity import DEFAULT_PROJECT_ID


def _insert(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})", list(row.values()))
    conn.commit()


def _insert_idempotent_proposal(conn: sqlite3.Connection, table: str,
                                row: dict[str, Any]) -> dict[str, Any]:
    """Insert a workflow proposal once; a key cannot be reused for other data."""
    record = dict(row)
    project_id = (record.get("project_id") or "").strip()
    proposal_id = (record.get("id") or "").strip()
    if not project_id or not proposal_id:
        raise ValueError("proposal requires project_id and id")
    existing = _get(conn, table, proposal_id)
    if existing:
        if existing.get("project_id") != project_id:
            raise ValueError(f"{table}/{proposal_id} belongs to another project")
        # updated_at may naturally differ on replay; all proposal content and
        # provenance must remain byte-for-byte equivalent.
        comparable = {k: v for k, v in existing.items() if k != "updated_at"}
        candidate = {k: v for k, v in record.items() if k != "updated_at"}
        if comparable != candidate:
            raise ValueError("idempotency key was already used with different proposal data")
        return existing
    _insert(conn, table, record)
    return record


def _get(conn: sqlite3.Connection, table: str, id: str) -> dict[str, Any] | None:
    r = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (id,)).fetchone()
    return dict(r) if r else None


def _list(conn: sqlite3.Connection, table: str, where: str = "", args: tuple = ()) -> list[dict[str, Any]]:
    q = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "")
    return [dict(r) for r in conn.execute(q, args).fetchall()]


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        return column in {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return False


def _scoped_list(conn, table: str, project_id: str | None = None,
                 extra_where: str = "", extra_args: tuple = ()) -> list[dict]:
    """Project-scoped list. project_id=None = legacy unfiltered (v0.1 compat)."""
    if project_id is not None and _has_column(conn, table, "project_id"):
        where = "project_id = ?" + (f" AND ({extra_where})" if extra_where else "")
        return _list(conn, table, where, (project_id, *extra_args))
    if extra_where:
        return _list(conn, table, extra_where, extra_args)
    return _list(conn, table)


def _scoped_get(conn, table: str, id: str, project_id: str | None = None) -> dict | None:
    row = _get(conn, table, id)
    if row is None:
        return None
    if project_id is not None and "project_id" in row and row["project_id"] != project_id:
        raise ValueError(f"{table}/{id} belongs to project {row['project_id']}, not {project_id}")
    return row


def _update_status(
    conn: sqlite3.Connection, table: str, id: str, status: str, extra: dict[str, Any] | None = None
) -> None:
    row = _get(conn, table, id)
    if row is None:
        raise KeyError(f"{table}/{id} not found")
    row["status"] = status
    if extra:
        row.update(extra)
    _insert(conn, table, row)


class Companies:
    TABLE = "companies"

    @staticmethod
    def upsert(conn, row): _insert(conn, Companies.TABLE, row)

    @staticmethod
    def get(conn, id): return _get(conn, Companies.TABLE, id)

    @staticmethod
    def list(conn): return _list(conn, Companies.TABLE)


class Projects:
    TABLE = "projects"

    @staticmethod
    def upsert(conn, row): _insert(conn, Projects.TABLE, row)

    @staticmethod
    def get(conn, id): return _get(conn, Projects.TABLE, id)

    @staticmethod
    def list(conn, status: str | None = None):
        if status:
            return _list(conn, Projects.TABLE, "status = ?", (status,))
        return _list(conn, Projects.TABLE)

    @staticmethod
    def archive(conn, id):
        row = _get(conn, Projects.TABLE, id)
        if row is None:
            raise KeyError(id)
        row["status"] = "archived"
        _insert(conn, Projects.TABLE, row)
        return row


class Memories:
    TABLE = "memories"
    KINDS = ("decision", "offer", "preference", "strategy", "learning")

    @staticmethod
    def insert(conn, row):
        if row.get("kind") not in Memories.KINDS:
            raise ValueError(f"invalid memory kind: {row.get('kind')}")
        if not (row.get("body_md") or "").strip():
            raise ValueError("memory body_md is required")
        _insert(conn, Memories.TABLE, row)

    @staticmethod
    def list(conn, project_id: str | None = None, kind: str | None = None):
        where, args = "", []
        clauses, params = [], []
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if clauses:
            where = " AND ".join(clauses)
        return _list(conn, Memories.TABLE, where, tuple(params))


class Conversations:
    TABLE = "conversations"

    @staticmethod
    def upsert(conn, row): _insert(conn, Conversations.TABLE, row)

    @staticmethod
    def get(conn, id, project_id: str | None = None):
        return _scoped_get(conn, Conversations.TABLE, id, project_id)

    @staticmethod
    def list(conn, project_id: str | None = None): return _scoped_list(conn, Conversations.TABLE, project_id)


class Messages:
    TABLE = "messages"

    @staticmethod
    def insert(conn, row): _insert(conn, Messages.TABLE, row)

    @staticmethod
    def for_conversation(conn, conversation_id):
        return _list(conn, Messages.TABLE, "conversation_id = ?", (conversation_id,))

    @staticmethod
    def by_client_id(conn, conversation_id, client_message_id):
        rows = _list(conn, Messages.TABLE,
                     "conversation_id = ? AND client_message_id = ?",
                     (conversation_id, client_message_id))
        return rows[0] if rows else None


class Turns:
    TABLE = "turns"
    TERMINAL = ("completed", "failed")

    @staticmethod
    def upsert(conn, row): _insert(conn, Turns.TABLE, row)

    @staticmethod
    def get(conn, id): return _get(conn, Turns.TABLE, id)

    @staticmethod
    def by_client_id(conn, conversation_id, client_message_id):
        rows = _list(conn, Turns.TABLE,
                     "conversation_id = ? AND client_message_id = ?",
                     (conversation_id, client_message_id))
        return rows[0] if rows else None

    @staticmethod
    def set_status(conn, id, status, extra=None):
        _update_status(conn, Turns.TABLE, id, status, extra)

    @staticmethod
    def latest_with_events(conn, conversation_id: str,
                           project_id: str | None = None) -> dict[str, Any] | None:
        """The conversation's most recent turn that has persisted execution
        events, newest first (created_at, then insertion order).

        Fail-closed scoping: the turn row must belong to the conversation_id
        passed in, and — when project_id is given — only events whose
        project_id matches that project (or legacy-empty) count, so a foreign
        project's events can never surface a turn here. Used by the SPA
        chat-reload replay read (DEV-008-SKILLS-OPS W12).
        """
        sql = (
            "SELECT t.* FROM turns t WHERE t.conversation_id = ? AND EXISTS ("
            "SELECT 1 FROM execution_events e WHERE e.turn_id = t.id"
        )
        args: list[Any] = [conversation_id]
        if project_id:
            sql += " AND e.project_id IN ('', ?)"
            args.append(project_id)
        sql += ") ORDER BY t.created_at DESC, t.rowid DESC LIMIT 1"
        row = conn.execute(sql, tuple(args)).fetchone()
        return dict(row) if row else None


class ExecutionEvents:
    TABLE = "execution_events"

    @staticmethod
    def insert(conn, row):
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        cur = conn.execute(f"INSERT INTO {ExecutionEvents.TABLE} ({cols}) VALUES ({placeholders})",
                           list(row.values()))
        conn.commit()
        return cur.lastrowid

    @staticmethod
    def for_turn(conn, turn_id, after_id: int = 0, limit: int = 200):
        return _list(conn, ExecutionEvents.TABLE, "turn_id = ? AND id > ? ORDER BY id",
                     (turn_id, after_id))[:limit]

    @staticmethod
    def for_conversation_jobs(conn, conversation_id, since: str = "", limit: int = 200):
        if since:
            return _list(conn, ExecutionEvents.TABLE,
                         "conversation_id = ? AND job_id <> '' AND created_at >= ? ORDER BY id",
                         (conversation_id, since))[:limit]
        return _list(conn, ExecutionEvents.TABLE,
                     "conversation_id = ? AND job_id <> '' ORDER BY id",
                     (conversation_id,))[:limit]

    @staticmethod
    def for_conversation_turn_terminal(conn, conversation_id, limit: int = 200):
        """Turn-scoped terminal events for reload (persisted with job_id='')."""
        return _list(conn, ExecutionEvents.TABLE,
                     "conversation_id = ? AND (job_id IS NULL OR job_id = '')"
                     " AND event_type IN ('turn_completed', 'assistant_completed', 'turn_failed') ORDER BY id",
                     (conversation_id,))[:limit]

    @staticmethod
    def for_job(conn, job_id, project_id: str | None = None, limit: int = 200):
        rows = _list(conn, ExecutionEvents.TABLE, "job_id = ? ORDER BY id", (job_id,))[:limit]
        if project_id is not None:
            rows = [r for r in rows if r.get("project_id") == project_id]
        return rows


class Campaigns:
    TABLE = "campaigns"

    @staticmethod
    def upsert(conn, row): _insert(conn, Campaigns.TABLE, row)

    @staticmethod
    def insert_proposal(conn, row):
        return _insert_idempotent_proposal(conn, Campaigns.TABLE, row)

    @staticmethod
    def get(conn, id, project_id: str | None = None):
        return _scoped_get(conn, Campaigns.TABLE, id, project_id)

    @staticmethod
    def list(conn, project_id: str | None = None): return _scoped_list(conn, Campaigns.TABLE, project_id)

    @staticmethod
    def set_status(conn, id, status): _update_status(conn, Campaigns.TABLE, id, status)


class CampaignDeliverables:
    """Repository methods for project/campaign-scoped deliverables."""

    @staticmethod
    def get(conn, project_id: str, campaign_id: str, deliverable_id: str):
        row = conn.execute(
            "SELECT * FROM campaign_deliverables "
            "WHERE project_id=? AND campaign_id=? AND id=?",
            (project_id, campaign_id, deliverable_id),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def list(conn, project_id: str, campaign_id: str):
        return _list(
            conn, "campaign_deliverables",
            "project_id=? AND campaign_id=? ORDER BY created_at, id",
            (project_id, campaign_id),
        )

    @staticmethod
    def insert(conn, row: dict[str, Any]) -> None:
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO campaign_deliverables ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )

    @staticmethod
    def update_if_version(conn, project_id: str, campaign_id: str,
                          deliverable_id: str, expected_version: int,
                          updates: dict[str, Any]) -> int:
        """Apply a validated field mapping only at the expected version."""
        if not updates:
            return 0
        assignments = ", ".join(f"{column}=?" for column in updates)
        values = list(updates.values())
        values.extend((project_id, campaign_id, deliverable_id, expected_version))
        result = conn.execute(
            f"UPDATE campaign_deliverables SET {assignments} "
            "WHERE project_id=? AND campaign_id=? AND id=? AND current_version=?",
            values,
        )
        return result.rowcount

    @staticmethod
    def insert_revision(conn, row: dict[str, Any]) -> None:
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO campaign_deliverable_revisions ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )

    @staticmethod
    def history(conn, project_id: str, campaign_id: str, deliverable_id: str):
        return _list(
            conn, "campaign_deliverable_revisions",
            "project_id=? AND campaign_id=? AND deliverable_id=? ORDER BY version",
            (project_id, campaign_id, deliverable_id),
        )

    @staticmethod
    def batch(conn, project_id: str, campaign_id: str, idempotency_key: str):
        row = conn.execute(
            "SELECT * FROM campaign_deliverable_batches "
            "WHERE project_id=? AND campaign_id=? AND idempotency_key=?",
            (project_id, campaign_id, idempotency_key),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def insert_batch(conn, row: dict[str, Any]) -> None:
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO campaign_deliverable_batches ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )


class Tasks:
    TABLE = "tasks"

    @staticmethod
    def upsert(conn, row): _insert(conn, Tasks.TABLE, row)

    @staticmethod
    def insert_proposal(conn, row):
        return _insert_idempotent_proposal(conn, Tasks.TABLE, row)

    @staticmethod
    def reject_proposal(conn, task_id: str, project_id: str, reason: str = ""):
        row = Tasks.get(conn, task_id, project_id)
        if row is None:
            raise ValueError(f"task {task_id} not in project {project_id}")
        if row.get("status") in ("rejected", "cancelled"):
            return row
        if row.get("status") != "proposed":
            raise ValueError("only proposed tasks can be rejected")
        row["status"] = "rejected"
        if reason:
            meta = json.loads(row.get("workflow_json") or "{}")
            meta["rejection_reason"] = reason
            row["workflow_json"] = json.dumps(meta, sort_keys=True)
        row["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _insert(conn, Tasks.TABLE, row)
        return row

    @staticmethod
    def get(conn, id, project_id: str | None = None):
        return _scoped_get(conn, Tasks.TABLE, id, project_id)

    @staticmethod
    def list(conn, project_id: str | None = None): return _scoped_list(conn, Tasks.TABLE, project_id)


class Approvals:
    TABLE = "approvals"

    @staticmethod
    def upsert(conn, row): _insert(conn, Approvals.TABLE, row)

    @staticmethod
    def insert_proposal(conn, row):
        return _insert_idempotent_proposal(conn, Approvals.TABLE, row)

    @staticmethod
    def get(conn, id, project_id: str | None = None):
        return _scoped_get(conn, Approvals.TABLE, id, project_id)

    @staticmethod
    def list(conn, status: str | None = None, project_id: str | None = None):
        if status and project_id is not None:
            if _has_column(conn, Approvals.TABLE, "project_id"):
                return _list(conn, Approvals.TABLE, "status = ? AND project_id = ?", (status, project_id))
            return _list(conn, Approvals.TABLE, "status = ?", (status,))
        if status:
            return _list(conn, Approvals.TABLE, "status = ?", (status,))
        return _scoped_list(conn, Approvals.TABLE, project_id)


class Prospects:
    TABLE = "prospects"

    @staticmethod
    def upsert(conn, row): _insert(conn, Prospects.TABLE, row)

    @staticmethod
    def list(conn, campaign_id: str | None = None, project_id: str | None = None):
        if campaign_id and project_id is not None:
            if _has_column(conn, Prospects.TABLE, "project_id"):
                return _list(conn, Prospects.TABLE, "campaign_id = ? AND project_id = ?",
                             (campaign_id, project_id))
            return _list(conn, Prospects.TABLE, "campaign_id = ?", (campaign_id,))
        if campaign_id:
            return _list(conn, Prospects.TABLE, "campaign_id = ?", (campaign_id,))
        return _scoped_list(conn, Prospects.TABLE, project_id)


class Experiments:
    TABLE = "experiments"

    @staticmethod
    def upsert(conn, row): _insert(conn, Experiments.TABLE, row)

    @staticmethod
    def insert_proposal(conn, row):
        return _insert_idempotent_proposal(conn, Experiments.TABLE, row)

    @staticmethod
    def get(conn, id, project_id: str | None = None):
        return _scoped_get(conn, Experiments.TABLE, id, project_id)

    @staticmethod
    def list(conn, project_id: str | None = None): return _scoped_list(conn, Experiments.TABLE, project_id)


class Measurements:
    TABLE = "measurements"

    @staticmethod
    def insert(conn, row): _insert(conn, Measurements.TABLE, row)

    @staticmethod
    def for_experiment(conn, experiment_id):
        return _list(conn, Measurements.TABLE, "experiment_id = ?", (experiment_id,))


class Learnings:
    TABLE = "learnings"

    @staticmethod
    def insert(conn, row):
        if not row.get("source") or not row.get("observed_at"):
            raise ValueError("learnings require source + observed_at (sourced+dated gate)")
        _insert(conn, Learnings.TABLE, row)

    @staticmethod
    def list(conn, project_id: str | None = None): return _scoped_list(conn, Learnings.TABLE, project_id)


class Settings:
    TABLE = "settings"

    @staticmethod
    def set(conn, key, value, updated_at):
        _insert(conn, Settings.TABLE, {"key": key, "value": value, "updated_at": updated_at})

    @staticmethod
    def get(conn, key):
        r = conn.execute("SELECT * FROM settings WHERE key = ?", (key,)).fetchone()
        return dict(r) if r else None


class Events:
    TABLE = "events"

    @staticmethod
    def log(conn, row): _insert(conn, Events.TABLE, row)

    @staticmethod
    def list(conn): return _list(conn, Events.TABLE)


class BackgroundJobs:
    TABLE = "background_jobs"

    @staticmethod
    def upsert(conn, row): _insert(conn, BackgroundJobs.TABLE, row)

    @staticmethod
    def get(conn, id, project_id: str | None = None):
        return _scoped_get(conn, BackgroundJobs.TABLE, id, project_id)

    @staticmethod
    def list(conn, project_id: str | None = None): return _scoped_list(conn, BackgroundJobs.TABLE, project_id)


class ModelCalls:
    """DEV-005 W5: additive model-call telemetry. Unknown usage stays NULL.

    All writes go through insert(); reads are project/turn scoped. Token
    totals treat NULL as unknown (never 0-filled); turn_totals sums only
    known legs and returns None when a leg has no known values.
    """
    TABLE = "model_calls"
    TOKEN_FIELDS = ("input_tokens", "cached_tokens", "output_tokens",
                    "reasoning_tokens", "total_tokens")

    @staticmethod
    def insert(conn, row: dict) -> dict:
        record = dict(row)
        if not (record.get("call_id") or "").strip():
            raise ValueError("model_calls require call_id")
        if not (record.get("turn_id") or "").strip():
            raise ValueError("model_calls require turn_id")
        if not (record.get("project_id") or "").strip():
            raise ValueError("model_calls require project_id")
        if not (record.get("provider") or "").strip():
            raise ValueError("model_calls require provider")
        if not (record.get("model") or "").strip():
            raise ValueError("model_calls require model")
        if not (record.get("route_reason") or "").strip():
            raise ValueError("model_calls require route_reason")
        record.setdefault("adapter", "")
        record.setdefault("quantization", "")
        record.setdefault("requested_model", "")
        record.setdefault("behavior_profile", "")
        record.setdefault("route_mode", "AUTO")
        record.setdefault("pricing_version", "")
        record.setdefault("cost_note", "")
        record.setdefault("started_at", "")
        record.setdefault("ended_at", "")
        for field in ModelCalls.TOKEN_FIELDS:
            record.setdefault(field, None)
        if record.get("latency_ms") is None:
            record["latency_ms"] = 0
        record["latency_ms"] = max(0, int(record["latency_ms"]))
        _insert(conn, ModelCalls.TABLE, record)
        return record

    @staticmethod
    def get(conn, call_id: str, project_id: str | None = None) -> dict | None:
        rows = _list(conn, ModelCalls.TABLE, "call_id = ?", (call_id,))
        row = rows[0] if rows else None
        if row is None:
            return None
        if project_id is not None and row.get("project_id") != project_id:
            raise ValueError(
                f"{ModelCalls.TABLE}/{call_id} belongs to project "
                f"{row.get('project_id')}, not {project_id}"
            )
        return row

    @staticmethod
    def for_turn(conn, turn_id: str, project_id: str | None = None) -> list[dict]:
        """Turn-scoped reads require project scope (fail closed).

        Filters in SQL on turn_id AND project_id before rows return, so a
        turn_id shared across projects never leaks the other project's rows.
        """
        if not (turn_id or "").strip():
            raise ValueError("model_calls for_turn requires turn_id")
        if not (project_id or "").strip():
            raise ValueError("model_calls for_turn requires project_id")
        return _list(conn, ModelCalls.TABLE,
                     "turn_id = ? AND project_id = ? ORDER BY rowid",
                     (turn_id, project_id))

    @staticmethod
    def for_project(conn, project_id: str) -> list[dict]:
        return _scoped_list(conn, ModelCalls.TABLE, project_id)

    @staticmethod
    def turn_totals(conn, turn_id: str, project_id: str | None = None) -> dict:
        if not (turn_id or "").strip():
            raise ValueError("model_calls turn_totals requires turn_id")
        if not (project_id or "").strip():
            raise ValueError("model_calls turn_totals requires project_id")
        totals: dict[str, int | None] = {}
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            vals = [r[field] for r in ModelCalls.for_turn(conn, turn_id, project_id)
                    if r.get(field) is not None]
            totals[field] = sum(vals) if vals else None
        return totals


class SocialAccounts:
    """DEV-003 C2: project-scoped social identity. All reads via _scoped_*."""
    TABLE = "social_accounts"
    PLATFORMS = ("instagram", "tiktok", "x", "facebook", "linkedin", "youtube")
    STATUSES = ("VERIFIED", "LIKELY", "UNVERIFIED")
    SOURCES = ("website", "manual", "discovery", "migration")
    HANDLE_RE = re.compile(r"^[\w.]{1,80}$")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _clean_handle(raw: str) -> str:
        return (raw or "").strip().lstrip("@").strip()

    @staticmethod
    def _validate(platform: str, handle: str, status: str, source: str) -> str:
        if platform not in SocialAccounts.PLATFORMS:
            raise ValueError(f"invalid platform: {platform}")
        cleaned = SocialAccounts._clean_handle(handle)
        if not SocialAccounts.HANDLE_RE.match(cleaned):
            raise ValueError(f"invalid handle: {handle!r}")
        if status not in SocialAccounts.STATUSES:
            raise ValueError(f"invalid status: {status}")
        if source not in SocialAccounts.SOURCES:
            raise ValueError(f"invalid source: {source}")
        return cleaned

    @staticmethod
    def upsert(conn, row):
        cleaned = SocialAccounts._validate(
            row.get("platform", ""), row.get("handle", ""),
            row.get("status", "LIKELY"), row.get("source", "manual"),
        )
        row = dict(row)
        row["handle"] = cleaned
        row.setdefault("project_id", DEFAULT_PROJECT_ID)
        if not row.get("id"):
            row["id"] = f"{row['project_id']}:{row['platform']}"
        existing = _get(conn, SocialAccounts.TABLE, row["id"])
        if existing is not None and existing.get("project_id") != row["project_id"]:
            raise ValueError(
                f"{SocialAccounts.TABLE}/{row['id']} belongs to project "
                f"{existing.get('project_id')}, not {row['project_id']}"
            )
        now = SocialAccounts._now()
        row.setdefault("status", "LIKELY")
        row.setdefault("source", "manual")
        row.setdefault("url", "")
        row.setdefault("evidence_url", "")
        row.setdefault("observed_at", "")
        row.setdefault("created_at", existing.get("created_at") if existing else now)
        row.setdefault("updated_at", now)
        _insert(conn, SocialAccounts.TABLE, row)

    @staticmethod
    def get(conn, id, project_id: str | None = None):
        return _scoped_get(conn, SocialAccounts.TABLE, id, project_id)

    @staticmethod
    def for_project(conn, project_id) -> list[dict]:
        rows = _scoped_list(conn, SocialAccounts.TABLE, project_id)
        rank = {"VERIFIED": 0, "LIKELY": 1, "UNVERIFIED": 2}
        return sorted(rows, key=lambda r: (rank.get(r.get("status"), 3), r.get("platform", "")))

    @staticmethod
    def set_handle(conn, project_id, platform, handle, *,
                   status="LIKELY", source="manual", evidence_url="") -> dict:
        cleaned = SocialAccounts._validate(platform, handle, status, source)
        row_id = f"{project_id}:{platform}"
        existing = _get(conn, SocialAccounts.TABLE, row_id)
        now = SocialAccounts._now()
        if existing is not None:
            if existing.get("project_id") != project_id:
                raise ValueError(
                    f"{SocialAccounts.TABLE}/{row_id} belongs to project "
                    f"{existing.get('project_id')}, not {project_id}"
                )
            existing["handle"] = cleaned
            existing["status"] = status
            existing["source"] = source
            existing["evidence_url"] = evidence_url or ""
            existing["observed_at"] = now
            existing["updated_at"] = now
            if "url" not in existing:
                existing["url"] = ""
            _insert(conn, SocialAccounts.TABLE, existing)
            return dict(existing)
        row = {
            "id": row_id, "project_id": project_id, "platform": platform,
            "handle": cleaned, "url": "", "status": status, "source": source,
            "evidence_url": evidence_url or "", "observed_at": now,
            "created_at": now, "updated_at": now,
        }
        _insert(conn, SocialAccounts.TABLE, row)
        return row

    @staticmethod
    def remove(conn, project_id, platform) -> None:
        if not (project_id or "").strip():
            raise ValueError("project_id is required")
        if platform not in SocialAccounts.PLATFORMS:
            raise ValueError(f"invalid platform: {platform}")
        row_id = f"{project_id}:{platform}"
        existing = _get(conn, SocialAccounts.TABLE, row_id)
        if existing is not None and existing.get("project_id") != project_id:
            raise ValueError(
                f"{SocialAccounts.TABLE}/{row_id} belongs to project "
                f"{existing.get('project_id')}, not {project_id}"
            )
        conn.execute(
            "DELETE FROM social_accounts WHERE project_id = ? AND platform = ?",
            (project_id, platform),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# DEV-007: Repositories for Tool Runs, Integrations, Vault Refs, MCP, Files
# ---------------------------------------------------------------------------

class ToolRuns:
    TABLE = "tool_runs"

    @staticmethod
    def list_for_project(conn, project_id: str, limit: int = 100) -> list[dict]:
        if not (project_id or "").strip():
            raise ValueError("project_id is required")
        return _list(conn, ToolRuns.TABLE,
                     "project_id = ? ORDER BY started_at DESC LIMIT ?",
                     (project_id, limit))

    @staticmethod
    def for_tool(conn, tool_id: str, project_id: str | None = None, limit: int = 100) -> list[dict]:
        if project_id:
            return _list(conn, ToolRuns.TABLE,
                         "tool_id = ? AND project_id = ? ORDER BY started_at DESC LIMIT ?",
                         (tool_id, project_id, limit))
        return _list(conn, ToolRuns.TABLE,
                     "tool_id = ? ORDER BY started_at DESC LIMIT ?",
                     (tool_id, limit))

    @staticmethod
    def latest_health(conn, project_id: str) -> dict[str, str]:
        rows = _list(conn, ToolRuns.TABLE,
                     "project_id = ? ORDER BY started_at ASC", (project_id,))
        out: dict[str, str] = {}
        for r in rows:
            out[r["tool_id"]] = r["status"]
        return out


class Integrations:
    TABLE = "integrations"

    @staticmethod
    def upsert(conn, row: dict) -> dict:
        _insert(conn, Integrations.TABLE, row)
        return row

    @staticmethod
    def get(conn, project_id: str, scope: str, integration_id: str, capability: str) -> dict | None:
        rows = _list(conn, Integrations.TABLE,
                     "project_id = ? AND scope = ? AND integration_id = ? AND capability = ?",
                     (project_id, scope, integration_id, capability))
        return rows[0] if rows else None

    @staticmethod
    def list_for_project(conn, project_id: str) -> list[dict]:
        return _list(conn, Integrations.TABLE,
                     "project_id = ? OR project_id = '' ORDER BY created_at ASC",
                     (project_id,))

    @staticmethod
    def find_for_capability(conn, capability: str, project_id: str) -> dict | None:
        # First check project-scoped, then installation-scoped ('')
        rows = _list(conn, Integrations.TABLE,
                     "capability = ? AND project_id = ? ORDER BY updated_at DESC",
                     (capability, project_id))
        if rows:
            return rows[0]
        inst_rows = _list(conn, Integrations.TABLE,
                          "capability = ? AND project_id = '' ORDER BY updated_at DESC",
                          (capability,))
        return inst_rows[0] if inst_rows else None

    @staticmethod
    def delete(conn, project_id: str, scope: str, integration_id: str, capability: str) -> None:
        conn.execute(
            "DELETE FROM integrations WHERE project_id = ? AND scope = ? AND integration_id = ? AND capability = ?",
            (project_id, scope, integration_id, capability),
        )
        conn.commit()


class CredentialRefs:
    TABLE = "credentials_refs"

    @staticmethod
    def insert(conn, row: dict) -> dict:
        _insert(conn, CredentialRefs.TABLE, row)
        return row

    @staticmethod
    def get(conn, secret_ref: str) -> dict | None:
        rows = _list(conn, CredentialRefs.TABLE, "secret_ref = ?", (secret_ref,))
        return rows[0] if rows else None

    @staticmethod
    def active_for(conn, scope: str, label: str, project_id: str = "", user_id: str = "") -> dict | None:
        rows = _list(conn, CredentialRefs.TABLE,
                     "scope = ? AND label = ? AND project_id = ? AND user_id = ? AND status = 'active'",
                     (scope, label, project_id, user_id))
        return rows[0] if rows else None

    @staticmethod
    def mark_revoked(conn, secret_ref: str, revoked_at: str) -> None:
        conn.execute(
            "UPDATE credentials_refs SET status = 'revoked', revoked_at = ? WHERE secret_ref = ?",
            (revoked_at, secret_ref),
        )
        conn.commit()

    @staticmethod
    def list_active(conn) -> list[dict]:
        return _list(conn, CredentialRefs.TABLE, "status = 'active' ORDER BY created_at ASC", ())


class McpServers:
    TABLE = "mcp_servers"

    @staticmethod
    def upsert(conn, row: dict) -> dict:
        _insert(conn, McpServers.TABLE, row)
        return row

    @staticmethod
    def get(conn, server_id: str) -> dict | None:
        rows = _list(conn, McpServers.TABLE, "server_id = ?", (server_id,))
        return rows[0] if rows else None

    @staticmethod
    def list(conn) -> list[dict]:
        return _list(conn, McpServers.TABLE, "1 = 1 ORDER BY server_id ASC", ())

    @staticmethod
    def set_health(conn, server_id: str, status: str, checked_at: str, connected: bool) -> None:
        conn.execute(
            "UPDATE mcp_servers SET last_health = ?, last_checked_at = ?, connected = ? WHERE server_id = ?",
            (status, checked_at, int(connected), server_id),
        )
        conn.commit()

    @staticmethod
    def delete(conn, server_id: str) -> None:
        conn.execute("DELETE FROM mcp_tools_allowlist WHERE server_id = ?", (server_id,))
        conn.execute("DELETE FROM mcp_servers WHERE server_id = ?", (server_id,))
        conn.commit()


class McpToolsAllowlist:
    TABLE = "mcp_tools_allowlist"

    @staticmethod
    def set(conn, server_id: str, project_id: str, allowed: list[str],
            granted_by: str = "", granted_at: str = "") -> None:
        if not (project_id or "").strip():
            raise ValueError("project_id is required")
        conn.execute(
            "DELETE FROM mcp_tools_allowlist WHERE server_id = ? AND project_id = ?",
            (server_id, project_id),
        )
        for tool_name in allowed:
            _insert(conn, McpToolsAllowlist.TABLE, {
                "server_id": server_id,
                "project_id": project_id,
                "tool_name": tool_name,
                "granted_by": granted_by,
                "granted_at": granted_at,
            })
        conn.commit()

    @staticmethod
    def list_for(conn, server_id: str, project_id: str) -> list[str]:
        if not (project_id or "").strip():
            raise ValueError("project_id is required")
        rows = _list(conn, McpToolsAllowlist.TABLE,
                     "server_id = ? AND project_id = ? ORDER BY tool_name ASC",
                     (server_id, project_id))
        return [r["tool_name"] for r in rows]

    @staticmethod
    def is_allowed(conn, server_id: str, project_id: str, tool_name: str) -> bool:
        if not (project_id or "").strip():
            return False
        rows = _list(conn, McpToolsAllowlist.TABLE,
                     "server_id = ? AND project_id = ? AND tool_name = ?",
                     (server_id, project_id, tool_name))
        return bool(rows)

    @staticmethod
    def add(conn, server_id: str, project_id: str, tool_name: str,
            granted_by: str = "", granted_at: str = "") -> None:
        if not (project_id or "").strip():
            raise ValueError("project_id is required")
        _insert(conn, McpToolsAllowlist.TABLE, {
            "server_id": server_id,
            "project_id": project_id,
            "tool_name": tool_name,
            "granted_by": granted_by,
            "granted_at": granted_at,
        })

    @staticmethod
    def remove(conn, server_id: str, project_id: str, tool_name: str) -> None:
        if not (project_id or "").strip():
            raise ValueError("project_id is required")
        conn.execute(
            "DELETE FROM mcp_tools_allowlist WHERE server_id = ? AND project_id = ? AND tool_name = ?",
            (server_id, project_id, tool_name),
        )
        conn.commit()

    @staticmethod
    def clear_project(conn, server_id: str, project_id: str) -> None:
        if not (project_id or "").strip():
            raise ValueError("project_id is required")
        conn.execute(
            "DELETE FROM mcp_tools_allowlist WHERE server_id = ? AND project_id = ?",
            (server_id, project_id),
        )
        conn.commit()


class ProjectFiles:
    TABLE = "project_files"

    @staticmethod
    def get(conn, file_id: str, project_id: str) -> dict | None:
        if not (project_id or "").strip():
            raise ValueError("project_id is required")
        rows = _list(conn, ProjectFiles.TABLE,
                     "file_id = ? AND project_id = ?", (file_id, project_id))
        return rows[0] if rows else None

    @staticmethod
    def list_for_project(conn, project_id: str) -> list[dict]:
        if not (project_id or "").strip():
            raise ValueError("project_id is required")
        return _list(conn, ProjectFiles.TABLE,
                     "project_id = ? ORDER BY created_at DESC", (project_id,))

    @staticmethod
    def upsert(conn, row: dict) -> dict:
        if not (row.get("project_id") or "").strip():
            raise ValueError("project_id is required")
        _insert(conn, ProjectFiles.TABLE, row)
        return row

    @staticmethod
    def delete(conn, file_id: str, project_id: str) -> None:
        if not (project_id or "").strip():
            raise ValueError("project_id is required")
        conn.execute(
            "DELETE FROM project_files WHERE file_id = ? AND project_id = ?",
            (file_id, project_id),
        )
        conn.commit()
