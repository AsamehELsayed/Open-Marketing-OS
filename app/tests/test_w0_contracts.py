"""W0 shared v1 contracts — TDD spec. RED first, then app/contracts/ GREEN.

Scope: typed contracts + runtime flags + frozen parity fixtures.
NOT in scope: LangGraph, RAG pipeline, local inference, UI, cutover.
"""
import json
import os
from pathlib import Path

import pytest
import yaml
from app.tests.internal_fixtures import requires_internal_runs

ROOT = Path(__file__).resolve().parents[2]
PARITY_DIR = ROOT / "development" / "runs" / "DEV-005" / "parity"


# ---------- runtime flags ----------

def test_runtime_defaults_legacy_and_cap4(monkeypatch):
    from app.contracts.runtime import RuntimeConfig, get_ai_runtime, get_max_agent_concurrency
    monkeypatch.delenv("AI_RUNTIME", raising=False)
    monkeypatch.delenv("MAX_AGENT_CONCURRENCY", raising=False)
    assert get_ai_runtime() == "langgraph"
    assert get_max_agent_concurrency() == 4
    assert RuntimeConfig().ai_runtime == "langgraph"
    assert RuntimeConfig().max_agent_concurrency == 4


def test_runtime_env_parsing(monkeypatch):
    from app.contracts import runtime as rt
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    monkeypatch.setenv("MAX_AGENT_CONCURRENCY", "2")
    assert rt.get_ai_runtime() == "langgraph"
    assert rt.get_max_agent_concurrency() == 2
    assert rt.RuntimeConfig.from_env().ai_runtime == "langgraph"


def test_runtime_invalid_fails_closed_to_legacy_defaults(monkeypatch):
    from app.contracts import runtime as rt
    monkeypatch.setenv("AI_RUNTIME", "bogus")
    monkeypatch.setenv("MAX_AGENT_CONCURRENCY", "99")
    assert rt.get_ai_runtime() == "langgraph"
    assert rt.get_max_agent_concurrency() == 4  # hard cap: never exceeds 4
    with pytest.raises(Exception):
        rt.RuntimeConfig(ai_runtime="bogus")
    with pytest.raises(Exception):
        rt.RuntimeConfig(max_agent_concurrency=99)


def test_runtime_is_legacy_serving(monkeypatch):
    from app.contracts import runtime as rt
    monkeypatch.setenv("AI_RUNTIME", "legacy")
    assert rt.is_legacy_serving() is True
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    assert rt.is_legacy_serving() is False


def test_v1_runtime_yaml_declares_safe_defaults():
    from app.contracts import runtime as rt
    cfg_path = ROOT / "config" / "v1_runtime.yaml"
    assert cfg_path.exists(), "config/v1_runtime.yaml must exist"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["ai_runtime"] == "langgraph"
    assert data["max_agent_concurrency"] == 4
    assert rt.AI_RUNTIME_DEFAULT == "langgraph"
    assert rt.MAX_AGENT_CONCURRENCY_DEFAULT == 4
    assert rt.MAX_AGENT_CONCURRENCY_HARD_CAP == 4


# ---------- graph execution events (wire-compatible) ----------

def test_legacy_wire_event_types_preserved():
    from app.contracts import events as ev
    # Every event type the current SSE transport can emit must stay valid.
    legacy = {
        "turn_started", "provider_selected", "context_started",
        "context_completed", "rag_started", "rag_completed",
        "web_started", "web_completed", "web_source",
        "instagram_provider_started", "instagram_provider_completed",
        "delegation_started", "state_read", "tool_completed",
        "tool_failed", "job_created", "approval_required",
        "synthesis_started", "assistant_delta", "assistant_completed",
        "turn_completed", "turn_failed", "turn_closed", "stream_end",
    }
    assert legacy <= set(ev.LEGACY_EVENT_TYPES)


def test_event_row_round_trip_and_sse_shape():
    from app.contracts import events as ev
    event = ev.GraphExecutionEvent(
        project_id="starter", conversation_id="c1", turn_id="t1",
        event_type="turn_completed", label="Response ready",
        metadata={"provider": "deterministic", "latency_ms": 120},
    )
    row = event.to_row()
    assert row["project_id"] == "starter" and row["turn_id"] == "t1"
    assert row["event_type"] == "turn_completed"
    back = ev.GraphExecutionEvent.from_row({**row, "id": 7})
    assert back.event_type == "turn_completed"
    sse = event.to_sse(id=7)
    assert sse["id"] == 7 and sse["event"] == "turn_completed"
    assert sse["data"]["label"] == "Response ready"
    assert "meta" in sse["data"]


