"""DEV-015 routing regressions for policy questions that need project RAG."""


def test_renewal_policy_question_routes_to_knowledge_not_state_only():
    from app.graphs.account_manager import classify_to_route
    from app.services import account_manager

    prompt = "Why is the renewal window thirty days under ORANGE-POLICY-927?"

    assert account_manager.classify(prompt) == account_manager.FLOW_EXPLAIN
    assert classify_to_route(prompt) == "knowledge"


def test_standalone_new_remains_a_changed_state_question():
    from app.graphs.account_manager import classify_to_route
    from app.services import account_manager

    assert account_manager.classify("What is new in this project?") == account_manager.FLOW_CHANGED
    assert classify_to_route("What is new in this project?") == "state_only"
