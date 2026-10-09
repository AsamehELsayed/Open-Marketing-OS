"""DEV-004 integrator: serve the built React SPA at /app (additive only).

New router — no backend logic, template, or existing-route changes.
- Built assets from frontend/dist are served under /assets (the Vite build
  emits absolute /assets/* references in index.html).
- GET /app + GET /app/{path} serve index.html (SPA fallback for client-side
  routes such as /app/chat/:id).
- Only /app/* and /assets/* are handled here; /api, /chat, /static, /jobs,
  /approvals, /settings, /system and all other legacy routes are untouched
  and keep their existing precedence.
- If frontend/dist is absent (e.g. `npm run build` was never run), respond
  503 with build instructions instead of crashing.
- Legacy Jinja routes stay the default; nothing flips existing behaviour.
"""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, PlainTextResponse

router = APIRouter(tags=["spa-frontend"])

DIST_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
INDEX_FILE = DIST_DIR / "index.html"
ASSETS_DIR = DIST_DIR / "assets"
FAVICON_FILE = DIST_DIR / "favicon.ico"
SOURCE_FAVICON_FILE = Path(__file__).resolve().parents[2] / "frontend" / "public" / "favicon.ico"

BUILD_INSTRUCTIONS = (
    "SPA bundle not built. From the repo root run:\n"
    "  cd frontend\n"
    "  npm install --no-audit --no-fund\n"
    "  npm run build\n"
    "then reload /app. Legacy UI routes are unaffected."
)


def _dist_ready() -> bool:
    try:
        return INDEX_FILE.is_file()
    except OSError:
        return False


def _safe_under(candidate: Path, root: Path) -> bool:
    """True if candidate resolves inside root (blocks path traversal)."""
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


@router.get("/favicon.ico", include_in_schema=False)
def spa_favicon():
    """Serve the packaged icon, falling back to the checked-in source asset."""
    if FAVICON_FILE.is_file():
        return FileResponse(str(FAVICON_FILE), media_type="image/x-icon")
    if SOURCE_FAVICON_FILE.is_file():
        return FileResponse(str(SOURCE_FAVICON_FILE), media_type="image/x-icon")
    return PlainTextResponse("not found", status_code=404)


@router.get("/assets/{file_path:path}", include_in_schema=False)
def spa_assets(file_path: str):
    target = ASSETS_DIR / file_path
    if ASSETS_DIR.is_dir() and file_path and _safe_under(target, ASSETS_DIR) and target.is_file():
        return FileResponse(str(target))
    return PlainTextResponse("not found", status_code=404)


@router.get("/app", include_in_schema=False)
def spa_root():
    if not _dist_ready():
        return PlainTextResponse(BUILD_INSTRUCTIONS, status_code=503)
    return FileResponse(str(INDEX_FILE), media_type="text/html")


@router.get("/app/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    if not _dist_ready():
        return PlainTextResponse(BUILD_INSTRUCTIONS, status_code=503)
    # Serve real dist files addressed under /app (e.g. /app/assets/x.js);
    # everything else falls back to index.html for client-side routing.
    if full_path:
        candidate = DIST_DIR / full_path
        if _safe_under(candidate, DIST_DIR) and candidate.is_file():
            return FileResponse(str(candidate))
    return FileResponse(str(INDEX_FILE), media_type="text/html")
