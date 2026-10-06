"""DEV-015: readiness status must describe usable retrieval branches only."""
from types import SimpleNamespace

import pytest

from app.services import knowledge_index


@pytest.mark.parametrize(
    ("mode", "vector_error", "lexical_ready", "expected_mode", "expected_vector"),
    [
        ("HYBRID", False, True, "HYBRID", "AVAILABLE"),
        ("HYBRID", False, False, "VECTOR DEGRADED", "AVAILABLE"),
        ("FTS_ONLY", False, True, "FTS", "NOT_AVAILABLE"),
        ("FTS_ONLY", False, False, "UNAVAILABLE", "NOT_AVAILABLE"),
        ("HYBRID", True, True, "FTS", "FAILED"),
        ("HYBRID", True, False, "UNAVAILABLE", "FAILED"),
    ],
)
def test_store_status_reports_only_ready_branches(
    monkeypatch, mode, vector_error, lexical_ready, expected_mode, expected_vector,
):
    monkeypatch.setattr(
        knowledge_index, "lexical_index_ready", lambda _conn, _pid: lexical_ready,
    )
    store = SimpleNamespace(
        mode=mode, _vector_error=vector_error, project_id="proj-a",
        offline_reason="vector runtime unavailable",
    )

    status = knowledge_index.retrieval_status_for_store(
        store, conn=object(), project_id="proj-a",
    )

    assert status["search_mode"] == expected_mode
    assert status["vector_status"] == expected_vector
    if expected_mode in ("VECTOR DEGRADED", "UNAVAILABLE"):
        assert "lexical_index_unavailable" in status["vector_reason"]


@pytest.mark.parametrize(
    ("lexical_ready", "expected_mode"),
    [(True, "FTS"), (False, "UNAVAILABLE")],
)
def test_runtime_failure_does_not_claim_fts_without_lexical_index(
    monkeypatch, lexical_ready, expected_mode,
):
    monkeypatch.setattr(
        knowledge_index, "retrieval_runtime",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(
        knowledge_index, "lexical_index_ready", lambda _conn, _pid: lexical_ready,
    )

    status = knowledge_index.retrieval_status("root", "proj-a", conn=object())

    assert status["search_mode"] == expected_mode
    assert status["vector_status"] == "FAILED"
    assert status["vector_reason"].startswith("runtime_failed:RuntimeError")


def test_status_without_database_proof_fails_closed():
    store = SimpleNamespace(
        mode="FTS_ONLY", _vector_error=False, project_id="proj-a",
        offline_reason="embedding_model_not_cached",
    )

    status = knowledge_index.retrieval_status_for_store(store)

    assert status["search_mode"] == "UNAVAILABLE"
    assert status["vector_status"] == "NOT_AVAILABLE"

