"""W2 image ingress (DEV-007 Phase B): FileRecord(kind=image) → observe().

Disk layout per frozen I-5: data/projects/<pid>/files/<safe_name>.
Missing project / missing row / missing file / kind != image all fail closed.
W2 modules are soft-imported when present; this worktree stays self-contained.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from app.services.vision.errors import VisionIngressError
from app.services.vision.interface import VisionObservation

_SAFE_NAME_OK = re.compile(r"^[A-Za-z0-9._-]+$")
_IMAGE_KIND = "image"
_IMAGE_MIME_PREFIXES = ("image/",)
_MAX_IMAGE_BYTES = 25 * 1024 * 1024


def project_files_root(root: str | Path | None = None) -> Path:
    if root is None:
        from app.deps import ROOT

        return Path(ROOT) / "data" / "projects"
    return Path(root) / "data" / "projects"


def image_disk_path(
    project_id: str,
    safe_name: str,
    *,
    root: str | Path | None = None,
) -> Path:
    """Resolve I-5 disk path with traversal fail-closed checks."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise VisionIngressError("project_id is required")
    if not isinstance(safe_name, str) or not safe_name:
        raise VisionIngressError("safe_name is required")
    if "/" in safe_name or "\\" in safe_name or safe_name in (".", ".."):
        raise VisionIngressError("unsafe safe_name")
    if not _SAFE_NAME_OK.match(safe_name):
        raise VisionIngressError("unsafe safe_name charset")
    base = project_files_root(root) / project_id.strip() / "files"
    path = (base / safe_name).resolve()
    base_resolved = base.resolve()
    if path != base_resolved and base_resolved not in path.parents:
        raise VisionIngressError("path escapes project files root")
    return path


def read_image_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise VisionIngressError(f"image file missing: {path.name}")
    data = path.read_bytes()
    if not data:
        raise VisionIngressError("image file empty")
    if len(data) > _MAX_IMAGE_BYTES:
        raise VisionIngressError("image exceeds size cap")
    return data


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def load_file_row(
    conn: sqlite3.Connection,
    project_id: str,
    file_id: str,
) -> dict:
    """Fetch I-5 row scoped to project; fail closed if W2 table missing."""
    if not isinstance(conn, sqlite3.Connection):
        raise TypeError("conn must be sqlite3.Connection")
    if not project_id or not str(project_id).strip():
        raise VisionIngressError("project_id is required")
    if not file_id or not str(file_id).strip():
        raise VisionIngressError("file_id is required")
    if not _table_exists(conn, "project_files"):
        raise VisionIngressError("project_files table not available (W2 not applied)")
    row = conn.execute(
        "SELECT * FROM project_files WHERE file_id = ? AND project_id = ?",
        (str(file_id).strip(), str(project_id).strip()),
    ).fetchone()
    if row is None:
        raise VisionIngressError("file not found for project")
    return {k: row[k] for k in row.keys()}


def image_bytes_from_record(
    record: dict,
    *,
    project_id: str | None = None,
    root: str | Path | None = None,
) -> bytes:
    """Load bytes for a FileRecord-shaped dict (kind must be image)."""
    if not isinstance(record, dict):
        raise TypeError("record must be a mapping")
    pid = str(record.get("project_id") or "").strip()
    if project_id is not None and pid != str(project_id).strip():
        raise VisionIngressError("file belongs to another project")
    if not pid:
        raise VisionIngressError("project_id is required")
    kind = str(record.get("kind") or "").strip()
    if kind != _IMAGE_KIND:
        raise VisionIngressError(f"expected kind=image, got {kind!r}")
    mime = str(record.get("mime_detected") or "").strip().lower()
    if mime and not mime.startswith(_IMAGE_MIME_PREFIXES):
        raise VisionIngressError(f"expected image mime, got {mime!r}")
    extraction = str(record.get("extraction") or "").strip()
    if extraction == "failed":
        raise VisionIngressError("file extraction failed")
    safe_name = str(record.get("safe_name") or "").strip()
    if not safe_name:
        raise VisionIngressError("safe_name missing on file record")
    path = image_disk_path(pid, safe_name, root=root)
    return read_image_bytes(path)


def observe_image_file(
    project_id: str,
    file_id: str,
    *,
    conn: sqlite3.Connection,
    root: str | Path | None = None,
    provider: str = "local",
    observe_fn=None,
) -> VisionObservation:
    """W2 FileRecord(kind=image) → bytes → frozen observe()."""
    from app.services.vision.interface import observe as _observe

    fn = observe_fn or _observe
    record = load_file_row(conn, project_id, file_id)
    image_bytes = image_bytes_from_record(
        record, project_id=project_id, root=root
    )
    return fn(image_bytes, provider=provider)
