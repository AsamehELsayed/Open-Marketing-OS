"""DEV-005 W5 — model router + call telemetry (TDD spec).

Scope: additive SQLite model-call telemetry + local-first AUTO/LOCAL/OPENAI
router policy. No edits to app/services/llm/router.py, requirements, locks,
graphs, RAG, FastAPI, React, cutover. No external calls (FakeProvider only).
"""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _fresh_db(tmp_path, name="w5.db"):
    from app.database.sqlite import connect
    conn = connect(tmp_path / name)
    return conn


def _call_kwargs(**over):
    base = {
        "call_id": "call-1",
        "turn_id": "turn-1",
        "project_id": "starter",
        "provider": "local",
        "model": "qwen3-winner",
        "route_mode": "LOCAL",
        "route_reason": "benchmark winner default",
    }
    base.update(over)
    return base


# ---------- telemetry schema ----------

def test_model_calls_table_exists_fresh(tmp_path):
    conn = _fresh_db(tmp_path)
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(model_calls)").fetchall()}
    for required in ("call_id", "turn_id", "project_id", "provider", "model",
                     "adapter", "quantization", "route_mode", "route_reason",
                     "input_tokens", "cached_tokens", "output_tokens",
                     "reasoning_tokens", "total_tokens", "latency_ms",
                     "estimated_cost_usd", "pricing_version", "cost_note",
                     "started_at", "ended_at"):
        assert required in cols, f"missing column {required}"
    conn.close()


