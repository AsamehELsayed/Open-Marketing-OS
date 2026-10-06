from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import deps
from app.database import repos
from app.main import templates
from app.services import state as store

router = APIRouter()


@router.get("/results", response_class=HTMLResponse)
def results(request: Request):
    return RedirectResponse("/app/results", status_code=303)


def record_decision(request: Request, experiment_id: str,
                    decision: str = Form(...), evidence_md: str = Form("")):
    with deps.get_db() as conn:
        try:
            pid = store.require_active_project(conn)
        except store.NoActiveProject:
            return HTMLResponse('<div class="error">no active project</div>', status_code=400)
        try:
            row = store.record_measurement_decision(conn, experiment_id, evidence_md, decision, pid)
        except (KeyError, ValueError) as e:
            return HTMLResponse(f'<div class="error">{e}</div>', status_code=400)
    return HTMLResponse(
        f'<div class="ok">Recorded {row["decision"]} for {experiment_id}.</div>')
