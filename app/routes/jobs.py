import json as _json
import time as _time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from app import deps
from app.contracts.events import project_event_boundary, safe_error_message, sanitize_user_text
from app.database import repos
from app.main import templates
from app.services import state as store

router = APIRouter()


@router.get("/jobs", response_class=HTMLResponse)
def jobs(request: Request):
    return RedirectResponse("/app/jobs", status_code=303)


def _scoped_job(conn, pid: str, job_id: str):
    try:
        return repos.BackgroundJobs.get(conn, job_id, pid)
    except ValueError:
        return None


@router.get("/jobs/{job_id}/status", response_class=HTMLResponse)
def job_status(request: Request, job_id: str):
    return RedirectResponse("/app/jobs", status_code=303)


@router.get("/jobs/{job_id}/log", response_class=HTMLResponse)
def job_log(request: Request, job_id: str):
    return RedirectResponse("/app/jobs", status_code=303)


@router.get("/jobs/{job_id}/events")
def job_events(request: Request, job_id: str, after: int = 0):
    """SSE: real persisted job lifecycle events. Scoped to the active project;
    foreign job ids yield 404. Supports Last-Event-ID resume."""
    last_hdr = request.headers.get("last-event-id", "")
    try:
        after = int(last_hdr or after)
    except (ValueError, TypeError):
        after = 0
    with deps.get_db() as conn:
        try:
            pid = store.require_active_project(conn)
        except store.NoActiveProject:
            return HTMLResponse("no active project", status_code=400)
        if _scoped_job(conn, pid, job_id) is None:
            return HTMLResponse("unknown job", status_code=404)

    def _gen():
        yield ": connected\n\n"
        last_id = after
        deadline = _time.time() + 600
        while _time.time() < deadline:
            with deps.get_db() as conn2:
                rows = [e for e in repos.ExecutionEvents.for_job(conn2, job_id, pid)
                        if e["id"] > last_id]
                cur = _scoped_job(conn2, pid, job_id)
            if not cur:
                yield "event: stream_end\ndata: {}\n\n"
                return
            for e in rows:
                last_id = max(last_id, e["id"])
                safe_event = project_event_boundary(e)
                event_type = sanitize_user_text(e["event_type"], 80)
                yield f"id: {e['id']}\nevent: {event_type}\n"
                yield f"data: {_json.dumps({'label': safe_event['label'], 'detail': safe_event['detail'], 'status': sanitize_user_text((cur or {}).get('status', ''), 40)}, ensure_ascii=False)}\n\n"
            if (cur or {}).get("status") in ("completed", "failed", "cancelled"):
                yield f"event: job_terminal\ndata: {_json.dumps({'status': sanitize_user_text(cur['status'], 40)})}\n\n"
                return
            _time.sleep(1.0)
        yield "event: stream_end\ndata: {}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/jobs/{job_id}/process", response_class=HTMLResponse)
def job_process(request: Request, job_id: str):
    return RedirectResponse("/app/jobs", status_code=303)


@router.get("/jobs/{job_id}/result")
def job_result(request: Request, job_id: str):
    """Artifact download: persisted result JSON when available."""
    from fastapi.responses import Response
    from app.services import jobs as jobsvc
    with deps.get_db() as conn:
        try:
            pid = store.require_active_project(conn)
        except store.NoActiveProject:
            return HTMLResponse("no active project", status_code=400)
        if _scoped_job(conn, pid, job_id) is None:
            return HTMLResponse("unknown job", status_code=404)
    payload = jobsvc.read_result_json(deps.ROOT, pid, job_id)
    if payload is None:
        return HTMLResponse("no result artifact yet", status_code=404)
    return Response(
        content=_json.dumps(payload, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.json"'},
    )


def job_cancel(request: Request, job_id: str):
    from app.services import jobs as jobsvc

    with deps.get_db() as conn:
        try:
            pid = store.require_active_project(conn)
        except store.NoActiveProject:
            return HTMLResponse('<div class="error">no active project</div>', status_code=400)
        if _scoped_job(conn, pid, job_id) is None:
            return HTMLResponse('<div class="error">unknown job</div>', status_code=404)
        try:
            job = jobsvc.cancel(conn, job_id)
        except (KeyError, ValueError) as e:
            return HTMLResponse(
                f'<div class="error">{safe_error_message(e, "The job could not be updated.")}</div>',
                status_code=400,
            )
    return templates.TemplateResponse(request, "_partials/job_poll.html", {"job": job})
