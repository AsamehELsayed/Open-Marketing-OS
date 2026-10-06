"""DEV-005 W2 remediation — LangChain-first RAG product path (TDD RED).

Proves real maintained LangChain components construct and compose:
Document, RecursiveCharacterTextSplitter, DeterministicFakeEmbedding,
InMemoryVectorStore retriever, BM25Retriever lexical, EnsembleRetriever
fusion (c=60 parity with legacy RRF_K), Chroma vector store, and
ContextualCompressionRetriever compression. Thin domain adapters enforce
project_id fail-closed BEFORE top-k; citations carry full provenance;
FTS_ONLY legacy remains as tested degraded fallback.

No model download. No network. Uses only offline-safe embeddings
(DeterministicFakeEmbedding) + local text.
"""
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_text_splitters")
pytest.importorskip("langchain_community")
pytest.importorskip("langchain_classic")
pytest.importorskip("langchain_chroma")
pytest.importorskip("rank_bm25")

from app.services.rag import langchain_pipeline as lp


def _docs():
    return [
        ("knowledge/brand.md", "Acme Test Company builds custom websites with live-link proof.", "starter"),
        ("knowledge/offers.md", "Offer guarantee with risk reversal for agencies.", "starter"),
        ("knowledge/canary.md", "zebra quantum xyzzy canary secret", "other-project"),
    ]


def test_lc_construction_real_components():
    docs = lp.to_langchain_documents(_docs(), project_id="starter", scope_filter=True)
    assert docs  # canary filtered before indexing
    assert all(d.metadata["project_id"] == "starter" for d in docs)

    splitter = lp.build_text_splitter(chunk_size=200, chunk_overlap=40)
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    assert isinstance(splitter, RecursiveCharacterTextSplitter)
    splits = splitter.split_documents(docs)
    assert splits

    emb = lp.build_fake_embeddings(size=64)
    from langchain_core.embeddings import DeterministicFakeEmbedding

    assert isinstance(emb, DeterministicFakeEmbedding)
    assert len(emb.embed_query("hello")) == 64

    lex = lp.build_lexical_retriever(splits, k=4)
    from langchain_community.retrievers import BM25Retriever

    assert isinstance(lex, BM25Retriever)

    dense = lp.build_memory_vector_retriever(splits, emb, k=4)
    from langchain_core.vectorstores import VectorStoreRetriever

    assert isinstance(dense, VectorStoreRetriever)

    hybrid = lp.build_hybrid_retriever(lex, dense, c=60)
    from langchain_classic.retrievers import EnsembleRetriever

    assert isinstance(hybrid, EnsembleRetriever)
    assert hybrid.c == 60  # parity with legacy fusion.RRF_K

    comp = lp.build_compression_retriever(dense, top_n=2)
    from langchain_classic.retrievers import ContextualCompressionRetriever

    assert isinstance(comp, ContextualCompressionRetriever)


def test_lc_scope_fail_closed_before_top_k():
    docs = lp.to_langchain_documents(_docs(), project_id="starter", scope_filter=True)
    with pytest.raises(ValueError):
        lp.retrieve_scoped_langchain("websites", project_id="")
    with pytest.raises(ValueError):
        lp.retrieve_scoped_langchain("websites", project_id=None, documents=docs)
    # Canary query must never surface other-project evidence.
    hits = lp.retrieve_scoped_langchain(
        "zebra quantum xyzzy", project_id="starter", documents=docs, k=4)
    assert all(h.metadata["project_id"] == "starter" for h in hits)
    assert not any("canary" in (h.page_content or "") for h in hits)
    # Scope is applied before retrieval: unscoped doc count never indexed.
    assert len(docs) == 2


def test_lc_fusion_deterministic_and_mixed():
    docs = lp.to_langchain_documents(_docs(), project_id="starter", scope_filter=True)
    once = lp.retrieve_scoped_langchain("websites agencies guarantee", project_id="starter",
                                        documents=docs, k=4)
    twice = lp.retrieve_scoped_langchain("websites agencies guarantee", project_id="starter",
                                         documents=docs, k=4)
    assert [d.page_content for d in once] == [d.page_content for d in twice]
    paths = {d.metadata.get("path") for d in once}
    assert "knowledge/brand.md" in paths or "knowledge/offers.md" in paths


