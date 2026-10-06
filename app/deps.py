"""Service wiring. Routes depend on services via this module —
never on database internals or subprocess (acceptance F3/F4).

DEV-008: `ROOT` and `DB_PATH` are resolved through `app.paths` so a frozen
Windows build writes all mutable state under `%LOCALAPPDATA%\\OpenMarketingOS`
instead of the read-only install folder. In a source checkout `app.paths`
returns the repository root, so the values below are unchanged from v1.0.0 and
the existing dev workflow and tests behave exactly as before.

These stay module-level and freely rebindable: tests monkeypatch
`deps.DB_PATH` to relocate the database.
"""
from contextlib import contextmanager
from pathlib import Path

from app import paths

#: Workspace root. Holds the user's `company/`, `knowledge/`, `production/`,
#: `strategy/`, `state/` files *and* the `data/` subtree (see `app.paths`).
ROOT = paths.user_data_root()

#: SQLite database. `ROOT / "data" / "marketing.db"` in every mode.
DB_PATH = ROOT / "data" / "marketing.db"


@contextmanager
def get_db(path: str | Path | None = None):
    """Yield a SQLite connection (schema-ensured). Test overrode via path."""
    from app.database.sqlite import connect

    conn = connect(path or DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def init_db(path: str | Path | None = None):
    """Create schema + minimal seed. Idempotent; safe on every startup."""
    from app.database.seed import ensure_seed
    from app.database.sqlite import connect

    conn = connect(path or DB_PATH)
    try:
        ensure_seed(conn)
    finally:
        conn.close()
