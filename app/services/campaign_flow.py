"""Owner-facing campaign actions: answer questions, generate, ask for changes.

These are the UI-driven steps between "OMOS understood my request" and "I have
an approved package". Every action goes through the same production path the
chat uses (``campaign_production.generate_batch`` / ``revise_deliverable``, the
ModelRouter, output validation, and the deliverable store), so nothing here can
save content that chat generation could not.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from app.services import campaign_deliverables as deliverables
from app.services import campaign_production as production
from app.services import campaign_request
from app.services.business_workspace import get_profile

MAX_INSTRUCTION_CHARS = 1000
_TEXT_LIMITS = {"objective": 300, "target_audience": 200, "duration": 60,
                "offer": 200, "geography": 100, "business": 100, "product": 120}


class FlowError(RuntimeError):
    """A refused or failed action, with an HTTP-ish status for the route."""

    def __init__(self, message: str, *, status: int = 422, code: str = "",
                 extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.extra = dict(extra or {})


def _intake_of(campaign: dict) -> dict:
    try:
        meta = json.loads(campaign.get("workflow_json") or "{}")
    except (TypeError, ValueError):
        return {}
    intake = meta.get("campaign_intake") if isinstance(meta, dict) else None
    return dict(intake) if isinstance(intake, dict) else {}


def provider_selection(conn) -> str:
    """The owner's configured AI route, defaulting to AUTO."""
    try:
        from app.services.config_service import ConfigService

        value = ConfigService.get_setting("manager_provider", "auto", conn=conn)
    except Exception:
        value = "auto"
    return production.resolve_mode(str(value or "auto"))


def save_answers(conn, project_id: str, campaign_id: str, answers: dict) -> dict:
    """Record details the owner filled in. Only known fields, trimmed and bounded."""
    campaign = deliverables._require_scope(conn, project_id, campaign_id)
    if not isinstance(answers, dict) or not answers:
        raise FlowError("Nothing to save.")
    intake = _intake_of(campaign)
    changed = False
    for field, raw in answers.items():
        if field not in campaign_request.EDITABLE_FIELDS:
            raise FlowError(f"'{field}' cannot be edited here.")
        if field == "channels":
            items = raw if isinstance(raw, list) else re.split(r"[,/]| and ", str(raw or ""))
            channels = [str(i).strip().lower() for i in items if str(i).strip()]
            unknown = [c for c in channels if c not in campaign_request.CHANNEL_LABELS]
            if unknown:
                raise FlowError("Choose channels from the list: "
                                + ", ".join(campaign_request.CHANNEL_LABELS.values()) + ".")
            value = list(dict.fromkeys(channels))[:12]
        else:
            if not isinstance(raw, str):
                raise FlowError(f"'{field}' must be text.")
            value = re.sub(r"\s+", " ", raw).strip()[:_TEXT_LIMITS[field]]
        if value:
            intake[field] = value
        else:
            intake.pop(field, None)
        changed = True
    if changed:
        meta = json.loads(campaign.get("workflow_json") or "{}")
        meta = meta if isinstance(meta, dict) else {}
        meta["campaign_intake"] = intake
        if not production._write_campaign_metadata(conn, project_id, campaign_id, meta):
            raise FlowError("The details could not be saved.", status=500)
    return intake


_GEN_LOCKS: dict[str, threading.Lock] = {}
_GEN_LOCKS_GUARD = threading.Lock()
DEFAULT_LEASE_TIMEOUT_SECONDS = 60
HEARTBEAT_INTERVAL_SECONDS = 5.0


def _is_lease_active(
    in_prog: dict | None,
    *,
    default_timeout_s: float = DEFAULT_LEASE_TIMEOUT_SECONDS,
    now_dt: datetime | None = None,
) -> tuple[bool, float, str]:
    """Return ``(is_active, elapsed_seconds, active_timestamp_str)`` for a lease dict."""
    if not isinstance(in_prog, dict):
        return False, 0.0, ""
    active_str = str(in_prog.get("heartbeat_at") or in_prog.get("started_at") or "").strip()
    if not active_str:
        return False, 0.0, ""
    try:
        active_dt = datetime.fromisoformat(active_str)
    except Exception:
        return False, 0.0, ""
    if active_dt.tzinfo is None:
        active_dt = active_dt.replace(tzinfo=timezone.utc)
    current_dt = now_dt or datetime.now(timezone.utc)
    elapsed = (current_dt - active_dt).total_seconds()
    try:
        raw_timeout = in_prog.get("lease_timeout_s")
        owner_timeout = float(raw_timeout if raw_timeout is not None else default_timeout_s)
    except (TypeError, ValueError):
        owner_timeout = float(default_timeout_s)
    if owner_timeout <= 0:
        return False, elapsed, active_str
    return elapsed < owner_timeout, elapsed, active_str


