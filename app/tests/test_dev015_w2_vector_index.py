"""Real Chroma plumbing with SYNTHETIC vectors; no semantic quality proof."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.database.sqlite import connect
from app.services.files.chunk_index import index_file_text
from app.services.knowledge_index import retrieval_status_for_store
from app.services.rag.chroma_store import ChromaStore
from app.services.rag.embeddings import LocalHashEmbeddings
from app.services.rag.scoped_retrieval import retrieve_scoped
from app.services.rag.indexing_service import IndexingService


class SyntheticProvider:
    """Mock provider for protocol testing, NOT a semantic model."""
    id = "synthetic-protocol-fixture"
    version = "fixture-1"
    dim = 3
    semantic = True  # Exercises semantic-capability wiring only.
    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]
    def embed_query(self, text):
        return [1.0, 0.0, 0.0]
    def embed(self, text):
        raise AssertionError("Generic embed must not be called")


def database(path):
    conn = connect(path)
    for pid in ("a", "b"):
        conn.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES(?,?,?,?)", (pid, pid, "now", "now"))
    conn.commit()
    return conn


def seed(conn, store=None, provider=None, text="Campaign ID MK-92741-Z. NEBULA-MARKETING-742"):
    return index_file_text(conn, file_id="synthetic-file", project_id="a", original_name="notes.txt",
        sha256="synthetic-source-sha", text=text, mime="text/plain", vector_store=store, provider=provider)


def store_or_skip(tmp_path, provider=None):
    pytest.importorskip("chromadb")
    store = ChromaStore(tmp_path / "chroma", provider or SyntheticProvider(), project_id="a")
    assert store.operational, store.offline_reason
    return store


def test_real_chroma_write_filter_restart_and_branch_counts(tmp_path):
    conn = database(tmp_path / "state.db")
    provider = SyntheticProvider()
    store = store_or_skip(tmp_path, provider)
    assert store.mode == "FTS_ONLY"  # Collection presence alone is insufficient.
    seed(conn, store, provider)
    assert store.coverage(conn) and store.mode == "HYBRID"
    store._collection.upsert(ids=["foreign:c000"], embeddings=[[1.0, 0.0, 0.0]],
        metadatas=[{"project_id": "b", "path": "foreign/b", "document_id": "foreign", "chunk_id": "c000", "file_sha": "foreign"}])
    assert all(meta["project_id"] == "a" for _, meta, _ in store.query(provider.embed_query("x"), 1, where={"project_id": "a"}))
    with pytest.raises(ValueError):
        store.query(provider.embed_query("x"), 1)
    result = retrieve_scoped(conn, "MK-92741-Z", project_id="a", store=store, provider=provider)
    assert result["mode"] == "HYBRID"
    assert result["lexical_hits"] == result["vector_hits"] == result["fused_hits"] == 1
    assert result["hits"][0]["sources"] == ["fts", "semantic"]
    assert result["hits"][0]["file_id"] == "synthetic-file"
    assert not retrieve_scoped(conn, "NEBULA-MARKETING-742", project_id="b")["hits"]
    command = [sys.executable, "-c", (
        "import json; from app.database.sqlite import connect; "
        "from app.services.rag.chroma_store import ChromaStore; "
        "from app.tests.test_dev015_w2_vector_index import SyntheticProvider; "
        f"s=ChromaStore({str(tmp_path / 'chroma')!r},SyntheticProvider(),project_id='a'); "
        f"c=connect({str(tmp_path / 'state.db')!r}); "
        "print(json.dumps({'coverage':s.coverage(c),'count':s._collection.count(),"
        "'filtered':len(s.query([1,0,0],1,where={'project_id':'a'}))}))")]
    restarted = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=60)
    assert restarted.returncode == 0, restarted.stderr
    proof = json.loads(restarted.stdout.strip().splitlines()[-1])
    assert proof == {"coverage": True, "count": 2, "filtered": 1}


def test_nonsemantic_hash_never_enables_hybrid(tmp_path):
    conn = database(tmp_path / "state.db")
    provider = LocalHashEmbeddings()
    store = store_or_skip(tmp_path, provider)
    seed(conn, store, provider)
    assert store.mode == "FTS_ONLY"
    result = retrieve_scoped(conn, "MK-92741-Z", project_id="a", store=store, provider=provider)
    assert result["lexical_hits"] == 1 and result["vector_hits"] == 0
    assert result["mode"] == "FTS_ONLY"


def test_model_failure_retains_fts_and_no_false_mode(tmp_path):
    class MissingModel(SyntheticProvider):
        def embed_documents(self, texts):
            raise RuntimeError("synthetic_model_unavailable")
    conn = database(tmp_path / "state.db")
    provider = MissingModel()
    store = store_or_skip(tmp_path, provider)
    result = seed(conn, store, provider)
    assert result["indexed"] and result["vector_failed"]
    retrieved = retrieve_scoped(conn, "MK-92741-Z", project_id="a", store=store, provider=provider)
    assert retrieved["lexical_hits"] == 1 and not retrieved["vector_hits"]
    assert retrieval_status_for_store(store, conn, "a")["search_mode"] == "FTS"


def test_versioned_collection_retains_prior_vectors(tmp_path):
    conn = database(tmp_path / "state.db")
    first = store_or_skip(tmp_path)
    seed(conn, first, first.provider)
    provider = SyntheticProvider()
    provider.version = "fixture-2"
    second = ChromaStore(tmp_path / "chroma", provider, project_id="a")
    assert first.collection_name != second.collection_name
    assert second._collection.count() == 0 and second.mode == "FTS_ONLY"
    assert second._client.get_collection(first.collection_name).count() == 1


def test_stale_source_hash_incomplete_vectors_dimension_and_lexical_failure(tmp_path):
    conn = database(tmp_path / "state.db")
    store = store_or_skip(tmp_path)
    seed(conn, store, store.provider)
    conn.execute("UPDATE documents SET file_sha='changed' WHERE project_id='a'")
    conn.commit()
    assert not store.coverage(conn)
    result = retrieve_scoped(conn, "MK-92741-Z", project_id="a", store=store, provider=store.provider)
    assert result["mode"] == "FTS_ONLY" and result["vector_hits"] == 0
    assert store.sync_document(conn, "project-files/a/synthetic-file", store.provider)
    conn.execute("DROP TABLE chunks_fts")
    result = retrieve_scoped(conn, "anything", project_id="a", store=store, provider=store.provider)
    assert result["mode"] == "FTS_ONLY" and result["search_mode"] == "VECTOR DEGRADED"
    assert result["vector_hits"] == 1 and "lexical" in result["branch_errors"]
    doc = conn.execute("SELECT id FROM documents WHERE project_id='a'").fetchone()[0]
    assert not store.upsert([(f"{doc}:c000", [1.0], {"document_id": doc, "chunk_id": "c000", "project_id": "a"})])
    assert store._vector_error


def test_refresh_repairs_vector_noop_and_removes_replaced_deleted_chunks(tmp_path, monkeypatch):
    from app.services.rag import indexing_service
    monkeypatch.setattr(indexing_service, "DEFAULT_ROOTS", ("knowledge",))
    root = tmp_path / "root"
    (root / "knowledge").mkdir(parents=True)
    file = root / "knowledge" / "fixture.md"
    file.write_text("# One\nCampaign MK-92741-Z\n# Two\nCheckout conversion detail", encoding="utf-8")
    conn = database(tmp_path / "state.db")
    store = store_or_skip(tmp_path)
    service = IndexingService(conn, root, vector_store=store, provider=store.provider, project_id="a")
    assert service.build_or_update()["indexed"] == 1
    assert store._collection.count() == 2
    lost = store._collection.get()["ids"][0]
    store._collection.delete(ids=[lost])
    assert not store.coverage(conn)
    assert service.build_or_update()["skipped"] == 1
    assert store.coverage(conn) and store._collection.count() == 2
    file.write_text("# One\nReplacement", encoding="utf-8")
    service.build_or_update()
    assert store._collection.count() == 1
    file.unlink()
    assert service.build_or_update()["deleted"] == 1
    assert store._collection.count() == 0
    assert not conn.execute("SELECT 1 FROM chunks_fts").fetchone()


def test_missing_chroma_fts_only_status_and_trace(tmp_path):
    conn = database(tmp_path / "state.db")
    provider = SyntheticProvider()
    store = ChromaStore(tmp_path / "disabled", provider, enabled=False, project_id="a")
    seed(conn, store, provider)
    result = retrieve_scoped(conn, "MK-92741-Z", project_id="a", store=store, provider=provider)
    assert result["mode"] == "FTS_ONLY" and result["lexical_hits"] == 1
    assert result["branch_errors"]["vector"] == "vector_disabled"


def test_missing_real_e5_cache_forces_fts_even_with_persisted_semantic_flag(tmp_path):
    from app.services.rag.embeddings import LocalMultilingualE5Embeddings

    provider = LocalMultilingualE5Embeddings(cache_dir=str(tmp_path / "missing-model-cache"))
    store = store_or_skip(tmp_path, provider)
    assert not provider.ready
    store._collection.modify(metadata={**store.identity, "semantic_inference": True})
    reopened = ChromaStore(tmp_path / "chroma", provider, project_id="a")
    assert reopened._semantic_inference is True
    assert reopened.mode == "FTS_ONLY"
    assert reopened.offline_reason == "embedding_model_not_cached"
