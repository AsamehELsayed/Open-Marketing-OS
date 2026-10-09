"""Privacy-preserving evidence projection used by the DEV-023 test fixture.

The original acceptance helper lived under the ignored ``development/`` tree,
so public source checkouts could not import it. Keep the small test-only
projection beside the tests that exercise its persisted-turn contract.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_RUNTIME_FIELDS = (
    "retrieval_mode",
    "lexical_hits",
    "vector_hits",
    "fused_hits",
    "selected_chunk_ids",
    "source_file_ids",
)


def capture_persisted_turn(
    conn,
    *,
    project_id: str,
    conversation_id: str,
    turn_id: str,
    assistant_message_id: str,
    output_path: str | Path,
) -> dict[str, Any]:
    """Write a scoped turn record without copying retrieved document text."""
    turn = conn.execute(
        "SELECT project_id, conversation_id, user_message_id FROM turns WHERE id = ?",
        (turn_id,),
    ).fetchone()
    if (turn is None or turn["project_id"] != project_id
            or turn["conversation_id"] != conversation_id):
        raise ValueError("turn is missing or outside the requested project/conversation")

    user = conn.execute(
        "SELECT id, body_md FROM messages WHERE id = ? AND conversation_id = ? AND role = 'user'",
        (turn["user_message_id"], conversation_id),
    ).fetchone()
    assistant = conn.execute(
        "SELECT id, body_md, citations_json FROM messages "
        "WHERE id = ? AND conversation_id = ? AND role = 'assistant'",
        (assistant_message_id, conversation_id),
    ).fetchone()
    if user is None or assistant is None:
        raise ValueError("turn messages are missing or outside the requested conversation")

    event = conn.execute(
        "SELECT metadata_json FROM execution_events "
        "WHERE turn_id = ? AND project_id = ? AND event_type = 'model_completed' "
        "ORDER BY id DESC LIMIT 1",
        (turn_id, project_id),
    ).fetchone()
    metadata = json.loads(event["metadata_json"] or "{}") if event else {}
    telemetry = metadata.get("retrieval_telemetry")
    telemetry = telemetry if isinstance(telemetry, dict) else {}

    citations = json.loads(assistant["citations_json"] or "[]")
    if not isinstance(citations, list):
        citations = []
    safe_citations = [
        {key: item[key] for key in ("source_file_id", "source_ref", "title") if key in item}
        for item in citations
        if isinstance(item, dict)
    ]
    payload = {
        "schema": "omos.turn-evidence.v1",
        "project_id": project_id,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "user_message": {"id": user["id"], "text": user["body_md"]},
        "assistant_message": {
            "id": assistant["id"],
            "text": assistant["body_md"],
            "citations": safe_citations,
        },
        "runtime": {key: telemetry[key] for key in _RUNTIME_FIELDS if key in telemetry},
        "excluded_fields": ["retrieved_text"],
    }
    Path(output_path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload
