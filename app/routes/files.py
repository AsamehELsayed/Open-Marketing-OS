"""DEV-007 W2 — Files HTTP routes.

Export-only: integrator mounts `router` in main.py (not edited here).
Endpoints (envelope {"ok": true, "data"|...}):

  POST /files/upload          multipart: file, project_id, attach_scope
  POST /files/upload/image    image-only variant (kind must be image)
  GET  /files?project_id=     list metadata for a project
  GET  /files/{file_id}?project_id=   single file metadata

Fail-closed: blank project_id -> 400 missing_project_id. Responses never
contain rel_path, safe_name, or any filesystem path (chat/LLM safe).
"""
import asyncio

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse

from app import deps
from app.contracts.events import safe_error_message, sanitize_metadata
from app.services.files import IngestError, ingest
from app.services.files import repo as files_repo
from app.services.files.ingest import ingest_result_dict

router = APIRouter(prefix="/files", tags=["files"])


def _err(status: int, code: str, message: str) -> JSONResponse:
    fallback = "The file could not be accepted."
    safe_message = safe_error_message(message, fallback)
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": {
            "code": safe_error_message(code, "request_failed"),
            "message": safe_message,
        }},
    )


def _public(row: dict, conn=None) -> dict:
    """Metadata for API responses — strip disk-layout columns."""
    projected = {
        "file_id": row.get("file_id", ""),
        "project_id": row.get("project_id", ""),
        "original_name": row.get("original_name", ""),
        "mime_detected": row.get("mime_detected", ""),
        "size": row.get("size", 0),
        "sha256": row.get("sha256", ""),
        "kind": row.get("kind", ""),
        "width": row.get("width"),
        "height": row.get("height"),
        "extraction": row.get("extraction", ""),
        "attach_scope": row.get("attach_scope", ""),
        "indexed": bool(row.get("indexed")),
        "created_at": row.get("created_at", ""),
    }
    try:
        from app.services.knowledge_index import file_index_state
        projected.update(file_index_state(conn, row))
    except Exception:
        # Compatibility while an older runtime lacks the knowledge service.
        extraction = row.get("extraction") or "pending"
        state = ("failed" if extraction == "failed" else "not_searchable"
                 if row.get("kind") == "image" else
                 "failed" if row.get("index_status") == "failed" else "pending")
        projected.update({"indexed": False, "index_status": state,
                          "index_error": "state_unavailable"})
    return projected


def _ingest_upload_blocking(
    *, project_id: str, attach_scope: str, conversation_id: str,
    filename: str, data: bytes, claimed_mime: str, progress_id: str = "",
):
    """Run retrieval setup and ingestion off the async server loop.

    Open the SQLite connection in this worker. Connections are deliberately
    not accepted as arguments or returned across the thread boundary.
    """
    project_state = {}
    cid = (conversation_id or "").strip()
    with deps.get_db() as conn:
        if attach_scope == "turn":
            from app.database import repos
            convo = repos.Conversations.get(conn, cid) if cid else None
            if convo is None or (convo.get("project_id") or "") != project_id:
                raise IngestError(
                    "invalid_conversation",
                    "A matching conversation is required for turn uploads.",
                )
        from app.services import upload_progress
        report = lambda update: upload_progress.update(
            progress_id, project_id, update.get("stage", "processing_file"),
            current=update.get("current"), total=update.get("total"),
        )
        from app.services.knowledge_index import (
            retrieval_runtime, retrieval_status_for_store,
        )
        vector_store = provider = None
        # Let ingest produce its stable missing_project_id validation error.
        if (project_id or "").strip():
            if progress_id:
                vector_store, provider = retrieval_runtime(
                    deps.ROOT, project_id, progress_callback=report,
                )
            else:
                vector_store, provider = retrieval_runtime(deps.ROOT, project_id)
        report({"stage": "extracting_file"})
        result = ingest(
            conn,
            project_id=project_id,
            filename=filename,
            data=data,
            claimed_mime=claimed_mime,
            attach_scope=attach_scope,
            root=deps.ROOT,
            vector_store=vector_store,
            provider=provider,
            progress_callback=report if progress_id else None,
        )
        stored_row = files_repo.get_file(conn, result.record.file_id, project_id)
        if attach_scope == "turn":
            files_repo.bind_turn_file(conn, result.record.file_id, project_id, cid)
        if stored_row:
            try:
                from app.services.knowledge_index import file_index_state
                project_state = file_index_state(conn, stored_row)
            except Exception:
                pass
        try:
            retrieval_state = retrieval_status_for_store(
                vector_store, conn=conn, project_id=project_id,
            )
        except Exception:
            # A status-probe failure cannot establish that either index works.
            retrieval_state = {
                "search_mode": "UNAVAILABLE", "vector_status": "NOT_AVAILABLE",
                "vector_reason": "runtime_status_unavailable",
            }
    return result, cid, project_state, retrieval_state


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    project_id: str = Form(""),
    attach_scope: str = Form("project"),
    conversation_id: str = Form(""),
    progress_id: str = Form(""),
):
    data = await file.read()
    from app.services import upload_progress
    # Direct route invocation in tests may leave FastAPI's Form marker as the
    # default value; only actual opaque string IDs enable polling.
    progress_id = progress_id if isinstance(progress_id, str) else ""
    if progress_id:
        upload_progress.begin(progress_id, project_id)
    try:
        result, cid, project_state, retrieval_state = await asyncio.to_thread(
            _ingest_upload_blocking,
            project_id=project_id,
            attach_scope=attach_scope,
            conversation_id=conversation_id,
            filename=file.filename,
            data=data,
            claimed_mime=file.content_type or "",
            progress_id=progress_id,
        )
        if progress_id:
            upload_progress.finish(progress_id, project_id, "completed")
    except IngestError as e:
        if progress_id:
            upload_progress.finish(progress_id, project_id, "failed")
        return _err(
            e.status,
            e.code,
            ("A matching conversation is required for turn uploads."
             if e.code == "invalid_conversation" else
             "The file could not be accepted."),
        )
    except ValueError:
        if progress_id:
            upload_progress.finish(progress_id, project_id, "failed")
        return _err(400, "ingest_rejected", "The file could not be accepted.")
    except Exception:
        if progress_id:
            upload_progress.finish(progress_id, project_id, "failed")
        return _err(400, "ingest_rejected", "The file could not be accepted.")
    body = sanitize_metadata(ingest_result_dict(result))
    if result.attach_scope == "turn":
        body["conversation_id"] = cid
    body.update(project_state)
    body.update(retrieval_state)
    body.setdefault("index_status", "indexed" if result.indexed else
                    "quarantined" if result.quarantine else
                    "not_searchable" if result.record.kind == "image" or
                    result.record.extraction == "ready" and not result.indexed else
                    "failed" if result.record.extraction == "failed" else "pending")
    body.setdefault("index_error", "secret_detected" if result.quarantine else
                    "extraction_failed" if result.record.extraction == "failed" else "")
    return JSONResponse(status_code=201, content={"ok": True, "data": body})


