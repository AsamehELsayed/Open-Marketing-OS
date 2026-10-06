"""IndexingService: workspace Markdown → documents/chunks/FTS5 (+ vectors via store).

Allow-listed roots only; archives (tests/, execution/, maintenance/) and
`*simulation*` paths are never indexed (SIM fixtures must not become truth).
Secret-hit files are quarantined, never indexed. Hash-compare drives
new/changed/deleted handling; FTS rows are managed per-document by path.
"""
import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.database.identity import DEFAULT_PROJECT_ID

from app.services.rag import secrets as secret_scan
from app.services.rag.chroma_store import ChromaStore

HEADER_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

# Relative to repo root. Files (not dirs) may be listed to pin single docs.
DEFAULT_ROOTS = (
    "knowledge",
    ".agents/product-marketing.md",
    "strategy",
    "production/weekly-plan.md",
    "production/approval-queue.md",
    "production/active-campaigns.md",
    "production/measurement-queue.md",
    "production/learning-log.md",
    "production/system-health.md",
    "research",
    "company",
    "analytics",
)

EXCLUDED_DIRS = {"tests", "execution", "maintenance", ".git", "node_modules", "data", ".venv"}


def status_for(rel_path: str) -> str:
    p = rel_path.replace("\\", "/")
    if "founder-decisions" in p:
        return "FOUNDER-CONFIRMED"
    if "learning-log" in p:
        return "VERIFIED"
    if p.startswith("knowledge/") or "product-marketing" in p:
        return "VERIFIED"
    if p.startswith("research/"):
        return "VERIFIED"
    return "UNKNOWN"