def test_lc_citations_complete_and_scoped():
    from app.services.rag import citations

    docs = lp.to_langchain_documents(_docs(), project_id="starter", scope_filter=True)
    hits = lp.retrieve_scoped_langchain("custom websites", project_id="starter",
                                        documents=docs, k=4)
    assert hits
    rows = lp.langchain_docs_to_hits(hits, project_id="starter")
    cites = citations.build_citations(rows, project_id="starter")
    required = {"source_id", "project_id", "path", "chunk_id", "source_type",
                "content_hash", "status", "language"}
    for c in cites:
        assert required.issubset(c.keys())
        assert c["project_id"] == "starter"
    with pytest.raises(ValueError):
        citations.build_citations(
            [{**rows[0], "project_id": "other-project"}], project_id="starter")


def test_lc_fallback_fts_only_still_serves(tmp_path):
    from app.database.sqlite import connect
    from app.services.rag import scoped_retrieval
    from app.services.rag.chroma_store import ChromaStore
    from app.services.rag.embeddings import LocalHashEmbeddings
    from app.services.rag.indexing_service import IndexingService

    root = tmp_path / "ws"
    (root / "knowledge").mkdir(parents=True)
    (root / "knowledge" / "brand.md").write_text(
        "# Brand\n\nAcme Test Company builds custom websites with live-link proof.\n",
        encoding="utf-8")
    conn = connect(tmp_path / "t.db")
    IndexingService(conn, root).build_or_update()
    import os

    os.environ["CHROMA_ENABLED"] = "0"
    try:
        store = ChromaStore(tmp_path / "chroma", LocalHashEmbeddings())
        assert store.mode == "FTS_ONLY"
        res = scoped_retrieval.retrieve_scoped(
            conn, "websites", project_id="starter", store=store,
            provider=LocalHashEmbeddings())
        assert res["mode"] == "FTS_ONLY"
        assert res["hits"]
    finally:
        os.environ.pop("CHROMA_ENABLED", None)
        conn.close()


def test_lc_chroma_scoped_filter_before_top_k(tmp_path):
    store = lp.build_chroma_store(
        lp.to_langchain_documents(_docs(), project_id="starter", scope_filter=False),
        lp.build_fake_embeddings(size=32),
        collection_name="w2_remediation_probe",
        persist_directory=str(tmp_path / "lc_chroma"),
    )
    hits = store.similarity_search("websites", k=4, filter={"project_id": "starter"})
    assert all(h.metadata.get("project_id") == "starter" for h in hits)
    assert not any("canary" in (h.page_content or "") for h in hits)


def test_lc_loader_policy_parity(tmp_path):
    (tmp_path / "knowledge").mkdir(parents=True)
    (tmp_path / "knowledge" / "brand.md").write_text(
        "# Brand\n\nAcme Test Company builds custom websites.\n", encoding="utf-8")
    # DEV-008-PUBLISH-GATE: this canary used AWS's published example access key.
    # The point of the test is that the secret scanner quarantines the file, and
    # that works on the `api_key =` pattern alone -- it does not need a
    # credential-shaped value. A realistic key in an exported test would trip
    # every downstream secret scanner, including GitHub push protection, for no
    # additional coverage.
    (tmp_path / "knowledge" / "secret.md").write_text(
        "api_key = THIS-IS-NOT-A-REAL-CREDENTIAL sentinel leak canary",
        encoding="utf-8")
    docs = lp.load_workspace_langchain_documents(tmp_path, project_id="starter")
    paths = {d.metadata.get("path") for d in docs}
    assert "knowledge/brand.md" in paths
    assert "knowledge/secret.md" not in paths  # quarantine parity
    assert all(d.metadata.get("project_id") == "starter" for d in docs)
    # Real loader classes remain importable (integration surface proof).
    from langchain_community.document_loaders import DirectoryLoader, TextLoader

    assert DirectoryLoader is not None and TextLoader is not None
