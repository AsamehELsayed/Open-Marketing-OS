from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/settings")
def settings_page(request: Request):
    return RedirectResponse("/app/settings", status_code=303)


@router.get("/system")
def system_health(request: Request):
    return RedirectResponse("/app/settings?section=system", status_code=303)