def _chunk_markdown(text: str) -> list[tuple[str, str]]:
    """Split on H1–H3 headers → (header_path, body). Pre-header text kept as intro."""
    matches = list(HEADER_RE.finditer(text))
    if not matches:
        return [("", text.strip())]
    chunks: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        chunks.append(("", text[: matches[0].start()].strip()))
    stack: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        level, title = len(m.group(1)), m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        header = " / ".join(t for _, t in stack)
        if body:
            chunks.append((header, body))
    return chunks


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IndexingService:
    def __init__(self, conn, root: str | Path, vector_store=None, provider=None,
                 project_id: str = DEFAULT_PROJECT_ID):
        self.conn = conn
        self.root = Path(root)
        self.vector_store = vector_store
        self.provider = provider
        self.project_id = (project_id or "").strip()
        if not self.project_id:
            raise ValueError("project_id is required for workspace indexing")

    def _iter_files(self):
        for entry in DEFAULT_ROOTS:
            path = self.root / entry
            if path.is_file():
                yield path
            elif path.is_dir():
                files: list[Path] = []
                files.extend(path.rglob("*.md"))
                files.extend(path.rglob("*.yaml"))
                for f in sorted(set(files)):
                    parts = set(f.relative_to(self.root).parts)
                    if parts & EXCLUDED_DIRS:
                        continue
                    if "simulation" in f.name:
                        continue
                    yield f

    def build_or_update(self) -> dict:
        report = {"discovered": 0, "indexed": 0, "skipped": 0, "deleted": 0,
                  "quarantined": [], "failed": 0, "chunks": 0, "errors": []}
        seen: set[str] = set()
        for path in self._iter_files():
            rel = path.relative_to(self.root).as_posix()
            seen.add(rel)
            report["discovered"] += 1
            try:
                raw = path.read_bytes()
                digest = _sha(raw)
                row = self.conn.execute(
                    "SELECT * FROM documents WHERE project_id=? AND source_kind='workspace' AND source_ref=?",
                    (self.project_id, rel)).fetchone()
                if row is None:
                    legacy = self.conn.execute(
                        "SELECT * FROM documents WHERE path=? AND project_id=? AND source_kind='legacy_unknown'",
                        (rel, self.project_id)).fetchone()
                    if legacy:
                        row = legacy
                        self.conn.execute("UPDATE documents SET source_kind='workspace',source_ref=? WHERE id=?",
                                          (rel, legacy["id"]))
                logical = f"workspace-files/{self.project_id}/{rel}"
                by_path = self.conn.execute("SELECT * FROM documents WHERE path=?", (logical,)).fetchone()
                if by_path and (by_path["project_id"] != self.project_id or
                                by_path["source_kind"] != "workspace" or by_path["source_ref"] != rel):
                    raise ValueError("logical_path_owned")
                row = row or by_path
                if row and row["file_sha"] == digest:
                    report["skipped"] += 1
                    continue
                text = raw.decode("utf-8", errors="replace")
                if secret_scan.find_secrets(text):
                    if row:
                        self._remove_document(row["id"], row["path"])
                    report["quarantined"].append({"source_ref": rel, "code": "secret_detected"})
                    report["errors"].append({"source_ref": rel, "code": "secret_detected"})
                    continue
                self._index_document(rel, row["path"] if row else logical, text, digest, row)
                report["indexed"] += 1
            except Exception:
                owned = self.conn.execute(
                    "SELECT id,path FROM documents WHERE project_id=? AND source_kind='workspace' AND source_ref=?",
                    (self.project_id, rel)).fetchone()
                if owned:
                    self._remove_document(owned["id"], owned["path"])
                report["failed"] += 1
                report["errors"].append({"source_ref": rel, "code": "index_failed"})
        rows = self.conn.execute(
            "SELECT id,path,source_ref FROM documents WHERE project_id=? AND source_kind='workspace'",
            (self.project_id,)).fetchall()
        for row in rows:
            if row["source_ref"] not in seen:
                self._remove_document(row["id"], row["path"])
                report["deleted"] += 1
        report["quarantined"] = len(report["quarantined"])
        report["chunks"] = self.conn.execute("SELECT COUNT(*) FROM chunks WHERE project_id=?",
                                             (self.project_id,)).fetchone()[0]
        self.conn.commit()
        if isinstance(self.vector_store, ChromaStore):
            self.vector_store.reconcile(self.conn)
        return report

    def _index_document(self, rel: str, logical: str, text: str, digest: str, row) -> None:
        old_path = row["path"] if row else logical
        doc_id = row["id"] if row else uuid.uuid4().hex
        vector_inputs = []
        vector_failed = False
        self.conn.execute("SAVEPOINT workspace_document")
        try:
            if row:
                self.conn.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
                self.conn.execute("DELETE FROM chunks_fts WHERE path=?", (old_path,))
                self.conn.execute("UPDATE documents SET path=?,source_kind='workspace',source_ref=?,file_sha=?,status_tag=?,indexed_at=? WHERE id=?",
                                  (logical, rel, digest, status_for(rel), _now(), doc_id))
            else:
                self.conn.execute("INSERT INTO documents (id,project_id,path,source_kind,source_ref,file_sha,status_tag,indexed_at) VALUES (?,?,?,?,?,?,?,?)",
                                  (doc_id, self.project_id, logical, "workspace", rel, digest, status_for(rel), _now()))
            pairs = _chunk_markdown(text)
            for i, (header, body) in enumerate(pairs):
                chunk_id = f"c{i:03d}"
                self.conn.execute("INSERT INTO chunks (id,project_id,document_id,chunk_id,header,text,token_est) VALUES (?,?,?,?,?,?,?)",
                                  (uuid.uuid4().hex, self.project_id, doc_id, chunk_id, header, body, max(1, len(body)//4)))
                self.conn.execute("INSERT INTO chunks_fts (text,header,path) VALUES (?,?,?)", (body, header, logical))
                if self.vector_store is not None and self.provider is not None:
                    vector_inputs.append((f"{doc_id}:{chunk_id}", f"{header}\n{body}",
                                          {"path": logical, "document_id": doc_id, "source_ref": rel, "chunk_id": chunk_id,
                                           "project_id": self.project_id, "status_tag": status_for(rel), "file_sha": digest}))
            self.conn.execute("UPDATE documents SET expected_chunk_count=? WHERE id=?", (len(pairs), doc_id))
            self.conn.execute("RELEASE SAVEPOINT workspace_document")
            self.conn.commit()
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT workspace_document")
            self.conn.execute("RELEASE SAVEPOINT workspace_document")
            raise
        if isinstance(self.vector_store, ChromaStore):
            self.vector_store.sync_document(self.conn, logical, self.provider)
            return
        if row and self.vector_store is not None:
            try:
                self.vector_store.remove_by_path(old_path)
            except Exception:
                vector_failed = True
        vectors = []
        if self.vector_store is not None and self.provider is not None and not vector_failed:
            try:
                encoded = (self.provider.embed_documents([text for _, text, _ in vector_inputs])
                           if hasattr(self.provider, "embed_documents") else
                           [self.provider.embed(text) for _, text, _ in vector_inputs])
                if len(encoded) != len(vector_inputs):
                    raise RuntimeError("embedding_batch_incomplete")
                vectors = [(key, vector, metadata) for (key, _, metadata), vector in zip(vector_inputs, encoded)]
            except Exception:
                vector_failed = True
        if vectors and self.vector_store is not None and not vector_failed:
            try:
                self.vector_store.upsert(vectors)
            except Exception:
                vector_failed = True
        if vector_failed and self.vector_store is not None:
            try:
                self.vector_store._vector_error = True
                self.vector_store.offline_reason = "vector operation failed"
            except Exception:
                pass

    def _remove_document(self, doc_id: str, rel: str) -> None:
        self.conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
        self.conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        self.conn.execute("DELETE FROM chunks_fts WHERE path = ?", (rel,))
        if self.vector_store is not None:
            try:
                self.vector_store.remove_by_path(rel)
            except Exception:
                try:
                    self.vector_store._vector_error = True
                    self.vector_store.offline_reason = "vector operation failed"
                except Exception:
                    pass
