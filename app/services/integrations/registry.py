"""Integration registry (I-2): CAPABILITY separated from PROVIDER.

Frozen shapes other workers code against:
- IntegrationRecord (frozen dataclass): integration_id, capability, provider,
  scope, status, config (non-secret ONLY), secret_ref, last_health.
- register_integration(rec) — validated upsert; returns the record.
- resolve_provider(capability, project_id) -> provider str; fail closed
  (raises) on missing project_id, unknown capability, or ambiguous owner ids.
- integration_status(project_id) -> list[dict] presence-only UI cards.

Resolution never falls back to env vars (env reads are W7's migration job):
no registry row means IntegrationNotFound, not a default provider. Secrets
live only in the vault; this table stores the vault:// ref plus non-secret
config. Status payloads carry presence booleans/labels — never secret_ref
values or secret material.
"""
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.credentials import vault as _vault
from app.services.rag.secrets import contains_secrets

PROVIDERS = frozenset({"apify", "meta", "brightdata", "browser", "openai"})
SCOPES = frozenset({"installation", "project", "user"})
STATUSES = frozenset({"connected", "not_configured", "error"})

CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FORBIDDEN_CONFIG_TOKENS = frozenset({
    "secret", "secrets", "token", "tokens", "password", "passwd",
    "credential", "credentials", "apikey", "key", "private", "bearer",
    "auth", "authorization",
})

# Additive DDL — mirrors development/runs/DEV-007/migrations/w5.sql exactly
# (test_dev007_vault asserts both stay in sync). project_id NOT NULL on this
# project-owned table; installation/user rows park at project_id ''.
INTEGRATIONS_DDL = (
    """CREATE TABLE IF NOT EXISTS integrations (
  integration_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  provider TEXT NOT NULL,
  scope TEXT NOT NULL,
  status TEXT NOT NULL,
  project_id TEXT NOT NULL DEFAULT '',
  user_id TEXT NOT NULL DEFAULT '',
  config_json TEXT NOT NULL DEFAULT '{}',
  secret_ref TEXT,
  last_health TEXT,
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (project_id, scope, integration_id, capability))""",
    "CREATE INDEX IF NOT EXISTS idx_integrations_project ON integrations(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_integrations_capability ON integrations(capability, project_id)",
)


class IntegrationNotFound(LookupError):
    """No registry row for the capability — fail closed, never a default provider."""


@dataclass(frozen=True)
class IntegrationRecord:
    integration_id: str
    capability: str
    provider: str
    scope: str
    status: str
    config: dict
    secret_ref: str | None
    last_health: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_integrations(conn) -> None:
    """Idempotent create of integrations (additive; safe on every call)."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='integrations'"
    ).fetchone()
    if row is not None:
        return
    for stmt in INTEGRATIONS_DDL:
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


def _require_project_id(project_id) -> str:
    """Fail closed: resolve/status never run without an explicit project."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("project_id is required (fail closed)")
    return project_id.strip()


def _ownership(scope: str, project_id, user_id) -> tuple[str, str]:
    """Ownership matrix (fail closed): installation -> ('', ''); project needs
    project_id; user needs user_id. Non-applicable owner ids are rejected."""
    if scope == "installation":
        if project_id or user_id:
            raise ValueError("installation-scoped records take no project_id/user_id")
        return "", ""
    if scope == "project":
        pid = project_id.strip() if isinstance(project_id, str) else ""
        if not pid:
            raise ValueError("project-scoped records require project_id (fail closed)")
        if user_id:
            raise ValueError("project-scoped records take no user_id")
        return pid, ""
    uid = user_id.strip() if isinstance(user_id, str) else ""
    if not uid:
        raise ValueError("user-scoped records require user_id (fail closed)")
    if project_id:
        raise ValueError("user-scoped records take no project_id")
    return "", uid


def _config_key_forbidden(key: str) -> bool:
    parts = [p for p in re.split(r"[^a-z0-9]+", str(key).lower()) if p]
    if not parts:
        return True
    return any(p in _FORBIDDEN_CONFIG_TOKENS for p in parts)


def _validate_config(config) -> str:
    if not isinstance(config, dict):
        raise ValueError("config must be a dict (non-secret values only)")
    for key in config:
        if _config_key_forbidden(key):
            raise ValueError(
                f"config key {key!r} looks secret-bearing — use the vault, not config"
            )
    try:
        blob = json.dumps(config, sort_keys=True)
    except (TypeError, ValueError) as e:
        raise ValueError(f"config must be JSON-serializable: {e}") from None
    if contains_secrets(blob):
        raise ValueError("config value matches a secret pattern — use the vault, not config")
    return blob


