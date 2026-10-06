"""DEV-005 W1 — LangGraph Account Manager (graph layer only).

v1 PRODUCT PATH (framework-first, requires langgraph — see workers/w1.md
for the exact I1 package list):
- app.graphs.account_manager_graph.build_account_manager_graph()
- app.graphs.deep_research_graph.build_deep_research_graph()
- app.graphs.approval_flow.build_approval_graph()
- app.graphs.state.LangGraphState (reducer-annotated)

LEGACY/TEST FALLBACK (no langgraph dependency, never the v1 path):
- app.graphs.account_manager.run_graph()
- app.graphs.deep_research (ThreadPoolExecutor fan-out)
- app.graphs.approvals (pure interrupt/resume helpers)

W1 owns app/graphs/. No FastAPI routes, React, RAG internals,
provider router, or cutover logic live here. All external
capabilities (tools, state, RAG) are reached through thin
dependency-injected adapters.
"""
