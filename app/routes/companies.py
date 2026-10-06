from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import deps
from app.database import repos
from app.main import templates

router = APIRouter()


@router.get("/companies", response_class=HTMLResponse)
def company_list(request: Request):
    return RedirectResponse("/app", status_code=303)
