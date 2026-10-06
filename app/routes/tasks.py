from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import deps
from app.database import repos
from app.main import templates
from app.services import state as store

router = APIRouter()


@router.get("/tasks", response_class=HTMLResponse)
def task_list(request: Request):
    return RedirectResponse("/app/jobs", status_code=303)


def task_status(request: Request, task_id: str, status: str = Form(...)):
    with deps.get_db() as conn:
        try:
            pid = store.require_active_project(conn)
        except store.NoActiveProject:
            return HTMLResponse('<div class="error">no active project</div>', status_code=400)
        try:
            row = store.set_task_status(conn, task_id, status, pid)
        except (KeyError, ValueError) as e:
            return HTMLResponse(f'<div class="error">{e}</div>', status_code=400)
    return templates.TemplateResponse(request, "_partials/task_row.html",
                                      {"task": row}, status_code=200)
