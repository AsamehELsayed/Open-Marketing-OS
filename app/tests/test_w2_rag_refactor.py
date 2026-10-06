"""DEV-005 W2 — RAG refactor acceptance (TDD RED first).

Scope: app/services/rag/ + RAG eval fixtures only.
No graph, FastAPI, React, DB schema, requirements, lockfile, router, cutover.
"""
import json
from pathlib import Path

import pytest

from app.database.sqlite import connect
from app.services.rag import rag_service
from app.services.rag.embeddings import LocalHashEmbeddings
from app.services.rag.indexing_service import IndexingService


def _corpus(root: Path):
    files = {
        "knowledge/brand.md": "# Brand\n\nAcme Test Company builds custom websites with live-link proof.\n",
        "knowledge/offers.md": "# Offers\n\nOffer guarantee with risk reversal for agencies.\n",
        "knowledge/icp.md": "# ICP\n\nIdeal customer profile pains for agencies seeking white-label.\n",
        "knowledge/brand-ar.md": "# العلامة\n\nنبني مواقع مخصصة مع إثبات رابط حي للوكالات.\n",
        "knowledge/mixed.md": "# Mixed\n\nالوكالات agencies تحتاج white-label مواقع websites.\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _indexed(tmp_path):
    root = _corpus(tmp_path / "ws")
    conn = connect(tmp_path / "t.db")
    IndexingService(conn, root).build_or_update()
    return conn, root


def test_w2_scope_missing_fails_closed_before_store_query(tmp_path):
    from app.services.rag import scoped_retrieval

    conn, _ = _indexed(tmp_path)
    for bad in (None, "", "   "):
        with pytest.raises(ValueError):
            scoped_retrieval.retrieve_scoped(conn, "brand", project_id=bad)
    conn.close()


def test_w2_cross_project_canary_isolation(tmp_path):
    from app.services.rag import scoped_retrieval
    from app.contracts.retrieval import RetrievalResult

    conn, _ = _indexed(tmp_path)
    # Canary row belonging to another project must never surface.
    import uuid as _uuid

    doc_id = _uuid.uuid4().hex
    conn.execute(
        "INSERT INTO documents (id, project_id, path, file_sha, status_tag, indexed_at)"
        " VALUES (?,?,?,?,?,?)",
        (doc_id, "other-project", "knowledge/canary.md", "x" * 64, "VERIFIED", "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO chunks (id, document_id, chunk_id, header, text, token_est)"
        " VALUES (?,?,?,?,?,?)",
        (_uuid.uuid4().hex, doc_id, "c000", "Canary", "zebra quantum xyzzy canary", 5),
    )
    conn.execute("INSERT INTO chunks_fts (text, header, path) VALUES (?,?,?)",
                 ("zebra quantum xyzzy canary", "Canary", "knowledge/canary.md"))
    conn.commit()
    res = scoped_retrieval.retrieve_scoped(conn, "zebra quantum xyzzy", project_id="starter")
    assert all(h["project_id"] == "starter" for h in res["hits"])
    assert not any(h["path"] == "knowledge/canary.md" for h in res["hits"])
    # Contract validator itself rejects cross-project hits.
    with pytest.raises(ValueError):
        RetrievalResult(query="q", project_id="starter", mode="FTS_ONLY",
                        hits=[{"path": "x", "chunk_id": "c000", "project_id": "other-project"}])
    conn.close()


def test_w2_fusion_deterministic(tmp_path):
    from app.services.rag import fusion

    conn, _ = _indexed(tmp_path)
    fts = rag_service._fts_query(conn, "agencies websites", 6, None)
    once = fusion.rrf_fuse([("fts", fts), ("semantic", list(reversed(fts)))])
    twice = fusion.rrf_fuse([("fts", fts), ("semantic", list(reversed(fts)))])
    assert [ (h["path"], h["chunk_id"]) for h in once] == [
        (h["path"], h["chunk_id"]) for h in twice]
    assert once == twice  # byte-identical ordering incl. tie-break
    conn.close()


def test_w2_fts_only_parity_with_legacy(tmp_path):
    """Scoped lexical path must equal legacy retrieve() hits for same scope."""
    from app.services.rag import scoped_retrieval

    conn, _ = _indexed(tmp_path)
    legacy = rag_service.retrieve(conn, "custom websites", project_id="starter")
    scoped = scoped_retrieval.retrieve_scoped(
        conn, "custom websites", project_id="starter", mode="lexical")
    assert scoped["mode"] == "FTS_ONLY"
    assert [(h["path"], h["chunk_id"]) for h in scoped["hits"]] == [
        (h["path"], h["chunk_id"]) for h in legacy["hits"]]
    conn.close()


def test_w2_fallback_serves_fts_only(tmp_path, monkeypatch):
    from app.services.rag import scoped_retrieval
    from app.services.rag.chroma_store import ChromaStore

    monkeypatch.setenv("CHROMA_ENABLED", "0")
    conn, _ = _indexed(tmp_path)
    store = ChromaStore(tmp_path / "chroma", LocalHashEmbeddings())
    assert store.mode == "FTS_ONLY"
    res = scoped_retrieval.retrieve_scoped(
        conn, "websites", project_id="starter", store=store, provider=LocalHashEmbeddings())
    assert res["mode"] == "FTS_ONLY"
    assert res["hits"]
    conn.close()


def test_w2_citations_complete(tmp_path):
    from app.services.rag import citations, scoped_retrieval

    conn, _ = _indexed(tmp_path)
    res = scoped_retrieval.retrieve_scoped(conn, "custom websites", project_id="starter")
    cites = citations.build_citations(res["hits"], project_id="starter")
    assert cites
    required = {"source_id", "project_id", "path", "chunk_id", "source_type",
                "content_hash", "status", "language"}
    for c in cites:
        assert required.issubset(c.keys())
        assert c["project_id"] == "starter"
    conn.close()


def test_w2_eval_harness_ar_en_mixed_repeatable(tmp_path):
    from app.services.rag import eval as rag_eval

    conn, _ = _indexed(tmp_path)
    fixture = Path("app/tests/fixtures/w2_rag_ar_en_mixed.json")
    assert fixture.exists()
    cases = json.loads(fixture.read_text(encoding="utf-8"))["cases"]
    langs = {c["language"] for c in cases}
    assert {"ar", "en", "mixed"}.issubset(langs)

    def lexical_fn(q, k=5):
        return rag_service.retrieve(conn, q, project_id="starter")["hits"][:k]

    m1 = rag_eval.evaluate(cases, lexical_fn)
    m2 = rag_eval.evaluate(cases, lexical_fn)
    q1 = {k: v for k, v in m1.items() if not k.endswith("_ms")}
    q2 = {k: v for k, v in m2.items() if not k.endswith("_ms")}
    assert q1 == q2  # quality metrics deterministic; latency measured
    for key in ("recall@5", "mrr", "ndcg@5", "no_answer_honesty",
                "citation_correctness", "leakage", "p50_ms", "p95_ms"):
        assert key in m1
    assert m1["p50_ms"] >= 0 and m1["p95_ms"] >= m1["p50_ms"]
    assert m1["leakage"] == 0
    conn.close()


def test_w2_benchmark_hooks_compare_three_legs_without_download(tmp_path):
    from app.services.rag import benchmark

    conn, _ = _indexed(tmp_path)
    out = benchmark.compare(
        conn, "custom websites", project_id="starter", provider=LocalHashEmbeddings())
    assert set(out.keys()) == {"lexical", "dense", "hybrid"}
    for leg in out.values():
        assert "hits" in leg and "latency_ms" in leg
    conn.close()


def test_w2_bge_m3_candidate_only_no_download():
    from app.services.rag import bge_m3

    cand = bge_m3.BGEM3Candidate()
    assert cand.is_available() is False
    with pytest.raises(RuntimeError):
        cand.embed("hello")


def test_w2_interfaces_framework_compatible():
    from app.services.rag import interfaces

    assert hasattr(interfaces, "Document")
    assert hasattr(interfaces, "BaseLoader")
    assert hasattr(interfaces, "BaseSplitter")
    assert hasattr(interfaces, "BaseEmbeddings")
    assert hasattr(interfaces, "BaseRetriever")
    assert hasattr(interfaces, "BaseReranker")
    assert hasattr(interfaces, "BaseCompressor")
    d = interfaces.Document(page_content="hello", metadata={"project_id": "starter"})
    assert d.page_content == "hello"


def test_w2_only_scope_no_forbidden_surface():
    import pathlib

    rag_dir = pathlib.Path("app/services/rag")
    forbidden = ("app.graphs", "app.routes", "app/services/llm/model_router",
                 "frontend/", "schema.sql", "requirements.txt")
    for py in rag_dir.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for f in forbidden:
            assert f not in text, f"{py.name} references forbidden surface {f}"
