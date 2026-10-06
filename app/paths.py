"""Single source of truth for filesystem locations (DEV-008 distribution).

Two modes, one layout:

- **Source checkout (development).** ``OMOS_WORKSPACE_DIR`` is unset, so the
  workspace root is the repository root and everything behaves exactly as it
  did before DEV-008. Existing tests, seeds and dev scripts are unaffected.

- **Frozen build (shipped Windows app).** The application payload is installed
  under ``Program Files`` and is **read-only**. Every mutable byte must live
  under the per-user data root instead::

      %LOCALAPPDATA%\\OpenMarketingOS\\
        data\\marketing.db        SQLite database
        data\\chroma\\             optional hybrid search index
        data\\backups\\            workspace backups
        data\\rag_debug\\          opt-in retrieval debug dumps
        data\\credentials\\        legacy default (see CREDENTIALS_DIR)
        credentials\\              DPAPI-encrypted secrets   <- the real vault
        logs\\                     launcher + backend logs
        company\\ knowledge\\ ...  user workspace files

The launcher exports ``OMOS_WORKSPACE_DIR``, ``OMOS_CREDENTIALS_DIR`` and
``OMOS_LOG_DIR`` explicitly, but every default below also self-resolves when
the frozen app is started directly (for example from the portable ZIP), so a
missing environment variable degrades to a writable location rather than to
``Program Files``.

Nothing here contacts the network, and no path is ever sent off the machine.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "OpenMarketingOS"
APP_TITLE = "Open Marketing OS"
APP_VERSION = "1.1.0-beta.1"
"""Single authoritative version string.

`app/routes/health.py` and the release manifest both read this so the version
reported by the running app can never drift from the packaged artifact.
"""

# Environment overrides. The launcher sets all three; they are also honoured in
# development so tests can relocate state without monkeypatching internals.
ENV_WORKSPACE = "OMOS_WORKSPACE_DIR"
ENV_CREDENTIALS = "OMOS_CREDENTIALS_DIR"
ENV_LOGS = "OMOS_LOG_DIR"
ENV_BUNDLE = "OMOS_BUNDLE_DIR"

# Set to "1" by the frozen build so the app behaves as packaged even when it
# somehow runs from a writable copy of itself.
ENV_FROZEN = "OMOS_FROZEN"

_LOGS_SUBDIR = "logs"
_CREDENTIALS_SUBDIR = "credentials"


def is_frozen() -> bool:
    """True when running from a PyInstaller-style bundle.

    ``sys.frozen`` is set by PyInstaller. The env override exists so the
    behaviour is testable and reproducible from a source checkout.
    """
    if bool(getattr(sys, "frozen", False)):
        return True
    return (os.environ.get(ENV_FROZEN, "") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def bundle_root() -> Path:
    """Directory holding the installed application payload (may be read-only).

    In a source checkout this is the repository root. In a frozen build this is
    the directory containing the bundled interpreter, so that anything shipped
    *with* the app (the built React bundle, default templates) is read from
    here and never from user-writable space.
    """
    override = (os.environ.get(ENV_BUNDLE, "") or "").strip()
    if override:
        return Path(override)
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _local_app_data() -> Path:
    """Per-user local application data root for this app.

    Falls back to ``~/AppData/Local`` then ``~/.local/share`` so the app still
    starts on an unusual Windows configuration instead of hard-failing. The
    backend never needs to be reachable by other machines, so local (not
    roaming) scope is correct.
    """
    base = (os.environ.get("LOCALAPPDATA", "") or "").strip()
    if base:
        return Path(base) / APP_NAME
    return Path.home() / "AppData" / "Local" / APP_NAME


def user_data_root() -> Path:
    """Root of everything the user owns. Never inside the install folder.

    In a source checkout this intentionally stays the repository root so the
    dev experience (and the existing test suite) is byte-for-byte unchanged.
    """
    override = (os.environ.get(ENV_WORKSPACE, "") or "").strip()
    if override:
        return Path(override)
    if is_frozen():
        return _local_app_data()
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Mutable application state: database, search index, backups."""
    return user_data_root() / "data"


def database_path() -> Path:
    return data_dir() / "marketing.db"


def credentials_dir() -> Path:
    """DPAPI-encrypted secret store.

    Defaults to the user data root, not ``<workspace>/data/credentials``, so
    that an uninstall or a manual workspace move can never orphan secrets.
    """
    override = (os.environ.get(ENV_CREDENTIALS, "") or "").strip()
    if override:
        return Path(override)
    return user_data_root() / _CREDENTIALS_SUBDIR


def logs_dir() -> Path:
    override = (os.environ.get(ENV_LOGS, "") or "").strip()
    if override:
        return Path(override)
    return user_data_root() / _LOGS_SUBDIR


def ensure_writable_dirs() -> None:
    """Create the mutable directories. Safe to call repeatedly.

    Failures are intentionally *not* swallowed: a packaged app that cannot
    write its data directory cannot work, and the launcher surfaces this as a
    friendly error rather than letting the backend die later with a raw
    traceback.
    """
    for path in (user_data_root(), data_dir(), credentials_dir(), logs_dir()):
        path.mkdir(parents=True, exist_ok=True)


def describe() -> dict[str, str]:
    """Resolved locations, for the launcher's diagnostics and bug reports.

    Paths may appear in a local log or a Show Details panel. They are never
    transmitted anywhere.
    """
    return {
        "mode": "frozen" if is_frozen() else "source",
        "bundle": str(bundle_root()),
        "user_data": str(user_data_root()),
        "database": str(database_path()),
        "credentials": str(credentials_dir()),
        "logs": str(logs_dir()),
    }
