from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import deps
from app.database import repos
from app.main import templates
from app.services import state as store

router = APIRouter()


@router.get("/campaigns", response_class=HTMLResponse)
def campaign_list(request: Request):
    return RedirectResponse("/app/campaigns", status_code=303)


def campaign_status(request: Request, campaign_id: str, status: str = Form(...)):
    with deps.get_db() as conn:
        try:
            pid = store.require_active_project(conn)
        except store.NoActiveProject:
            return HTMLResponse('<div class="error">no active project</div>', status_code=400)
        try:
            row = store.set_campaign_status(conn, campaign_id, status, pid)
        except (KeyError, ValueError) as e:
            return HTMLResponse(f'<div class="error">{e}</div>', status_code=400)
    return templates.TemplateResponse(request, "_partials/campaign_row.html", {"campaign": row})
