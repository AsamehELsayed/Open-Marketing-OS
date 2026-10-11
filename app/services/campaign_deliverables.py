"""Project-scoped campaign deliverables with versioned editorial history.

The W2 generated-output contract is :func:`save_generated_batch`: items are
dicts with required ``type``, ``title``, and ``content_md`` strings, plus an
optional ``platform`` string. It returns the ordered persisted deliverable
rows (including ``id`` and ``type``). A repeated project/campaign/key returns
those existing rows unchanged, even if a user has since edited or approved
one of them. ``provenance`` is a JSON-serializable mapping recorded on the
batch and each initial revision.

The scoped manual-save contract is :func:`create_manual`; optimistic edits use
:func:`revise` with ``expected_version``; status changes use :func:`transition`;
and immutable snapshots are read through :func:`history`. All mutations are
transactional and validate campaign ownership before reading child rows.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import re
import uuid
import zipfile

from app.database import repos


DELIVERABLE_TYPES = frozenset({
    "strategy_brief", "social_post", "ad_copy", "creative_brief",
    "content_calendar",
})
STATUSES = ("DRAFT", "IN_REVIEW", "APPROVED")
MAX_TITLE_LENGTH = 200
MAX_PLATFORM_LENGTH = 80
MAX_CONTENT_LENGTH = 1_000_000


class DeliverableNotFound(LookupError):
    """Project, campaign, or scoped deliverable does not exist."""


class DeliverableConflict(RuntimeError):
    """A stale write or invalid editorial transition was requested."""


class DeliverableValidationError(ValueError):
    """A deliverable payload violates the API contract."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _atomic(conn):
    """Commit an owned transaction or keep a caller's outer transaction."""
    owns_transaction = not conn.in_transaction
    savepoint = f"deliverables_{uuid.uuid4().hex}"
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except Exception:
        if owns_transaction:
            conn.rollback()
        else:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        if owns_transaction:
            conn.commit()
        else:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")


def _require_scope(conn, project_id: str, campaign_id: str) -> dict:
    if not isinstance(project_id, str) or not project_id.strip():
        raise DeliverableValidationError("project_id is required")
    if not isinstance(campaign_id, str) or not campaign_id.strip():
        raise DeliverableValidationError("campaign_id is required")
    if repos.Projects.get(conn, project_id) is None:
        raise DeliverableNotFound("unknown project")
    try:
        campaign = repos.Campaigns.get(conn, campaign_id, project_id)
    except ValueError:
        # The shared campaign repo distinguishes owner mismatch internally;
        # this API deliberately returns the same not-found response as a
        # missing campaign so cross-project callers learn nothing about it.
        campaign = None
    if campaign is None:
        # Keep foreign campaign existence private across project boundaries.
        raise DeliverableNotFound("unknown campaign")
    return campaign