def _acquire_db_lock(conn, project_id: str, campaign_id: str, turn_id: str,
                     *, lease_timeout_s: float = DEFAULT_LEASE_TIMEOUT_SECONDS) -> str:
    """Acquire generation lease in database using an atomic transaction.

    Returns the unique owner token for fencing. Raises FlowError(409) if an active lease exists.
    """
    deliverables._require_scope(conn, project_id, campaign_id)
    if getattr(conn, "in_transaction", False):
        try:
            conn.commit()
        except Exception:
            pass

    try:
        with deliverables._atomic(conn):
            row = conn.execute(
                "SELECT workflow_json FROM campaigns WHERE id=? AND project_id=?",
                (campaign_id, project_id),
            ).fetchone()
            if not row:
                raise deliverables.DeliverableNotFound(
                    f"Campaign {campaign_id} not found in project {project_id}"
                )

            raw_meta = (row["workflow_json"] if hasattr(row, "keys") else row[0]) or "{}"
            try:
                meta = json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta)
            except (TypeError, ValueError):
                meta = {}

            in_prog = meta.get("generation_in_progress")
            now_dt = datetime.now(timezone.utc)
            active, elapsed, active_str = _is_lease_active(
                in_prog, default_timeout_s=lease_timeout_s, now_dt=now_dt
            )
            if active and isinstance(in_prog, dict):
                started_str = str(in_prog.get("started_at") or active_str)
                try:
                    started_dt = datetime.fromisoformat(started_str)
                    if started_dt.tzinfo is None:
                        started_dt = started_dt.replace(tzinfo=timezone.utc)
                    total_elapsed = int((now_dt - started_dt).total_seconds())
                except Exception:
                    total_elapsed = int(elapsed)
                raise FlowError(
                    f"Content is already being written for this campaign (started {total_elapsed}s ago).",
                    status=409,
                    code="generation_in_progress",
                    extra={"started_at": started_str, "elapsed_s": total_elapsed},
                )

            lease_token = uuid.uuid4().hex
            now_iso = now_dt.isoformat()
            meta["generation_in_progress"] = {
                "owner_token": lease_token,
                "pid": os.getpid(),
                "turn_id": str(turn_id or ""),
                "started_at": now_iso,
                "heartbeat_at": now_iso,
                "lease_timeout_s": float(lease_timeout_s),
            }
            conn.execute(
                "UPDATE campaigns SET workflow_json=? WHERE id=? AND project_id=?",
                (json.dumps(meta, sort_keys=True), campaign_id, project_id),
            )
            return lease_token
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise FlowError(
                "Database is currently busy; retry in a moment.",
                status=409,
                code="generation_in_progress",
            ) from exc
        raise FlowError(f"Database lock error: {exc}", status=500) from exc


def _heartbeat_db_lock(conn, project_id: str, campaign_id: str, lease_token: str) -> bool:
    """Extend the lease for an active in-flight generation. Returns True if lease renewed."""
    if not lease_token:
        return False
    if getattr(conn, "in_transaction", False):
        try:
            conn.commit()
        except Exception:
            pass
    try:
        with deliverables._atomic(conn):
            row = conn.execute(
                "SELECT workflow_json FROM campaigns WHERE id=? AND project_id=?",
                (campaign_id, project_id),
            ).fetchone()
            if not row:
                return False
            raw_meta = (row["workflow_json"] if hasattr(row, "keys") else row[0]) or "{}"
            meta = json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta)
            in_prog = meta.get("generation_in_progress")
            now_dt = datetime.now(timezone.utc)
            active, _elapsed, _active_str = _is_lease_active(in_prog, now_dt=now_dt)
            if active and isinstance(in_prog, dict) and in_prog.get("owner_token") == lease_token:
                in_prog["heartbeat_at"] = now_dt.isoformat()
                conn.execute(
                    "UPDATE campaigns SET workflow_json=? WHERE id=? AND project_id=?",
                    (json.dumps(meta, sort_keys=True), campaign_id, project_id),
                )
                return True
            return False
    except Exception:
        return False


