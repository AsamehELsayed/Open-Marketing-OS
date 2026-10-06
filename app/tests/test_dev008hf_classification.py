"""DEV-008-SKILLS-OPS-HOTFIX — Packet 1 classification tests.

Covers contract sections 1, 2, and 10 on the real classifier:
- the pinned byte-exact Arabic founder prompt (§10) classifies
  COMPOUND_MARKETING_TASK and detects all seven contract domains;
- a single explicit request ("/audit <url>", "scrap … @handle") stays
  SINGLE_CAPABILITY and still reaches tool_capability — the DEV-007R-HOTFIX-2
  behavior the hotfix must not break;
- precedence: conversation_meta > compound_marketing_task > single_capability;
- empty / whitespace / garbage input never raises and is always in
  INTENT_CLASSES;
- an English compound prompt classifies COMPOUND_MARKETING_TASK;
- routing + branch dispatch integration in app.graphs.account_manager.

Two assertions in the first draft of this file were corrected against the real
system rather than made to pass: "scrape @company" and "What was the previous
question …" are NOT capability/meta intents in the frozen DEV-007R classifier
(see test_contract_example_single_ask_is_not_compound and
test_conversation_meta_wins_over_compound).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.graphs.account_manager import classify_to_route, decide_route
from app.graphs.intent import (
    INTENT_CLASSES,
    MARKETING_DOMAINS,
    classify_intent,
    classify_task_class,
    detect_compound_task,
)

PINNED_PROMPT_FIXTURE = Path(__file__).parent / "fixtures" / "pinned-founder-prompt.txt"

# §10: pinned byte-for-byte. Never normalized, reordered or translated. The
# test re-reads it from the contract so the two cannot drift apart silently.
PINNED_FOUNDER_PROMPT = (
    "حلّل موقعنا الإلكتروني، حسابنا على انستجرام، Positioning بتاعنا، "
    "الـSEO، الـconversion funnel، والمنافسين، وبعدين اقترحلي ٣ تجارب نمو "
    "مرتبة حسب الأولوية"
)

# §10 names the seven requested domains of that prompt.
CONTRACT_DOMAINS = ("website", "instagram", "positioning", "seo", "cro",
                    "competitor", "experiment")

# The verified single-capability reproductions: each of these reaches
# tool_capability on the frozen system before and after this hotfix.
SINGLE_CAPABILITY_PROMPTS = (
    "scrap this instagram account @acme_test",
    "/audit https://example.com",
    "/scrap @acme_test",
    "audit acme-test.example",
    "crawl our website https://acme-test.example",
)

# An English mirror of the §10 ask: detection must not be Arabic-only.
EN_COMPOUND = (
    "Analyze our website, our Instagram, our positioning, our SEO, our "
    "conversion funnel, and our competitors, then propose 3 growth "
    "experiments"
)


def _contract_founder_prompt() -> str:
    return PINNED_PROMPT_FIXTURE.read_text(encoding="utf-8").strip()


# ------------------------------------------------------- frozen token sets

def test_intent_classes_and_marketing_domains_contract():
    from typing import get_args

    from app.contracts.routing import GraphRoute

    assert INTENT_CLASSES == (
        "CONVERSATION_META",
        "COMPOUND_MARKETING_TASK",
        "SINGLE_CAPABILITY",
        "PROJECT_STATE",
        "KNOWLEDGE",
        "MARKETING_REASONING",
    )
    assert MARKETING_DOMAINS == (
        "website", "seo", "cro", "social", "instagram",
        "competitor", "positioning", "experiment",
    )
    # §2: exactly one literal added to the route union.
    routes = get_args(GraphRoute)
    assert "compound_marketing_task" in routes
    assert set(routes) - {"compound_marketing_task"} == {
        "state_only", "conversation_meta", "tool_capability", "knowledge",
        "external_research", "social_research", "deep_research",
        "campaign_operation", "approval_operation", "job_followup"}


# ---------------------------------------------- §10 pinned founder prompt

def test_pinned_prompt_still_matches_the_contract():
    assert _contract_founder_prompt() == PINNED_FOUNDER_PROMPT


def test_pinned_arabic_founder_prompt_detection():
    compound = detect_compound_task(PINNED_FOUNDER_PROMPT)
    assert compound is not None, "the pinned prompt must classify as compound"
    assert compound["intent_class"] == "COMPOUND_MARKETING_TASK"
    detected = compound["domains"]
    # §10 counts seven: a platform mention must not double-count as "social".
    assert len(detected) == len(CONTRACT_DOMAINS), detected
    for expected in CONTRACT_DOMAINS:
        assert expected in detected, (expected, detected)
    assert 0.0 < compound["confidence"] <= 1.0
    assert compound["reason"]

    assert classify_task_class(PINNED_FOUNDER_PROMPT) == "COMPOUND_MARKETING_TASK"

    intent = classify_intent(PINNED_FOUNDER_PROMPT)
    assert intent["route"] == "compound_marketing_task"
    assert intent["intent_class"] == "COMPOUND_MARKETING_TASK"
    assert intent["confidence"] >= 0.9
    # the compound verdict carries no single capability to execute
    assert "capability" not in intent

    assert classify_to_route(PINNED_FOUNDER_PROMPT) == "compound_marketing_task"
    decision = decide_route(PINNED_FOUNDER_PROMPT, project_id="starter")
    assert decision.route == "compound_marketing_task"
    assert decision.confidence >= 0.85


def test_english_compound_prompt():
    compound = detect_compound_task(EN_COMPOUND)
    assert compound is not None, "detection must work in English too"
    assert compound["intent_class"] == "COMPOUND_MARKETING_TASK"
    detected = set(compound["domains"])
    assert not [d for d in CONTRACT_DOMAINS if d not in detected], compound
    assert classify_task_class(EN_COMPOUND) == "COMPOUND_MARKETING_TASK"
    assert classify_to_route(EN_COMPOUND) == "compound_marketing_task"


@pytest.mark.parametrize("text,domain", [
    ("موقع", "website"), ("سيو", "seo"), ("تحويل", "cro"),
    ("انستجرام", "instagram"), ("منافسين", "competitor"),
    ("تموضع", "positioning"), ("تجارب", "experiment"),
    ("website", "website"), ("seo", "seo"), ("conversion", "cro"),
    ("instagram", "instagram"), ("competitors", "competitor"),
    ("brand positioning", "positioning"), ("A/B test", "experiment"),
])
def test_domain_detection_works_in_arabic_and_english(text, domain):
    from app.graphs.intent import _domains_in

    assert domain in _domains_in(text), (text, domain)


# ------------------------------------- single capability regression guard

@pytest.mark.parametrize("text", SINGLE_CAPABILITY_PROMPTS)
def test_single_capability_fast_route_preserved(text):
    assert classify_task_class(text) == "SINGLE_CAPABILITY", text
    intent = classify_intent(text)
    assert intent["route"] == "tool_capability", (text, intent)
    assert intent["intent_class"] == "SINGLE_CAPABILITY", (text, intent)
    # the frozen return keys other callers and tests depend on
    assert intent["capability"]["intent_type"] == "TOOL_CAPABILITY", intent
    assert intent["capability"]["capability"] in (
        "instagram_public_profile", "website_marketing_audit",
        "website_fetch", "website_crawl"), intent
    assert intent["confidence"] > 0.0 and intent["reason"]
    assert classify_to_route(text) == "tool_capability", text


def test_contract_example_single_ask_is_not_compound():
    """§1's "scrape @company" example: it must not become a compound turn.

    It carries no marketing-domain vocabulary, so the compound rule must leave
    it exactly as the frozen DEV-007R-HOTFIX-2 classifier left it. Its own
    capability/social route is that run's behavior, not this hotfix's to
    change, so it is deliberately not asserted here.
    """
    assert detect_compound_task("scrape @company") is None
    assert classify_task_class("scrape @company") != "COMPOUND_MARKETING_TASK"
    assert classify_to_route("scrape @company") != "compound_marketing_task"
    # and a real single-capability scrape still reaches the tool registry
    assert classify_to_route("scrap this instagram account @acme_test") == (
        "tool_capability")


@pytest.mark.parametrize("text", [
    "Can you do an SEO audit of our SaaS website? We're getting about 2,000 "
    "organic visits/month but feel like we should be getting more. "
    "URL: https://example.com",
    "Can you audit our website for AI search readiness? We want to know how "
    "visible we are across ChatGPT, Perplexity, Google AI Overviews, and "
    "other AI platforms.",
    "how do I improve my SEO audit readiness and fix our technical SEO with a "
    "proper site architecture review",
])
def test_two_domain_words_in_one_ask_stay_single_capability(text):
    """Two domain *words* in one clause is still ONE ask.

    DEV-008-SKILLS-OPS pins these prompts to a tool capability; the compound
    rule must not rewrite them.
    """
    assert detect_compound_task(text) is None, text
    assert classify_to_route(text) == "tool_capability", text


# ------------------------------------------------------------- precedence

def test_compound_outranks_single_capability():
    text = ("Audit our website https://example.com and analyze our competitors "
            "positioning too")
    # a single capability is genuinely present in this message ...
    from app.graphs.intent import detect_website_intent

    assert detect_website_intent(text) is not None
    # ... and compound still wins, so the turn never runs that one capability
    assert classify_task_class(text) == "COMPOUND_MARKETING_TASK"
    intent = classify_intent(text)
    assert intent["route"] == "compound_marketing_task"
    assert intent["intent_class"] == "COMPOUND_MARKETING_TASK"
    assert "capability" not in intent
    assert classify_to_route(text) == "compound_marketing_task"


def test_slash_command_never_becomes_compound():
    assert detect_compound_task("/audit https://example.com") is None
    assert detect_compound_task(
        "/audit https://example.com and our competitors") is None


def test_conversation_meta_wins_over_compound():
    text = ("ملخص ريكاب: في الشات ده طلبنا نراجع موقعنا وحسابنا على انستجرام "
            "ومرضانا، summarize our conversation please")
    assert classify_task_class(text) == "CONVERSATION_META", text
    intent = classify_intent(text)
    assert intent["route"] == "conversation_meta"
    assert intent["intent_class"] == "CONVERSATION_META"
    assert classify_to_route(text) == "conversation_meta"


def test_intent_shape_keeps_every_pre_existing_key():
    meta = classify_intent("what this chat talking about")
    assert set(meta) >= {"route", "confidence", "reason", "intent_class"}
    assert meta["route"] == "conversation_meta"
    cap = classify_intent("scrap this instagram account @acme_test")
    assert set(cap) >= {"route", "capability", "confidence", "reason",
                        "intent_class"}
    web = classify_intent("crawl our website https://acme-test.example")
    assert set(web) >= {"route", "capability", "confidence", "reason",
                        "intent_class"}
    assert web["capability"]["capability"] == "website_crawl"
    other = classify_intent("اي رايك في نجم")
    assert set(other) >= {"route", "confidence", "reason", "intent_class"}
    assert other["route"] == ""


# ------------------------------------------------- classify_task_class total

@pytest.mark.parametrize("text", [
    "", "   ", "\n\t  ", "؟؟؟", "!!!", "zzq explaining the unusual violet badgers",
    "%%%^^^&&&", "\U0001f642", "ا", "0", "?", "   ,,,   ", "12345!@#$",
])
def test_classify_task_class_robustness_on_empty_and_garbage(text):
    value = classify_task_class(text)
    assert value in INTENT_CLASSES, (text, value)
    assert value != ""


def test_classify_task_class_survives_a_non_string():
    for value in (None, 0, [], {}):
        assert classify_task_class(value) in INTENT_CLASSES, value


def test_every_intent_class_is_reachable():
    """The class token is a real partition, not a label with dead members."""
    seen = {
        classify_task_class(PINNED_FOUNDER_PROMPT),
        classify_task_class("scrap this instagram account @acme_test"),
        classify_task_class("what needs my attention?"),
        classify_task_class("why did we choose agencies?"),
        classify_task_class("what is our positioning strategy?"),
        classify_task_class("what this chat talking about"),
    }
    assert seen == {
        "COMPOUND_MARKETING_TASK", "SINGLE_CAPABILITY", "PROJECT_STATE",
        "KNOWLEDGE", "MARKETING_REASONING", "CONVERSATION_META"}, seen


# ------------------------------------------------- branch dispatch (§2/§3)

def test_compound_branch_never_falls_through_to_tool_capability(
        tmp_path, monkeypatch):
    from app.database.seed import ensure_seed
    from app.database.sqlite import connect

    conn = connect(tmp_path / "dev008hf-classification.db")
    ensure_seed(conn)
    called: list[dict] = []
    monkeypatch.setattr(
        "app.graphs.tool_capability.resolve_and_execute",
        lambda *a, **kw: called.append(kw) or {"answer": "TOOL-RAN",
                                                "tool_run_id": "tr-x",
                                                "social_results": []})
    from app.graphs import account_manager as am

    out = am.run_graph(conn, root="", project_id="starter",
                       conversation_id="c1", turn_id="t-compound",
                       user_text=PINNED_FOUNDER_PROMPT)
    assert out["route"] == "compound_marketing_task", out["route"]
    assert not called, "a compound turn must not execute a single capability"
    assert out["errors"] == [], out["errors"]
    assert set(out["state"]["compound_domains"]) == set(CONTRACT_DOMAINS)
    labels = [getattr(e, "label", "") for e in out["events"]]
    assert "Compound marketing task" in labels, labels
    # the missing graph-side node is a recorded limitation, never a silent
    # fallback answer and never a fatal turn error
    assert out["limitations"], out
    assert out["final_answer"]
    conn.close()


def test_single_capability_still_executes_the_capability(tmp_path, monkeypatch):
    from app.database.seed import ensure_seed
    from app.database.sqlite import connect

    conn = connect(tmp_path / "dev008hf-single.db")
    ensure_seed(conn)
    called: list[dict] = []
    monkeypatch.setattr(
        "app.graphs.tool_capability.resolve_and_execute",
        lambda *a, **kw: called.append(kw) or {"answer": "TOOL-RAN",
                                                "tool_run_id": "tr-y",
                                                "social_results": []})
    from app.graphs import account_manager as am

    out = am.run_graph(conn, root="", project_id="starter",
                       conversation_id="c1", turn_id="t-single",
                       user_text="scrap this instagram account @acme_test")
    assert out["route"] == "tool_capability", out["route"]
    assert called, "a single capability must still reach the tool registry"
    assert called[0]["capability"] == "instagram_public_profile"
    assert out["final_answer"] == "TOOL-RAN"
    assert out["limitations"] == []
    conn.close()


def test_compound_runner_probe_is_defensive_and_never_invents_a_path():
    from app.graphs.account_manager import (_compound_unavailable,
                                            resolve_compound_runner)

    probe = resolve_compound_runner()
    assert probe is None or callable(probe)
    if probe is None:
        out = _compound_unavailable("x", ["seo"])
        assert out["reply_md"] and out["limitations"]
        assert out["provenance"] == [] and out["job_ids"] == []


def test_branch_and_node_lists_carry_the_compound_route():
    from app.graphs.account_manager import BRANCHES, NODES, route_targets

    assert "compound_marketing_task" in BRANCHES
    assert "compound_marketing_task" in NODES
    assert route_targets("compound_marketing_task") == [
        "compound_marketing_task"]
    assert route_targets("nonsense") == ["state_only"]
