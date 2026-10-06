from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import deps
from app.main import templates
from app.services import state as store

router = APIRouter()


@router.get("/brain", response_class=HTMLResponse)
def brain(request: Request):
    return RedirectResponse("/app/knowledge", status_code=303)
