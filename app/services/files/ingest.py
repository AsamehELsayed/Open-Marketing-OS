"""Upload ingest: validate -> hash -> write disk -> metadata row -> extract
-> (project scope) chunk+index. Fail-closed at every gate.

FileRecord is the frozen I-5 shape:
{file_id, project_id, original_name, safe_name, mime_detected, size,
 sha256, kind, width, height, extraction}
"""
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from app.services.files import extract as extractor
from app.services.files import limits, repo, safe_name, sniff
from app.services.files.chunk_index import chat_file_summary, index_file_text
from app.services.files.hashing import sha256_hex

ATTACH_SCOPES = ("turn", "project")

# Integration shape (W4 ToolRecord documented fields) — local value objects
# only; registry.py is owned by another worker.
def tool_specs() -> list[dict]:
    return [
        {"tool_id": "files_upload", "source_type": "native",
         "side_effect": "yellow", "credential_scope": "project"},
        {"tool_id": "files_get", "source_type": "native",
         "side_effect": "green", "credential_scope": "project"},
        {"tool_id": "files_list", "source_type": "native",
         "side_effect": "green", "credential_scope": "project"},
    ]


class IngestError(ValueError):
    """Validation rejection with stable machine code for the route layer."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class FileRecord:
    file_id: str
    project_id: str
    original_name: str
    safe_name: str
    mime_detected: str
    size: int
    sha256: str
    kind: str
    width: int | None = None
    height: int | None = None
    extraction: str = "pending"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class IngestResult:
    """FileRecord (frozen I-5) + runtime index/scope side-channel."""
    record: FileRecord
    attach_scope: str
    indexed: bool
    quarantine: list[str]
    note: str


def _require_project_id(project_id: str | None) -> str:
    pid = (project_id or "").strip()
    if not pid:
        raise IngestError("missing_project_id",
                          "project_id is required (fail-closed)")
    if not re.fullmatch(r"[A-Za-z0-9._\-]{1,64}", pid) or pid in (".", ".."):
        raise IngestError("invalid_project_id", "invalid project_id format")
    return pid


def _validate_scope(attach_scope: str | None) -> str:
    scope = (attach_scope or "project").strip().lower()
    if scope not in ATTACH_SCOPES:
        raise IngestError(
            "invalid_attach_scope",
            f"attach_scope must be one of {ATTACH_SCOPES}")
    return scope


def _validate_name(name: str | None) -> str:
    try:
        raw = safe_name.reject_unsafe_filename(name)
    except safe_name.UnsafeFilename as e:
        raise IngestError("unsafe_filename", str(e)) from e
    if safe_name.is_executable_extension(raw):
        raise IngestError(
            "executable_extension",
            "executable/script extensions are not accepted")
    return raw


def _check_sizes(data: bytes, kind: str) -> int:
    abs_max = limits.absolute_max_bytes()
    if len(data) > abs_max:
        raise IngestError(
            "file_too_large",
            f"file exceeds absolute cap of {abs_max} bytes", status=413)
    if not data:
        raise IngestError("empty_file", "uploaded file is empty")
    cap = limits.max_bytes_for(kind)
    if len(data) > cap:
        raise IngestError(
            "file_too_large",
            f"file exceeds {kind} cap of {cap} bytes", status=413)
    return len(data)


def _sniff(data: bytes, original_name: str) -> sniff.SniffResult:
    try:
        result = sniff.sniff_bytes(data, original_name)
    except ValueError as e:
        msg = str(e)
        if msg.startswith("executable_payload"):
            raise IngestError("executable_payload", msg) from e
        if msg.startswith("unsupported_type"):
            raise IngestError("unsupported_type", msg, status=415) from e
        raise IngestError("unsupported_type", msg, status=415) from e
    if result.mime in sniff.EXECUTABLE_MIMES:
        raise IngestError("executable_payload", "executable content rejected")
    if result.mime not in sniff.ALLOWED_MIMES:
        raise IngestError("unsupported_type",
                          f"unsupported mime: {result.mime}", status=415)
    return result


def ingest(
    conn,
    *,
    project_id: str | None,
    filename: str | None,
    data: bytes,
    claimed_mime: str = "",
    attach_scope: str | None = "project",
    root: str | Path,
    vector_store=None,
    provider=None,
    progress_callback=None,
) -> IngestResult:
    """Run the full pipeline. Raises IngestError (or UnsafeFilename via
    IngestError) on any validation failure BEFORE writing to disk."""
    pid = _require_project_id(project_id)
    scope = _validate_scope(attach_scope)
    name = _validate_name(filename)

    repo.ensure_schema(conn)
    exists = conn.execute(
        "SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone()
    if exists is None:
        raise IngestError("unknown_project",
                          f"unknown project_id {pid!r}", status=404)

    sniffed = _sniff(data, name)
    _check_sizes(data, sniffed.kind)

    if not sniff.claimed_compatible(claimed_mime, sniffed.mime):
        raise IngestError(
            "mime_mismatch",
            f"declared {claimed_mime!r} does not match content "
            f"{sniffed.mime!r}")

    digest = sha256_hex(data)
    file_id = uuid.uuid4().hex
    ext = safe_name.extension_for(sniffed.mime, name)
    sname = safe_name.build_safe_name(file_id, name, ext)

    # fail-closed project dir: sanitized pid, never user path segments
    proj_dir = Path(root) / "data" / "projects" / pid / "files"
    proj_dir.mkdir(parents=True, exist_ok=True)
    dest = proj_dir / sname
    dest.write_bytes(data)  # original bytes preserved (macros and all)

    rel_path = f"data/projects/{pid}/files/{sname}"
    created_at = repo.now_iso()
    row = {
        "file_id": file_id, "project_id": pid, "original_name": name,
        "safe_name": sname, "mime_detected": sniffed.mime,
        "size": len(data), "sha256": digest, "kind": sniffed.kind,
        "width": None, "height": None, "extraction": "pending",
        "attach_scope": scope, "indexed": 0, "rel_path": rel_path,
        "created_at": created_at,
    }
    repo.insert_file(conn, row)

    try:
        if progress_callback:
            progress_callback({"stage": "extracting_file"})
        result = extractor.extract(data, sniffed.mime, name)
    except Exception as exc:
        result = extractor.ExtractionResult(status="failed", note=f"extract_exception:{type(exc).__name__}")

    indexed = False
    quarantine: list[str] = []
    index_error = ""
    index_status = "not_searchable"
    if (scope == "project" and sniffed.kind == "document"
            and result.status in ("ready", "stripped") and result.text):
        try:
            if progress_callback:
                progress_callback({"stage": "indexing_file"})
            idx = index_file_text(
                conn, file_id=file_id, project_id=pid, original_name=name,
                sha256=digest, text=result.text, mime=sniffed.mime,
                vector_store=vector_store, provider=provider,
            )
            indexed = bool(idx.get("indexed"))
            quarantine = list(idx.get("quarantined") or [])
            index_status = "quarantined" if quarantine else ("indexed" if indexed else "not_searchable")
        except Exception:
            index_error = "index_failed"
            index_status = "failed"
            conn.rollback()
    elif scope == "project" and sniffed.kind == "document":
        if result.note == "no_extractable_text":
            index_status = "not_searchable"
            index_error = "no_extractable_text"
        else:
            index_status = "failed"
            index_error = "extraction_failed"

    # quarantine note: secret hit downgrades to failed extraction marker
    extraction_status = result.status
    if quarantine and extraction_status in ("ready", "stripped"):
        extraction_status = "stripped"

    repo.update_extraction(
        conn, file_id, extraction=extraction_status,
        width=result.width, height=result.height, indexed=indexed,
        index_status=index_status, index_error=index_error,
    )

    final = repo.get_file(conn, file_id, pid)
    assert final is not None  # just inserted
    record = FileRecord(
        file_id=final["file_id"],
        project_id=final["project_id"],
        original_name=final["original_name"],
        safe_name=final["safe_name"],
        mime_detected=final["mime_detected"],
        size=final["size"],
        sha256=final["sha256"],
        kind=final["kind"],
        width=final.get("width"),
        height=final.get("height"),
        extraction=final.get("extraction", "pending"),
    )
    return IngestResult(
        record=record,
        attach_scope=scope,
        indexed=indexed,
        quarantine=quarantine,
        note=result.note,
    )


def ingest_result_dict(result: IngestResult) -> dict:
    """HTTP response body: I-5 record + scope flags, never rel_path/safe_name."""
    d = result.record.as_dict()
    d.pop("safe_name", None)  # internal disk filename — never leave the API
    d.update({
        "attach_scope": result.attach_scope,
        "indexed": bool(result.indexed),
        "quarantine": result.quarantine or [],
        "index_note": result.note,
    })
    return d


__all__ = [
    "ATTACH_SCOPES",
    "FileRecord",
    "IngestError",
    "IngestResult",
    "chat_file_summary",
    "ingest",
    "ingest_result_dict",
    "tool_specs",
]
