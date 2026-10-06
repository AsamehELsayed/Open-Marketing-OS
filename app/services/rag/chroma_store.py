"""Versioned persistent Chroma. SQLite remains canonical; FTS works offline."""
from __future__ import annotations
import hashlib
import json
import math
import os
from collections import Counter
from app.database.identity import DEFAULT_PROJECT_ID

INDEX_SCHEMA = "2"


def lexical_index_ready(conn, project_id):
    """Check project chunk/FTS mapping and source-derived expected counts."""
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        field = "expected_chunk_count" if "expected_chunk_count" in columns else "NULL AS expected_chunk_count"
        docs = conn.execute(f"SELECT id,path,{field} FROM documents WHERE project_id=?", (project_id,)).fetchall()
        conn.execute("SELECT count(*) FROM chunks_fts").fetchone()
        for doc in docs:
            chunks = conn.execute("SELECT header,text FROM chunks WHERE document_id=?", (doc["id"],)).fetchall()
            if doc["expected_chunk_count"] is not None and len(chunks) != doc["expected_chunk_count"]:
                return False
            actual = conn.execute("SELECT header,text FROM chunks_fts WHERE path=?", (doc["path"],)).fetchall()
            if Counter((r["header"], r["text"]) for r in chunks) != Counter((r["header"], r["text"]) for r in actual):
                return False
        return True
    except Exception:
        return False


