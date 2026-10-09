"""Typed graph state. Field names are normative (docs/v1/architecture.md §4).

Python state stays native dicts/TypedDict — TOON crosses only the
model semantic boundary (never graph state). List fields use
append/union reducer semantics in nodes (never in-place aliasing).
"""
from typing import Annotated, TypedDict

import operator


class GraphState(TypedDict, total=False):
    project_id: str
    conversation_id: str
    turn_id: str
    user_request: str
    model_provider: str
    model_id: str
    project_context: dict
    state_results: dict
    retrieved_evidence: list
    web_results: list
    social_results: list
    research_tasks: list
    worker_results: list
    approval_state: dict
    model_route: str
    model_usage: dict
    errors: list
    final_answer: str
    task_plan: dict
    write_intents: dict
    write_constraints: dict
    write_results: dict
    compound_domains: list
    employee_results: list


GRAPH_STATE_FIELDS = (
    "project_id",
    "conversation_id",
    "turn_id",
    "user_request",
    "model_provider",
    "model_id",
    "project_context",
    "state_results",
    "retrieved_evidence",
    "web_results",
    "social_results",
    "research_tasks",
    "worker_results",
    "approval_state",
    "model_route",
    "model_usage",
    "errors",
    "final_answer",
    "conversation_context",
    "project_state",
    "intent_type",
    "context_sources_used",
    "rag_invoked",
    "rag_query",
    "rag_hits",
    "citation_evidence",
    "rag_debug",
    "retrieval_telemetry",
    "tool_capability_name",
    # --- execution-tree block (DEV-008-SKILLS-OPS §1.3, §3.4 W3) ---
    "skill_ids",
    "skill_reason_codes",
    "skill_selection_reason",
    "employee_roles",
    "evidence",
    "foundation_injected",
    # --- compound orchestration block (DEV-008-SKILLS-OPS-HOTFIX §3) ---
    "task_plan",
    "write_intents",
    "write_constraints",
    "write_results",
    "compound_domains",
    "employee_results",
)

# Fields merged with append/union semantics (LangGraph reducer equivalent).
LIST_FIELDS = frozenset({
    "retrieved_evidence",
    "web_results",
    "social_results",
    "research_tasks",
    "worker_results",
    "errors",
    # execution-tree block: fan-out/fan-in branches each contribute items,
    # so these must merge rather than last-write-wins.
    "skill_ids",
    "skill_reason_codes",
    "employee_roles",
    "evidence",
    "compound_domains",
    "employee_results",
})


def initial_state(*, project_id: str, conversation_id: str,
                  turn_id: str, user_request: str,
                  model_provider: str = "AUTO", model_id: str = "") -> GraphState:
    return {
        "project_id": project_id,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "user_request": user_request,
        "model_provider": model_provider,
        "model_id": model_id,
        "project_context": {},
        "state_results": {},
        "retrieved_evidence": [],
        "web_results": [],
        "social_results": [],
        "research_tasks": [],
        "worker_results": [],
        "approval_state": {},
        "model_route": "",
        "model_usage": {},
        "errors": [],
        "final_answer": "",
        "conversation_context": [],
        "project_state": {},
        "intent_type": "",
        "context_sources_used": [],
        "rag_invoked": False,
        "rag_query": "",
        "rag_hits": [],
        "citation_evidence": [],
        "rag_debug": {},
        "retrieval_telemetry": {},
        "tool_capability_name": "",
        "skill_ids": [],
        "skill_reason_codes": [],
        "skill_selection_reason": "",
        "employee_roles": [],
        "evidence": [],
        "foundation_injected": False,
        "task_plan": {},
        "write_intents": {},
        "write_constraints": {"forbidden_actions": [], "analysis_only": False},
        "write_results": {},
        "compound_domains": [],
        "employee_results": [],
        "attachment_evidence": [],
    }


def merge_lists(current: list, incoming: list) -> list:
    """Reducer: append incoming items onto a fresh list (no aliasing)."""
    return list(current or []) + list(incoming or [])


def _add_unique(current: list, incoming: list) -> list:
    """Reducer: append-only union preserving first-seen order."""
    out = list(current or [])
    for item in incoming or []:
        if item not in out:
            out.append(item)
    return out