def test_model_calls_upgrade_additive(tmp_path):
    """Pre-W5 DB (without model_calls) upgrades in place, data preserved."""
    import sqlite3
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(legacy))
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
    conn.execute("INSERT INTO projects (id, name) VALUES ('starter', 'Acme Test Company')")
    conn.commit()
    conn.close()
    from app.database.sqlite import connect
    upgraded = connect(legacy)
    tables = {r[0] for r in upgraded.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "model_calls" in tables
    assert "projects" in tables
    assert upgraded.execute("SELECT name FROM projects").fetchone()[0] == "Acme Test Company"
    upgraded.close()


def test_model_calls_unknown_usage_stays_null(tmp_path):
    from app.database import repos
    conn = _fresh_db(tmp_path)
    repos.ModelCalls.insert(conn, _call_kwargs())
    row = repos.ModelCalls.get(conn, "call-1")
    assert row["input_tokens"] is None
    assert row["total_tokens"] is None
    assert row["estimated_cost_usd"] is None  # raw insert never fabricates cost
    conn.close()


def test_model_calls_project_isolation(tmp_path):
    from app.database import repos
    conn = _fresh_db(tmp_path)
    repos.ModelCalls.insert(conn, _call_kwargs(call_id="c-starter", project_id="starter"))
    repos.ModelCalls.insert(conn, _call_kwargs(call_id="c-other", project_id="other"))
    rows = repos.ModelCalls.for_project(conn, "starter")
    assert {r["call_id"] for r in rows} == {"c-starter"}
    with pytest.raises(ValueError):
        repos.ModelCalls.get(conn, "c-other", project_id="starter")
    conn.close()


def test_model_calls_for_turn_and_totals(tmp_path):
    from app.database import repos
    conn = _fresh_db(tmp_path)
    repos.ModelCalls.insert(conn, _call_kwargs(
        call_id="c1", turn_id="t9", input_tokens=100, output_tokens=50,
        total_tokens=150, estimated_cost_usd=0.0))
    repos.ModelCalls.insert(conn, _call_kwargs(
        call_id="c2", turn_id="t9", input_tokens=10, output_tokens=5,
        total_tokens=15, estimated_cost_usd=0.0))
    rows = repos.ModelCalls.for_turn(conn, "t9", "starter")
    assert len(rows) == 2
    totals = repos.ModelCalls.turn_totals(conn, "t9", "starter")
    assert totals["input_tokens"] == 110
    assert totals["output_tokens"] == 55
    assert totals["total_tokens"] == 165
    conn.close()


# ---------- pricing config ----------

def test_pricing_config_versioned_with_effective_dates():
    from app.services.llm import model_router as mr
    doc = mr.load_pricing(ROOT / "config" / "model_pricing.yaml")
    assert doc.versions, "needs at least one versioned entry"
    seen = set()
    for v in doc.versions:
        assert v.version and v.effective_date
        assert v.version not in seen
        seen.add(v.version)
    str(doc.effective_version())


def test_pricing_selection_picks_applicable_version():
    from app.services.llm import model_router as mr
    doc = mr.load_pricing(ROOT / "config" / "model_pricing.yaml")
    ordered = sorted(doc.versions, key=lambda v: (v.effective_date, v.version))
    first, latest = ordered[0], ordered[-1]
    assert doc.effective_version(as_of=first.effective_date).version == first.version
    assert doc.effective_version(as_of="2999-01-01").version == latest.version
    with pytest.raises(mr.PricingUnavailable):
        doc.effective_version(as_of="2000-01-01")


def test_pricing_local_cost_zero_and_unknown_stays_unknown():
    from app.services.llm import model_router as mr
    doc = mr.load_pricing(ROOT / "config" / "model_pricing.yaml")
    assert mr.estimate_cost_usd(doc, provider="local", model="anything",
                                input_tokens=1000, output_tokens=500) == 0.0
    assert mr.estimate_cost_usd(doc, provider="openai", model="gpt-4o-mini",
                                input_tokens=None, output_tokens=5) is None
    assert mr.estimate_cost_usd(doc, provider="openai", model="no-such-model",
                                input_tokens=10, output_tokens=5) is None


def test_pricing_token_arithmetic_exact():
    from app.services.llm import model_router as mr
    doc = mr.load_pricing(ROOT / "config" / "model_pricing.yaml")
    rate = doc.effective_version().rate_for("gpt-4o-mini")
    assert rate is not None
    inp, cached_rate, out = rate
    got = mr.estimate_cost_usd(doc, provider="openai", model="gpt-4o-mini",
                               input_tokens=1_000_000, output_tokens=1_000_000)
    assert got == pytest.approx(inp + out)


# ---------- router policy ----------

def test_auto_defaults_local_no_random_escalation():
    from app.services.llm import model_router as mr
    route = mr.decide_route("AUTO", local_available=True, openai_configured=True)
    assert route.provider == "local"
    assert route.reason == "benchmark winner default"


def test_auto_escalation_only_approved_reasons():
    from app.services.llm import model_router as mr
    from app.contracts.routing import ESCALATION_REASONS
    for reason in ESCALATION_REASONS:
        if reason == "benchmark winner default":
            continue
        route = mr.decide_route("AUTO", local_available=reason == "local unavailable" or False,
                                openai_configured=True, escalation_reason=reason)
        if reason in ("explicit user selection", "local unavailable", "context overflow",
                      "repeated format/tool-route failure", "low confidence",
                      "strategic-complexity policy"):
            assert route.provider == "openai", reason
            assert route.reason == reason
    with pytest.raises(ValueError):
        mr.decide_route("AUTO", local_available=True, openai_configured=True,
                        escalation_reason="vibes said so")


def test_missing_openai_key_keeps_local_usable(monkeypatch):
    from app.services.llm import model_router as mr
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    route = mr.decide_route("AUTO", local_available=True, openai_configured=False)
    assert route.provider == "local"
    # Escalation need without a key must not jump to OpenAI.
    route2 = mr.decide_route("AUTO", local_available=True, openai_configured=False,
                             escalation_reason="low confidence")
    assert route2.provider == "local"
    assert "openai unavailable" in route2.reason


def test_local_and_openai_modes_explicit():
    from app.services.llm import model_router as mr
    assert mr.decide_route("LOCAL").provider == "local"
    assert mr.decide_route("OPENAI", openai_configured=True).provider == "openai"
    with pytest.raises(RuntimeError):
        mr.decide_route("OPENAI", openai_configured=False)


# ---------- end-to-end via injected providers (no network) ----------

def test_complete_persists_telemetry_local(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.database import repos
    from app.services.llm import model_router as mr
    from app.services.llm.fake_provider import FakeProvider
    conn = _fresh_db(tmp_path)
    fake_local = FakeProvider()
    fake_local.queue(text="local answer",
                     usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
    router = mr.ModelRouter(local_provider=fake_local,
                            pricing=mr.load_pricing(ROOT / "config" / "model_pricing.yaml"))
    resp, call = router.complete(conn, turn_id="t1", project_id="starter",
                                 system="s", messages=[], tools=[], mode="LOCAL")
    assert resp.text == "local answer"
    assert call.provider == "local"
    assert call.estimated_cost_usd == 0.0
    assert call.local_api_cost_usd() == 0.0
    assert call.totals_consistent() is True
    row = repos.ModelCalls.get(conn, call.call_id)
    assert row["turn_id"] == "t1" and row["route_reason"]
    assert "not metered" in (row["cost_note"] or "").lower()
    conn.close()


def test_complete_unknown_usage_persists_null(tmp_path):
    from app.services.llm import model_router as mr
    from app.services.llm.fake_provider import FakeProvider
    conn = _fresh_db(tmp_path)
    fake_local = FakeProvider()
    fake_local.queue(text="hi", usage={})
    router = mr.ModelRouter(local_provider=fake_local,
                            pricing=mr.load_pricing(ROOT / "config" / "model_pricing.yaml"))
    _, call = router.complete(conn, turn_id="t2", project_id="starter",
                              system="s", messages=[], tools=[], mode="LOCAL")
    assert call.input_tokens is None and call.total_tokens is None
    assert call.estimated_cost_usd == 0.0  # local API cost fact, not usage-derived
    conn.close()


def test_complete_openai_cost_uses_pricing(tmp_path, monkeypatch):
    from app.services.llm import model_router as mr
    from app.services.llm.fake_provider import FakeProvider
    conn = _fresh_db(tmp_path)
    fake_openai = FakeProvider()
    fake_openai.queue(text="cloud answer",
                      usage={"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500})
    router = mr.ModelRouter(openai_provider=fake_openai,
                            pricing=mr.load_pricing(ROOT / "config" / "model_pricing.yaml"),
                            default_openai_model="gpt-4o-mini")
    monkeypatch.setattr(mr, "openai_configured", lambda: True)
    _, call = router.complete(conn, turn_id="t3", project_id="starter",
                              system="s", messages=[], tools=[], mode="OPENAI")
    assert call.provider == "openai"
    assert call.pricing_version
    assert call.estimated_cost_usd is not None and call.estimated_cost_usd > 0
    assert call.totals_consistent() is True
    conn.close()


def test_w5_scope_guard():
    """W5 must not own forbidden surfaces: legacy router untouched, deps declared.

    DEV-008 removed three `assert <expr> or True` lines from this guard. They
    were tautologies — `<anything> or True` is unconditionally `True` — left
    over from a work packet that finished long ago. Two of them were already
    false before DEV-008 (`model_router.py` and `app/graphs/` both exist), so
    no live invariant was destroyed by deleting them; but a test that cannot
    fail is worse than no test, because a reviewer scanning for coverage sees a
    green assertion and concludes the area is checked.

    The dependency assertion below is kept and inverted: the app imports `yaml`,
    so PyYAML must be declared. The original "PyYAML must be absent" check
    encoded a work-packet boundary, not a product property, and was actively
    wrong once DEV-008 fixed the under-declared requirements.
    """
    legacy = (ROOT / "app" / "services" / "llm" / "router.py").read_text(encoding="utf-8")
    assert "MANAGER_PROVIDER" in legacy  # legacy file still the original
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pyyaml" in reqs.lower(), \
        "the app imports yaml, so PyYAML must be a declared dependency"


# ---------- remediation RED: per-million cached pricing ----------

def test_pricing_schema_requires_per_million_fields():
    from app.services.llm import model_router as mr
    doc = mr.load_pricing(ROOT / "config" / "model_pricing.yaml")
    for v in doc.versions:
        assert v.effective_date, "effective_date is required"
        assert not hasattr(v, "effective_from"), "legacy effective_from must go"
    rate = doc.effective_version().rate_for("gpt-4o-mini")
    assert rate is not None and len(rate) == 3, "rate must be (in, cached_in, out) per-million"
    text = (ROOT / "config" / "model_pricing.yaml").read_text(encoding="utf-8")
    assert "input_per_million" in text
    assert "cached_input_per_million" in text
    assert "output_per_million" in text
    assert "per_1k" not in text


def test_cached_input_priced_separately_no_double_count():
    from app.services.llm import model_router as mr
    doc = mr.load_pricing(ROOT / "config" / "model_pricing.yaml")
    rate = doc.effective_version().rate_for("gpt-4o-mini")
    assert rate is not None
    inp, cached_rate, out = rate
    got = mr.estimate_cost_usd(doc, provider="openai", model="gpt-4o-mini",
                               input_tokens=1000, cached_tokens=400,
                               output_tokens=500)
    expected = (600 / 1_000_000 * inp + 400 / 1_000_000 * cached_rate
                + 500 / 1_000_000 * out)
    assert got == pytest.approx(expected)
    # Uncached leg must not be double-counted: cached-only input costs cached rate.
    got_all_cached = mr.estimate_cost_usd(
        doc, provider="openai", model="gpt-4o-mini",
        input_tokens=1000, cached_tokens=1000, output_tokens=0)
    assert got_all_cached == pytest.approx(1000 / 1_000_000 * cached_rate)


# ---------- remediation RED: effective-date selection / no valid version ----------

def test_effective_date_selection_picks_applicable_version():
    from app.services.llm import model_router as mr
    doc = mr.load_pricing(ROOT / "config" / "model_pricing.yaml")
    ordered = sorted(doc.versions, key=lambda v: (v.effective_date, v.version))
    assert len(ordered) >= 2
    first, latest = ordered[0], ordered[-1]
    assert doc.effective_version(as_of=first.effective_date).version == first.version
    assert doc.effective_version(as_of="2999-01-01").version == latest.version


def test_no_valid_version_raises_pricing_unavailable():
    from app.services.llm import model_router as mr
    doc = mr.load_pricing(ROOT / "config" / "model_pricing.yaml")
    with pytest.raises(mr.PricingUnavailable):
        doc.effective_version(as_of="2000-01-01")
    with pytest.raises(mr.PricingUnavailable):
        mr.estimate_cost_usd(doc, provider="openai", model="gpt-4o-mini",
                             input_tokens=10, output_tokens=5, as_of="2000-01-01")


# ---------- remediation RED: cross-project turn-id canary ----------

def test_for_turn_requires_project_scope(tmp_path):
    from app.database import repos
    conn = _fresh_db(tmp_path)
    repos.ModelCalls.insert(conn, _call_kwargs(call_id="c-a", turn_id="t-shared",
                                               project_id="proj-a"))
    repos.ModelCalls.insert(conn, _call_kwargs(call_id="c-b", turn_id="t-shared",
                                               project_id="proj-b"))
    with pytest.raises(ValueError):
        repos.ModelCalls.for_turn(conn, "t-shared")
    with pytest.raises(ValueError):
        repos.ModelCalls.for_turn(conn, "t-shared", "")
    rows_a = repos.ModelCalls.for_turn(conn, "t-shared", "proj-a")
    assert {r["call_id"] for r in rows_a} == {"c-a"}
    rows_b = repos.ModelCalls.for_turn(conn, "t-shared", "proj-b")
    assert {r["call_id"] for r in rows_b} == {"c-b"}
    conn.close()


def test_turn_totals_requires_project_scope(tmp_path):
    from app.database import repos
    conn = _fresh_db(tmp_path)
    repos.ModelCalls.insert(conn, _call_kwargs(
        call_id="c-a1", turn_id="t-shared", project_id="proj-a",
        input_tokens=100, output_tokens=50, total_tokens=150,
        estimated_cost_usd=0.0))
    repos.ModelCalls.insert(conn, _call_kwargs(
        call_id="c-b1", turn_id="t-shared", project_id="proj-b",
        input_tokens=999, output_tokens=999, total_tokens=1998,
        estimated_cost_usd=0.0))
    with pytest.raises(ValueError):
        repos.ModelCalls.turn_totals(conn, "t-shared")
    totals = repos.ModelCalls.turn_totals(conn, "t-shared", "proj-a")
    assert totals["input_tokens"] == 100
    assert totals["output_tokens"] == 50
    assert totals["total_tokens"] == 150
    conn.close()
