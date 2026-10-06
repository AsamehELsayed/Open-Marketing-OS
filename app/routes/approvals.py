from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import deps
from app.database import repos
from app.main import templates
from app.services import state as store

router = APIRouter()


@router.get("/approvals", response_class=HTMLResponse)
def approval_list(request: Request):
    return RedirectResponse("/app/approvals", status_code=303)


def approval_decide(request: Request, approval_id: str, decision: str = Form(...)):
    with deps.get_db() as conn:
        try:
            pid = store.require_active_project(conn)
        except store.NoActiveProject:
            return HTMLResponse('<div class="error">no active project</div>', status_code=400)
        try:
            row = store.decide_approval(conn, approval_id, decision, project_id=pid)
        except (KeyError, ValueError) as e:
            return HTMLResponse(f'<div class="error">{e}</div>', status_code=400)
    return templates.TemplateResponse(request, "_partials/approval_row.html", {"approval": row})