@router.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    project_id: str = Form(""),
    attach_scope: str = Form("project"),
):
    data = await file.read()
    try:
        with deps.get_db() as conn:
            result = ingest(
                conn,
                project_id=project_id,
                filename=file.filename,
                data=data,
                claimed_mime=file.content_type or "",
                attach_scope=attach_scope,
                root=deps.ROOT,
            )
    except IngestError as e:
        return _err(
            e.status,
            e.code,
            "The file could not be accepted.",
        )
    except ValueError:
        return _err(400, "ingest_rejected", "The file could not be accepted.")
    except Exception:
        return _err(400, "ingest_rejected", "The file could not be accepted.")
    if result.record.kind != "image":
        return _err(415, "not_an_image", "The uploaded file is not an image.")
    body = sanitize_metadata(ingest_result_dict(result))
    return JSONResponse(status_code=201, content={"ok": True, "data": body})


@router.get("")
def list_files(project_id: str = Query(""), attach_scope: str | None = Query(None)):
    try:
        with deps.get_db() as conn:
            files_repo.ensure_schema(conn)
            rows = files_repo.list_files(conn, project_id)
            if attach_scope:
                if attach_scope not in ("turn", "project"):
                    return _err(400, "invalid_attach_scope", "attach_scope must be project or turn")
                rows = [row for row in rows if row.get("attach_scope") == attach_scope]
            rows = [_public(r, conn) for r in rows]
    except ValueError:
        return _err(400, "missing_project_id", "project_id is required (fail-closed)")
    except Exception:
        return _err(400, "files_unavailable", "The files could not be loaded.")
    return JSONResponse(content={"ok": True, "data": [sanitize_metadata(r) for r in rows]})


@router.get("/progress/{progress_id}")
def get_upload_progress(progress_id: str, project_id: str = Query("")):
    from app.services import upload_progress
    state = upload_progress.get(progress_id, project_id)
    if state is None:
        return _err(404, "progress_not_found", "Upload progress is no longer available.")
    return JSONResponse(content={"ok": True, "data": state})


@router.get("/{file_id}")
def get_file(file_id: str, project_id: str = Query("")):
    try:
        with deps.get_db() as conn:
            files_repo.ensure_schema(conn)
            row = files_repo.get_file(conn, file_id, project_id)
            row = _public(row, conn) if row else None
    except ValueError:
        return _err(400, "missing_project_id", "project_id is required (fail-closed)")
    except Exception:
        return _err(400, "files_unavailable", "The file could not be loaded.")
    if row is None:
        return _err(404, "not_found", "file not found in this project")
    return JSONResponse(content={"ok": True, "data": sanitize_metadata(row)})
