from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from app.database import sqlite
from app.services import campaign_flow
from app.services import campaign_production


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


def test_expired_owner_cannot_save_batch_if_lease_lost_during_generation(tmp_path, monkeypatch):
    """Demonstrate end-to-end write fencing: if lease expires/is preempted during model generation,

    generate_batch aborts before saving to DB and preserves existing deliverables.
    """
    from app.services.llm.model_router import ModelRouter
    from app.tests.campaign_fake_provider import DeterministicCampaignProvider

    db_file = tmp_path / "marketing.db"
    _setup_test_campaign(db_file)
    conn = sqlite.connect(db_file)

    provider = DeterministicCampaignProvider()
    router_obj = ModelRouter(local_provider=provider, default_local_model="deterministic-test")
    monkeypatch.setattr(campaign_production, "_router", lambda: router_obj)

    try:
        # Pass a bogus or expired lease token
        result = campaign_production.generate_batch(
            conn, project_id="p1", campaign_id="c1", turn_id="turn-fencing-test",
            user_request="Create an Instagram social post", types=["social_post"],
            lease_token="expired-or-stolen-token-999"
        )
        assert result["ok"] is False
        assert result["failure_code"] == "lease_expired"
        assert result.get("preserved_existing") is True

        # Ensure NO deliverables were persisted to database
        row = conn.execute("SELECT COUNT(*) FROM campaign_deliverables WHERE campaign_id='c1'").fetchone()
        assert row[0] == 0, "Deliverables were saved despite expired lease!"
    finally:
        conn.close()