def test_event_metadata_never_carries_prompts_or_secrets():
    from app.contracts import events as ev
    event = ev.GraphExecutionEvent(
        project_id="starter", event_type="assistant_completed",
        label="Response ready",
        metadata={"prompt": "SECRET", "system": "SECRET", "provider": "deterministic"},
    )
    assert "prompt" not in event.metadata and "system" not in event.metadata
    assert ev.sanitize_metadata({"prompt": "x", "a": 1}) == {"a": 1}
    with pytest.raises(Exception):
        ev.GraphExecutionEvent(project_id="", event_type="turn_started")


def test_event_terminal_set_matches_transport():
    from app.contracts import events as ev
    assert {"turn_completed", "turn_failed"} <= set(ev.TERMINAL_EVENT_TYPES)


# ---------- model-call observability ----------

def test_model_call_unknown_tokens_stay_unknown():
    from app.contracts import model_call as mc
    call = mc.ModelCall(
        turn_id="t1", project_id="starter",
        provider="openai", model="gpt-4o-mini",
        route_mode="AUTO", route_reason="explicit user selection",
    )
    assert call.input_tokens is None  # never fabricated as 0
    assert call.total_tokens is None
    assert call.unknown_token_fields() != []


def test_model_call_local_cost_zero_and_arithmetic_exact():
    from app.contracts import model_call as mc
    call = mc.ModelCall(
        turn_id="t1", project_id="starter", provider="local",
        model="qwen3-8b", route_mode="LOCAL",
        route_reason="benchmark winner default",
        input_tokens=100, cached_tokens=10, output_tokens=50,
        total_tokens=150, latency_ms=900,
        pricing_version="2026-09-01",
    )
    assert call.local_api_cost_usd() == 0.0
    assert call.totals_consistent() is True
    bad = call.model_copy(update={"total_tokens": 999})
    assert bad.totals_consistent() is False


def test_model_call_totals_follow_provider_semantics():
    from app.contracts import model_call as mc
    call = mc.ModelCall(
        turn_id="t1", project_id="starter", provider="openai",
        model="gpt-4o-mini", route_mode="AUTO",
        route_reason="explicit user selection",
        input_tokens=100, cached_tokens=30, output_tokens=50,
        reasoning_tokens=20, total_tokens=150,
        latency_ms=100,
    )
    # total == input + output; cached/reasoning are subsets, never added again.
    assert call.totals_consistent() is True
    double_counted = call.model_copy(update={"total_tokens": 200})
    assert double_counted.totals_consistent() is False


