"""Regression coverage for fail-closed terminal development gates."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dev_run.py"


def seed_run(root: Path, *, qa_verdict="PASS", review_verdict="APPROVE",
             missing_roles=()):
    run_id = "GATE-TEST"
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_id,
        "title": "terminal gate proof",
        "classification": "ARCHITECTURAL",
        "status": "review",
        "current_stage": "review",
        "required_roles": ["planner", "implementer", "qa", "reviewer"],
        "founder_plan_approved": True,
    }))
    (run_dir / "plan.md").write_text("approved plan")
    qa_artifact = run_dir / "qa.md"
    qa_text = {
        "BLOCK_MARKDOWN": "**Current verdict: BLOCK**\n",
        "BLOCK_THEN_PASS": "VERDICT: BLOCK\n**Current verdict: PASS**\n",
    }.get(qa_verdict, f"VERDICT: {qa_verdict}\n")
    qa_artifact.write_text(qa_text)
    review_artifact = run_dir / "review.md"
    review_text = ("VERDICT: BLOCK\n**Current verdict: APPROVE**\n"
                   if review_verdict == "BLOCK_THEN_APPROVE"
                   else f"VERDICT: {review_verdict}\n")
    review_artifact.write_text(review_text)
    output_by_role = {
        "planner": "AGENTS.md",
        "implementer": "AGENTS.md",
        "qa": str(qa_artifact),
        "reviewer": str(review_artifact),
    }
    records = []
    for role, artifact in output_by_role.items():
        if role in missing_roles:
            continue
        records.append({
            "run_id": run_id,
            "role": role,
            "agent": f"test-{role}",
            "task": "proof fixture",
            "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:01:00Z",
            "status": "completed",
            "input_artifact": "AGENTS.md",
            "output_artifact": artifact,
        })
    (run_dir / "executions.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records)
    )
    return run_id, run_dir


def invoke(root: Path, run_id: str, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--id", run_id],
        capture_output=True,
        text=True,
        cwd=REPO,
        env={**os.environ, "DEV_RUNS_DIR": str(root)},
    )


def test_unknown_gate_stage_fails_closed_without_state_change(tmp_path):
    run_id, run_dir = seed_run(tmp_path)
    before = json.loads((run_dir / "run.json").read_text())
    result = invoke(tmp_path, run_id, "gate", "--stage", "completee")
    after = json.loads((run_dir / "run.json").read_text())
    assert result.returncode != 0
    assert "GATE BLOCKED" in result.stdout and "unknown stage" in result.stdout
    assert "GATE OPEN" not in result.stdout
    assert after["current_stage"] == before["current_stage"]


@pytest.mark.parametrize("terminal_stage", ["approved", "complete"])
def test_set_stage_cannot_bypass_terminal_gate(tmp_path, terminal_stage):
    run_id, run_dir = seed_run(tmp_path, missing_roles=("reviewer",))
    result = invoke(tmp_path, run_id, "set-stage", "--stage", terminal_stage)
    saved = json.loads((run_dir / "run.json").read_text())
    assert result.returncode != 0
    assert "GATE BLOCKED" in result.stdout
    assert saved["current_stage"] == "review"
    assert saved["status"] == "review"


@pytest.mark.parametrize("verdict", ["BLOCK", "FAIL"])
def test_approved_gate_rejects_nonpassing_qa(tmp_path, verdict):
    run_id, _ = seed_run(tmp_path, qa_verdict=verdict)
    result = invoke(tmp_path, run_id, "gate", "--stage", "approved")
    assert result.returncode != 0
    assert f"latest QA verdict is {verdict}" in result.stdout


def test_approved_gate_parses_current_markdown_qa_verdict(tmp_path):
    run_id, _ = seed_run(tmp_path, qa_verdict="BLOCK_MARKDOWN")
    result = invoke(tmp_path, run_id, "gate", "--stage", "approved")
    assert result.returncode != 0
    assert "latest QA verdict is BLOCK" in result.stdout


def test_latest_qa_verdict_supersedes_historical_block(tmp_path):
    run_id, _ = seed_run(tmp_path, qa_verdict="BLOCK_THEN_PASS")
    result = invoke(tmp_path, run_id, "gate", "--stage", "approved")
    assert result.returncode == 0, result.stdout + result.stderr


def test_complete_gate_requires_passing_qa(tmp_path):
    run_id, _ = seed_run(tmp_path, qa_verdict="FAIL")
    result = invoke(tmp_path, run_id, "gate", "--stage", "complete")
    assert result.returncode != 0
    assert "latest QA verdict is FAIL" in result.stdout


def test_complete_gate_requires_all_roles_and_passing_evidence(tmp_path):
    run_id, run_dir = seed_run(tmp_path)
    result = invoke(tmp_path, run_id, "gate", "--stage", "complete")
    saved = json.loads((run_dir / "run.json").read_text())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GATE OPEN for stage 'complete'" in result.stdout
    assert saved["current_stage"] == "complete"
    assert saved["status"] == "complete"


def test_complete_gate_rejects_nonapproving_latest_reviewer(tmp_path):
    run_id, _ = seed_run(tmp_path, review_verdict="APPROVE_WITH_CHANGES")
    result = invoke(tmp_path, run_id, "gate", "--stage", "complete")
    assert result.returncode != 0
    assert "latest review verdict is APPROVE_WITH_CHANGES" in result.stdout


def test_latest_reviewer_verdict_supersedes_historical_block(tmp_path):
    run_id, _ = seed_run(tmp_path, review_verdict="BLOCK_THEN_APPROVE")
    result = invoke(tmp_path, run_id, "gate", "--stage", "approved")
    assert result.returncode == 0, result.stdout + result.stderr


def test_complete_gate_requires_each_required_role_artifact(tmp_path):
    run_id, _ = seed_run(tmp_path, missing_roles=("implementer",))
    result = invoke(tmp_path, run_id, "gate", "--stage", "complete")
    assert result.returncode != 0
    assert "required completed executions/artifacts missing: implementer" in result.stdout


@pytest.mark.parametrize("terminal_stage", ["approved", "complete"])
def test_terminal_gate_requires_re_review_after_remediation(tmp_path, terminal_stage):
    run_id, run_dir = seed_run(tmp_path)
    remediation = run_dir / "remediation.md"
    remediation.write_text("remediation happened after the review")
    remediation_time = remediation.stat().st_mtime + 5
    import os
    os.utime(remediation, (remediation_time, remediation_time))
    # A stale reviewer artifact touched later must not impersonate a new execution.
    review = run_dir / "review.md"
    os.utime(review, (remediation_time + 10, remediation_time + 10))
    records = json.loads("[" + ",".join(
        (run_dir / "executions.jsonl").read_text().splitlines()) + "]")
    records.append({
        "run_id": run_id, "role": "implementer", "agent": "test-remediator",
        "task": "remediation", "started_at": "2026-01-01T00:02:00Z",
        "completed_at": "2026-01-01T00:02:30Z", "status": "completed",
        "input_artifact": "AGENTS.md", "output_artifact": str(remediation),
    })
    (run_dir / "executions.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records))
    result = invoke(tmp_path, run_id, "gate", "--stage", terminal_stage)
    assert result.returncode != 0
    assert "no completed reviewer execution ran after every logged output" in result.stdout


def test_terminal_gate_accepts_logged_review_after_remediation(tmp_path):
    run_id, run_dir = seed_run(tmp_path)
    remediation = run_dir / "remediation.md"
    remediation.write_text("implemented remediation")
    records = [json.loads(line) for line in
               (run_dir / "executions.jsonl").read_text().splitlines()]
    records.append({
        "run_id": run_id, "role": "implementer", "agent": "test-remediator",
        "task": "remediation", "started_at": "2026-01-01T00:02:00Z",
        "completed_at": "2026-01-01T00:02:30Z", "status": "completed",
        "input_artifact": "AGENTS.md", "output_artifact": str(remediation),
    })
    reviewer = next(record for record in records if record["role"] == "reviewer")
    reviewer["completed_at"] = "2026-01-01T00:03:00Z"
    (run_dir / "executions.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records))
    result = invoke(tmp_path, run_id, "gate", "--stage", "complete")
    assert result.returncode == 0, result.stdout + result.stderr


def test_reviewer_between_two_remediations_does_not_satisfy_terminal_gate(tmp_path):
    run_id, run_dir = seed_run(tmp_path)
    first = run_dir / "remediation.md"
    second = run_dir / "remediation2.md"
    first.write_text("first remediation")
    second.write_text("second remediation")
    records = [json.loads(line) for line in
               (run_dir / "executions.jsonl").read_text().splitlines()]
    records.extend([
        {
            "run_id": run_id, "role": "implementer", "agent": "rem1",
            "task": "first remediation", "started_at": "2026-01-01T00:02:00Z",
            "completed_at": "2026-01-01T00:02:30Z", "status": "completed",
            "input_artifact": "AGENTS.md", "output_artifact": str(first),
        },
        {
            "run_id": run_id, "role": "implementer", "agent": "rem2",
            "task": "second remediation", "started_at": "2026-01-01T00:04:00Z",
            "completed_at": "2026-01-01T00:04:30Z", "status": "completed",
            "input_artifact": "AGENTS.md", "output_artifact": str(second),
        },
    ])
    reviewer = next(record for record in records if record["role"] == "reviewer")
    reviewer["completed_at"] = "2026-01-01T00:03:30Z"
    execution_log = run_dir / "executions.jsonl"
    execution_log.write_text("".join(json.dumps(record) + "\n" for record in records))

    blocked = invoke(tmp_path, run_id, "gate", "--stage", "approved")
    assert blocked.returncode != 0
    assert "no completed reviewer execution ran after every logged output" in blocked.stdout

    reviewer["completed_at"] = "2026-01-01T00:05:00Z"
    execution_log.write_text("".join(json.dumps(record) + "\n" for record in records))
    opened = invoke(tmp_path, run_id, "gate", "--stage", "approved")
    assert opened.returncode == 0, opened.stdout + opened.stderr