def _verify_db_lock(conn, project_id: str, campaign_id: str, lease_token: str) -> bool:
    """Fencing check: verify that this owner_token still holds an unexpired active lease."""
    if not lease_token:
        return False
    try:
        row = conn.execute(
            "SELECT workflow_json FROM campaigns WHERE id=? AND project_id=?",
            (campaign_id, project_id),
        ).fetchone()
        if not row:
            return False
        raw_meta = (row["workflow_json"] if hasattr(row, "keys") else row[0]) or "{}"
        meta = json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta)
        in_prog = meta.get("generation_in_progress")
        if not isinstance(in_prog, dict) or in_prog.get("owner_token") != lease_token:
            return False
        active, _elapsed, _active_str = _is_lease_active(in_prog)
        return active
    except Exception:
        return False


def _release_db_lock(conn, project_id: str, campaign_id: str,
                     lease_token: str | None = None, *, force: bool = False) -> bool:
    """Release generation lock in database. Only the lease owner (or force) may release."""
    if lease_token is None and not force:
        return False
    if getattr(conn, "in_transaction", False):
        try:
            conn.commit()
        except Exception:
            pass
    try:
        with deliverables._atomic(conn):
            row = conn.execute(
                "SELECT workflow_json FROM campaigns WHERE id=? AND project_id=?",
                (campaign_id, project_id),
            ).fetchone()
            if not row:
                return False
            raw_meta = (row["workflow_json"] if hasattr(row, "keys") else row[0]) or "{}"
            meta = json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta)
            in_prog = meta.get("generation_in_progress")
            if force or (
                isinstance(in_prog, dict)
                and (lease_token is None or in_prog.get("owner_token") == lease_token)
            ):
                meta.pop("generation_in_progress", None)
                conn.execute(
                    "UPDATE campaigns SET workflow_json=? WHERE id=? AND project_id=?",
                    (json.dumps(meta, sort_keys=True), campaign_id, project_id),
                )
                return True
            return False
    except Exception:
        return False


def _start_lease_heartbeat(conn, project_id: str, campaign_id: str, lease_token: str,
                           stop_event: threading.Event,
                           interval_s: float = HEARTBEAT_INTERVAL_SECONDS) -> threading.Thread | None:
    db_path = None
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        for r in rows:
            f = r["file"] if hasattr(r, "keys") else r[2]
            if f and f not in ("", ":memory:"):
                db_path = Path(f)
                break
    except Exception:
        pass
    if not db_path:
        return None

    def _worker():
        hb_conn = None
        try:
            while not stop_event.wait(interval_s):
                try:
                    if hb_conn is None:
                        hb_conn = sqlite3.connect(str(db_path), timeout=5.0)
                    ok = _heartbeat_db_lock(hb_conn, project_id, campaign_id, lease_token)
                    if not ok:
                        break
                except Exception:
                    break
        except Exception:
            pass
        finally:
            if hb_conn is not None:
                try:
                    hb_conn.close()
                except Exception:
                    pass

    t = threading.Thread(target=_worker, daemon=True, name=f"lease-hb-{campaign_id[:8]}")
    t.start()
    return t


def generate_from_plan(conn, *, project_id: str, campaign_id: str,
                       turn_id: str = "", provider: str = "",
                       model_id: str = "",
                       lease_timeout_s: float = DEFAULT_LEASE_TIMEOUT_SECONDS,
                       heartbeat_interval_s: float = HEARTBEAT_INTERVAL_SECONDS) -> dict:
    """Serialise per campaign so a double click makes one provider call."""
    with _GEN_LOCKS_GUARD:
        lock = _GEN_LOCKS.setdefault(f"{project_id}:{campaign_id}",
                                     threading.Lock())
    deliverables._require_scope(conn, project_id, campaign_id)
    if not lock.acquire(blocking=False):
        raise FlowError("Content is already being written for this campaign.",
                        status=409, code="generation_in_progress")
    t0 = time.time()
    lease_token = None
    stop_heartbeat = threading.Event()
    hb_thread = None
    try:
        lease_token = _acquire_db_lock(
            conn, project_id, campaign_id, turn_id, lease_timeout_s=lease_timeout_s
        )
        hb_thread = _start_lease_heartbeat(
            conn, project_id, campaign_id, lease_token, stop_heartbeat,
            interval_s=heartbeat_interval_s,
        )
        result = _generate_from_plan(
            conn, project_id=project_id, campaign_id=campaign_id,
            turn_id=turn_id, provider=provider, model_id=model_id,
            lease_token=lease_token)
        result["elapsed_s"] = round(time.time() - t0, 2)
        return result
    finally:
        stop_heartbeat.set()
        if hb_thread and hb_thread.is_alive():
            hb_thread.join(timeout=2.0)
        try:
            if lease_token is not None:
                _release_db_lock(conn, project_id, campaign_id, lease_token)
        finally:
            lock.release()


