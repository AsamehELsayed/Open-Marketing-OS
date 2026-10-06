"""DEV-008-SKILLS-OPS W9 (integrator) — two-sided event-registration gate.

Closes the pre-existing defect where 13 of 26 event types never reached the
SPA: the server always sends a NAMED ``event:`` field (``chat.py:185``) and
the client's ``src.onmessage`` fallback cannot catch named events, so every
name the server may emit must be registered by name in
``frontend/src/api/client.ts``.

This is the ONLY test that reads a file W3 owns
(``app/contracts/events.py``) and a file W7 owns
(``frontend/src/api/client.ts``) at once — neither worker could see the
other, so the gate lives with the integrator (plan §6.2, §7 A13).

Both directions are asserted: nothing the server emits is missing from the
client, and nothing the client listens for is absent from the server.
Additionally, no registered type may be a stream-closer in a way that would
close the SSE before the tree finishes arriving.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.contracts.events import LEGACY_EVENT_TYPES, TERMINAL_EVENT_TYPES

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_TS = REPO_ROOT / "frontend" / "src" / "api" / "client.ts"

# The 15 mission execution-tree types: 11 new (W3) + 4 pre-existing-but-
# SPA-invisible (W7 gap fix). All 15 must be registered on both sides.
TREE_TYPES_15 = (
    "account_manager_started", "task_planned",
    "employee_queued", "employee_started", "employee_completed",
    "employee_failed", "skill_selected", "skill_loaded",
    "tool_started", "tool_completed", "tool_failed",
    "evidence_added", "synthesis_started", "synthesis_completed",
    "approval_required",
)

# The four types that terminate the client stream (client.ts
# STREAM_CLOSING_EVENT_TYPES). A tree type landing in this set would close
# the SSE mid-tree.
STREAM_CLOSERS = (
    "turn_completed", "turn_failed", "turn_closed", "stream_end",
)


def _extract_const_array(source: str, const_name: str) -> tuple[str, ...]:
    """Extract string literals from an exported const array in client.ts."""
    pattern = re.compile(
        r"export\s+const\s+" + re.escape(const_name)
        + r"\s*(?::[^=]*)?=\s*\[(.*?)\]\s*as\s+const",
        re.S,
    )
    match = pattern.search(source)
    assert match is not None, (
        f"client.ts has no exported const {const_name!r} array"
    )
    return tuple(re.findall(r'"([a-z0-9_]+)"', match.group(1)))


def _client_source() -> str:
    assert CLIENT_TS.exists(), f"client.ts absent: {CLIENT_TS}"
    return CLIENT_TS.read_text(encoding="utf-8")


def test_server_catalog_has_37_types():
    print(f"\nmeasured: len(LEGACY_EVENT_TYPES) = {len(LEGACY_EVENT_TYPES)}")
    assert len(LEGACY_EVENT_TYPES) == 37, (
        f"expected the frozen 26 + 11 = 37 contract, got {len(LEGACY_EVENT_TYPES)}"
    )
    assert len(set(LEGACY_EVENT_TYPES)) == 37, "duplicate names in LEGACY_EVENT_TYPES"


def test_client_listener_array_equals_server_catalog_both_directions():
    """set(LEGACY_EVENT_TYPES) == set(client.ts listener array)."""
    client_names = _extract_const_array(_client_source(), "LEGACY_EVENT_TYPES")
    server = set(LEGACY_EVENT_TYPES)
    client = set(client_names)
    print(f"\nmeasured: server={len(server)} client_unique={len(client)} "
          f"client_literals={len(client_names)}")
    missing_from_client = sorted(server - client)
    extra_on_client = sorted(client - server)
    assert not missing_from_client, (
        f"server emits {len(missing_from_client)} type(s) the SPA never "
        f"listens for (the pre-existing 13-type gap, reopened): "
        f"{missing_from_client}"
    )
    assert not extra_on_client, (
        f"client listens for {len(extra_on_client)} type(s) the server "
        f"never emits: {extra_on_client}"
    )
    assert len(client_names) == len(client), (
        "duplicate literals in the client listener array"
    )


def test_all_15_mission_types_registered_on_both_sides():
    client_names = set(_extract_const_array(_client_source(), "LEGACY_EVENT_TYPES"))
    server = set(LEGACY_EVENT_TYPES)
    for name in TREE_TYPES_15:
        assert name in server, f"mission type {name!r} missing server-side"
        assert name in client_names, f"mission type {name!r} missing client-side"


def test_turn_summary_absent_on_both_sides():
    client_names = set(_extract_const_array(_client_source(), "LEGACY_EVENT_TYPES"))
    assert "turn_summary" not in LEGACY_EVENT_TYPES
    assert "turn_summary" not in client_names


def test_no_registered_type_closes_the_stream_early():
    """No tree/registered type may be a stream-closer.

    The client closes the SSE the moment it handles a type in
    STREAM_CLOSING_EVENT_TYPES. If any execution-tree type were in that
    set, the stream would close before the remaining rows arrive.
    """
    closers = set(_extract_const_array(_client_source(), "STREAM_CLOSING_EVENT_TYPES"))
    print(f"\nmeasured: stream_closers={sorted(closers)}")
    assert closers == set(STREAM_CLOSERS), (
        f"closing set drifted: {sorted(closers)} != {sorted(STREAM_CLOSERS)}"
    )
    for name in TREE_TYPES_15:
        assert name not in closers, (
            f"tree type {name!r} would close the stream early"
        )
    # The server's own terminal pair must stay inside the client's closer set
    # (otherwise the client would never resolve), and nothing else registered
    # may join it.
    for name in TERMINAL_EVENT_TYPES:
        assert name in closers, f"server terminal {name!r} not in client closers"
    registered_closers = set(LEGACY_EVENT_TYPES) & closers
    assert registered_closers == set(STREAM_CLOSERS), (
        f"registered types that close the stream: {sorted(registered_closers)}"
    )


def test_stream_listener_iterates_the_catalog_not_a_copy():
    """streamTurnEvents must iterate LEGACY_EVENT_TYPES, not an inline list."""
    source = _client_source()
    assert re.search(
        r"for\s*\(\s*const\s+\w+\s+of\s+LEGACY_EVENT_TYPES\s*\)", source
    ), "streamTurnEvents no longer iterates the exported catalog array"
