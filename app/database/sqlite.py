"""SQLite connection + schema init. Only module (with repos) that touches sqlite3."""
import sqlite3
from pathlib import Path

from app.database.identity import DEFAULT_PROJECT_ID

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
SCHEMA_VERSION = 14

# Columns added post-foundation (Gate 2 Required 1). Fresh DBs get them from
# DDL; DBs created by the older schema are upgraded in place below.
UPGRADE_COLUMNS = {
    "tasks": "updated_at",
    "approvals": "updated_at",
    "experiments": "updated_at",
}
UPGRADE_DEFAULT = "1970-01-01T00:00:00+00:00"

# v0.2 multi-project columns (plan §10). Fresh DBs get them from DDL;
# v0.1 DBs are upgraded in place.
V02_PROJECT_COLUMNS = {
    "conversations": "project_id",
    "campaigns": "project_id",
    "tasks": "project_id",
    "approvals": "project_id",
    "prospects": "project_id",
    "experiments": "project_id",
    "learnings": "project_id",
    "background_jobs": "project_id",
    "documents": "project_id",
    "chunks": "project_id",
}
V02_EXTRA_JOB_COLUMNS = {
    "background_jobs": "conversation_id",
}
#: Default written into a `project_id` column added by the v0.1 -> v0.2 upgrade.
#:
#: DEV-008-PUBLISH-GATE: this was the literal "njm", the initials of a real
#: business, which meant an *upgraded* database silently adopted that real
#: business's identity as its default. It now uses the same generic constant as
#: `schema.sql`, so DDL and migration cannot disagree.
#:
#: Existing rows are untouched: `ALTER TABLE ... ADD COLUMN` only supplies the
#: default for rows inserted afterwards, and any row that already carries a
#: legacy project id keeps it.
V02_PROJECT_DEFAULT = DEFAULT_PROJECT_ID


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, column in UPGRADE_COLUMNS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT '{UPGRADE_DEFAULT}'")
    conn.commit()


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    for table, column in {**V02_PROJECT_COLUMNS, **V02_EXTRA_JOB_COLUMNS}.items():
        try:
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            continue
        if not existing or column in existing:
            continue
        default = "" if column == "conversation_id" else V02_PROJECT_DEFAULT
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS projects(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, website TEXT NOT NULL DEFAULT '',
        goal TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
        settings_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS memories(
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL DEFAULT 'starter',
        kind TEXT NOT NULL DEFAULT 'learning', body_md TEXT NOT NULL DEFAULT '',
        confidence TEXT NOT NULL DEFAULT 'MEDIUM', source_ref TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id)")
    conn.commit()


# v0.2 hybrid-delegation columns (SOL R3). Additive only.
V03_JOB_COLUMNS = {
    "background_jobs": ("job_type", "brief_md", "created_at", "updated_at"),
}
V03_DEFAULT = ""


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    for table, columns in V03_JOB_COLUMNS.items():
        try:
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            continue
        for column in columns:
            if not existing or column in existing:
                continue
            default = UPGRADE_DEFAULT if column in ("created_at", "updated_at") else V03_DEFAULT
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
    conn.commit()


# v0.2.1 live-chat columns. Tables (turns, execution_events) come from DDL;
# only the messages.client_message_id column needs an in-place upgrade.
V04_MESSAGE_COLUMNS = {
    "messages": ("client_message_id",),
}


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    for table, columns in V04_MESSAGE_COLUMNS.items():
        try:
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            continue
        for column in columns:
            if not existing or column in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    conn.commit()


# v0.2.1 chat history (archive flag). Additive only.
V05_CONVERSATION_COLUMNS = {
    "conversations": ("archived_at",),
}


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    for table, columns in V05_CONVERSATION_COLUMNS.items():
        try:
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            continue
        for column in columns:
            if not existing or column in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    conn.commit()


# DEV-003 W1 (C1): social_accounts table. Additive only, backfill none, idempotent.
def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS social_accounts(
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL DEFAULT 'starter',
        platform TEXT NOT NULL,
        handle TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'LIKELY',
        source TEXT NOT NULL DEFAULT 'manual',
        evidence_url TEXT NOT NULL DEFAULT '',
        observed_at TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (project_id, platform))"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_social_project ON social_accounts(project_id)")
    conn.commit()


# DEV-005 W5: model_calls telemetry table. Additive only, backfill none, idempotent.
MODEL_CALLS_DDL = """CREATE TABLE IF NOT EXISTS model_calls(
call_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL DEFAULT '',
    project_id TEXT NOT NULL DEFAULT 'starter',
provider TEXT NOT NULL DEFAULT '',
model TEXT NOT NULL DEFAULT '',
adapter TEXT NOT NULL DEFAULT '',
quantization TEXT NOT NULL DEFAULT '',
route_mode TEXT NOT NULL DEFAULT 'AUTO',
route_reason TEXT NOT NULL DEFAULT '',
input_tokens INTEGER,
cached_tokens INTEGER,
output_tokens INTEGER,
reasoning_tokens INTEGER,
total_tokens INTEGER,
latency_ms INTEGER NOT NULL DEFAULT 0,
estimated_cost_usd REAL,
pricing_version TEXT NOT NULL DEFAULT '',
cost_note TEXT NOT NULL DEFAULT '',
started_at TEXT NOT NULL DEFAULT '',
ended_at TEXT NOT NULL DEFAULT '')"""
MODEL_CALLS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_model_calls_turn ON model_calls(turn_id)",
    "CREATE INDEX IF NOT EXISTS idx_model_calls_project ON model_calls(project_id)",
)


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    conn.execute(MODEL_CALLS_DDL)
    for stmt in MODEL_CALLS_INDEXES:
        conn.execute(stmt)
    conn.commit()


# DEV-007R-HOTFIX-3: additive model_calls columns. Idempotent, no backfill.
def _migrate_model_calls_hotfix3(conn: sqlite3.Connection) -> None:
    """Add the hotfix-3 columns if they are missing.

    DEV-008 hardening. The original version read `PRAGMA table_info` once and
    then issued two ALTERs based on that single snapshot. Under two connections
    reaching the same database — which happens for real when a user opens OMOS
    twice, and in the test suite when two tests share a database file — both can
    observe the pre-migration schema and both can attempt the same ADD COLUMN,
    and the loser gets `sqlite3.OperationalError: duplicate column name`.

    Each column is now checked and added independently, and the ALTER is treated
    as idempotent: a duplicate-column error means another process won the race,
    which is the desired end state, not a failure. Genuine errors still raise.
    """
    for column, decl in (
        ("requested_model", "TEXT NOT NULL DEFAULT ''"),
        ("behavior_profile", "TEXT NOT NULL DEFAULT ''"),
    ):
        existing = {
            r[1]
            for r in conn.execute("PRAGMA table_info(model_calls)").fetchall()
        }
        if column in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE model_calls ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.commit()


# DEV-007: additive migrations for W2, W4, W5, W6
DEV007_DDL = """CREATE TABLE IF NOT EXISTS project_files (
  file_id        TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL,
  original_name  TEXT NOT NULL,
  safe_name      TEXT NOT NULL,
  mime_detected  TEXT NOT NULL,
  size           INTEGER NOT NULL,
  sha256         TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('document','image','data','other')),
  width          INTEGER,
  height         INTEGER,
  extraction     TEXT NOT NULL DEFAULT 'pending'
                   CHECK (extraction IN ('pending','ready','stripped','failed')),
  attach_scope   TEXT NOT NULL DEFAULT 'project'
                   CHECK (attach_scope IN ('turn','project')),
  indexed        INTEGER NOT NULL DEFAULT 0,
  rel_path       TEXT NOT NULL,
  created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_runs (
  tool_run_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  tool_id TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL DEFAULT '',
  completed_at TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '',
  latency_ms INTEGER NOT NULL DEFAULT 0,
  cost_note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS integrations (
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
  PRIMARY KEY (project_id, scope, integration_id, capability)
);
CREATE TABLE IF NOT EXISTS credentials_refs (
  secret_ref TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  label TEXT NOT NULL,
  project_id TEXT NOT NULL DEFAULT '',
  user_id TEXT NOT NULL DEFAULT '',
  backend TEXT NOT NULL DEFAULT 'dpapi_file',
  backend_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT '',
  revoked_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mcp_servers (
  server_id TEXT PRIMARY KEY,
  endpoint TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT '',
  secret_ref TEXT NOT NULL DEFAULT '',
  connected INTEGER NOT NULL DEFAULT 0,
  last_health TEXT NOT NULL DEFAULT 'unknown',
  last_checked_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mcp_tools_allowlist (
  server_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  granted_by TEXT NOT NULL DEFAULT '',
  granted_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (server_id, project_id, tool_name),
  FOREIGN KEY (server_id) REFERENCES mcp_servers(server_id),
  FOREIGN KEY (project_id) REFERENCES projects(id)
);
"""
DEV007_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_project_files_project ON project_files(project_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_project_files_sha ON project_files(project_id, sha256)",
    "CREATE INDEX IF NOT EXISTS idx_tool_runs_project ON tool_runs(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_tool_runs_tool ON tool_runs(tool_id, started_at)",
    "CREATE INDEX IF NOT EXISTS idx_integrations_project ON integrations(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_integrations_capability ON integrations(capability, project_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_credentials_refs_active ON credentials_refs(scope, label, project_id, user_id) WHERE status = 'active'",
    "CREATE INDEX IF NOT EXISTS idx_credentials_refs_scope ON credentials_refs(scope, label)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_servers_scope ON mcp_servers(scope)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_allowlist_project ON mcp_tools_allowlist(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_allowlist_server ON mcp_tools_allowlist(server_id)",
)


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    conn.executescript(DEV007_DDL)
    for stmt in DEV007_INDEXES:
        conn.execute(stmt)
    conn.commit()


def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    """DEV-010 workflow provenance on persisted proposals; additive/idempotent."""
    for table in ("campaigns", "tasks", "experiments"):
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "workflow_json" not in existing:
            try:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN workflow_json TEXT NOT NULL DEFAULT '{{}}'"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
    conn.commit()