class ChromaStore:
    def __init__(self, persist_dir, provider, enabled=None, project_id=None):
        self.persist_dir = str(persist_dir)
        self.provider = provider
        self.project_id = project_id or DEFAULT_PROJECT_ID
        self.identity = {"embedding_model": str(provider.id), "embedding_version": str(provider.version),
            "embedding_dimension": int(provider.dim), "index_schema": INDEX_SCHEMA,
            "encoding_version": str(getattr(provider, "encoding_version", "query-passage-v1"))}
        self.fingerprint = hashlib.sha256(json.dumps(self.identity, sort_keys=True).encode()).hexdigest()[:20]
        project_key = hashlib.sha256(self.project_id.encode()).hexdigest()[:16]
        self.collection_name = f"omos_{project_key}_{self.fingerprint}"
        self.enabled = os.getenv("CHROMA_ENABLED", "1") != "0" if enabled is None else enabled
        self._client = self._collection = None
        self._vector_error = False
        self._coverage_complete = self._semantic_inference = False
        self.offline_reason = "vector_disabled" if not self.enabled else "vector_not_initialized"
        if self.enabled:
            self._try_init()

    @property
    def operational(self):
        return self._collection is not None and not self._vector_error

    @property
    def mode(self):
        model_ready = getattr(self.provider, "ready", True)
        if not model_ready:
            self.offline_reason = "embedding_model_not_cached"
        return "HYBRID" if (self.operational and getattr(self.provider, "semantic", False)
            and model_ready and self._semantic_inference and self._coverage_complete) else "FTS_ONLY"

    def _failed(self, reason):
        self._vector_error = True
        self._coverage_complete = False
        self.offline_reason = reason

    def _try_init(self):
        try:
            import chromadb
            from chromadb.config import Settings
            os.makedirs(self.persist_dir, exist_ok=True)
            self._client = chromadb.PersistentClient(path=self.persist_dir, settings=Settings(anonymized_telemetry=False))
            self._collection = self._client.get_or_create_collection(name=self.collection_name,
                metadata={**self.identity, "semantic_inference": False}, embedding_function=None)
            meta = self._collection.metadata or {}
            if any(meta.get(key) != value for key, value in self.identity.items()):
                self._collection = None
                self._failed("vector_identity_mismatch")
                return
            self._semantic_inference = meta.get("semantic_inference") is True
            self.offline_reason = "vector_coverage_not_checked"
        except ImportError:
            self.offline_reason = "chromadb_runtime_missing"
        except Exception as exc:
            self._collection = None
            self.offline_reason = f"chroma_init_failed:{type(exc).__name__}"

    def upsert(self, items):
        """Return a verified acknowledgment; no swallowed successful writes."""
        if not self.operational:
            return False
        if not items:
            return True
        try:
            for key, vector, meta in items:
                if len(vector) != self.provider.dim or not all(math.isfinite(value) for value in vector):
                    raise ValueError("vector_dimension_or_value_mismatch")
                if meta.get("project_id") != self.project_id:
                    raise ValueError("vector_project_mismatch")
                if key != f"{meta.get('document_id')}:{meta.get('chunk_id')}":
                    raise ValueError("vector_canonical_id_mismatch")
            self._collection.upsert(ids=[key for key, _, _ in items], embeddings=[v for _, v, _ in items],
                metadatas=[{**meta, "index_fingerprint": self.fingerprint} for _, _, meta in items])
            actual = self._collection.get(ids=[key for key, _, _ in items], include=["metadatas"])
            if set(actual["ids"]) != {key for key, _, _ in items}:
                raise RuntimeError("vector_write_incomplete")
            self._coverage_complete = False
            return True
        except Exception as exc:
            self._failed(f"vector_write_failed:{type(exc).__name__}")
            return False

    def remove_by_path(self, path):
        if not self.operational:
            return False
        try:
            self._collection.delete(where={"$and": [{"path": path}, {"project_id": self.project_id}]})
            self._coverage_complete = False
            return True
        except Exception as exc:
            self._failed(f"vector_delete_failed:{type(exc).__name__}")
            return False

    def query(self, vector, k=6, where=None):
        if not self.operational:
            return []
        if not where or where.get("project_id") != self.project_id:
            raise ValueError("project_filter_required_before_top_k")
        try:
            if len(vector) != self.provider.dim:
                raise ValueError("query_dimension_mismatch")
            count = self._collection.count()
            if not count or k <= 0:
                return []
            result = self._collection.query(query_embeddings=[vector], n_results=min(k, count),
                where=where, include=["metadatas", "distances"])
            return [(key, meta or {}, float(distance)) for key, meta, distance in
                zip(result["ids"][0], result["metadatas"][0], result["distances"][0])]
        except Exception as exc:
            self._failed(f"vector_query_failed:{type(exc).__name__}")
            return []

    def _expected(self, conn):
        return conn.execute("SELECT d.id AS document_id,d.path,d.file_sha,d.status_tag,c.chunk_id,c.header,c.text"
            " FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.project_id=? ORDER BY d.id,c.chunk_id",
            (self.project_id,)).fetchall()

    def coverage(self, conn):
        """Validate exact IDs/current source hashes rather than vector count."""
        self._coverage_complete = False
        if not self.operational:
            return False
        try:
            expected = {f"{r['document_id']}:{r['chunk_id']}": r for r in self._expected(conn)}
            actual = self._collection.get(where={"project_id": self.project_id}, include=["metadatas"])
            if not expected:
                self.offline_reason = "no_indexed_knowledge"
                return False
            valid = set(actual["ids"]) == set(expected)
            for key, meta in zip(actual["ids"], actual["metadatas"]):
                row = expected.get(key)
                if row is None or not meta or any(meta.get(field) != row[field]
                    for field in ("document_id", "path", "chunk_id", "file_sha")):
                    valid = False
                if not meta or meta.get("index_fingerprint") != self.fingerprint:
                    valid = False
            self._coverage_complete = valid
            self.offline_reason = ("" if valid and self._semantic_inference else
                "semantic_inference_not_verified" if valid else "vector_coverage_incomplete")
            return valid
        except Exception as exc:
            self._failed(f"vector_coverage_failed:{type(exc).__name__}")
            return False

    def sync_document(self, conn, path, provider):
        """Backfill canonical text, retaining lexical success on vector errors."""
        if not self.operational:
            return False
        if not getattr(provider, "semantic", False):
            self.offline_reason = "non_semantic_provider"
            return False
        rows = [r for r in self._expected(conn) if r["path"] == path]
        if not rows:
            return self.remove_by_path(path)
        try:
            items = []
            for start in range(0, len(rows), 16):
                batch = rows[start:start + 16]
                vectors = provider.embed_documents([f"{r['header']}\n{r['text']}" for r in batch])
                if len(vectors) != len(batch):
                    raise RuntimeError("embedding_batch_incomplete")
                for row, vector in zip(batch, vectors):
                    meta = {field: row[field] for field in ("document_id", "path", "file_sha", "status_tag", "chunk_id")}
                    meta["project_id"] = self.project_id
                    items.append((f"{row['document_id']}:{row['chunk_id']}", vector, meta))
            if not self.remove_by_path(path) or not self.upsert(items):
                raise RuntimeError("vector_sync_unacknowledged")
            self._semantic_inference = True
            self._collection.modify(metadata={**self.identity, "semantic_inference": True})
            self.coverage(conn)
            return True
        except Exception as exc:
            self._failed(f"semantic_index_failed:{type(exc).__name__}")
            return False

    def reconcile(self, conn):
        """Delete scoped orphans and backfill this compatible version only."""
        if not self.operational:
            return False
        try:
            expected = {f"{r['document_id']}:{r['chunk_id']}" for r in self._expected(conn)}
            actual = self._collection.get(where={"project_id": self.project_id}, include=["metadatas"])
            stale = [key for key in actual["ids"] if key not in expected]
            if stale:
                self._collection.delete(ids=stale)
            if self.coverage(conn):
                return True
            for row in conn.execute("SELECT path FROM documents WHERE project_id=?", (self.project_id,)):
                if not self.sync_document(conn, row["path"], self.provider):
                    return False
            return self.coverage(conn)
        except Exception as exc:
            self._failed(f"vector_rebuild_failed:{type(exc).__name__}")
            return False
