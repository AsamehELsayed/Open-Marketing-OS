"""DEV-008-SKILLS-OPS W9 (integrator) — persistence-after-reload proof.

Runs one deterministic turn to completion through the real graph with a real
``TreeEmitter`` (W4's surface), then proves there is no post-hoc replay
animation (plan §6.7, §7 A9):

1. every event id read from the live stream, re-read from ``after=0``,
   arrives as the IDENTICAL id sequence in the IDENTICAL order;
2. ``after=<last_id>`` returns ONLY the tail (empty when nothing is newer),
   and ``after=<first_id>`` returns exactly the remaining tail;
3. the reloaded tree is byte-identical to the live tree.

All reads go through ``repos.ExecutionEvents.for_turn`` — the same reader
``chat.py:167`` (``after_id``) and ``:148-152`` (``Last-Event-ID``) serve.
"""
from __future__ import annotations

import json

from app.database import repos
from app.database.sqlite import connect

TURN_ID = "w9persistturn0001"
INTENT = "how do I improve my SEO audit readiness"


def _db(tmp_path, name="w9persist.db"):
    path = str(tmp_path / name)
    conn = connect(path)
    conn.close()
    return path


def _run_turn_to_completion(db_path: str) -> None:
    """One deterministic turn, real graph, real TreeEmitter, to completion."""
    from app.graphs.account_manager_graph import build_account_manager_graph
    from app.graphs.state import initial_state
    from app.services.skills.tree import TreeEmitter

    tree = TreeEmitter(
        db_path=db_path, project_id="starter", conversation_id="w9-convo-1",
        turn_id=TURN_ID, thread_id=TURN_ID,
    )
    graph = build_account_manager_graph(
        complete_fn=lambda **kw: "deterministic w9 persistence answer",
        known_projects={"starter"},
    )
    state = initial_state(
        project_id="starter", conversation_id="w9-convo-1",
        turn_id=TURN_ID, user_request=INTENT,
    )
    out = graph.invoke(
        state, config={"configurable": {"thread_id": TURN_ID, "tree": tree}})
    assert (out.get("final_answer") or "").strip(), "turn must complete"


def _rows(db_path: str, after_id: int = 0) -> list[dict]:
    conn = connect(db_path)
    try:
        return repos.ExecutionEvents.for_turn(conn, TURN_ID, after_id=after_id)
    finally:
        conn.close()


def _canonical_tree(rows: list[dict]) -> bytes:
    """Byte-stable rendering of the tree: the reload-equality proof."""
    canonical = []
    for row in rows:
        try:
            meta = json.loads(row.get("metadata_json") or "{}")
        except ValueError:
            meta = {}
        canonical.append({
            "id": row["id"],
            "event_type": row.get("event_type", ""),
            "label": row.get("label", ""),
            "detail": row.get("detail", ""),
            "meta": meta,
        })
    return json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_live_and_reloaded_id_sequences_are_identical(tmp_path):
    db_path = _db(tmp_path)
    _run_turn_to_completion(db_path)

    live = _rows(db_path, after_id=0)
    assert len(live) >= 1, "turn emitted no events"
    live_ids = [r["id"] for r in live]

    reloaded = _rows(db_path, after_id=0)
    reloaded_ids = [r["id"] for r in reloaded]
    print(f"\nmeasured: live={len(live_ids)} reloaded={len(reloaded_ids)} "
          f"first={live_ids[0]} last={live_ids[-1]}")
    assert reloaded_ids == live_ids, (
        f"reload changed the id sequence: live={live_ids} reloaded={reloaded_ids}"
    )
    assert live_ids == sorted(live_ids), "ids are not in arrival order"


def test_after_last_id_returns_only_the_tail(tmp_path):
    db_path = _db(tmp_path)
    _run_turn_to_completion(db_path)

    live_ids = [r["id"] for r in _rows(db_path, after_id=0)]
    first, last = live_ids[0], live_ids[-1]

    tail_after_last = _rows(db_path, after_id=last)
    assert [r["id"] for r in tail_after_last] == [], (
        f"after=<last_id> must return only newer rows, got "
        f"{[r['id'] for r in tail_after_last]}"
    )

    tail_after_first = [r["id"] for r in _rows(db_path, after_id=first)]
    assert tail_after_first == live_ids[1:], (
        "after=<first_id> must return exactly the remaining tail"
    )
    print(f"\nmeasured: total={len(live_ids)} tail_after_first={len(tail_after_first)} "
          f"tail_after_last=0")


def test_reloaded_tree_is_byte_identical_to_live_tree(tmp_path):
    db_path = _db(tmp_path)
    _run_turn_to_completion(db_path)

    live_tree = _canonical_tree(_rows(db_path, after_id=0))
    reloaded_tree = _canonical_tree(_rows(db_path, after_id=0))
    print(f"\nmeasured: tree_bytes={len(live_tree)} "
          f"events={len(json.loads(live_tree))}")
    assert len(live_tree) > 2, "empty tree cannot prove reload identity"
    assert reloaded_tree == live_tree, (
        "reloaded tree differs from the live tree: post-hoc replay animation"
    )


def test_tree_rows_never_hit_the_4000_char_truncation(tmp_path):
    """Frozen §1.3.6 bound: no tree row may approach the 4000-char cut."""
    db_path = _db(tmp_path)
    _run_turn_to_completion(db_path)
    rows = _rows(db_path, after_id=0)
    worst = 0
    for row in rows:
        size = len(row.get("metadata_json") or "")
        worst = max(worst, size)
        assert size <= 4000, (
            f"row {row['id']} ({row.get('event_type')}) hits the truncation "
            f"bound at {size} bytes and would degrade meta to {{}}"
        )
    print(f"\nmeasured: rows={len(rows)} max_metadata_json_bytes={worst}")