def _required_text(value, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise DeliverableValidationError(f"{field} must be text")
    value = value.strip()
    if not value:
        raise DeliverableValidationError(f"{field} is required")
    if len(value) > maximum:
        raise DeliverableValidationError(f"{field} exceeds {maximum} characters")
    return value


def _validate_item(item: dict) -> dict:
    if not isinstance(item, dict):
        raise DeliverableValidationError("each deliverable item must be an object")
    kind = item.get("type")
    if not isinstance(kind, str) or kind not in DELIVERABLE_TYPES:
        raise DeliverableValidationError("type must be a supported campaign deliverable type")
    title = _required_text(item.get("title"), "title", MAX_TITLE_LENGTH)
    content = item.get("content_md")
    if not isinstance(content, str) or not content.strip():
        raise DeliverableValidationError("content_md is required")
    if len(content) > MAX_CONTENT_LENGTH:
        raise DeliverableValidationError("content_md exceeds the maximum size")
    platform = item.get("platform")
    if platform is not None:
        if not isinstance(platform, str):
            raise DeliverableValidationError("platform must be text or null")
        platform = platform.strip()
        if len(platform) > MAX_PLATFORM_LENGTH:
            raise DeliverableValidationError(
                f"platform exceeds {MAX_PLATFORM_LENGTH} characters"
            )
        platform = platform or None
    return {"type": kind, "title": title, "platform": platform,
            "content_md": content}


def _version(expected_version) -> int:
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise DeliverableValidationError("expected_version must be an integer")
    if expected_version < 1:
        raise DeliverableValidationError("expected_version must be positive")
    return expected_version


def _public(row: dict) -> dict:
    fields = (
        "id", "project_id", "campaign_id", "type", "title", "platform",
        "content_md", "status", "current_version", "created_at", "updated_at",
    )
    return {field: row[field] for field in fields}


def _revision(row: dict, operation: str, provenance_json: str = "{}") -> dict:
    return {
        "revision_id": uuid.uuid4().hex,
        "project_id": row["project_id"],
        "campaign_id": row["campaign_id"],
        "deliverable_id": row["id"],
        "version": row["current_version"],
        "operation": operation,
        "type": row["type"],
        "title": row["title"],
        "platform": row.get("platform"),
        "content_md": row["content_md"],
        "status": row["status"],
        "provenance_json": provenance_json,
        "created_at": row["updated_at"],
    }


def _get(conn, project_id: str, campaign_id: str, deliverable_id: str) -> dict:
    row = repos.CampaignDeliverables.get(
        conn, project_id, campaign_id, deliverable_id
    )
    if row is None:
        raise DeliverableNotFound("unknown deliverable")
    return row


def list_deliverables(conn, project_id: str, campaign_id: str) -> list[dict]:
    """Return only deliverables whose campaign belongs to the supplied project."""
    _require_scope(conn, project_id, campaign_id)
    return [_public(row) for row in repos.CampaignDeliverables.list(
        conn, project_id, campaign_id
    )]


def get_deliverable(conn, project_id: str, campaign_id: str,
                    deliverable_id: str) -> dict:
    _require_scope(conn, project_id, campaign_id)
    return _public(_get(conn, project_id, campaign_id, deliverable_id))


def get_generated_batch(conn, project_id: str, campaign_id: str,
                        idempotency_key: str) -> list[dict] | None:
    """Read a previously saved exact-scope generation batch, if present."""
    key = _required_text(idempotency_key, "idempotency_key", 200)
    _require_scope(conn, project_id, campaign_id)
    batch = repos.CampaignDeliverables.batch(
        conn, project_id, campaign_id, key)
    if batch is None:
        return None
    try:
        ids = json.loads(batch["deliverable_ids_json"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DeliverableValidationError(
            "the saved generation batch record is invalid") from exc
    if not isinstance(ids, list) or not ids:
        raise DeliverableValidationError(
            "the saved generation batch contains no deliverables")
    return [_public(_get(conn, project_id, campaign_id, str(deliverable_id)))
            for deliverable_id in ids]


def create_manual(conn, project_id: str, campaign_id: str, item: dict) -> dict:
    """Create a manual DRAFT and its version-1 CREATE snapshot."""
    clean = _validate_item(item)
    with _atomic(conn):
        _require_scope(conn, project_id, campaign_id)
        stamp = now()
        row = {
            "id": uuid.uuid4().hex,
            "project_id": project_id,
            "campaign_id": campaign_id,
            **clean,
            "status": "DRAFT",
            "current_version": 1,
            "generation_idempotency_key": None,
            "generation_ordinal": None,
            "created_at": stamp,
            "updated_at": stamp,
        }
        repos.CampaignDeliverables.insert(conn, row)
        repos.CampaignDeliverables.insert_revision(conn, _revision(row, "CREATE"))
        return _public(row)


def revise(conn, project_id: str, campaign_id: str, deliverable_id: str,
           expected_version: int, changes: dict) -> dict:
    """Edit one scoped deliverable with CAS; approved content reopens as DRAFT."""
    expected_version = _version(expected_version)
    if not isinstance(changes, dict):
        raise DeliverableValidationError("changes must be an object")
    allowed = {"type", "title", "platform", "content_md"}
    unknown = set(changes) - allowed
    if unknown:
        raise DeliverableValidationError("unsupported editable field")
    if not changes:
        raise DeliverableValidationError("at least one editable field is required")
    with _atomic(conn):
        _require_scope(conn, project_id, campaign_id)
        current = _get(conn, project_id, campaign_id, deliverable_id)
        if current["current_version"] != expected_version:
            raise DeliverableConflict("deliverable changed; reload the latest version")
        candidate = {
            "type": changes.get("type", current["type"]),
            "title": changes.get("title", current["title"]),
            "platform": changes.get("platform", current["platform"]),
            "content_md": changes.get("content_md", current["content_md"]),
        }
        clean = _validate_item(candidate)
        new_status = "DRAFT" if current["status"] == "APPROVED" else current["status"]
        stamp = now()
        next_version = expected_version + 1
        updates = {
            **clean,
            "status": new_status,
            "current_version": next_version,
            "updated_at": stamp,
        }
        changed = repos.CampaignDeliverables.update_if_version(
            conn, project_id, campaign_id, deliverable_id,
            expected_version, updates,
        )
        if changed != 1:
            raise DeliverableConflict("deliverable changed; reload the latest version")
        saved = {**current, **updates}
        repos.CampaignDeliverables.insert_revision(
            conn, _revision(saved, "EDIT")
        )
        return _public(saved)


def transition(conn, project_id: str, campaign_id: str, deliverable_id: str,
               expected_version: int, target_status: str) -> dict:
    """Advance DRAFT -> IN_REVIEW -> APPROVED with a versioned snapshot."""
    expected_version = _version(expected_version)
    if target_status not in STATUSES:
        raise DeliverableValidationError("status must be DRAFT, IN_REVIEW, or APPROVED")
    with _atomic(conn):
        _require_scope(conn, project_id, campaign_id)
        current = _get(conn, project_id, campaign_id, deliverable_id)
        if current["current_version"] != expected_version:
            raise DeliverableConflict("deliverable changed; reload the latest version")
        allowed_next = {"DRAFT": "IN_REVIEW", "IN_REVIEW": "APPROVED"}
        if allowed_next.get(current["status"]) != target_status:
            raise DeliverableConflict(
                f"invalid status transition from {current['status']} to {target_status}"
            )
        stamp = now()
        next_version = expected_version + 1
        updates = {
            "status": target_status,
            "current_version": next_version,
            "updated_at": stamp,
        }
        changed = repos.CampaignDeliverables.update_if_version(
            conn, project_id, campaign_id, deliverable_id,
            expected_version, updates,
        )
        if changed != 1:
            raise DeliverableConflict("deliverable changed; reload the latest version")
        saved = {**current, **updates}
        repos.CampaignDeliverables.insert_revision(
            conn, _revision(saved, "STATUS_TRANSITION")
        )
        return _public(saved)


def history(conn, project_id: str, campaign_id: str,
            deliverable_id: str) -> list[dict]:
    """Return immutable snapshots for one deliverable inside its campaign scope."""
    _require_scope(conn, project_id, campaign_id)
    _get(conn, project_id, campaign_id, deliverable_id)
    return repos.CampaignDeliverables.history(
        conn, project_id, campaign_id, deliverable_id
    )


def save_generated_batch(conn, project_id: str, campaign_id: str,
                         idempotency_key: str, items: list[dict],
                         provenance: dict) -> list[dict]:
    """Atomically persist generated campaign content for the W2 producer.

    ``items`` is an ordered non-empty list of mappings with required
    ``type``, ``title``, and ``content_md`` and optional ``platform``. Supported
    types are strategy_brief, social_post, ad_copy, creative_brief, and
    content_calendar. ``provenance`` must be a JSON-serializable mapping.
    The returned list is ordered like the original batch and contains each
    persisted public row. A retry with the same project, campaign, and
    idempotency key returns the original rows without changing content,
    version, status, or history.
    """
    key = _required_text(idempotency_key, "idempotency_key", 200)
    if not isinstance(items, list) or not items:
        raise DeliverableValidationError("items must be a non-empty list")
    clean_items = [_validate_item(item) for item in items]
    if not isinstance(provenance, dict):
        raise DeliverableValidationError("provenance must be an object")
    try:
        provenance_json = json.dumps(provenance, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise DeliverableValidationError("provenance must be JSON serializable") from exc
    with _atomic(conn):
        _require_scope(conn, project_id, campaign_id)
        existing_batch = repos.CampaignDeliverables.batch(
            conn, project_id, campaign_id, key
        )
        if existing_batch:
            ids = json.loads(existing_batch["deliverable_ids_json"])
            rows = [
                _get(conn, project_id, campaign_id, deliverable_id)
                for deliverable_id in ids
            ]
            return [_public(row) for row in rows]

        stamp = now()
        ids = []
        rows = []
        for ordinal, item in enumerate(clean_items):
            stable_id = "del_" + sha256(
                f"{project_id}\0{campaign_id}\0{key}\0{ordinal}".encode("utf-8")
            ).hexdigest()[:32]
            row = {
                "id": stable_id,
                "project_id": project_id,
                "campaign_id": campaign_id,
                **item,
                "status": "DRAFT",
                "current_version": 1,
                "generation_idempotency_key": key,
                "generation_ordinal": ordinal,
                "created_at": stamp,
                "updated_at": stamp,
            }
            repos.CampaignDeliverables.insert(conn, row)
            repos.CampaignDeliverables.insert_revision(
                conn, _revision(row, "GENERATED", provenance_json)
            )
            ids.append(stable_id)
            rows.append(row)
        repos.CampaignDeliverables.insert_batch(conn, {
            "project_id": project_id,
            "campaign_id": campaign_id,
            "idempotency_key": key,
            "deliverable_ids_json": json.dumps(ids),
            "provenance_json": provenance_json,
            "created_at": stamp,
        })
        return [_public(row) for row in rows]


def markdown_export(conn, project_id: str, campaign_id: str,
                    deliverable_id: str) -> tuple[dict, str]:
    """Return one scoped deliverable and its Markdown body for attachment export."""
    row = get_deliverable(conn, project_id, campaign_id, deliverable_id)
    return row, row["content_md"]


def package_export(conn, project_id: str, campaign_id: str) -> bytes:
    """Create a ZIP containing Markdown for only the requested campaign scope."""
    rows = list_deliverables(conn, project_id, campaign_id)
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for row in rows:
            slug = re.sub(r"[^A-Za-z0-9_-]+", "-", row["title"]).strip("-_")[:80]
            slug = slug or row["type"]
            archive.writestr(
                f"{row['type']}/{slug}-{row['id'][:8]}.md",
                row["content_md"],
            )
    return output.getvalue()