def test_real_subprocesses_prevent_duplicate_generation(tmp_path):
    """Launch two completely independent OS Python processes simultaneously against the same DB.

    Verifies cross-process atomic lease acquisition and rejection at OS process boundary.
    """
    db_file = tmp_path / "marketing.db"
    _setup_test_campaign(db_file)

    script = f"""
import sys, time
from pathlib import Path
from app.database import sqlite
from app.services import campaign_flow

db_file = Path(r"{db_file}")
conn = sqlite.connect(db_file)
proc_id = sys.argv[1]
try:
    token = campaign_flow._acquire_db_lock(conn, "p1", "c1", f"proc-{{proc_id}}", lease_timeout_s=10)
    print(f"ACQUIRED:{{proc_id}}:{{token}}", flush=True)
    # Hold the lock briefly to ensure the second process collides
    time.sleep(1.0)
    campaign_flow._release_db_lock(conn, "p1", "c1", token)
    print(f"RELEASED:{{proc_id}}", flush=True)
except campaign_flow.FlowError as exc:
    print(f"REJECTED:{{proc_id}}:{{exc.code}}", flush=True)
finally:
    conn.close()
"""
    cwd = Path(__file__).resolve().parents[2]
    p1 = subprocess.Popen([sys.executable, "-c", script, "1"], cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # Give p1 a tiny headstart so both processes overlap
    time.sleep(0.05)
    p2 = subprocess.Popen([sys.executable, "-c", script, "2"], cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    out1, err1 = p1.communicate(timeout=10)
    out2, err2 = p2.communicate(timeout=10)

    combined = out1 + "\n" + out2
    # Exactly one process must acquire and one must be rejected with generation_in_progress
    assert ("ACQUIRED:1" in combined and "REJECTED:2:generation_in_progress" in combined) or \
           ("ACQUIRED:2" in combined and "REJECTED:1:generation_in_progress" in combined), f"Unexpected output:\nProcess 1:\n{out1}\n{err1}\nProcess 2:\n{out2}\n{err2}"


def _setup_ready_campaign(db_path: Path, project_id="p1", campaign_id="c1"):
    conn = sqlite.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO projects (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (project_id, "Northstar Bakery", "2026-10-28", "2026-10-28"),
    )
    intake = campaign_production.campaign_intake(
        "Create a 30-day Instagram campaign targeting local families.",
        objective="",
        turn_id="t-init",
    )
    meta = {"campaign_intake": intake}
    conn.execute(
        "INSERT OR REPLACE INTO campaigns (id, project_id, title, status, updated_at, workflow_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (campaign_id, project_id, "Family Campaign", "active", "2026-10-28", json.dumps(meta)),
    )
    conn.commit()
    conn.close()


def test_lease_expiration_without_ownership_replacement_fences_persistence(tmp_path, monkeypatch):
    """Concern 1a: If a lease expires without another owner replacing it,
    neither _verify_db_lock nor save_generated_batch may treat the expired
    owner_token as valid, and persistence must fail closed with 0 rows committed.
    """
    from app.services import campaign_deliverables as deliverables
    from app.services.llm.model_router import ModelRouter
    from app.tests.campaign_fake_provider import DeterministicCampaignProvider

    db_file = tmp_path / "expire_no_replace.db"
    _setup_ready_campaign(db_file, "p1", "c_exp")
    conn = sqlite.connect(db_file)

    provider = DeterministicCampaignProvider()
    orig_complete = provider.complete

    def slow_complete(*args, **kwargs):
        time.sleep(0.35)
        return orig_complete(*args, **kwargs)

    monkeypatch.setattr(provider, "complete", slow_complete)
    router_obj = ModelRouter(local_provider=provider, default_local_model="deterministic-test")
    monkeypatch.setattr(campaign_production, "_router", lambda: router_obj)

    try:
        # Lease timeout is 0.12s while heartbeat interval is 10s (so no heartbeat renews it),
        # and NO second owner attempts to acquire the lock.
        with pytest.raises(campaign_flow.FlowError) as exc_info:
            campaign_flow.generate_from_plan(
                conn,
                project_id="p1",
                campaign_id="c_exp",
                turn_id="turn-exp-1",
                lease_timeout_s=0.12,
                heartbeat_interval_s=10.0,
            )
        assert exc_info.value.status == 409
        assert exc_info.value.code == "lease_expired"

        # Verify 0 deliverables, 0 revisions, and 0 batches were persisted
        assert conn.execute("SELECT COUNT(*) FROM campaign_deliverables WHERE campaign_id='c_exp'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM campaign_deliverable_revisions WHERE campaign_id='c_exp'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM campaign_deliverable_batches WHERE campaign_id='c_exp'").fetchone()[0] == 0

        # Also verify direct save_generated_batch atomic transaction fencing when
        # the same owner_token is still in workflow_json but its timestamp is expired:
        expired_token = campaign_flow._acquire_db_lock(conn, "p1", "c_exp", "turn-exp-direct", lease_timeout_s=0.05)
        time.sleep(0.12)
        assert campaign_flow._verify_db_lock(conn, "p1", "c_exp", expired_token) is False
        with pytest.raises(deliverables.DeliverableLeaseError):
            deliverables.save_generated_batch(
                conn,
                "p1",
                "c_exp",
                "batch-exp-direct",
                [{"type": "social_post", "title": "Post", "platform": "Instagram", "content_md": "Body"}],
                {"provider": "test"},
                lease_token=expired_token,
            )
        assert conn.execute("SELECT COUNT(*) FROM campaign_deliverables WHERE campaign_id='c_exp'").fetchone()[0] == 0
    finally:
        conn.close()


def test_owner_change_between_validation_and_persistence_fences_commit(tmp_path, monkeypatch):
    """Concern 1b: If the lease owner changes AFTER output validation (and even
    after the pre-check) right before save_generated_batch enters its atomic
    BEGIN IMMEDIATE transaction, persistence must abort inside the transaction,
    preserve Owner 2's active lease, and let Owner 2 succeed cleanly.
    """
    from app.services.llm.model_router import ModelRouter
    from app.tests.campaign_fake_provider import DeterministicCampaignProvider

    db_file = tmp_path / "owner_change_race.db"
    _setup_ready_campaign(db_file, "p1", "c_race")
    conn1 = sqlite.connect(db_file)
    conn2 = sqlite.connect(db_file)

    provider = DeterministicCampaignProvider()
    router_obj = ModelRouter(local_provider=provider, default_local_model="deterministic-test")
    monkeypatch.setattr(campaign_production, "_router", lambda: router_obj)

    owner2_lease = {}

    def steal_lease_after_validation(_c, pid, cid, lease_token_1):
        # Simulate Owner 1's lease expiring right after validation/pre-check,
        # and Owner 2 acquiring the lease before save_generated_batch's BEGIN IMMEDIATE.
        row = conn2.execute(
            "SELECT workflow_json FROM campaigns WHERE id=? AND project_id=?",
            (cid, pid),
        ).fetchone()
        meta = json.loads(row[0])
        assert meta["generation_in_progress"]["owner_token"] == lease_token_1
        stale = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        meta["generation_in_progress"]["started_at"] = stale
        meta["generation_in_progress"]["heartbeat_at"] = stale
        conn2.execute(
            "UPDATE campaigns SET workflow_json=? WHERE id=? AND project_id=?",
            (json.dumps(meta), cid, pid),
        )
        conn2.commit()
        owner2_lease["token"] = campaign_flow._acquire_db_lock(
            conn2, pid, cid, "turn-owner-2", lease_timeout_s=60
        )

    monkeypatch.setattr(campaign_production, "_before_persist_hook", steal_lease_after_validation)

    try:
        with pytest.raises(campaign_flow.FlowError) as exc_info:
            campaign_flow.generate_from_plan(
                conn1, project_id="p1", campaign_id="c_race", turn_id="turn-owner-1"
            )
        assert exc_info.value.status == 409
        assert exc_info.value.code == "lease_expired"

        # Owner 1's transaction must have rolled back with 0 rows
        assert conn2.execute("SELECT COUNT(*) FROM campaign_deliverables WHERE campaign_id='c_race'").fetchone()[0] == 0
        assert conn2.execute("SELECT COUNT(*) FROM campaign_deliverable_batches WHERE campaign_id='c_race'").fetchone()[0] == 0

        # Owner 2's lease must still be active and untouched by Owner 1's finally block
        assert "token" in owner2_lease
        assert campaign_flow._verify_db_lock(conn2, "p1", "c_race", owner2_lease["token"]) is True

        # Now Owner 2 finishes its generation using its valid lease and persists cleanly
        monkeypatch.setattr(campaign_production, "_before_persist_hook", None)
        res2 = campaign_flow._generate_from_plan(
            conn2,
            project_id="p1",
            campaign_id="c_race",
            turn_id="turn-owner-2",
            lease_token=owner2_lease["token"],
        )
        assert res2["ok"] is True
        assert len(res2["deliverables"]) == 2
        assert conn2.execute("SELECT COUNT(*) FROM campaign_deliverables WHERE campaign_id='c_race'").fetchone()[0] == 2
    finally:
        conn1.close()
        conn2.close()


def test_heartbeat_renewal_and_heartbeat_failure_fencing(tmp_path, monkeypatch):
    """Concern 1c: Verify that a working background heartbeat keeps a slow
    generation lease valid past its initial timeout, whereas a heartbeat failure
    allows the lease to expire and fences persistence from committing.
    """
    from app.services.llm.model_router import ModelRouter
    from app.tests.campaign_fake_provider import DeterministicCampaignProvider

    db_file = tmp_path / "heartbeat_fencing.db"
    _setup_ready_campaign(db_file, "p1", "c_hb_ok")
    _setup_ready_campaign(db_file, "p1", "c_hb_fail")

    provider = DeterministicCampaignProvider()
    orig_complete = provider.complete

    def slow_complete(*args, **kwargs):
        time.sleep(0.45)
        return orig_complete(*args, **kwargs)

    monkeypatch.setattr(provider, "complete", slow_complete)
    router_obj = ModelRouter(local_provider=provider, default_local_model="deterministic-test")
    monkeypatch.setattr(campaign_production, "_router", lambda: router_obj)

    conn = sqlite.connect(db_file)
    try:
        # Part A: Heartbeat succeeds every 0.06s; lease_timeout_s is 0.22s; generation takes 0.45s.
        res_ok = campaign_flow.generate_from_plan(
            conn,
            project_id="p1",
            campaign_id="c_hb_ok",
            turn_id="turn-hb-ok",
            lease_timeout_s=0.22,
            heartbeat_interval_s=0.06,
        )
        assert res_ok["ok"] is True
        assert conn.execute("SELECT COUNT(*) FROM campaign_deliverables WHERE campaign_id='c_hb_ok'").fetchone()[0] == 2

        # Part B: Heartbeat fails to renew (simulated DB/heartbeat failure).
        # Generation takes 0.45s while lease_timeout_s is 0.18s -> lease expires and persistence is fenced!
        monkeypatch.setattr(campaign_flow, "_heartbeat_db_lock", lambda *_a, **_kw: False)
        with pytest.raises(campaign_flow.FlowError) as exc_info:
            campaign_flow.generate_from_plan(
                conn,
                project_id="p1",
                campaign_id="c_hb_fail",
                turn_id="turn-hb-fail",
                lease_timeout_s=0.18,
                heartbeat_interval_s=0.05,
            )
        assert exc_info.value.status == 409
        assert exc_info.value.code == "lease_expired"
        assert conn.execute("SELECT COUNT(*) FROM campaign_deliverables WHERE campaign_id='c_hb_fail'").fetchone()[0] == 0
    finally:
        conn.close()


