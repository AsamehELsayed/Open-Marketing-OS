from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import threading
import time

import pytest

from app.database import sqlite
from app.services import campaign_flow


def _setup_test_campaign(db_path: Path, project_id="p1", campaign_id="c1", title="Test Campaign"):
    conn = sqlite.connect(db_path)
    conn.execute(
        "INSERT INTO projects (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (project_id, "Test Project", "2026-10-28", "2026-10-28"),
    )
    conn.execute(
        "INSERT INTO campaigns (id, project_id, title, status, updated_at, workflow_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (campaign_id, project_id, title, "active", "2026-10-28", "{}"),
    )
    conn.commit()
    conn.close()


def test_rejected_request_must_never_release_another_requests_lock(tmp_path):
    db_file = tmp_path / "marketing.db"
    _setup_test_campaign(db_file)
    conn1 = sqlite.connect(db_file)
    conn2 = sqlite.connect(db_file)

    try:
        # Owner 1 acquires lock
        lease1 = campaign_flow._acquire_db_lock(conn1, "p1", "c1", "turn-1")
        assert lease1 is not None

        # Verify Owner 1's lock in DB
        row = conn1.execute("SELECT workflow_json FROM campaigns WHERE id='c1'").fetchone()
        meta = json.loads(row[0])
        assert meta["generation_in_progress"]["turn_id"] == "turn-1"

        # Conn 2 attempts generate_from_plan and is rejected
        with pytest.raises(campaign_flow.FlowError) as exc_info:
            campaign_flow.generate_from_plan(conn2, project_id="p1", campaign_id="c1")
        assert exc_info.value.status == 409
        assert exc_info.value.code == "generation_in_progress"

        # REGRESSION ASSERTION: Conn 2's rejection must NOT have cleared Conn 1's lock!
        row_after = conn1.execute("SELECT workflow_json FROM campaigns WHERE id='c1'").fetchone()
        meta_after = json.loads(row_after[0])
        assert "generation_in_progress" in meta_after, "Rejected request wiped out Owner 1's lock!"
        assert meta_after["generation_in_progress"]["turn_id"] == "turn-1"
    finally:
        conn1.close()
        conn2.close()


def test_running_provider_request_longer_than_180s_remains_protected(tmp_path):
    db_file = tmp_path / "marketing.db"
    _setup_test_campaign(db_file)
    conn1 = sqlite.connect(db_file)
    conn2 = sqlite.connect(db_file)

    try:
        # Owner 1 acquired 200s ago, but actively heartbeats/renews
        lease1 = campaign_flow._acquire_db_lock(conn1, "p1", "c1", "turn-1")
        # Simulate active heartbeat while started_at is >180s ago
        started_200s_ago = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
        fresh_heartbeat = datetime.now(timezone.utc).isoformat()
        row = conn1.execute("SELECT workflow_json FROM campaigns WHERE id='c1'").fetchone()
        meta = json.loads(row[0])
        meta["generation_in_progress"]["started_at"] = started_200s_ago
        meta["generation_in_progress"]["heartbeat_at"] = fresh_heartbeat
        conn1.execute("UPDATE campaigns SET workflow_json=? WHERE id='c1'", (json.dumps(meta),))
        conn1.commit()

        # Conn 2 attempts acquisition
        # REGRESSION ASSERTION: Conn 2 must NOT steal the lock simply because started_at > 180s!
        with pytest.raises(campaign_flow.FlowError) as exc_info:
            campaign_flow._acquire_db_lock(conn2, "p1", "c1", "turn-2")
        assert exc_info.value.status == 409
        assert exc_info.value.code == "generation_in_progress"
    finally:
        conn1.close()
        conn2.close()


def test_crashed_owner_recovers_safely(tmp_path):
    db_file = tmp_path / "marketing.db"
    _setup_test_campaign(db_file)
    conn1 = sqlite.connect(db_file)
    conn2 = sqlite.connect(db_file)

    try:
        # Owner 1 crashed: both started_at and heartbeat_at are long in the past
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        meta = {
            "generation_in_progress": {
                "owner_token": "crashed-token-123",
                "pid": 999999,
                "turn_id": "turn-crashed",
                "started_at": old_time,
                "heartbeat_at": old_time,
                "lease_timeout_s": 60,
            }
        }
        conn1.execute("UPDATE campaigns SET workflow_json=? WHERE id='c1'", (json.dumps(meta),))
        conn1.commit()

        # Conn 2 should recover safely and acquire a new lease
        lease2 = campaign_flow._acquire_db_lock(conn2, "p1", "c1", "turn-recovered", lease_timeout_s=60)
        assert lease2 is not None
        assert lease2 != "crashed-token-123"

        row = conn2.execute("SELECT workflow_json FROM campaigns WHERE id='c1'").fetchone()
        meta2 = json.loads(row[0])
        assert meta2["generation_in_progress"]["turn_id"] == "turn-recovered"
    finally:
        conn1.close()
        conn2.close()