def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    """DEV-014 explicit document ownership and persisted file index state."""
    doc_columns = {r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
    for name, decl in (("source_kind", "TEXT NOT NULL DEFAULT 'legacy_unknown'"),
                       ("source_ref", "TEXT NOT NULL DEFAULT ''"),
                       ("expected_chunk_count", "INTEGER")):
        if name not in doc_columns:
            try:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {name} {decl}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
    file_columns = {r[1] for r in conn.execute("PRAGMA table_info(project_files)").fetchall()}
    for name, decl in (("index_status", "TEXT NOT NULL DEFAULT 'pending'"),
                       ("index_error", "TEXT NOT NULL DEFAULT ''")):
        if name not in file_columns:
            try:
                conn.execute(f"ALTER TABLE project_files ADD COLUMN {name} {decl}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
    # Historic counts remain NULL: only a validated reindex can establish the
    # expected set without trusting possibly damaged live chunk rows.
    # Only exact legacy upload identity is authoritative enough to backfill.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_files'").fetchone():
        conn.execute(
            "UPDATE documents SET source_kind='project_file', "
            "source_ref=substr(path, length('project-files/' || project_id || '/') + 1) "
            "WHERE source_kind='legacy_unknown' AND EXISTS ("
            " SELECT 1 FROM project_files pf WHERE pf.project_id=documents.project_id "
            " AND pf.attach_scope='project' "
            " AND documents.path='project-files/' || pf.project_id || '/' || pf.file_id)"
        )
        # Backfill only complete one-to-one chunk/FTS mappings. A surviving
        # arbitrary FTS row must not preserve an Indexed metadata claim.
        valid_upload = (
            "(SELECT COUNT(*) FROM documents owner WHERE owner.project_id=project_files.project_id "
            " AND owner.source_kind='project_file' AND owner.source_ref=project_files.file_id)=1 "
            "AND EXISTS (SELECT 1 FROM documents d WHERE d.project_id=project_files.project_id "
            " AND d.source_kind='project_file' AND d.source_ref=project_files.file_id "
            " AND EXISTS (SELECT 1 FROM chunks c WHERE c.document_id=d.id) "
            " AND d.expected_chunk_count>0 "
            " AND d.expected_chunk_count=(SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) "
            " AND (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id AND c.project_id=d.project_id)="
            "     (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) "
            " AND (SELECT COUNT(*) FROM chunks_fts f WHERE f.path=d.path)="
            "     (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) "
            " AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.document_id=d.id AND "
            "     (SELECT COUNT(*) FROM chunks_fts f WHERE f.path=d.path AND f.header=c.header AND f.text=c.text)<>1) "
            " AND NOT EXISTS (SELECT 1 FROM chunks_fts f WHERE f.path=d.path AND NOT EXISTS "
            "     (SELECT 1 FROM chunks c WHERE c.document_id=d.id AND c.header=f.header AND c.text=f.text)))"
        )
        conn.execute(
            f"UPDATE project_files SET indexed=CASE WHEN {valid_upload} THEN 1 ELSE 0 END, "
            f"index_status=CASE WHEN {valid_upload} THEN 'indexed' "
            "WHEN index_status IN ('failed','quarantined','not_searchable') THEN index_status ELSE 'pending' END, "
            f"index_error=CASE WHEN {valid_upload} THEN '' "
            "WHEN index_status IN ('failed','quarantined','not_searchable') THEN index_error ELSE '' END "
            "WHERE attach_scope='project'"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_owner ON documents(project_id, source_kind, source_ref)")
    conn.commit()


def _migrate_v10_to_v11(conn: sqlite3.Connection) -> None:
    """DEV-015 conversation-bound attachment persistence (additive/idempotent)."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS turn_file_bindings (
      file_id TEXT PRIMARY KEY REFERENCES project_files(file_id) ON DELETE CASCADE,
      project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_turn_file_binding_conversation
      ON turn_file_bindings(project_id, conversation_id);
    CREATE TABLE IF NOT EXISTS turn_attachment_selections (
      turn_id TEXT NOT NULL, file_id TEXT NOT NULL, project_id TEXT NOT NULL,
      conversation_id TEXT NOT NULL, PRIMARY KEY(turn_id,file_id)
    );
    """)
    conn.commit()


def _migrate_v11_to_v12(conn: sqlite3.Connection) -> None:
    """DEV-030 conversation model preferences and message-turn linkage."""
    additions = {
        "conversations": {
            "model_provider": "TEXT NOT NULL DEFAULT 'AUTO'",
            "model_id": "TEXT NOT NULL DEFAULT ''",
        },
        "turns": {
            "model_provider": "TEXT NOT NULL DEFAULT 'AUTO'",
            "model_id": "TEXT NOT NULL DEFAULT ''",
        },
        "messages": {"turn_id": "TEXT NOT NULL DEFAULT ''"},
    }
    for table, columns in additions.items():
        existing = {row[1] for row in conn.execute(
            f"PRAGMA table_info({table})").fetchall()}
        for column, declaration in columns.items():
            if column not in existing:
                try:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
    conn.commit()


def _migrate_v12_to_v13(conn: sqlite3.Connection) -> None:
    """DEV-031 project-keyed client profile and editable brief."""
    conn.execute("""CREATE TABLE IF NOT EXISTS business_profiles (
        project_id TEXT PRIMARY KEY NOT NULL, business_name TEXT NOT NULL DEFAULT '',
        website TEXT NOT NULL DEFAULT '', industry TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '', audience TEXT NOT NULL DEFAULT '',
        location TEXT NOT NULL DEFAULT '', offer TEXT NOT NULL DEFAULT '',
        differentiators TEXT NOT NULL DEFAULT '', profile_json TEXT NOT NULL DEFAULT '{}',
        brief_md TEXT NOT NULL DEFAULT '', sources_json TEXT NOT NULL DEFAULT '[]',
        generation_json TEXT NOT NULL DEFAULT '{}', profile_revision INTEGER NOT NULL DEFAULT 1,
        brief_revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    conn.commit()


def _migrate_v13_to_v14(conn: sqlite3.Connection) -> None:
    """DEV-032 deliverables; mirrors the canonical schema.sql DDL block."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS campaign_deliverable_batches (
      project_id TEXT NOT NULL,
      campaign_id TEXT NOT NULL,
      idempotency_key TEXT NOT NULL,
      deliverable_ids_json TEXT NOT NULL,
      provenance_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL,
      PRIMARY KEY (project_id, campaign_id, idempotency_key),
      FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS campaign_deliverables (
      id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL,
      campaign_id TEXT NOT NULL,
      type TEXT NOT NULL CHECK (type IN (
        'strategy_brief', 'social_post', 'ad_copy', 'creative_brief', 'content_calendar'
      )),
      title TEXT NOT NULL,
      platform TEXT,
      content_md TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'IN_REVIEW', 'APPROVED')),
      current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
      generation_idempotency_key TEXT,
      generation_ordinal INTEGER,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE (project_id, campaign_id, id),
      UNIQUE (project_id, campaign_id, generation_idempotency_key, generation_ordinal),
      FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_campaign_deliverables_scope
      ON campaign_deliverables(project_id, campaign_id, created_at, id);
    CREATE TABLE IF NOT EXISTS campaign_deliverable_revisions (
      revision_id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL,
      campaign_id TEXT NOT NULL,
      deliverable_id TEXT NOT NULL,
      version INTEGER NOT NULL CHECK (version >= 1),
      operation TEXT NOT NULL CHECK (operation IN ('CREATE', 'GENERATED', 'EDIT', 'STATUS_TRANSITION')),
      type TEXT NOT NULL CHECK (type IN (
        'strategy_brief', 'social_post', 'ad_copy', 'creative_brief', 'content_calendar'
      )),
      title TEXT NOT NULL,
      platform TEXT,
      content_md TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('DRAFT', 'IN_REVIEW', 'APPROVED')),
      provenance_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL,
      UNIQUE (project_id, campaign_id, deliverable_id, version),
      FOREIGN KEY (deliverable_id) REFERENCES campaign_deliverables(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_campaign_deliverable_revisions_scope
      ON campaign_deliverable_revisions(project_id, campaign_id, deliverable_id, version);
    """)
    conn.commit()


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    _ensure_columns(conn)
    _migrate_v1_to_v2(conn)
    _migrate_v2_to_v3(conn)
    _migrate_v3_to_v4(conn)
    _migrate_v4_to_v5(conn)
    _migrate_v5_to_v6(conn)
    _migrate_v6_to_v7(conn)
    _migrate_v7_to_v8(conn)
    _migrate_v8_to_v9(conn)
    _migrate_v9_to_v10(conn)
    _migrate_v10_to_v11(conn)
    _migrate_v11_to_v12(conn)
    _migrate_v12_to_v13(conn)
    _migrate_v13_to_v14(conn)
    _migrate_model_calls_hotfix3(conn)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION};")
    conn.commit()
    return conn


def get_user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version;").fetchone()[0]
