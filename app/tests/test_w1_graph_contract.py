"""DEV-007R-HOTFIX-3 W1 — frozen graph contract (state fields + labels).

The compact lockfile W2 relies on: state field set, the labeled model-context
sections, and the deterministic intent export surface.
"""


def test_state_contract_fields_present():
    from app.graphs.state import GRAPH_STATE_FIELDS, initial_state

    for field in ("conversation_context", "project_state", "intent_type",
                  "context_sources_used", "rag_invoked", "rag_query",
                  "rag_hits", "rag_debug", "tool_capability_name"):
        assert field in GRAPH_STATE_FIELDS, field
    state = initial_state(project_id="starter", conversation_id="c",
                          turn_id="t", user_request="hi")
    assert state["rag_invoked"] is False
    assert state["rag_hits"] == []
    assert state["conversation_context"] == []
    assert state["project_state"] == {}
    assert state["context_sources_used"] == []
    assert state["tool_capability_name"] == ""


def test_intent_surface_exports_website_intent():
    from app.graphs.intent import detect_website_intent

    cap = detect_website_intent("analyze acme-test.example")
    assert cap["capability"] == "website_marketing_audit"
    assert cap["intent_type"] == "TOOL_CAPABILITY"
    assert detect_website_intent("recap the strategy") is None


def test_meta_examples_match_frozen_contract():
    from app.graphs.intent import detect_conversation_meta

    assert detect_conversation_meta(
        "عايز ملخص ريكاب عن كل حاجة حصلت في الشات ده")
    assert not detect_conversation_meta("ملخص الكامبين")
    assert not detect_conversation_meta("summarize the report")


def test_labeled_model_context_sections_are_frozen():
    from app.routes.graph_runtime import build_context_text

    text = build_context_text({
        "project_state": {"name": "Acme Test Company",
                          "website": "https://acme-test.example",
                          "goal": "Get more customers",
                          "instagram_handle": "acme_test"},
        "conversation_context": [
            {"role": "user", "body_md": "hi"},
            {"role": "assistant", "body_md": "we planned the launch"},
        ],
        "tool_results": [{"path": "@acme_test", "chunk_id": "run-1"}],
        "rag_evidence": [{"path": "knowledge/brand.md", "chunk_id": "c1",
                          "text": "Acme Test Company builds custom websites."}],
        "context_sources_used": ["user_turn", "conversation_context"],
        "route": "knowledge",
    })
    assert "[PROJECT CONTEXT] source: project_state" in text
    assert "Acme Test Company" in text
    assert "[CONVERSATION SO FAR] source: conversation_context" in text
    assert "Assistant: we planned the launch" in text
    assert "[TOOL RESULTS THIS TURN] source: tool_results" in text
    assert "[PROJECT KNOWLEDGE] source: project_rag" in text
    assert "SOURCE: knowledge/brand.md#c1" in text


def test_honesty_prompt_terms_present():
    from app.routes.graph_runtime import _HONESTY_PROMPT

    assert "SOURCE PRIORITY" in _HONESTY_PROMPT
    assert "HONESTY" in _HONESTY_PROMPT