def _validate_record(rec: IntegrationRecord, conn) -> None:
    if not isinstance(rec, IntegrationRecord):
        raise TypeError("register_integration expects an IntegrationRecord")
    if not isinstance(rec.integration_id, str) or not ID_RE.match(rec.integration_id):
        raise ValueError("integration_id must be a short slug ([A-Za-z0-9._-])")
    if not isinstance(rec.capability, str) or not CAPABILITY_RE.match(rec.capability):
        raise ValueError("capability must be a lowercase slug ([a-z0-9._-], 2-64)")
    if rec.provider not in PROVIDERS:
        raise ValueError(f"provider {rec.provider!r} not in {sorted(PROVIDERS)}")
    if rec.scope not in SCOPES:
        raise ValueError(f"scope {rec.scope!r} not in {sorted(SCOPES)}")
    if rec.status not in STATUSES:
        raise ValueError(f"status {rec.status!r} not in {sorted(STATUSES)}")
    _validate_config(rec.config)
    if rec.last_health is not None and not isinstance(rec.last_health, str):
        raise ValueError("last_health must be str or None")
    if rec.secret_ref is not None:
        if not isinstance(rec.secret_ref, str) or not _vault.REF_RE.match(rec.secret_ref):
            raise ValueError("secret_ref must be None or vault://<scope>/<32-hex>")
        ref_scope = _vault.REF_RE.match(rec.secret_ref).group(1)
        if ref_scope != rec.scope:
            raise ValueError("secret_ref scope must match record scope")
        row = _vault.get_ref_row(rec.secret_ref, conn=conn)
        if row is None:
            raise ValueError("secret_ref not found in vault — store() the secret first")
        if row["status"] != _vault.ACTIVE:
            raise ValueError("secret_ref is revoked")


def register_integration(rec: IntegrationRecord, *, project_id: str | None = None,
                         user_id: str | None = None, conn=None) -> IntegrationRecord:
    """Validated upsert keyed by (project_id, scope, integration_id, capability).

    Owner ids are keyword-only extras on the frozen call shape
    (register_integration(rec)): installation records take neither, project
    records must pass project_id=..., user records must pass user_id=...
    (fail closed otherwise). secret_ref, if set, must already be an active
    vault ref with matching scope (store the secret first, then register).
    """
    _validate_record(rec, conn)
    pid, uid = _ownership(rec.scope, project_id, user_id)
    config_json = _validate_config(rec.config)
    with _db(conn) as c:
        ensure_integrations(c)
        existing = c.execute(
            """SELECT created_at FROM integrations
               WHERE project_id = ? AND scope = ? AND integration_id = ? AND capability = ?""",
            (pid, rec.scope, rec.integration_id, rec.capability),
        ).fetchone()
        created = existing["created_at"] if existing and existing["created_at"] else _now()
        c.execute(
            """INSERT OR REPLACE INTO integrations
               (integration_id, capability, provider, scope, status, project_id,
                user_id, config_json, secret_ref, last_health, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rec.integration_id, rec.capability, rec.provider, rec.scope, rec.status,
             pid, uid, config_json, rec.secret_ref, rec.last_health, created, _now()),
        )
        c.commit()
    return rec


def resolve_integration(capability: str, project_id: str, *, conn=None) -> IntegrationRecord:
    """Best row for a capability: exact project scope first, then installation.

    Among candidates, status preference is connected > error > not_configured,
    then most recently updated. User-scope rows are never auto-resolved (the
    frozen signature carries no user identity). Raises ValueError on missing
    project_id, IntegrationNotFound when nothing matches.
    """
    project_id = _require_project_id(project_id)
    if not isinstance(capability, str) or not capability.strip():
        raise ValueError("capability is required (fail closed)")
    with _db(conn) as c:
        ensure_integrations(c)
        row = c.execute(
            """SELECT * FROM integrations
               WHERE capability = ?
                 AND (scope = 'installation'
                      OR (scope = 'project' AND project_id = ?))
               ORDER BY CASE WHEN scope = 'project' THEN 0 ELSE 1 END,
                        CASE status WHEN 'connected' THEN 0 WHEN 'error' THEN 1 ELSE 2 END,
                        updated_at DESC, integration_id
               LIMIT 1""",
            (capability.strip(), project_id),
        ).fetchone()
        if row is None:
            raise IntegrationNotFound(
                f"no integration for capability {capability!r} in project {project_id!r}"
            )
        return _row_to_record(row)


def resolve_provider(capability: str, project_id: str, *, conn=None) -> str:
    """Provider string for a capability in a project — fail closed, no env fallback."""
    return resolve_integration(capability, project_id, conn=conn).provider


def integration_status(project_id: str, *, conn=None) -> list[dict]:
    """Presence-only UI cards: labels + booleans + non-secret config.

    Lists installation rows, this project's rows, and user-scope rows
    (single-operator install). Never includes secret_ref or secret values.
    """
    project_id = _require_project_id(project_id)
    with _db(conn) as c:
        ensure_integrations(c)
        rows = c.execute(
            """SELECT * FROM integrations
               WHERE scope = 'installation' OR scope = 'user'
                  OR (scope = 'project' AND project_id = ?)
               ORDER BY capability, integration_id, scope""",
            (project_id,),
        ).fetchall()
        cards = []
        for row in rows:
            cards.append({
                "integration_id": row["integration_id"],
                "capability": row["capability"],
                "provider": row["provider"],
                "scope": row["scope"],
                "status": row["status"],
                "configured": row["status"] == "connected",
                "has_secret": bool(row["secret_ref"]),
                "last_health": row["last_health"],
                "config": json.loads(row["config_json"] or "{}"),
            })
        return cards


def _row_to_record(row) -> IntegrationRecord:
    return IntegrationRecord(
        integration_id=row["integration_id"],
        capability=row["capability"],
        provider=row["provider"],
        scope=row["scope"],
        status=row["status"],
        config=json.loads(row["config_json"] or "{}"),
        secret_ref=row["secret_ref"],
        last_health=row["last_health"],
    )