def test_expired_owner_cannot_overwrite_or_delete_newer_owner(tmp_path):
    db_file = tmp_path / "marketing.db"
    _setup_test_campaign(db_file)
    conn1 = sqlite.connect(db_file)
    conn2 = sqlite.connect(db_file)

    try:
        lease1 = "expired-token-1"
        lease2 = "active-token-2"

        # Owner 2 currently owns the active lock
        now_iso = datetime.now(timezone.utc).isoformat()
        meta = {
            "generation_in_progress": {
                "owner_token": lease2,
                "pid": 2222,
                "turn_id": "turn-owner-2",
                "started_at": now_iso,
                "heartbeat_at": now_iso,
                "lease_timeout_s": 60,
            }
        }
        conn2.execute("UPDATE campaigns SET workflow_json=? WHERE id='c1'", (json.dumps(meta),))
        conn2.commit()

        # Expired Owner 1 attempts to release the lock with lease1
        released = campaign_flow._release_db_lock(conn1, "p1", "c1", lease_token=lease1)
        assert released is False, "Expired owner 1 was allowed to release!"

        # Verify Owner 2's lock is still intact in DB
        row = conn2.execute("SELECT workflow_json FROM campaigns WHERE id='c1'").fetchone()
        meta_check = json.loads(row[0])
        assert meta_check["generation_in_progress"]["owner_token"] == lease2

        # Expired Owner 1 attempts to verify lock for fencing
        assert campaign_flow._verify_db_lock(conn1, "p1", "c1", lease_token=lease1) is False
    finally:
        conn1.close()
        conn2.close()


def test_two_separate_connections_prevent_duplicate_calls(tmp_path, monkeypatch):
    db_file = tmp_path / "marketing.db"
    _setup_test_campaign(db_file)

    started = threading.Event()
    finish = threading.Event()
    provider_calls = 0
    call_lock = threading.Lock()

    def fake_generate(*args, **kwargs):
        nonlocal provider_calls
        with call_lock:
            provider_calls += 1
        started.set()
        assert finish.wait(timeout=5)
        return {"ok": True, "deliverables": []}

    monkeypatch.setattr(campaign_flow, "_generate_from_plan", fake_generate)

    # Thread 1 starts generating using its own connection
    def run_worker1():
        c1 = sqlite.connect(db_file)
        try:
            campaign_flow.generate_from_plan(
                c1, project_id="p1", campaign_id="c1", turn_id="turn-1"
            )
        finally:
            c1.close()

    thread1 = threading.Thread(target=run_worker1)
    thread1.start()
    assert started.wait(timeout=5)

    conn2 = sqlite.connect(db_file)
    try:
        # Thread 2 / Conn 2 attempts generating on the same campaign
        with pytest.raises(campaign_flow.FlowError) as exc_info:
            campaign_flow.generate_from_plan(
                conn2, project_id="p1", campaign_id="c1", turn_id="turn-2"
            )
        assert exc_info.value.status == 409
        assert exc_info.value.code == "generation_in_progress"
    finally:
        finish.set()
        thread1.join(timeout=5)
        conn2.close()

    assert provider_calls == 1, f"Expected exactly 1 provider call, got {provider_calls}"


def test_cross_process_db_lock_prevents_duplicate_generation(tmp_path, monkeypatch):
    """Simulate two completely separate OS processes sharing the same SQLite DB.

    In process 2, the Python in-memory threading lock dictionary is empty,
    so concurrency protection relies purely on SQLite atomic transaction leases.
    """
    db_file = tmp_path / "marketing.db"
    _setup_test_campaign(db_file)

    conn1 = sqlite.connect(db_file)
    conn2 = sqlite.connect(db_file)

    try:
        # Process 1 acquires DB lease
        lease1 = campaign_flow._acquire_db_lock(conn1, "p1", "c1", "proc1-turn")
        assert lease1 is not None

        # Process 2 has a fresh in-memory lock table (simulating independent process)
        with monkeypatch.context() as m:
            m.setattr(campaign_flow, "_GEN_LOCKS", {})
            with pytest.raises(campaign_flow.FlowError) as exc_info:
                campaign_flow.generate_from_plan(
                    conn2, project_id="p1", campaign_id="c1", turn_id="proc2-turn"
                )
            assert exc_info.value.status == 409
            assert exc_info.value.code == "generation_in_progress"

        # Ensure Process 1's lease was NOT released or altered by Process 2's failed attempt
        assert campaign_flow._verify_db_lock(conn1, "p1", "c1", lease_token=lease1) is True
    finally:
        conn1.close()
        conn2.close()


def test_expired_owner_cannot_write_state(tmp_path):
    """Demonstrate fencing: if owner's lease expires/stolen, _generate_from_plan refuses to write."""
    db_file = tmp_path / "marketing.db"
    _setup_test_campaign(db_file)
    conn = sqlite.connect(db_file)

    try:
        # Simulate that lease token belongs to old expired owner
        fake_stale_token = "stale-token-abc"
        with pytest.raises(campaign_flow.FlowError) as exc_info:
            campaign_flow._generate_from_plan(
                conn, project_id="p1", campaign_id="c1",
                turn_id="turn-stale", lease_token=fake_stale_token
            )
        assert exc_info.value.status == 409
        assert exc_info.value.code == "lease_expired"
    finally:
        conn.close()
