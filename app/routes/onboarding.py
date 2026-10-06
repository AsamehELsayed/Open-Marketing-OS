"""First-run onboarding (DEV-008).

The public entry experience is: install -> open -> setup AI -> create business
-> start working. This router is the backend half of that flow.

Design constraints, all deliberate:

- **No new persistence model.** "Create Your Business" writes the real
  `company/company.yaml` and then runs the product's existing
  file -> importer -> database flow. It does not insert a project row behind the
  adapters' back, so onboarding cannot drift from the rest of the product.
- **Friendly errors only.** `app.contracts.events.safe_error_message` is the
  leak boundary, so a non-developer never sees a traceback, a SQL string or a
  provider stack.
- **Never returns a secret.** Status is a boolean per provider; the key itself
  is never read back out of the vault.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import deps
from app.contracts.events import safe_error_message
from app.database import repos
from app.database.seed import DEFAULT_COMPANY_NAME
from app.services import state as store
from app.services.workspace_bootstrap import (
    apply_business_details,
    ensure_workspace,
    workspace_is_seeded,
)

router = APIRouter(prefix="/api", tags=["onboarding"])
START_HERE_VERSION = "start-here-v1"
START_HERE_SETTING = "intro_version_seen"

#: Offered on the final onboarding step and reused by the empty-chat state, so
#: a brand-new user lands on something actionable rather than a blank dashboard.
STARTING_POINTS: tuple[dict[str, str], ...] = (
    {"id": "analysis", "label": "Full marketing analysis",
     "prompt": "Analyze my business and tell me the three biggest growth opportunities."},
    {"id": "website", "label": "Review my website",
     "prompt": "Review my website and tell me what is working and what is holding conversions back."},
    {"id": "competitors", "label": "Research my competitors",
     "prompt": "Research my main competitors and summarize how they position themselves."},
    {"id": "social", "label": "Look at my social media",
     "prompt": "Review my Instagram profile and tell me what to improve."},
    {"id": "positioning", "label": "Improve my offer and positioning",
     "prompt": "Critique my offer and positioning, then propose sharper versions."},
    {"id": "other", "label": "Something else",
     "prompt": ""},
)

_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)


def _ok(data):
    return {"ok": True, "data": data}


def _workspace_project_id(conn) -> str:
    """The project this onboarding flow acts on.

    DEV-008-PUBLISH-GATE. This used to be the `DEFAULT_PROJECT_ID` constant,
    which is correct for a fresh install and wrong for an existing one: an
    install created before the rename has its project stored under a legacy id
    and its `active_project_id` pointing at that row, so reading the constant
    would have reported "no business described yet" and then written the new
    business into a *second*, empty project instead of the user's real one.

    Resolving through the stored active project makes fresh-install behaviour
    and historical-install behaviour the same code path: a fresh database has
    `active_project_id = starter` and gets `starter`; an existing one has its
    own id and keeps it. No user data is renamed, moved or deleted either way.
    """
    return store.active_project_id(conn)


def _existing_project(conn, project_id: str) -> dict:
    """The project row, created if the database somehow has none yet.

    Tolerates a hand-edited or partially restored database instead of failing
    the wizard, which is the one flow a new user cannot retry.
    """
    row = repos.Projects.get(conn, project_id)
    if row is not None:
        return row
    try:
        return store.create_project(conn, project_id,
                                    name=DEFAULT_COMPANY_NAME)
    except Exception:
        return {}


def _normalise_website(raw: str) -> str:
    """Accept what a user actually types and store a usable absolute URL.

    People paste `example.com`. The website tool requires an absolute http(s)
    URL, so a bare host is upgraded to `https://` rather than being rejected
    by a validation error the user cannot interpret.
    """
    value = (raw or "").strip()
    if not value:
        return ""
    if not _URL_RE.match(value):
        value = "https://" + value.lstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="Enter a valid website address using http:// or https://",
        )
    # Normalize scheme casing while preserving the user's host and path.
    return parsed.scheme.lower() + ":" + value[len(parsed.scheme) + 1:]


@router.get("/onboarding/status")
def onboarding_status():
    """Drives the wizard. Cheap, read-only, never echoes a credential."""
    from app.services.config_service import ConfigService

    with deps.get_db() as conn:
        project_id = _workspace_project_id(conn)
        project = repos.Projects.get(conn, project_id) or {}
        company = repos.Companies.get(conn, project_id) or {}
        active_id = project_id

    project_name = (project.get("name") or "").strip()
    seeded = workspace_is_seeded(deps.ROOT)
    # The neutral seed placeholder means the user has not described their
    # business yet. Compared against the seed constant only, never against a
    # literal: a hardcoded client name in product code is exactly the class of
    # bug DEV-008-PUBLISH-GATE removed from this codebase.
    described = bool(project_name) and project_name != DEFAULT_COMPANY_NAME

    return _ok({
        "app_version": _app_version(),
        "workspace_seeded": seeded,
        "business_described": described,
        "intro_version_seen": ConfigService.get_setting(START_HERE_SETTING, "") or "",
        "project": {
            "id": project.get("id", project_id),
            "name": project_name,
            "website": project.get("website", "") or "",
            "goal": project.get("goal", "") or "",
        },
        "company_name": company.get("name", "") or "",
        "active_project_id": active_id,
        "ai": {
            "openrouter_connected": _safe_flag(ConfigService.is_openrouter_configured),
            "openai_connected": _safe_flag(ConfigService.is_openai_configured),
        },
        "starting_points": list(STARTING_POINTS),
    })


class IntroCompletionBody(BaseModel):
    version: str = Field(default=START_HERE_VERSION, min_length=1, max_length=40)


@router.post("/onboarding/intro/complete")
def complete_intro(body: IntroCompletionBody):
    """Persist the Start Here version without exposing any settings or secrets."""
    from app.services.config_service import ConfigService

    version = body.version.strip()
    if not version:
        raise HTTPException(status_code=400, detail="Version is required")
    ConfigService.set_setting(START_HERE_SETTING, version)
    return _ok({"intro_version_seen": version})


def _safe_flag(fn) -> bool:
    """A credential probe must never break the wizard.

    `is_*_configured` touches the vault, which touches the filesystem. On a
    locked-down machine that can raise; the wizard should still render and let
    the user try to connect, rather than 500.
    """
    try:
        return bool(fn())
    except Exception:
        return False


def _app_version() -> str:
    try:
        from app import paths

        return paths.APP_VERSION
    except Exception:
        return "unknown"


class BusinessBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    website: str = Field(default="", max_length=300)
    # `context` and `primary_market` are the supported first-run fields. Keep
    # the older goal/markets aliases for clients already using DEV-008 API.
    context: str = Field(default="", max_length=200)
    primary_market: str = Field(default="", max_length=120)
    industry: str = Field(default="", max_length=120)
    goal: str = Field(default="", max_length=200)
    markets: list[str] = Field(default_factory=list, max_length=12)
    languages: list[str] = Field(default_factory=list, max_length=12)
    services: list[str] = Field(default_factory=list, max_length=24)


@router.post("/onboarding/business")
def onboarding_business(body: BusinessBody):
    """Create the user's business through the real workspace import path."""
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Business name is required")
    website = _normalise_website(body.website)

    def _clean(items: list[str], limit: int) -> list[str]:
        out: list[str] = []
        for item in items:
            value = (item or "").strip()
            if value and value not in out:
                out.append(value)
            if len(out) >= limit:
                break
        return out

    context = (body.context or body.goal or "").strip()
    primary_market = (body.primary_market or "").strip()
    markets = [primary_market] if primary_market else _clean(body.markets, 12)

    try:
        apply_business_details(
            deps.ROOT,
            name=name,
            website=website,
            industry=(body.industry or "").strip(),
            markets=markets,
            languages=_clean(body.languages, 12),
            services=_clean(body.services, 24),
        )
    except OSError:
        raise HTTPException(
            status_code=500,
            detail=(
                "Open Marketing OS could not save your business details. "
                "Check that you have permission to write to your user data folder."
            ),
        )

    # Import through the adapters so the Companies row is produced by the same
    # parser that every other workspace source uses.
    try:
        with deps.get_db() as conn:
            store.run_imports(conn, deps.ROOT)
    except Exception:
        # The file is written and correct; a failed import must not lose the
        # user's input. Surface it as a warning, not a failure.
        import_warning = "Your business was saved, but the index needs a refresh."
    else:
        import_warning = ""

    try:
        with deps.get_db() as conn:
            project_id = _workspace_project_id(conn)
            project = _existing_project(conn, project_id)
            project.update({
                "id": project_id,
                "name": name,
                "website": website,
                "status": project.get("status") or "active",
                "settings_json": project.get("settings_json") or "{}",
                "created_at": project.get("created_at") or store.now_iso(),
            })
            if context:
                project["goal"] = context
            project["updated_at"] = store.now_iso()
            repos.Projects.upsert(conn, project)
            store.set_active_project(conn, project_id)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=(
                "Open Marketing OS could not finish setting up your workspace. "
                "Your business details were saved. Restart the app and try again."
            ),
        )

    return _ok({
        "project": {"id": project_id, "name": name, "website": website},
        "warning": import_warning,
        "starting_points": list(STARTING_POINTS),
    })


@router.post("/onboarding/workspace/seed")
def onboarding_seed_workspace():
    """Idempotently create any missing starter workspace file.

    Exposed so the wizard can offer a "restore starter files" affordance and
    so a user who deleted a file is not permanently broken.
    """
    try:
        created = ensure_workspace(deps.ROOT)
    except OSError:
        raise HTTPException(
            status_code=500,
            detail=(
                "Open Marketing OS could not create its workspace files. "
                "Check that you have permission to write to your user data folder."
            ),
        )
    return _ok({"created": created, "count": len(created)})


@router.get("/onboarding/error")
def onboarding_error_probe():
    """Never called by the product.

    Present so a launcher or QA harness can confirm the friendly-error boundary
    is actually installed on this build, without having to trigger a real
    failure. Returns the sanitiser's output for a deliberately unsafe message.
    """
    return _ok({
        "sample": safe_error_message(
            ValueError("boom"), "Something went wrong. Please try again."
        )
    })
