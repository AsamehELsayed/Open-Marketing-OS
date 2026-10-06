from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import deps
from app.main import templates
from app.services import display, state as store

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def today(request: Request):
    """Open the primary application workspace."""
    return RedirectResponse("/app", status_code=303)


@router.get("/legacy")
def today_legacy(request: Request):
    """Open the primary application workspace."""
    return RedirectResponse("/app", status_code=303)
