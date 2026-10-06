from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health():
    """Liveness plus the running build's version.

    DEV-008: the version used to be a hardcoded literal here, which meant the
    app could report a different version than the artifact it shipped inside.
    It now reads `app.paths.APP_VERSION`, the same constant the release
    manifest and the installer stamp, so the three cannot drift. The launcher
    polls this endpoint to decide when the backend is ready.
    """
    from app import paths

    return {"status": "ok", "version": paths.APP_VERSION}