def _generate_from_plan(conn, *, project_id: str, campaign_id: str,
                        turn_id: str = "", provider: str = "",
                        model_id: str = "", lease_token: str = "") -> dict:
    """Write the proposed deliverables for one campaign, once.

    Refuses (instead of guessing) while essential details are missing, and
    refuses a second generation so a double click or a repeated "go ahead"
    can never pile up duplicate drafts.
    """
    if lease_token and not _verify_db_lock(conn, project_id, campaign_id, lease_token):
        raise FlowError(
            "Generation lock lease expired or was taken by another request; refusing to write state.",
            status=409, code="lease_expired")
    campaign = deliverables._require_scope(conn, project_id, campaign_id)
    intake = _intake_of(campaign)
    profile = get_profile(conn, project_id) or {}
    profile_view = profile
    view = campaign_request.understanding(intake, profile_view)
    essential = [m for m in view["missing"] if m["essential"]]
    if essential:
        raise FlowError(
            "Before I write anything I need: "
            + " ".join(m["question"] for m in essential),
            status=422, code="needs_input", extra={"missing": essential})
    if deliverables.list_deliverables(conn, project_id, campaign_id):
        raise FlowError("Content has already been written for this campaign. "
                        "Open it to review, or ask for changes to a specific item.",
                        status=409, code="already_generated")
    types = campaign_request.planned_types(intake)
    if not types:
        raise FlowError("There is nothing to write yet. Choose at least one "
                        "channel or say what content you want.",
                        status=422, code="needs_input")
    result = production.generate_batch(
        conn, project_id=project_id, campaign_id=campaign_id,
        turn_id=turn_id or f"ui-generate-{campaign_id}",
        user_request=campaign_request.plan_request_text(intake, profile_view),
        types=types, campaign_title=str(campaign.get("title") or ""),
        provider=provider or provider_selection(conn), model_id=model_id,
        lease_token=lease_token, require_final_copy=True)
    if not result.get("ok"):
        code = str(result.get("failure_code") or "")
        if code == "lease_expired":
            status = 409
        elif code == "output_validation":
            status = 422
        else:
            status = 502
        raise FlowError(str(result.get("error") or "Content could not be written."),
                        status=status, code=code or "generation_failed",
                        extra={"preserved_existing": True})
    return result


def ai_revise(conn, *, project_id: str, campaign_id: str, deliverable_id: str,
              instruction: str, provider: str = "", model_id: str = "") -> dict:
    """Revise one deliverable from a plain-language instruction."""
    text = re.sub(r"\s+", " ", str(instruction or "")).strip()
    if not text:
        raise FlowError("Say what you want changed.")
    if len(text) > MAX_INSTRUCTION_CHARS:
        raise FlowError(f"Keep the request under {MAX_INSTRUCTION_CHARS} characters.")
    deliverables.get_deliverable(conn, project_id, campaign_id, deliverable_id)
    result = production.revise_deliverable(
        conn, project_id=project_id, campaign_id=campaign_id,
        turn_id=f"ui-revise-{uuid.uuid4().hex[:12]}", deliverable_id=deliverable_id,
        instruction=text, provider=provider or provider_selection(conn),
        model_id=model_id, require_final_copy=True)
    if not result.get("ok"):
        code = str(result.get("failure_code") or "")
        status = 409 if code == "revision_conflict" else (
            422 if code == "output_validation" else 502)
        raise FlowError(str(result.get("error") or "The change could not be made."),
                        status=status, code=code or "revision_failed",
                        extra={"preserved_existing": True})
    return result
