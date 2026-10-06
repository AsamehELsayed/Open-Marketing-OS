"""project_files metadata repository + idempotent schema (mirrors w2.sql).

Bytes never touch SQLite — only metadata. All reads are project-scoped
(fail-closed): blank/None project_id raises; queries always filter it.
"""
import sqlite3
from datetime import datetime, timezone

# Keep byte-identical with development/runs/DEV-007/migrations/w2.sql
# (drift-guarded by test_dev007_files.py::test_w2_sql_matches_repo).
W2_SQL = """
CREATE TABLE IF NOT EXISTS project_files (
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

CREATE INDEX IF NOT EXISTS idx_project_files_project
  ON project_files(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_project_files_sha
  ON project_files(project_id, sha256);
"""

FILE_COLUMNS = (
    "file_id, project_id, original_name, safe_name, mime_detected, size,"
    " sha256, kind, width, height, extraction, attach_scope, indexed,"
    " index_status, index_error, rel_path, created_at"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(W2_SQL)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(project_files)").fetchall()}
    for name, decl in (("index_status", "TEXT NOT NULL DEFAULT 'pending'"),
                       ("index_error", "TEXT NOT NULL DEFAULT ''")):
        if name not in existing:
            conn.execute(f"ALTER TABLE project_files ADD COLUMN {name} {decl}")
    conn.execute("""CREATE TABLE IF NOT EXISTS turn_file_bindings (
        file_id TEXT PRIMARY KEY REFERENCES project_files(file_id) ON DELETE CASCADE,
        project_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
        created_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_turn_file_binding_conversation "
                 "ON turn_file_bindings(project_id, conversation_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS turn_attachment_selections (
        turn_id TEXT NOT NULL, file_id TEXT NOT NULL, project_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL, PRIMARY KEY(turn_id,file_id))""")
    conn.commit()


def require_project_id(project_id: str | None) -> str:
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required (fail-closed)")
    return pid


def insert_file(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        f"INSERT INTO project_files ({FILE_COLUMNS}) VALUES"
        " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            row["file_id"], row["project_id"], row["original_name"],
            row["safe_name"], row["mime_detected"], row["size"],
            row["sha256"], row["kind"], row.get("width"),
            row.get("height"), row.get("extraction", "pending"),
            row.get("attach_scope", "project"), int(bool(row.get("indexed"))),
            row.get("index_status", "pending"), row.get("index_error", ""),
            row["rel_path"], row.get("created_at") or now_iso(),
        ),
    )
    conn.commit()


def bind_turn_file(conn: sqlite3.Connection, file_id: str, project_id: str,
                   conversation_id: str) -> None:
    conn.execute("INSERT INTO turn_file_bindings(file_id,project_id,conversation_id,created_at) VALUES(?,?,?,?)",
                 (file_id, project_id, conversation_id, now_iso()))
    conn.commit()


def list_turn_files(conn: sqlite3.Connection, file_ids: list[str], *,
                    project_id: str, conversation_id: str) -> list[dict]:
    if not file_ids:
        return []
    placeholders = ",".join("?" for _ in file_ids)
    rows = conn.execute(
        f"SELECT f.* FROM project_files f LEFT JOIN turn_file_bindings b ON b.file_id=f.file_id "
        f"WHERE f.file_id IN ({placeholders}) AND f.project_id=? AND "
        "(f.attach_scope='project' OR (f.attach_scope='turn' AND b.project_id=? AND b.conversation_id=?)) "
        "ORDER BY f.created_at,f.file_id",
        (*file_ids, project_id, project_id, conversation_id)).fetchall()
    return [dict(r) for r in rows]


def save_turn_selection(conn, *, turn_id, file_ids, project_id, conversation_id):
    conn.executemany("INSERT OR IGNORE INTO turn_attachment_selections VALUES(?,?,?,?)",
                     [(turn_id, fid, project_id, conversation_id) for fid in file_ids])
    conn.commit()


def selected_for_turn(conn, *, turn_id, project_id, conversation_id):
    rows = conn.execute("SELECT file_id FROM turn_attachment_selections WHERE turn_id=? AND project_id=? AND conversation_id=? ORDER BY file_id",
                        (turn_id, project_id, conversation_id)).fetchall()
    return [r[0] for r in rows]


def update_extraction(
    conn: sqlite3.Connection, file_id: str, *, extraction: str,
    width: int | None = None, height: int | None = None,
    indexed: bool = False,
    index_status: str | None = None,
    index_error: str = "",
) -> None:
    conn.execute(
        "UPDATE project_files SET extraction = ?, width = ?, height = ?,"
        " indexed = ?, index_status = ?, index_error = ? WHERE file_id = ?",
        (extraction, width, height, int(bool(indexed)),
         index_status or ("indexed" if indexed else "pending"), index_error, file_id),
    )
    conn.commit()


def get_file(conn: sqlite3.Connection, file_id: str,
             project_id: str | None) -> dict | None:
    pid = require_project_id(project_id)
    row = conn.execute(
        f"SELECT {FILE_COLUMNS} FROM project_files"
        " WHERE file_id = ? AND project_id = ?",
        (file_id, pid),
    ).fetchone()
    return dict(row) if row else None


def list_files(conn: sqlite3.Connection, project_id: str | None) -> list[dict]:
    pid = require_project_id(project_id)
    rows = conn.execute(
        f"SELECT {FILE_COLUMNS} FROM project_files"
        " WHERE project_id = ? ORDER BY created_at DESC, file_id",
        (pid,),
    ).fetchall()
    return [dict(r) for r in rows]
