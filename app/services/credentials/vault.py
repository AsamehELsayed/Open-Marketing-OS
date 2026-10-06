"""3-scope credential vault (I-3): refs in SQLite, ciphertext in the OS store.

Scopes: installation (this install / org BYOK), project (one project's key),
user (the operator's personal key). secret_ref format:
vault://<scope>/<uuid4hex>. SQLite holds REFS ONLY — the plaintext value
never touches the database, logs, status payloads, telemetry, exports,
prompts, RAG, chat, or error text. resolve() is server-side only: callers
must keep the returned string out of every outbound surface listed above.
.env stays bootstrap/dev-only; normal users configure credentials through
the product UI (BYOK / installation model). The OMOS Hosted Integration
Gateway is DOCUMENTED AS NOT-IMPLEMENTED — never built in this run.
"""
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from app.services.credentials.store import SecretBackend, default_backend, validate_key

SCOPES = frozenset({"installation", "project", "user"})
ACTIVE = "active"
REVOKED = "revoked"
REF_RE = re.compile(r"^vault://(installation|project|user)/([0-9a-f]{32})$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Additive DDL — mirrors development/runs/DEV-007/migrations/w5.sql exactly
# (test_dev007_vault asserts both stay in sync). project_id NOT NULL on every
# project-owned table; installation/user rows park at project_id ''.
CREDENTIALS_REFS_DDL = (
    """CREATE TABLE IF NOT EXISTS credentials_refs (
  secret_ref TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  label TEXT NOT NULL,
  project_id TEXT NOT NULL DEFAULT '',
  user_id TEXT NOT NULL DEFAULT '',
  backend TEXT NOT NULL DEFAULT 'dpapi_file',
  backend_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT '',
  revoked_at TEXT NOT NULL DEFAULT '')""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_credentials_refs_active
  ON credentials_refs(scope, label, project_id, user_id)
  WHERE status = 'active'""",
    """CREATE INDEX IF NOT EXISTS idx_credentials_refs_scope
  ON credentials_refs(scope, label)""",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_credentials_refs(conn) -> None:
    """Idempotent create of credentials_refs (additive; safe on every call)."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='credentials_refs'"
    ).fetchone()
    if row is not None:
        return
    for stmt in CREDENTIALS_REFS_DDL:
        conn.execute(stmt)
    conn.commit()


@contextmanager
def _db(conn):
    """Use the caller's connection when given; otherwise open the app DB per call."""
    if conn is not None:
        if conn.row_factory is not sqlite3.Row:
            conn.row_factory = sqlite3.Row
        yield conn
        return
    from app.database.sqlite import connect
    from app.deps import DB_PATH

    fresh = connect(DB_PATH)
    try:
        if fresh.row_factory is not sqlite3.Row:
            fresh.row_factory = sqlite3.Row
        yield fresh
    finally:
        fresh.close()


def _validate_scope(scope: str) -> str:
    if scope not in SCOPES:
        raise ValueError(f"invalid scope {scope!r}; expected one of {sorted(SCOPES)}")
    return scope


def _validate_label(label: str) -> str:
    if not isinstance(label, str) or not LABEL_RE.match(label):
        raise ValueError("label must be a short slug ([A-Za-z0-9._-], 1-64 chars)")
    return label


def _ownership(scope: str, project_id, user_id) -> tuple[str, str]:
    """Ownership matrix (fail closed): installation -> ('', ''); project needs
    project_id; user needs user_id. Non-applicable owner ids are rejected."""
    if scope == "installation":
        if project_id or user_id:
            raise ValueError("installation-scoped secrets take no project_id/user_id")
        return "", ""
    if scope == "project":
        pid = project_id.strip() if isinstance(project_id, str) else ""
        if not pid:
            raise ValueError("project scope requires project_id (fail closed)")
        if user_id:
            raise ValueError("project-scoped secrets take no user_id")
        return pid, ""
    uid = user_id.strip() if isinstance(user_id, str) else ""
    if not uid:
        raise ValueError("user scope requires user_id (fail closed)")
    if project_id:
        raise ValueError("user-scoped secrets take no project_id")
    return "", uid


def _active_row(conn, scope: str, label: str, project_id: str, user_id: str):
    return conn.execute(
        """SELECT * FROM credentials_refs
           WHERE scope = ? AND label = ? AND project_id = ? AND user_id = ?
             AND status = 'active'""",
        (scope, label, project_id, user_id),
    ).fetchone()


def _retire_active(conn, scope: str, label: str, project_id: str, user_id: str,
                   backend: SecretBackend) -> None:
    """Mark any active row for this key revoked and drop its ciphertext."""
    row = _active_row(conn, scope, label, project_id, user_id)
    if row is None:
        return
    conn.execute(
        "UPDATE credentials_refs SET status = ?, revoked_at = ? WHERE secret_ref = ?",
        (REVOKED, _now(), row["secret_ref"]),
    )
    conn.commit()
    try:
        backend.delete(row["backend_key"])
    except Exception:
        pass  # ref is already dead (fail closed); orphan ciphertext is DPAPI-bound


def store(scope: str, label: str, value: str, *, project_id: str | None = None,
          user_id: str | None = None, conn=None, backend: SecretBackend | None = None) -> str:
    """Persist a secret in the OS backend; return its vault:// ref (SQLite refs only)."""
    scope = _validate_scope(scope)
    _validate_label(label)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("value must be a non-empty string")
    pid, uid = _ownership(scope, project_id, user_id)
    be = backend if backend is not None else default_backend()
    with _db(conn) as c:
        ensure_credentials_refs(c)
        previous = _active_row(c, scope, label, pid, uid)
        token = uuid.uuid4().hex
        backend_key = f"{scope}/{token}"
        ref = f"vault://{scope}/{token}"
        # Write the replacement ciphertext before changing the active ref.
        # If DPAPI/backend storage fails, the existing credential remains usable.
        be.put(backend_key, value.encode("utf-8"))
        savepoint = "vault_store_replace"
        try:
            c.execute(f"SAVEPOINT {savepoint}")
            if previous is not None:
                c.execute(
                    "UPDATE credentials_refs SET status = ?, revoked_at = ? WHERE secret_ref = ?",
                    (REVOKED, _now(), previous["secret_ref"]),
                )
            c.execute(
                """INSERT INTO credentials_refs
                   (secret_ref, scope, label, project_id, user_id, backend,
                    backend_key, status, created_at, revoked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, '')""",
                (ref, scope, label, pid, uid, be.name, backend_key, _now()),
            )
            c.execute(f"RELEASE SAVEPOINT {savepoint}")
            c.commit()
        except Exception:
            try:
                c.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                c.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                pass
            try:
                c.rollback()
            except Exception:
                pass
            try:
                be.delete(backend_key)
            except Exception:
                pass
            raise
        # The old ref is retired only after the replacement is durable. Ciphertext
        # cleanup is best effort; a leftover blob has no active database reference.
        if previous is not None:
            try:
                be.delete(previous["backend_key"])
            except Exception:
                pass
        return ref


def credential_status(scope: str, label: str, *, project_id: str | None = None,
                      user_id: str | None = None, conn=None,
                      backend: SecretBackend | None = None) -> str:
    """Return a safe readiness state without exposing vault errors or values."""
    scope = _validate_scope(scope)
    _validate_label(label)
    pid, uid = _ownership(scope, project_id, user_id)
    try:
        with _db(conn) as c:
            ensure_credentials_refs(c)
            row = _active_row(c, scope, label, pid, uid)
            if row is None:
                return "not_configured"
            be = backend if backend is not None else default_backend()
            if row["backend"] != be.name:
                return "unreadable"
            blob = be.get(row["backend_key"])
            if blob is None:
                return "missing"
            value = blob.decode("utf-8")
            if not value.strip():
                return "empty"
            return "configured"
    except Exception:
        # OS/backend exception text can contain paths and platform details. Keep
        # readiness responses to the fixed enum only.
        return "unreadable"


def get_secret_ref(scope: str, label: str, *, project_id: str | None = None,
                   user_id: str | None = None, conn=None) -> str | None:
    """Active vault:// ref for (scope, label) or None — never the secret itself."""
    scope = _validate_scope(scope)
    _validate_label(label)
    pid, uid = _ownership(scope, project_id, user_id)
    with _db(conn) as c:
        ensure_credentials_refs(c)
        row = _active_row(c, scope, label, pid, uid)
        return row["secret_ref"] if row is not None else None


def resolve(secret_ref: str, *, conn=None, backend: SecretBackend | None = None) -> str:
    """SERVER-SIDE ONLY: decrypt to plaintext. Never log, export, prompt, or show."""
    if not isinstance(secret_ref, str) or not REF_RE.match(secret_ref):
        raise ValueError("malformed secret_ref (expected vault://<scope>/<32-hex>)")
    scope = REF_RE.match(secret_ref).group(1)
    with _db(conn) as c:
        ensure_credentials_refs(c)
        row = c.execute(
            "SELECT * FROM credentials_refs WHERE secret_ref = ?", (secret_ref,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown secret_ref {secret_ref}")
        if row["scope"] != scope:
            raise ValueError("secret_ref scope mismatch")
        if row["status"] != ACTIVE:
            raise ValueError(f"secret_ref {secret_ref} is revoked")
        be = backend if backend is not None else default_backend()
        if row["backend"] != be.name:
            raise ValueError(
                f"backend mismatch: ref stored by {row['backend']!r}, resolve got {be.name!r}"
            )
        blob = be.get(row["backend_key"])
        if blob is None:
            raise KeyError(f"ciphertext missing for secret_ref {secret_ref}")
        return blob.decode("utf-8")


def revoke(secret_ref: str, *, conn=None, backend: SecretBackend | None = None) -> None:
    """Kill a ref: mark revoked in SQLite, delete ciphertext. Idempotent once revoked."""
    if not isinstance(secret_ref, str) or not REF_RE.match(secret_ref):
        raise ValueError("malformed secret_ref (expected vault://<scope>/<32-hex>)")
    be = backend if backend is not None else default_backend()
    with _db(conn) as c:
        ensure_credentials_refs(c)
        row = c.execute(
            "SELECT * FROM credentials_refs WHERE secret_ref = ?", (secret_ref,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown secret_ref {secret_ref}")
        if row["status"] == ACTIVE:
            c.execute(
                "UPDATE credentials_refs SET status = ?, revoked_at = ? WHERE secret_ref = ?",
                (REVOKED, _now(), secret_ref),
            )
            c.commit()
        try:
            be.delete(row["backend_key"])
        except Exception:
            pass  # ref already dead; orphan ciphertext is DPAPI-bound (sweep later)


def get_ref_row(secret_ref: str, *, conn=None) -> dict | None:
    """Ref-row metadata for integrity checks (no secret material on the row)."""
    if not isinstance(secret_ref, str) or not REF_RE.match(secret_ref):
        raise ValueError("malformed secret_ref (expected vault://<scope>/<32-hex>)")
    with _db(conn) as c:
        ensure_credentials_refs(c)
        row = c.execute(
            "SELECT * FROM credentials_refs WHERE secret_ref = ?", (secret_ref,)
        ).fetchone()
        return dict(row) if row is not None else None


def export_status(conn=None) -> list[dict]:
    """Presence-only inventory for UI/settings (labels + booleans, no refs/values)."""
    with _db(conn) as c:
        ensure_credentials_refs(c)
        rows = c.execute(
            """SELECT scope, label, project_id, user_id, status, created_at, revoked_at
               FROM credentials_refs ORDER BY scope, label"""
        ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["active"] = d["status"] == ACTIVE
            out.append(d)
        return out
