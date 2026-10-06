"""Idempotent Markdown→SQLite import with conflict surfacing (D7).

Rule: if the source file changed since last import AND the DB row was
updated after that import, do NOT overwrite — record a conflict event and
leave the row untouched. Resolution (Keep-DB / Keep-file) is explicit.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.database import repos


def file_sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _last_import(conn, adapter: str) -> tuple[str | None, str | None]:
    row = repos.Settings.get(conn, f"import_{adapter}")
    if not row:
        return None, None
    try:
        meta = json.loads(row["value"])
        return meta.get("sha"), meta.get("at")
    except (ValueError, AttributeError):
        return None, None


def _mark_imported(conn, adapter: str, sha: str) -> None:
    repos.Settings.set(conn, f"import_{adapter}", json.dumps({"sha": sha, "at": _now()}), _now())


def import_rows(conn, adapter: str, source_path: str | Path, rows: list[dict],
                upsert, get, row_id: str = "id", updated_key: str | None = "updated_at") -> dict:
    """Generic idempotent import. Returns {inserted, updated, skipped, conflicts}."""
    sha = file_sha(source_path)
    last_sha, last_at = _last_import(conn, adapter)
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "conflicts": []}
    if last_sha == sha:
        stats["skipped"] = len(rows)
        return stats
    for row in rows:
        try:
            existing = get(conn, row[row_id])
        except Exception:
            existing = None
        if (
            existing and updated_key and existing.get(updated_key)
            and last_at and existing[updated_key] > last_at
        ):
            stats["conflicts"].append(row[row_id])
            repos.Events.log(conn, {
                "id": f"conflict-{adapter}-{row[row_id]}-{_now()}",
                "kind": "import_conflict",
                "ref_id": row[row_id],
                "detail_json": json.dumps({"adapter": adapter, "source": str(source_path)}),
                "created_at": _now(),
            })
            continue
        is_new = existing is None
        if updated_key and updated_key in row:
            row[updated_key] = _now()
        upsert(conn, row)
        stats["inserted" if is_new else "updated"] += 1
    _mark_imported(conn, adapter, sha)
    repos.Events.log(conn, {
        "id": f"import-{adapter}-{_now()}",
        "kind": "import",
        "ref_id": adapter,
        "detail_json": json.dumps(stats),
        "created_at": _now(),
    })
    return stats


def resolve_conflict_keep_file(conn, adapter: str, source_path: str | Path, rows: list[dict], upsert) -> None:
    for row in rows:
        upsert(conn, row)
    _mark_imported(conn, adapter, file_sha(source_path))


def resolve_conflict_keep_db(conn, adapter: str, source_path: str | Path) -> None:
    _mark_imported(conn, adapter, file_sha(source_path))
