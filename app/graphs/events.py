"""Graph → frozen SSE wire event mapping (W0 events contract).

Every node maps onto exactly one LEGACY_EVENT_TYPES wire type.
No new wire type may be emitted (Hyrum's Law guard for useTurnStream).
Metadata is sanitized: no prompts, systems, secrets, or reasoning.
"""
from app.contracts.events import GraphExecutionEvent
from app.database.identity import DEFAULT_PROJECT_ID

NODE_EVENT_MAP = {
    "load_project": "state_read",
    "load_conversation": "context_completed",
    "understand": "tool_completed",
    "state_only": "state_read",
    "knowledge": "rag_completed",
    "external_research": "web_completed",
    "social_research": "instagram_provider_completed",
    "deep_research": "delegation_started",
    "campaign_operation": "tool_completed",
    "approval_operation": "approval_required",
    "job_followup": "state_read",
    "aggregate": "tool_completed",
    "synthesize": "synthesis_started",
    "approval-interrupt": "approval_required",
    "respond": "turn_completed",
    "respond_failed": "turn_failed",
    # --- execution-tree block (DEV-008-SKILLS-OPS §1.3.1, §3.4 W3) ---
    # W4 introduces these nodes. Before this row existed, graph_runtime._emit
    # fell through build_event's `tool_completed` default for all seven, so
    # they were emitted on the wire as generic tool completions. Each node now
    # maps onto the wire type that shares its name stem, so _emit can emit
    # them without an ADR. Terminal/failure variants of a stem
    # (employee_completed / employee_failed, tool_completed / tool_failed) are
    # emitted through the `event_type=` override _emit already accepts, which
    # is how the existing approval_operation / approval-interrupt pair works.
    "skill_route": "skill_selected",
    "skill_load": "skill_loaded",
    "employee_fanout": "employee_queued",
    "employee": "employee_started",
    "employee_fanin": "employee_completed",
    "evidence_collect": "evidence_added",
    "synthesis_complete": "synthesis_completed",
}

NODE_LABELS = {
    "load_project": "Reading project information",
    "load_conversation": "Project context loaded",
    "understand": "Understanding your request",
    "state_only": "Reading project information",
    "knowledge": "Internal knowledge searched",
    "external_research": "Web research finished",
    "social_research": "Instagram checked",
    "deep_research": "Delegating to Marketing PM",
    "campaign_operation": "Proposal prepared",
    "approval_operation": "Waiting for your approval",
    "job_followup": "Checking background work",
    "aggregate": "Combining evidence",
    "synthesize": "Writing response",
    "approval-interrupt": "Waiting for your approval",
    "respond": "Response ready",
    "respond_failed": "Something interrupted this run.",
    "skill_route": "Choosing the right playbooks",
    "skill_load": "Loaded playbook",
    "employee_fanout": "Assigning the work",
    "employee": "Team member working",
    "employee_fanin": "Team work combined",
    "evidence_collect": "Gathering evidence",
    "synthesis_complete": "Response written",
}


def build_event(node: str, *, project_id: str, turn_id: str,
                conversation_id: str = "", job_id: str = "",
                label: str = "", detail: str = "",
                metadata: dict | None = None) -> GraphExecutionEvent:
    event_type = NODE_EVENT_MAP.get(node, "tool_completed")
    return GraphExecutionEvent(
        project_id=project_id or DEFAULT_PROJECT_ID,
        conversation_id=conversation_id or "",
        turn_id=turn_id or "",
        job_id=job_id or "",
        event_type=event_type,
        label=(label or NODE_LABELS.get(node, ""))[:300],
        detail=(detail or "")[:6000],
        metadata=dict(metadata or {}),
    )
