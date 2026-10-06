"""FastAPI factory. Routes stay presentation-only (F3/F4)."""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

from app.services import display as _display

templates.env.filters["status_label"] = _display.label
templates.env.filters["status_badge"] = _display.badge


def _from_json(value):
    import json as _json
    try:
        parsed = _json.loads(value or "[]")
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


templates.env.filters["from_json"] = _from_json


def create_app() -> FastAPI:
    from app import paths

    app = FastAPI(title=f"Open Marketing OS v{paths.APP_VERSION}")

    @app.middleware("http")
    async def _dead_post_chat_404(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/chat":
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return await call_next(request)

    @app.on_event("startup")
    def _startup():
        from app import paths
        from app import deps

        # Detect an existing installation before init_db creates the database.
        # Edition profiles only seed defaults for genuinely new user state.
        database_preexisted = deps.DB_PATH.exists()

        # DEV-008: in a frozen Windows build every mutable byte lives under
        # %LOCALAPPDATA%. Create it before the database, and lay down the
        # starter workspace, because the workspace parsers assume their source
        # files exist and a source checkout always supplied them. A development
        # checkout is left completely untouched.
        if paths.is_frozen():
            paths.ensure_writable_dirs()
            from app.services.workspace_bootstrap import ensure_workspace

            ensure_workspace(paths.user_data_root())
        deps.init_db()
        if paths.is_frozen():
            from app.services.edition_profile import apply_first_run_profile

            apply_first_run_profile(
                paths.bundle_root() / "edition-profile.json",
                database_preexisted=database_preexisted,
            )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from app.routes import (
        approvals,
        api_spa,
        brain,
        campaigns,
        chat,
        companies,
        health,
        jobs,
        onboarding,
        projects,
        results,
        settings,
        spa,
        tasks,
        today,
        files,
        skills,
    )

    for router in (
        health.router,
        api_spa.router,
        spa.router,
        today.router,
        chat.router,
        projects.router,
        campaigns.router,
        approvals.router,
        brain.router,
        results.router,
        settings.router,
        jobs.router,
        tasks.router,
        companies.router,
        files.router,
        onboarding.router,
        # DEV-008-SKILLS-OPS: the marketing-playbook registry. Mounted last and
        # without a try/except, on purpose -- it shares the /api prefix with
        # api_spa and a silent except around an include_router would drop the
        # Settings > Skills tab with no signal at all. If it fails to import the
        # app must not start.
        skills.router,
    ):
        app.include_router(router)

    # DEV-007 W1: graph routes mount under the default AI_RUNTIME=langgraph.
    # AI_RUNTIME=legacy remains a rollback-only opt-out (v1.0.0 tag).
    try:
        from app.contracts.runtime import get_ai_runtime as _w1_runtime
        if _w1_runtime() == "langgraph":
            from app.routes import graph_runtime as _graph_runtime
            app.include_router(_graph_runtime.router)
    except Exception:
        pass
    from app.routes import graph_runtime as _graph_runtime
    app.include_router(_graph_runtime.compat_router, prefix="/api")

    @app.get("/chroma-badge")
    def chroma_badge(request: Request):
        from app import deps
        from app.database import repos

        status = "keyword only"
        try:
            with deps.get_db() as conn:
                row = repos.Settings.get(conn, "chroma_status")
                if row and row["value"] == "hybrid":
                    status = "hybrid"
        except Exception:
            pass
        return HTMLResponse(f'<span class="badge">{status}</span>')

    return app
