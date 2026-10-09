"""W2 Account Manager adapter tests against the frozen scoped retrieval API."""
import sys
import types
import sqlite3

from app.graphs.adapters import RetrievalAdapter


def test_adapter_constructs_selected_project_runtime_and_calls_scoped_path(monkeypatch):
    runtime_calls = []
    scoped_calls = []
    service = types.ModuleType("app.services.knowledge_index")
    service.retrieval_runtime = lambda root, pid: (
        runtime_calls.append((root, pid)) or ("project-store", "project-provider"))
    monkeypatch.setitem(sys.modules, "app.services.knowledge_index", service)

    from app.services.rag import scoped_retrieval
    from app.services.rag import rag_service

    def retrieve_scoped(conn, query, **kwargs):
        scoped_calls.append(kwargs)
        return {"mode": "FTS_ONLY", "hits": [{
            "path": "project-files/a/file", "chunk_id": "c1",
            "text": "selected-project phrase", "project_id": "audit014-a",
            "source": "fts", "rank": 0,
        }]}

    monkeypatch.setattr(scoped_retrieval, "retrieve_scoped", retrieve_scoped)
    monkeypatch.setattr(rag_service, "retrieve", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("legacy global-top-k retriever was called")))
    result = RetrievalAdapter(object(), "audit014-a").retrieve("selected-project phrase")
    assert result.project_id == "audit014-a"
    assert result.hit_count() == 1
    assert runtime_calls and runtime_calls[0][1] == "audit014-a"
    assert scoped_calls[0]["project_id"] == "audit014-a"
    assert scoped_calls[0]["store"] == "project-store"


def test_adapter_rejects_blank_scope_before_runtime_or_retrieval():
    try:
        RetrievalAdapter(object(), " ")
    except ValueError as exc:
        assert "project_id" in str(exc)
    else:
        raise AssertionError("blank project scope should fail closed")


def _retrieval_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE documents (id TEXT PRIMARY KEY, path TEXT UNIQUE, project_id TEXT,
            file_sha TEXT, status_tag TEXT,
            source_kind TEXT NOT NULL DEFAULT 'legacy_unknown',
            source_ref TEXT NOT NULL DEFAULT '');
        CREATE TABLE chunks (document_id TEXT, chunk_id TEXT, header TEXT, text TEXT);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(path, header, text);
    """)
    # More than top-k foreign-project distractors contain the same phrase.
    for n in range(12):
        pid, path = "audit014-b", f"b/{n}.txt"
        conn.execute("INSERT INTO documents(id,path,project_id,file_sha,status_tag) VALUES(?,?,?,?,?)",
                     (f"b{n}", path, pid, "bsha", "BODY"))
        conn.execute("INSERT INTO chunks VALUES(?,?,?,?)", (f"b{n}", f"b{n}", "body", "ORANGE ELEPHANT distractor"))
        conn.execute("INSERT INTO chunks_fts VALUES(?,?,?)", (path, "body", "ORANGE ELEPHANT distractor"))
    path = "a/target.txt"
    conn.execute("INSERT INTO documents(id,path,project_id,file_sha,status_tag) VALUES(?,?,?,?,?)",
                 ("a1", path, "audit014-a", "asha", "BODY"))
    conn.execute("INSERT INTO chunks VALUES(?,?,?,?)", ("a1", "a1", "body", "ORANGE ELEPHANT selected project"))
    conn.execute("INSERT INTO chunks_fts VALUES(?,?,?)", (path, "body", "ORANGE ELEPHANT selected project"))
    conn.commit()
    return conn


def test_account_manager_adapter_finds_a_after_more_than_topk_b_distractors():
    result = RetrievalAdapter(_retrieval_db(), "audit014-a").retrieve("ORANGE ELEPHANT")
    assert result.hit_count() == 1
    assert result.hits[0].project_id == "audit014-a"
    assert result.hits[0].path == "a/target.txt"


def test_dense_store_without_scope_filter_fails_closed():
    conn = _retrieval_db()
    conn.execute("DELETE FROM documents WHERE project_id='audit014-a'")
    conn.execute("DELETE FROM chunks_fts WHERE path='a/target.txt'")
    conn.execute("DELETE FROM chunks WHERE document_id='a1'")
    conn.commit()

    class DenseStore:
        mode = "HYBRID"
        def __init__(self):
            self.called = False
        def query(self, vector, k):
            self.called = True
            return [("b0", {"project_id": "audit014-b", "path": "b/0.txt", "chunk_id": "b0"}, 0.0)]

    class Provider:
        def embed(self, query):
            return [0.1, 0.2]

    store = DenseStore()
    result = RetrievalAdapter(conn, "audit014-a", store=store, provider=Provider()).retrieve("ORANGE ELEPHANT")
    assert result.hit_count() == 0
    assert store.called is False  # query(where=...) raised TypeError before the unsafe signature ran

