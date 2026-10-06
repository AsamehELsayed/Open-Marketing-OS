"""W4 acceptance: R1–R8 + ContextRouter (NEVER-RAG, UNKNOWN, STALE)."""
import os

import pytest

from app.database.sqlite import connect
from app.services.rag import context_router, rag_service
from app.services.rag.chroma_store import ChromaStore
from app.services.rag.embeddings import LocalHashEmbeddings
from app.services.rag.indexing_service import IndexingService, status_for

CORPUS = {
    "knowledge/brand.md": "# Brand\n\nAcme Test Company builds custom websites with live-link proof.\n",
    "strategy/founder-decisions.md": "# Decisions\n\nAgencies are a founder-confirmed major segment for white-label work.\n",
    "strategy/opportunity-backlog.md": "## opp-01 — Demo section\n\n- id: opp-01\n- status: approved\n",
    "research/note.md": "# Snapshot\n\nCompetitor gaps observed in заменим later.\n",
    "production/learning-log.md": "# Learning Log\n\n## 2026-09-16 — entry\n\nAgencies pursued white-label.\n",
    "company/company.yaml": "company:\n  name: TestCo\n",
    "analytics/draft.md": "# Draft\n\nUnlabeled draft content about pricing guesses.\n",
    "tests/simulation/fake.md": "# SIM\n\nSynthetic fixture must never index.\n",
    "research/secret.md": "# Leak\n\nContact key: sk-abcdefgh12345678 do not index.\n",
}


def _corpus(tmp_path, extra=None):
    root = tmp_path / "ws"
    files = dict(CORPUS)
    files.update(extra or {})
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _indexed(tmp_path):
    root = _corpus(tmp_path)
    conn = connect(tmp_path / "t.db")
    svc = IndexingService(conn, root)
    report = svc.build_or_update()
    return conn, root, report


def test_r1_fts_hit_with_provenance(tmp_path):
    conn, root, _ = _indexed(tmp_path)
    res = rag_service.retrieve(conn, "white-label agencies")
    assert res["mode"] == "FTS_ONLY"
    assert res["hits"]
    top = res["hits"][0]
    assert top["path"] == "strategy/founder-decisions.md"  # hierarchy boost wins
    assert top["file_sha"] and top["status_tag"] == "FOUNDER-CONFIRMED"
    conn.close()


def test_r2_chroma_hit_or_skip(tmp_path):
    chromadb = pytest.importorskip("chromadb")
    conn, root, _ = _indexed(tmp_path)
    provider = LocalHashEmbeddings()
    store = ChromaStore(tmp_path / "chroma", provider)
    assert store.mode == "HYBRID"
    svc = IndexingService(conn, root, vector_store=store, provider=provider)
    svc.build_or_update()
    res = rag_service.retrieve(conn, "custom websites", store=store, provider=provider)
    assert res["mode"] == "HYBRID"
    assert res["hits"]
    conn.close()


def test_r3_fallback_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("CHROMA_ENABLED", "0")
    store = ChromaStore(tmp_path / "chroma", LocalHashEmbeddings())
    assert store.mode == "FTS_ONLY"
    assert store.query([0.0] * 256) == []
    conn, root, _ = _indexed(tmp_path)
    res = rag_service.retrieve(conn, "websites", store=store, provider=LocalHashEmbeddings())
    assert res["mode"] == "FTS_ONLY"
    conn.close()


def test_r5_hash_reindex_changed_only(tmp_path):
    conn, root, first = _indexed(tmp_path)
    svc = IndexingService(conn, root)
    second = svc.build_or_update()
    assert second["skipped"] > 0 and second["indexed"] == 0
    (root / "knowledge" / "brand.md").write_text(
        "# Brand\n\nAcme Test Company builds custom websites with live-link proof. New line.\n",
        encoding="utf-8")
    third = svc.build_or_update()
    assert third["indexed"] == 1
    conn.close()


def test_r6_sim_fixture_excluded(tmp_path):
    conn, root, _ = _indexed(tmp_path)
    paths = {r[0] for r in conn.execute("SELECT path FROM documents").fetchall()}
    assert not any("simulation" in p or p.startswith("tests/") for p in paths)
    assert rag_service.retrieve(conn, "synthetic fixture")["hits"] == []
    conn.close()


def test_r7_secret_quarantined(tmp_path):
    conn, root, report = _indexed(tmp_path)
    assert any(q["path"] == "research/secret.md" for q in report["quarantined"])
    paths = {r[0] for r in conn.execute("SELECT path FROM documents").fetchall()}
    assert "research/secret.md" not in paths
    conn.close()


def test_r8_unknown_labeled(tmp_path):
    assert status_for("analytics/draft.md") == "UNKNOWN"
    conn, root, _ = _indexed(tmp_path)
    res = rag_service.retrieve(conn, "pricing guesses")
    assert res["hits"]
    assert res["hits"][0]["status_tag"] == "UNKNOWN"
    conn.close()


def test_router_state_passthrough_and_unknown(tmp_path):
    conn, root, _ = _indexed(tmp_path)
    res = rag_service.retrieve(conn, "websites")
    state = {"company": {"id": "starter"}, "campaigns": [{"id": "opp-01", "status": "approved"}],
             "approvals": [], "measurements": []}
    ctx = context_router.build_context(conn, root, "What is our campaign status?", state, res["hits"])
    assert ctx["state_block"]["campaigns"] == [{"id": "opp-01", "status": "approved"}]
    assert all(e["status_tag"] != "UNKNOWN" for e in ctx["enrichment"])
    assert ctx["provenance"]
    # NEVER-RAG: empty hits leave the state answer unchanged.
    ctx2 = context_router.build_context(conn, root, "What is our campaign status?", state, [])
    assert ctx2["state_block"] == ctx["state_block"] and ctx2["enrichment"] == []
    conn.close()


def test_router_stale_marking(tmp_path):
    conn, root, _ = _indexed(tmp_path)
    res = rag_service.retrieve(conn, "live-link proof")
    assert res["hits"]
    (root / "knowledge" / "brand.md").write_text("# Brand\n\nEdited after index.\n", encoding="utf-8")
    ctx = context_router.build_context(conn, root, "proof?", {"company": {}}, res["hits"])
    assert any(s["path"] == "knowledge/brand.md" for s in ctx["stale"])
    assert all(e["path"] != "knowledge/brand.md" for e in ctx["enrichment"])
    conn.close()


def test_r4_provider_mismatch_rebuilds(tmp_path):
    chromadb = pytest.importorskip("chromadb")
    from app.services.rag.chroma_store import ChromaStore

    class V1(LocalHashEmbeddings):
        id = "test-prov"
        version = "1"

    class V2(LocalHashEmbeddings):
        id = "test-prov"
        version = "2"

    s1 = ChromaStore(tmp_path / "chroma", V1())
    assert s1.mode == "HYBRID"
    s1.upsert([("k1", V1().embed("hello"), {"path": "a.md"})])
    s2 = ChromaStore(tmp_path / "chroma", V2())
    assert s2.mode == "HYBRID"
    assert s2._collection.metadata["provider_version"] == "2"
    assert s2.query(V2().embed("hello")) == []  # old vectors gone, never mixed


def test_embeddings_deterministic():
    p = LocalHashEmbeddings()
    assert p.embed("hello world") == p.embed("hello world")
    assert len(p.embed("x")) == 256
