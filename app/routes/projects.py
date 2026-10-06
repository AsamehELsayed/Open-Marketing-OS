from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import deps
from app.main import templates
from app.services import state as store

import re

router = APIRouter()

# W3-owned validation vocabulary (mirrors C1/C2/C7; SocialAccounts repo itself is W1-owned).
SOCIAL_PLATFORMS = ("instagram", "tiktok", "x", "facebook", "linkedin", "youtube")
_HANDLE_RE = re.compile(r"^[\w.]{1,80}$")


def _social_repo():
    """W1 SocialAccounts repo via call-time import (valid pre/post W1 merge)."""
    from app.database import repos
    return getattr(repos, "SocialAccounts", None)


def _social_accounts_for(conn, project_id: str) -> list:
    repo = _social_repo()
    if repo is None:
        return []
    try:
        return repo.for_project(conn, project_id) or []
    except Exception:
        return []


def _clean_handle(raw: str) -> str:
    return (raw or "").strip().lstrip("@")


def _resolve_pid_or_400(conn, project_id: str):
    """Shared project-match-or-400 discipline (same as legacy instagram shim)."""
    try:
        pid = store.require_active_project(conn)
    except store.NoActiveProject:
        return None, HTMLResponse('<div class="error">no active project</div>', status_code=400)
    if (project_id or "").strip() and project_id.strip() != pid:
        return None, HTMLResponse('<div class="error">wrong project</div>', status_code=400)
    return pid, None


@router.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request):
    return RedirectResponse("/app", status_code=303)


def projects_new(request: Request, name: str = Form(...),
                 website: str = Form(""), goal: str = Form("")):
    with deps.get_db() as conn:
        row = store.create_project(conn, name, website, goal)
        store.set_active_project(conn, row["id"])
    return RedirectResponse("/chat", status_code=303)


def projects_switch(request: Request, project_id: str = Form(...)):
    with deps.get_db() as conn:
        store.set_active_project(conn, project_id)
    return RedirectResponse("/chat", status_code=303)


def projects_archive(request: Request, project_id: str = Form(...)):
    with deps.get_db() as conn:
        from app.database import repos
        repos.Projects.archive(conn, project_id)
    return RedirectResponse("/projects", status_code=303)


def projects_social_save(request: Request, project_id: str = Form(""),
                         platform: str = Form(""), handle: str = Form("")):
    """Add/update one per-project social account. Project-match-or-400 scoped."""
    with deps.get_db() as conn:
        pid, err = _resolve_pid_or_400(conn, project_id)
        if err is not None:
            return err
        platform = (platform or "").strip().lower()
        if platform not in SOCIAL_PLATFORMS:
            return HTMLResponse('<div class="error">unknown platform</div>', status_code=400)
        clean = _clean_handle(handle)
        if not _HANDLE_RE.match(clean):
            return HTMLResponse('<div class="error">invalid handle</div>', status_code=400)
        repo = _social_repo()
        if repo is None:
            return HTMLResponse('<div class="error">social accounts unavailable</div>',
                                status_code=503)
        status, source, evidence_url = "LIKELY", "manual", ""
        try:
            for row in repo.for_project(conn, pid) or []:
                if (row.get("platform") or "") == platform:
                    if (row.get("handle") or "") == clean:
                        status = row.get("status") or "LIKELY"
                        source = row.get("source") or "manual"
                        evidence_url = row.get("evidence_url") or ""
                    break
        except Exception:
            pass
        try:
            try:
                repo.set_handle(conn, pid, platform, clean,
                                status=status, source=source, evidence_url=evidence_url)
            except TypeError:
                repo.set_handle(conn, pid, platform, clean)
        except ValueError as e:
            return HTMLResponse(f'<div class="error">{e}</div>', status_code=400)
    return RedirectResponse("/projects", status_code=303)


def projects_social_remove(request: Request, project_id: str = Form(""),
                           platform: str = Form("")):
    """Remove one per-project social account. Project-match-or-400 scoped."""
    with deps.get_db() as conn:
        pid, err = _resolve_pid_or_400(conn, project_id)
        if err is not None:
            return err
        platform = (platform or "").strip().lower()
        if platform not in SOCIAL_PLATFORMS:
            return HTMLResponse('<div class="error">unknown platform</div>', status_code=400)
        repo = _social_repo()
        if repo is None:
            return HTMLResponse('<div class="error">social accounts unavailable</div>',
                                status_code=503)
        try:
            repo.remove(conn, pid, platform)
        except ValueError as e:
            return HTMLResponse(f'<div class="error">{e}</div>', status_code=400)
        except Exception:
            return HTMLResponse('<div class="error">social accounts unavailable</div>',
                                status_code=503)
    return RedirectResponse("/projects", status_code=303)