class LangGraphState(TypedDict, total=False):
    """v1 framework runtime state: identical fields, reducer-annotated lists.

    Normative field set equals GRAPH_STATE_FIELDS (architecture §4).
    List fields use LangGraph reducers so Send fan-out/fan-in merges
    branch outputs instead of last-write-wins. Scalar fields overwrite.
    """

    project_id: str
    conversation_id: str
    turn_id: str
    user_request: str
    model_provider: str
    model_id: str
    project_context: dict
    state_results: dict
    retrieved_evidence: Annotated[list, operator.add]
    web_results: Annotated[list, operator.add]
    social_results: Annotated[list, operator.add]
    research_tasks: Annotated[list, operator.add]
    worker_results: Annotated[list, operator.add]
    approval_state: dict
    model_route: str
    route: str
    intent: dict
    tool_run_id: str
    capability_answer: str
    model_usage: dict
    errors: Annotated[list, operator.add]
    final_answer: str
    conversation_context: Annotated[list, operator.add]
    project_state: dict
    intent_type: str
    context_sources_used: list
    rag_invoked: bool
    rag_query: str
    rag_hits: Annotated[list, operator.add]
    citation_evidence: list
    rag_debug: dict
    retrieval_telemetry: dict
    tool_capability_name: str
    # --- execution-tree block (DEV-008-SKILLS-OPS §1.3, §3.4 W3) ---
    # Reducer kind is stated per field, because it is a correctness decision
    # and not a style preference:
    #   skill_ids           operator.add  — one Send branch per employee, each
    #                                        contributes the skills it loaded;
    #                                        last-write-wins would drop all
    #                                        but one employee's playbooks.
    #   skill_reason_codes  operator.add  — REASON_CODES (§1.4.2) is a tuple,
    #                                        so the router emits several.
    #   employee_roles      operator.add  — one entry per fan-out role; the
    #                                        fan-in must union, not overwrite.
    #   evidence            operator.add  — evidence_collect runs per employee.
    #   skill_selection_reason  plain str  — exactly one decision per turn, so
    #                                        last-write-wins is the correct
    #                                        semantic. A reducer would union
    #                                        two conflicting reasons into one
    #                                        unreadable value.
    #   foundation_injected     plain bool — a turn-level fact set once by the
    #                                        router (§1.4.5). Accumulating it
    #                                        would be meaningless.
    skill_ids: Annotated[list, operator.add]
    skill_reason_codes: Annotated[list, operator.add]
    skill_selection_reason: str
    employee_roles: Annotated[list, operator.add]
    evidence: Annotated[list, operator.add]
    foundation_injected: bool
    # --- compound orchestration block (DEV-008-SKILLS-OPS-HOTFIX §3) ---
    task_plan: dict
    write_intents: dict
    write_constraints: dict
    write_results: dict
    compound_domains: Annotated[list, operator.add]
    employee_results: Annotated[list, operator.add]
    attachment_evidence: list


def citation_projection(*, project_id: str, rag_hits=None,
                        attachment_evidence=None) -> list[dict]:
    """Project graph evidence into history-safe citation metadata.

    Extracted text is deliberately excluded. RAG rows must carry their actual
    project scope; file identity is never inferred from model prose.
    """
    from app.contracts.events import sanitize_user_text

    pid = str(project_id or "").strip()
    citations: list[dict] = []

    def clean(item: dict, *, source: str, project_scope: bool) -> dict:
        citation = {"source": source}
        for key, limit in (
            ("file_id", 128), ("file_name", 240), ("filename", 240),
            ("project_id", 64), ("conversation_id", 128),
            ("chunk_id", 160), ("document_id", 128),
            ("source_id", 128), ("path", 500),
        ):
            value = item.get(key)
            if value is not None and str(value).strip():
                citation[key] = sanitize_user_text(str(value), limit)
        branches = item.get("sources")
        if isinstance(branches, (list, tuple)):
            safe_branches = [str(value) for value in branches
                             if str(value) in ("fts", "semantic")]
            if safe_branches:
                citation["retrieval_sources"] = list(dict.fromkeys(safe_branches))
        if project_scope:
            citation["scope"] = "project"
        return citation

    for item in attachment_evidence or []:
        if (isinstance(item, dict) and item.get("source") == "turn_attachment"
                and (not pid or item.get("project_id") == pid)):
            citations.append(clean(item, source="turn_attachment", project_scope=False))

    for item in rag_hits or []:
        if (not isinstance(item, dict) or not pid
                or str(item.get("project_id") or "").strip() != pid):
            continue
        if not str(item.get("path") or "").strip() or not str(item.get("chunk_id") or "").strip():
            continue
        citations.append(clean(item, source="project_rag", project_scope=True))

    seen = set()
    out = []
    for citation in citations:
        key = (citation.get("source"), citation.get("file_id"),
               citation.get("path"), citation.get("chunk_id"))
        if key not in seen:
            seen.add(key)
            out.append(citation)
    return out


def retrieve_project_citations(conn, *, project_id: str, query: str) -> list[dict]:
    """Build citation metadata from the same scoped retrieval index.

    Used as a completion boundary fallback when a graph node has consumed RAG
    context but a downstream state update omitted its citation projection.
    It never projects extracted text or fabricates missing source identifiers.
    """
    from app.graphs.adapters import RetrievalAdapter

    pid = str(project_id or "").strip()
    if not pid or not str(query or "").strip():
        return []
    try:
        result = RetrievalAdapter(conn, pid).retrieve(str(query))
    except Exception:
        return []
    hits = []
    for hit in getattr(result, "hits", None) or []:
        if hasattr(hit, "model_dump"):
            try:
                hits.append(dict(hit.model_dump()))
                continue
            except Exception:
                pass
        if isinstance(hit, dict):
            hits.append(dict(hit))
    return citation_projection(project_id=pid, rag_hits=hits)
