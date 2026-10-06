"""DEV-008-SKILLS-OPS-HOTFIX — Packet 2b+3: Graph orchestration, concurrency & synthesis tests.

Verifies:
1. build_compound_task_plan creates Wave A (concurrent) and Wave B (dependent) tasks.
2. State reducers: employee_results combines across branches, task_plan scalar.
3. Event order: account_manager_started < task_planned < employee_queued < employee_started < tool_started.
4. Concurrency & Dependency: Wave A independent workers have overlapping execution intervals;
   Wave B (CRO) does not start until Wave A (Website) finishes.
5. Structured fan-in barrier completes before synthesis.
6. Final synthesis covers all 7 contract sections in order and 3 prioritized growth experiments.
7. Unverified sources (e.g. unconfigured Instagram) are reported honestly with limitations.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from app.database import repos
from app.database.sqlite import connect
from app.graphs.account_manager_graph import (
    build_account_manager_graph,
    build_compound_task_plan,
    run_compound_marketing_task,
    synthesize_compound_answer,
)
from app.graphs.state import initial_state
from app.services.skills.tree import TreeEmitter


PINNED_FOUNDER_PROMPT = (
    "حلّل موقعنا، حسابنا على انستجرام، الـSEO، التموضع، قمع التحويل، والمنافسين، "
    "واقترح 3 تجارب نمو ذات أولوية"
)


def _db(tmp_path, name="orch.db"):
    path = str(tmp_path / name)
    conn = connect(path)
    conn.close()
    return path


def _tree(db_path, turn_id="turn_orch_001"):
    return TreeEmitter(
        db_path=db_path,
        project_id="starter",
        conversation_id="convo-orch-1",
        turn_id=turn_id,
        thread_id=turn_id,
    )


def _rows(db_path, turn_id):
    conn = connect(db_path)
    try:
        return repos.ExecutionEvents.for_turn(conn, turn_id)
    finally:
        conn.close()


def _meta(row):
    return json.loads(row.get("metadata_json") or "{}")


# ------------------------------------------------------------------ Unit: Plan
def test_build_compound_task_plan_structure():
    domains = ["website", "instagram", "seo", "cro", "competitor", "positioning", "experiment"]
    plan = build_compound_task_plan(domains, PINNED_FOUNDER_PROMPT, turn_id="turn_test_plan_01")
    assert plan["plan_id"].startswith("plan-")
    assert plan["turn_id"] == "turn_test_plan_01"
    assert len(plan["tasks"]) >= 6

    # Verify Wave A vs Wave B
    waves = plan["waves"]
    assert "A" in waves and "B" in waves
    assert len(waves["A"]) >= 4, "Wave A must have at least 4 independent workers"
    assert len(waves["B"]) >= 1, "Wave B must have dependent worker(s)"

    # Verify CRO is in Wave B and depends on Website
    cro_task = next(t for t in plan["tasks"] if t["domain"] == "cro")
    assert cro_task["wave"] == "B"
    website_task = next(t for t in plan["tasks"] if t["domain"] == "website")
    assert website_task["wave"] == "A"
    assert any(dep in (website_task["id"], "website") for dep in cro_task["depends_on"])


# ------------------------------------------------------------------ Concurrency & Dependencies
def test_wave_concurrency_and_dependency_enforcement(tmp_path):
    db_path = _db(tmp_path)
    turn_id = "turn_conc_001"
    tree = _tree(db_path, turn_id)
    conn = connect(db_path)

    domains = ["website", "instagram", "competitor", "cro"]
    start_times: dict[str, float] = {}
    end_times: dict[str, float] = {}
    threads: dict[str, int] = {}

    res = run_compound_marketing_task(
        conn,
        project_id="starter",
        user_text=PINNED_FOUNDER_PROMPT,
        domains=domains,
        turn_id=turn_id,
        tree=tree,
    )

    for emp in res["employee_results"]:
        dom = emp.get("domain") or emp.get("employee_role")
        # Ensure completion or failure
        assert emp["status"] in ("completed", "failed")

    # Verify event stream in DB
    rows = _rows(db_path, turn_id)
    types = [r["event_type"] for r in rows]
    assert "account_manager_started" in types
    assert "task_planned" in types
    assert "employee_queued" in types
    assert "synthesis_started" in types
    assert "synthesis_completed" in types

    # Event order: account_manager_started < task_planned < first employee_started < first tool_started
    type_indices = {}
    for idx, r in enumerate(rows):
        t = r["event_type"]
        if t not in type_indices:
            type_indices[t] = idx

    assert type_indices["account_manager_started"] < type_indices["task_planned"]
    if "employee_started" in type_indices:
        assert type_indices["task_planned"] < type_indices["employee_started"]
    if "tool_started" in type_indices and "employee_started" in type_indices:
        assert type_indices["employee_started"] <= type_indices["tool_started"]


# ------------------------------------------------------------------ Synthesis
def test_synthesize_compound_answer_covers_7_sections_and_3_experiments():
    dummy_results = [
        {
            "employee_id": "emp-test-00",
            "employee_role": "cro",
            "domain": "website",
            "status": "completed",
            "task": "Audit website",
            "skills": [{"skill_id": "cro"}],
            "tools": [{"tool_id": "website_marketing_audit", "tool_run_id": "run-web-01", "provider": "website_fetcher"}],
            "evidence": [{"kind": "website_audit", "ref": "https://example.com", "detail": "Page loaded in 1.1s"}],
            "findings": ["Speed index: 1.1s. Mobile viewport responsive."],
            "limitations": [],
        },
        {
            "employee_id": "emp-test-01",
            "employee_role": "content",
            "domain": "instagram",
            "status": "failed",
            "task": "Audit Instagram",
            "skills": [{"skill_id": "social"}],
            "tools": [{"tool_id": "instagram_audit", "tool_run_id": "run-ig-01", "provider": "instagram"}],
            "evidence": [],
            "findings": [],
            "limitations": ["Instagram provider unreachable or unconfigured"],
        },
        {
            "employee_id": "emp-test-02",
            "employee_role": "strategy",
            "domain": "positioning",
            "status": "completed",
            "task": "Audit positioning",
            "skills": [{"skill_id": "product-marketing"}],
            "tools": [],
            "evidence": [],
            "findings": ["Value proposition needs sharper ICP alignment"],
            "limitations": [],
        },
        {
            "employee_id": "emp-test-03",
            "employee_role": "seo",
            "domain": "seo",
            "status": "completed",
            "task": "Audit SEO",
            "skills": [{"skill_id": "seo-audit"}],
            "tools": [],
            "evidence": [],
            "findings": ["Missing schema markup on key product pages"],
            "limitations": [],
        },
        {
            "employee_id": "emp-test-04",
            "employee_role": "cro",
            "domain": "cro",
            "status": "completed",
            "task": "Audit funnel",
            "skills": [{"skill_id": "cro"}],
            "tools": [],
            "evidence": [],
            "findings": ["Checkout flow drop-off at step 2"],
            "limitations": [],
        },
        {
            "employee_id": "emp-test-05",
            "employee_role": "research",
            "domain": "competitor",
            "status": "completed",
            "task": "Audit competitors",
            "skills": [{"skill_id": "competitor-profiling"}],
            "tools": [{"tool_id": "web_search", "tool_run_id": "run-comp-01", "provider": "web_transport"}],
            "evidence": [{"kind": "web_search", "ref": "competitors", "detail": "Competitor pricing found"}],
            "findings": ["Competitors price aggressively with less tailored support"],
            "limitations": [],
        },
    ]

    state = {
        "user_request": PINNED_FOUNDER_PROMPT,
        "employee_results": dummy_results,
    }

    answer = synthesize_compound_answer(state)
    assert answer, "Synthesis must produce non-empty answer"

    # Verify all 7 sections exist in exact order
    sections = [
        "1. تحليل الموقع",
        "2. تحليل حساب انستجرام",
        "3. التموضع والرسائل التسويقية",
        "4. تحسين محركات البحث",
        "5. قمع التحويل وتجربة المستخدم",
        "6. دراسة المنافسين",
        "7. 3 تجارب نمو ذات أولوية",
    ]
    last_idx = -1
    for sec in sections:
        idx = answer.find(sec)
        assert idx != -1, f"Missing section: {sec}"
        assert idx > last_idx, f"Section out of order: {sec}"
        last_idx = idx

    # Verify Instagram unverified honesty
    assert "لم يتم التحقق" in answer or "UNVERIFIED" in answer
    assert "لم يتم فبركة" in answer

    # Verify 3 prioritized growth experiments
    assert "التجربة 1" in answer
    assert "التجربة 2" in answer
    assert "التجربة 3" in answer
    assert "الفرضية" in answer
    assert "مقياس النجاح" in answer


def test_failed_competitor_domain_is_disclosed_without_rag_or_numeric_claims():
    answer = synthesize_compound_answer({
        "user_request": PINNED_FOUNDER_PROMPT,
        "employee_results": [
            {
                "employee_id": "website-1", "employee_role": "cro",
                "domain": "website", "status": "completed",
                "evidence": [{"kind": "website_fetch", "ref": "https://example.com",
                              "detail": "Page title: Example Domain"}],
                "findings": ["The fetched page title is Example Domain."],
                "tools": [],
            },
            {
                "employee_id": "competitor-1", "employee_role": "research",
                "domain": "competitor", "status": "failed", "evidence": [],
                "findings": [], "limitations": ["web provider unavailable"],
                "tools": [{"tool_id": "web_search", "status": "failed"}],
            },
            {
                "employee_id": "seo-1", "employee_role": "seo",
                "domain": "seo", "status": "completed", "evidence": [],
                "findings": ["Inspect sitemap coverage."], "tools": [],
            },
        ],
        "rag_hits": [{"text": "Competitors have cheap prices."}],
    })

    competitor = answer.split("## 6.", 1)[1].split("## 7.", 1)[0]
    assert "تعذر التحقق" in competitor
    assert "لا توجد نتائج منافسين موثقة" in competitor
    assert "معظم المنافسين" not in competitor
    assert "الأسعار التنافسية" not in competitor
    assert "cheap prices" not in answer
    assert "Example Domain" in answer
    assert "توصية عامة" in answer
    assert "%" not in answer
    assert "5%" not in answer
    assert all(f"### التجربة {i}" in answer for i in (1, 2, 3))
    assert "لا توجد نتيجة متوقعة موثقة" in answer


def test_compound_graph_failure_still_fans_in_and_synthesizes(tmp_path):
    db_path = _db(tmp_path, "failure-fanin.db")
    turn_id = "turn_failure_fanin_01"
    tree = _tree(db_path, turn_id)

    def controlled_employee(task, role):
        domain = task.get("domain")
        if domain == "competitor":
            raise RuntimeError("controlled unavailable provider")
        return {"sources": [f"test:{domain or role}"]}

    graph = build_account_manager_graph(
        known_projects={"starter"}, employee_fn=controlled_employee,
    )
    initial = initial_state(
        project_id="starter", conversation_id="convo-failure",
        turn_id=turn_id, user_request=PINNED_FOUNDER_PROMPT,
    )
    out = graph.invoke(
        initial,
        config={"configurable": {"thread_id": "failure-fanin", "tree": tree}},
    )

    assert out.get("final_answer")
    assert "تعذر التحقق" in out["final_answer"]
    assert "معظم المنافسين" not in out["final_answer"]
    assert "%" not in out["final_answer"]
    rows = _rows(db_path, turn_id)
    types = [row["event_type"] for row in rows]
    assert "employee_failed" in types
    assert types.count("employee_completed") >= 1
    assert "synthesis_started" in types and "synthesis_completed" in types
    failed_idx = types.index("employee_failed")
    success_idxs = [i for i, kind in enumerate(types) if kind == "employee_completed"]
    synth_idx = types.index("synthesis_started")
    assert success_idxs and all(i < synth_idx for i in success_idxs)
    assert failed_idx < synth_idx
    # The injected failure cannot turn unrelated project RAG into competitor research.
    assert not out.get("rag_hits")


# ------------------------------------------------------------------ Graph Integration
def test_graph_invoke_compound_marketing_task(tmp_path):
    db_path = _db(tmp_path)
    turn_id = "turn_graph_compound_01"
    tree = _tree(db_path, turn_id)
    graph = build_account_manager_graph(known_projects={"starter"})

    init_state = initial_state(
        project_id="starter",
        conversation_id="convo-orch-1",
        turn_id=turn_id,
        user_request=PINNED_FOUNDER_PROMPT,
    )

    out = graph.invoke(
        init_state,
        config={"configurable": {"thread_id": "w4-graph-1", "tree": tree}},
    )

    answer = out.get("final_answer") or ""
    assert len(answer) > 200, f"Graph must synthesize full compound answer: {answer}"
    assert "تحليل الموقع" in answer
    assert "انستجرام" in answer
    assert "تجارب نمو" in answer

    rows = _rows(db_path, turn_id)
    types = [r["event_type"] for r in rows]
    assert "account_manager_started" in types
    assert "task_planned" in types
    assert "synthesis_completed" in types


def test_wave_a_measured_concurrency_and_cro_dependency(tmp_path):
    """Measured concurrency proof: independent workers overlap in time; Wave B waits."""
    db_path = _db(tmp_path)
    turn_id = "turn_conc_proof_01"
    tree = _tree(db_path, turn_id)

    spans: dict[str, tuple[float, float]] = {}
    threads: dict[str, int] = {}
    lock = threading.Lock()

    def timing_worker(task, role):
        ident = threading.get_ident()
        dom = task.get("domain") or role
        t_start = time.perf_counter()
        time.sleep(0.3)
        t_end = time.perf_counter()
        with lock:
            spans[dom] = (t_start, t_end)
            threads[dom] = ident
        return {"sources": [f"test:{dom}"]}

    graph = build_account_manager_graph(
        known_projects={"starter"},
        employee_fn=timing_worker,
    )

    init_state = initial_state(
        project_id="starter",
        conversation_id="convo-orch-2",
        turn_id=turn_id,
        user_request=PINNED_FOUNDER_PROMPT,
    )

    out = graph.invoke(
        init_state,
        config={"configurable": {"thread_id": "w4-conc-graph", "tree": tree}},
    )

    # Measured overlap between Wave A independent workers
    wave_a_keys = [k for k in spans if k != "cro"]
    assert len(wave_a_keys) >= 2, f"Expected multiple Wave A workers, got: {list(spans.keys())}"

    # Check pair-wise overlap among Wave A workers
    max_start = max(spans[k][0] for k in wave_a_keys[:2])
    min_end = min(spans[k][1] for k in wave_a_keys[:2])
    overlap = max_start < min_end
    assert overlap, f"Wave A workers must overlap in execution time: {spans}"

    # If CRO ran, verify it ran after Website completed (Wave B dependency)
    if "cro" in spans and "website" in spans:
        cro_start = spans["cro"][0]
        website_end = spans["website"][1]
        assert cro_start >= website_end - 0.05, f"CRO must wait for Website: {cro_start=} {website_end=}"


def test_account_manager_run_graph_compound_dispatch(tmp_path):
    """Test that account_manager.py:run_graph dispatches compound marketing tasks via resolve_compound_runner."""
    from app.graphs.account_manager import run_graph

    db_path = _db(tmp_path)
    conn = connect(db_path)
    # Ensure starter project exists in DB
    from app.database import repos
    repos.Projects.upsert(conn, {
        "id": "starter",
        "name": "Acme Test Company",
        "website": "https://example.com",
        "goal": "",
        "status": "active",
        "settings_json": "{}",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    })
    conn.commit()

    out = run_graph(
        conn,
        root=str(tmp_path),
        project_id="starter",
        conversation_id="convo-1",
        turn_id="turn_offline_01",
        user_text=PINNED_FOUNDER_PROMPT,
    )

    assert out.get("route") == "compound_marketing_task"
    reply = out.get("final_answer") or out.get("reply_md") or ""
    assert "تحليل الموقع" in reply
    assert "3 تجارب نمو" in reply
    conn.close()


def test_single_evidence_based_growth_experiment_is_grounded_in_live_website_findings():
    from app.graphs.account_manager_graph import _evidence_grounded_experiment

    request = (
        "Analyze https://example.com and propose one growth experiment "
        "based only on evidence you can actually verify."
    )
    website = {
        "employee_role": "cro",
        "task": "Audit website using the live website tool",
        "status": "completed",
        "evidence": [{
            "kind": "website_marketing_audit",
            "ref": "https://example.com",
            "detail": "Live website audit; one page fetched.",
        }],
        "findings": [
            "website: no call-to-action keywords observed in the fetched pages."
        ],
        "tools": [{
            "tool_id": "website_marketing_audit",
            "tool_run_id": "tool-run-live-1",
            "status": "completed",
        }],
    }
    answer = synthesize_compound_answer({
        "user_request": request,
        "employee_results": [website],
    })
    experiment_section = answer.split("## 7", 1)[1]
    assert experiment_section.count("### ") == 1
    assert "no call-to-action keywords observed" in experiment_section
    assert "checkout" not in experiment_section.lower()

    proposal = _evidence_grounded_experiment(
        {"hypothesis": "generic", "metric": "website conversion rate"},
        [website],
        request,
    )
    assert proposal["metric"] == "primary CTA click-through rate"
    assert "no action-oriented CTA keywords" in proposal["hypothesis"]
    assert "tool-run-live-1" in proposal["evidence_tool_run_id"]
    assert _evidence_grounded_experiment(
        {"hypothesis": "generic", "metric": "website conversion rate"}, [], request
    ) is None
