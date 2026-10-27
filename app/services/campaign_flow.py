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
import re
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
STALE_LOCK_TIMEOUT_SECONDS = 180


def _acquire_db_lock(conn, project_id: str, campaign_id: str, turn_id: str) -> None:
    """Acquire generation lock in database to prevent duplicate writes across processes."""
    campaign = deliverables._require_scope(conn, project_id, campaign_id)
    raw_meta = campaign.get("workflow_json") or "{}"
    try:
        meta = json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta)
    except (TypeError, ValueError):
        meta = {}

    in_prog = meta.get("generation_in_progress")
    if isinstance(in_prog, dict):
        started_str = in_prog.get("started_at")
        started_dt = None
        if started_str:
            try:
                started_dt = datetime.fromisoformat(started_str)
            except Exception:
                pass
        if started_dt:
            now_dt = datetime.now(timezone.utc)
            if started_dt.tzinfo is None:
                started_dt = started_dt.replace(tzinfo=timezone.utc)
            elapsed = (now_dt - started_dt).total_seconds()
            if elapsed < STALE_LOCK_TIMEOUT_SECONDS:
                raise FlowError(
                    f"Content is already being written for this campaign (started {int(elapsed)}s ago).",
                    status=409,
                    code="generation_in_progress",
                    extra={"started_at": started_str, "elapsed_s": int(elapsed)},
                )

    meta["generation_in_progress"] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "turn_id": str(turn_id or ""),
    }
    production._write_campaign_metadata(conn, project_id, campaign_id, meta)


def _release_db_lock(conn, project_id: str, campaign_id: str) -> None:
    """Release generation lock in database."""
    try:
        campaign = deliverables._require_scope(conn, project_id, campaign_id)
        raw_meta = campaign.get("workflow_json") or "{}"
        meta = json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta)
        if isinstance(meta, dict) and "generation_in_progress" in meta:
            meta.pop("generation_in_progress", None)
            production._write_campaign_metadata(conn, project_id, campaign_id, meta)
    except Exception:
        pass


def generate_from_plan(conn, *, project_id: str, campaign_id: str,
                       turn_id: str = "", provider: str = "",
                       model_id: str = "") -> dict:
    """Serialise per campaign so a double click makes one provider call."""
    with _GEN_LOCKS_GUARD:
        lock = _GEN_LOCKS.setdefault(f"{project_id}:{campaign_id}",
                                     threading.Lock())
    # Reject overlapping calls immediately; a queued retry after failure could
    # otherwise start a second expensive provider request without user intent.
    deliverables._require_scope(conn, project_id, campaign_id)
    if not lock.acquire(blocking=False):
        raise FlowError("Content is already being written for this campaign.",
                        status=409, code="generation_in_progress")
    t0 = time.time()
    try:
        _acquire_db_lock(conn, project_id, campaign_id, turn_id)
        result = _generate_from_plan(
            conn, project_id=project_id, campaign_id=campaign_id,
            turn_id=turn_id, provider=provider, model_id=model_id)
        result["elapsed_s"] = round(time.time() - t0, 2)
        return result
    finally:
        try:
            _release_db_lock(conn, project_id, campaign_id)
        finally:
            lock.release()


def _generate_from_plan(conn, *, project_id: str, campaign_id: str,
                        turn_id: str = "", provider: str = "",
                        model_id: str = "") -> dict:
    """Write the proposed deliverables for one campaign, once.

    Refuses (instead of guessing) while essential details are missing, and
    refuses a second generation so a double click or a repeated "go ahead"
    can never pile up duplicate drafts.
    """
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
        provider=provider or provider_selection(conn), model_id=model_id)
    if not result.get("ok"):
        code = str(result.get("failure_code") or "")
        status = 422 if code == "output_validation" else 502
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
        model_id=model_id)
    if not result.get("ok"):
        code = str(result.get("failure_code") or "")
        status = 409 if code == "revision_conflict" else (
            422 if code == "output_validation" else 502)
        raise FlowError(str(result.get("error") or "The change could not be made."),
                        status=status, code=code or "revision_failed",
                        extra={"preserved_existing": True})
    return result
