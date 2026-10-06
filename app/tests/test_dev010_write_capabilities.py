import json

import pytest

from app.database import repos
from app.database.sqlite import connect, _migrate_v8_to_v9
from app.services.tools.propose_tools import (
    register_propose_tools, t_propose_campaign, t_propose_experiment,
    t_propose_task, t_reject_task, t_request_approval,
)
from app.services.tools.registry import Registry


def _db(tmp_path):
    conn = connect(tmp_path / "dev010.sqlite")
    for project_id in ("p1", "p2"):
        repos.Projects.upsert(conn, {
            "id": project_id, "name": project_id, "website": "", "goal": "",
            "status": "active", "settings_json": "{}", "created_at": "now", "updated_at": "now",
        })
    return conn


def _invoke(fn, conn, project_id="p1", **args):
    return fn(conn, project_id=project_id, root=None, args=args)


def test_proposal_tools_registered_and_experiment_is_proposed(tmp_path):
    conn = _db(tmp_path)
    reg = Registry()
    register_propose_tools(reg)
    assert {"propose_campaign", "propose_task", "request_approval",
            "propose_experiment", "reject_task"} <= set(reg.names())
    camp = _invoke(t_propose_campaign, conn, title="Improve conversion", idempotency_key="c1")
    task = _invoke(t_propose_task, conn, title="Review funnel", campaign_id=camp["campaign_id"], idempotency_key="t1")
    exp = _invoke(t_propose_experiment, conn, hypothesis="Shorter form increases leads", metric="form completion", campaign_id=camp["campaign_id"], idempotency_key="e1")
    assert task["ok"] and exp["ok"]
    row = repos.Experiments.get(conn, exp["experiment_id"], "p1")
    assert row["status"] == "proposed"
    assert repos.Measurements.for_experiment(conn, row["id"]) == []
    assert "result" not in row


def test_experiment_workflow_keeps_task_and_website_evidence_provenance(tmp_path):
    conn = _db(tmp_path)
    campaign = _invoke(t_propose_campaign, conn, title="Improve website CTA",
                       idempotency_key="evidence-campaign")
    task = _invoke(t_propose_task, conn, title="Test a clear primary CTA",
                   campaign_id=campaign["campaign_id"],
                   idempotency_key="evidence-task")
    workflow = {
        "conversation_id": "cv-evidence",
        "turn_id": "turn-evidence",
        "thread_id": "turn-evidence",
        "task_id": task["task_id"],
        "campaign_id": campaign["campaign_id"],
        "assigned_role": "growth",
        "requested_by": "account_manager",
        "evidence_tool_run_id": "website-run-1",
        "evidence_ref": "https://example.com",
    }
    experiment = _invoke(
        t_propose_experiment,
        conn,
        hypothesis="Test a primary CTA because the live audit observed no action-oriented CTA keywords.",
        metric="primary CTA click-through rate",
        campaign_id=campaign["campaign_id"],
        workflow=workflow,
        idempotency_key="evidence-experiment",
    )
    row = repos.Experiments.get(conn, experiment["experiment_id"], "p1")
    assert experiment["ok"]
    assert json.loads(row["workflow_json"]) == workflow


def test_replay_is_idempotent_and_key_reuse_with_changed_payload_fails(tmp_path):
    conn = _db(tmp_path)
    a = _invoke(t_propose_campaign, conn, title="Campaign A", idempotency_key="stable")
    b = _invoke(t_propose_campaign, conn, title="Campaign A", idempotency_key="stable")
    c = _invoke(t_propose_campaign, conn, title="Campaign B", idempotency_key="stable")
    assert a["campaign_id"] == b["campaign_id"]
    assert not c["ok"]
    assert len(repos.Campaigns.list(conn, "p1")) == 1


def test_linked_ids_are_project_scoped_and_write_isolation_holds(tmp_path):
    conn = _db(tmp_path)
    c2 = _invoke(t_propose_campaign, conn, "p2", title="Private", idempotency_key="c2")
    bad = _invoke(t_propose_task, conn, "p1", title="Cross link", campaign_id=c2["campaign_id"], idempotency_key="bad")
    assert not bad["ok"]
    approval = _invoke(t_request_approval, conn, "p1", title="Approve", workflow={"campaign_id": c2["campaign_id"]}, idempotency_key="ap1")
    assert not approval["ok"]
    own = _invoke(t_propose_campaign, conn, "p1", title="Own", idempotency_key="own")
    assert repos.Campaigns.get(conn, own["campaign_id"], "p1")
    with pytest.raises(ValueError, match="belongs to project"):
        repos.Campaigns.get(conn, own["campaign_id"], "p2")


def test_approval_workflow_provenance_and_rejection_scope_status(tmp_path):
    conn = _db(tmp_path)
    task = _invoke(t_propose_task, conn, title="Draft landing page", idempotency_key="t")
    workflow = {"conversation_id": "cv", "turn_id": "turn", "thread_id": "thread",
                "task_id": task["task_id"], "campaign_id": "", "assigned_role": "web",
                "requested_by": "account_manager"}
    approval = _invoke(t_request_approval, conn, title="Review task", workflow=workflow, idempotency_key="a")
    row = repos.Approvals.get(conn, approval["approval_id"], "p1")
    assert json.loads(row["fields_json"])["workflow"] == workflow
    foreign = _invoke(t_reject_task, conn, "p2", task_id=task["task_id"], reason="No")
    assert not foreign["ok"]
    rejected = _invoke(t_reject_task, conn, task_id=task["task_id"], reason="Not approved")
    again = _invoke(t_reject_task, conn, task_id=task["task_id"], reason="Not approved")
    assert rejected["status"] == again["status"] == "rejected"
    assert repos.Tasks.get(conn, task["task_id"], "p1")["status"] == "rejected"


def test_workflow_provenance_migration_is_additive_and_idempotent(tmp_path):
    conn = _db(tmp_path)
    for table in ("campaigns", "tasks", "experiments"):
        conn.execute(f"ALTER TABLE {table} DROP COLUMN workflow_json")
    _migrate_v8_to_v9(conn)
    _migrate_v8_to_v9(conn)
    for table in ("campaigns", "tasks", "experiments"):
        assert "workflow_json" in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