def test_model_call_cached_cannot_exceed_input():
    from app.contracts import model_call as mc
    with pytest.raises(Exception):
        mc.ModelCall(
            turn_id="t1", project_id="starter", provider="openai",
            model="gpt-4o-mini", route_mode="AUTO",
            route_reason="explicit user selection",
            input_tokens=10, cached_tokens=20, output_tokens=5,
            total_tokens=15,
        )
    # Unknown side defers the check — allowed.
    ok_unknown = mc.ModelCall(
        turn_id="t1", project_id="starter", provider="openai",
        model="gpt-4o-mini", route_mode="AUTO",
        route_reason="explicit user selection",
        cached_tokens=20,
    )
    assert ok_unknown.input_tokens is None
    # Bypassed invalid row must still read inconsistent, never consistent.
    bypassed = ok_unknown.model_copy(
        update={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    )
    assert bypassed.totals_consistent() is False


def test_model_call_reasoning_cannot_exceed_output():
    from app.contracts import model_call as mc
    with pytest.raises(Exception):
        mc.ModelCall(
            turn_id="t1", project_id="starter", provider="openai",
            model="gpt-4o-mini", route_mode="AUTO",
            route_reason="explicit user selection",
            input_tokens=10, output_tokens=5, reasoning_tokens=20,
            total_tokens=15,
        )
    # Unknown side defers the check — allowed.
    ok_unknown = mc.ModelCall(
        turn_id="t1", project_id="starter", provider="openai",
        model="gpt-4o-mini", route_mode="AUTO",
        route_reason="explicit user selection",
        reasoning_tokens=20,
    )
    assert ok_unknown.output_tokens is None
    # Bypassed invalid row must still read inconsistent, never consistent.
    bypassed = ok_unknown.model_copy(
        update={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    )
    assert bypassed.totals_consistent() is False


def test_model_call_rejects_unknown_provider_fields_fabrication():
    from app.contracts import model_call as mc
    with pytest.raises(Exception):
        mc.ModelCall(turn_id="t1", project_id="starter", provider="watson",
                     model="x", route_mode="AUTO", route_reason="r")


# ---------- approvals / resume ----------

def test_approval_yellow_red_require_approval_and_idempotency():
    from app.contracts import approvals as ap
    req = ap.ApprovalRequest(
        approval_id="a1", project_id="starter", title="Send campaign",
        action_class="yellow", idempotency_key="charge:v1:a1",
    )
    assert req.executable() is False  # pending cannot execute
    approved = req.model_copy(update={"status": "approved"})
    assert approved.executable() is True
    with pytest.raises(Exception):
        ap.ApprovalRequest(approval_id="a2", project_id="starter",
                           title="x", action_class="red")  # key required


def test_approval_green_passes_through_and_resume():
    from app.contracts import approvals as ap
    req = ap.ApprovalRequest(
        approval_id="g1", project_id="starter", title="Read state",
        action_class="green",
    )
    assert req.executable() is True  # green needs no interrupt
    resume = ap.ApprovalResume(approval_id="g1", project_id="starter",
                               decision="approved", decided_by="founder")
    assert resume.should_resume_graph() is True
    rejected = ap.ApprovalResume(approval_id="g1", project_id="starter",
                                 decision="rejected", decided_by="founder")
    assert rejected.should_resume_graph() is False


# ---------- retrieval evidence ----------

def test_retrieval_result_requires_project_scope():
    from app.contracts import retrieval as rv
    hit = rv.RetrievalHit(path="knowledge/brand.md", chunk_id="c1",
                          header="Brand", text="...", file_sha="abc",
                          status_tag="VERIFIED", source="fts",
                          project_id="starter")
    res = rv.RetrievalResult(query="brand voice", project_id="starter",
                             mode="FTS_ONLY", hits=[hit])
    assert res.hit_count() == 1
    with pytest.raises(Exception):
        rv.RetrievalResult(query="x", project_id="", mode="FTS_ONLY", hits=[])
    with pytest.raises(Exception):
        rv.RetrievalResult(query="x", project_id="starter", mode="FTS_ONLY",
                           hits=[hit.model_copy(update={"project_id": "other"})])


def test_retrieval_from_legacy_adapter():
    from app.contracts import retrieval as rv
    legacy = {"hits": [{"path": "knowledge/brand.md", "chunk_id": "c1",
                        "header": "h", "text": "t", "file_sha": "s",
                        "status_tag": "VERIFIED", "source": "fts"}],
              "mode": "FTS_ONLY"}
    res = rv.RetrievalResult.from_legacy("brand", "starter", legacy)
    assert res.mode == "FTS_ONLY" and res.hit_count() == 1


# ---------- route decisions ----------

def test_route_decision_branches_and_confidence():
    from app.contracts import routing as ro
    d = ro.RouteDecision(route="knowledge", confidence=0.8,
                         reason="RAG-needed question", project_id="starter")
    assert d.requires_approval() is False
    assert ro.RouteDecision(route="approval_operation", confidence=0.9,
                            reason="yellow action", project_id="starter").requires_approval() is True
    with pytest.raises(Exception):
        ro.RouteDecision(route="nope", confidence=0.5, reason="x", project_id="starter")
    with pytest.raises(Exception):
        ro.RouteDecision(route="knowledge", confidence=1.5, reason="x", project_id="starter")


def test_model_route_escalation_reasons_recorded():
    from app.contracts import routing as ro
    m = ro.ModelRoute(mode="AUTO", provider="local", model="qwen3-8b",
                      reason="benchmark winner default")
    assert m.provider == "local"
    with pytest.raises(Exception):
        ro.ModelRoute(mode="AUTO", provider="local", model="m", reason="")


# ---------- TOON boundary ----------

def test_toon_only_at_model_semantic_boundaries():
    from app.contracts import toon_boundary as tb
    assert tb.is_toon_boundary("context_pack") is True
    assert tb.is_toon_boundary("rag_evidence") is True
    assert tb.is_toon_boundary("tool_observation") is True
    assert tb.is_toon_boundary("sqlite_row") is False
    assert tb.is_toon_boundary("fastapi_transport") is False
    assert tb.is_toon_boundary("react_state") is False
    assert tb.is_toon_boundary("provider_wire") is False
    assert tb.is_toon_boundary("graph_state") is False


def test_toon_response_fields_normative():
    from app.contracts import toon_boundary as tb
    assert set(tb.TOON_RESPONSE_FIELDS) == {
        "schema_version", "route", "confidence", "tool_calls",
        "evidence_refs", "approval", "unknowns", "final_answer", "errors",
    }
    missing = tb.validate_response_fields({"schema_version": "1"})
    assert "final_answer" in missing
    assert tb.validate_response_fields({f: None for f in tb.TOON_RESPONSE_FIELDS}) == []
    assert tb.safety_check({"project_id": "starter"}) == []
    problems = tb.safety_check({"api_key": "SECRET"})
    assert problems != []


# ---------- frozen parity fixtures ----------

@requires_internal_runs
def test_parity_fixtures_frozen_and_present():
    assert PARITY_DIR.is_dir(), "development/runs/DEV-005/parity/ must exist"
    for name in ("chat_corpus.json", "rag_golden.json", "state_cases.json"):
        p = PARITY_DIR / name
        assert p.exists(), f"{name} missing"
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data.get("frozen") is True, f"{name} must be marked frozen"
        assert data.get("schema_version"), f"{name} needs schema_version"


@requires_internal_runs
def test_chat_corpus_covers_parity_slices():
    data = json.loads((PARITY_DIR / "chat_corpus.json").read_text(encoding="utf-8"))
    cases = data["cases"]
    slices = {c["slice"] for c in cases}
    for required in ("arabic_msa", "egyptian", "english", "mixed",
                     "approvals", "rag_needed", "web_needed"):
        assert required in slices, f"missing slice {required}"
    for c in cases:
        assert c["text"] and c["expect"]
        # missing_project is the fail-closed negative case: empty scope.
        assert c["project_id"] or c["slice"] == "missing_project"
        assert "terminal_event" in c["expect"]


@requires_internal_runs
def test_rag_golden_has_fts_only_baseline():
    data = json.loads((PARITY_DIR / "rag_golden.json").read_text(encoding="utf-8"))
    assert data["retrieval_mode_baseline"] == "FTS_ONLY"
    assert len(data["queries"]) >= 3
    for q in data["queries"]:
        assert q["query"] and q["project_id"]
        assert "expected_top_path" in q or "retrieval_expectation" in q


# ---------- legacy preservation ----------

def test_legacy_manager_config_defaults_untouched(monkeypatch, tmp_path):
    """Legacy manager defaults must survive, read from a *clean* database.

    DEV-008: this test used to call `config_from_env()` with no database
    isolation, so `ConfigService.get_setting("manager_provider", "auto")` read
    the developer's real `data/marketing.db`. On any machine where someone had
    actually used the app, it returned that machine's setting instead of the
    default and the test failed. It was already recorded as a pre-existing
    failure, but the cause is a genuine test-isolation bug rather than an
    environment quirk, and a suite whose result depends on local state is not
    something to ship into a reproducible release build.

    Isolating the database makes the assertion test what it always meant to
    test: the *default*, on a database that has never been configured.
    """
    from app import deps

    for k in ("AI_RUNTIME", "MAX_AGENT_CONCURRENCY", "MANAGER_PROVIDER",
              "MANAGER_MODE", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(deps, "DB_PATH", tmp_path / "w0-clean.db")
    deps.init_db(tmp_path / "w0-clean.db")

    from app.services.llm.config import config_from_env
    cfg = config_from_env()
    assert cfg.manager_provider == "auto"
    assert cfg.manager_mode == "deterministic"
    assert cfg.provider_priority == ("openai", "deterministic")
