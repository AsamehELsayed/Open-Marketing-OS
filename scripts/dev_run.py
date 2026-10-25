"""Development run orchestrator — file-based, stdlib only.

Usage (from repo root):
  python scripts/dev_run.py new --id DEV-001 --title "..." --class MEDIUM
  python scripts/dev_run.py log-exec --id DEV-001 --role planner --agent general \
      --task "..." --started 2026-09-18T10:00:00Z --completed 2026-09-18T10:20:00Z \
      --status completed --input plan-packet --output development/runs/DEV-001/plan.md
  python scripts/dev_run.py gate --id DEV-001 --stage implementing
  python scripts/dev_run.py trace --id DEV-001
  python scripts/dev_run.py matrix --id DEV-001
  python scripts/dev_run.py set-stage --id DEV-001 --stage review

Fail-closed gates (MEDIUM+): implementation requires plan.md; TESTED requires
qa.md from an execution whose role=qa; APPROVED requires review.md with a
verdict line; COMPLETE after changes requires a re-review artifact.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def runs_base() -> Path:
    import os
    override = os.environ.get("DEV_RUNS_DIR")
    return Path(override) if override else REPO / "development" / "runs"


RUNS = runs_base()

REQUIRED_ROLES = {
    "TINY": [],
    "SMALL": ["implementer", "qa"],
    "MEDIUM": ["planner", "implementer", "qa", "reviewer"],
    "LARGE": ["planner", "implementer", "integrator", "qa", "reviewer"],
    "ARCHITECTURAL": ["planner", "implementer", "qa", "reviewer"],
}

STAGES = ["planning", "implementing", "integrating", "qa", "review",
          "remediation", "approved", "complete", "failed"]


def run_dir(run_id: str) -> Path:
    return runs_base() / run_id


def load_run(run_id: str) -> dict:
    p = run_dir(run_id) / "run.json"
    if not p.exists():
        sys.exit(f"no such run: {run_id}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_run(run_id: str, data: dict) -> None:
    data["updated_at"] = now()
    (run_dir(run_id) / "run.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_new(a) -> None:
    d = run_dir(a.id)
    if d.exists():
        sys.exit(f"run exists: {a.id}")
    (d / "workers").mkdir(parents=True)
    cls = a.class_.upper()
    if cls not in REQUIRED_ROLES:
        sys.exit(f"unknown classification: {cls}")
    save_new = {
        "run_id": a.id,
        "title": a.title,
        "classification": cls,
        "status": "planning" if cls in ("MEDIUM", "LARGE", "ARCHITECTURAL") else "implementing",
        "planner": None, "workers": [], "qa": None, "reviewer": None,
        "required_roles": REQUIRED_ROLES[cls],
        "founder_plan_approved": False, "plan_approved_at": None,
        "created_at": now(), "updated_at": now(),
        "current_stage": "planning" if cls in ("MEDIUM", "LARGE", "ARCHITECTURAL") else "implementing",
    }
    (d / "run.json").write_text(json.dumps(save_new, indent=2), encoding="utf-8")
    (d / "executions.jsonl").write_text("", encoding="utf-8")
    print(f"created {d / 'run.json'}")


def cmd_log_exec(a) -> None:
    run = load_run(a.id)
    rec = {
        "run_id": a.id,
        "parent_run_id": a.parent or "orchestrator-session",
        "role": a.role,
        "agent": a.agent,
        "task": a.task,
        "started_at": a.started,
        "completed_at": a.completed,
        "status": a.status,
        "input_artifact": a.input,
        "output_artifact": a.output,
    }
    if getattr(a, "worker_id", None):
        rec["worker_id"] = a.worker_id
    with open(run_dir(a.id) / "executions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    if a.role == "planner":
        run["planner"] = {"agent": a.agent, "status": a.status, "artifact": a.output}
    elif a.role == "implementer":
        worker = {"agent": a.agent, "status": a.status, "artifact": a.output}
        if getattr(a, "worker_id", None):
            worker["worker_id"] = a.worker_id
        run["workers"].append(worker)
    elif a.role == "qa":
        run["qa"] = {"agent": a.agent, "status": a.status, "artifact": a.output}
    elif a.role == "reviewer":
        run["reviewer"] = {"agent": a.agent, "status": a.status, "artifact": a.output}
    save_run(a.id, run)
    print(f"logged {a.role} ({a.agent}) -> {a.status}")


def execs(run_id: str) -> list:
    p = run_dir(run_id) / "executions.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def roles_completed(run_id: str) -> dict:
    """role -> True if a completed execution with that role exists AND its
    output artifact file exists on disk."""
    done: dict = {}
    for e in execs(run_id):
        if e.get("status") == "completed":
            art = e.get("output_artifact", "")
            if art and (REPO / art).is_file():
                done[e["role"]] = True
    return done


def review_verdict(run_id: str) -> str:
    """Latest real reviewer execution's artifact verdict."""
    return execution_verdict(run_id, "reviewer")


def execution_verdict(run_id: str, role: str) -> str:
    """Verdict from the latest completed role execution with an existing artifact."""
    candidates = []
    for index, execution in enumerate(execs(run_id)):
        if execution.get("role") != role or execution.get("status") != "completed":
            continue
        artifact = execution.get("output_artifact", "")
        path = REPO / artifact if artifact else None
        if path is not None and path.is_file():
            candidates.append((parse_ts(execution.get("completed_at", "")), index, path))
    if not candidates:
        return "NONE"
    # Preserve append order as a deterministic tie-breaker for native timestamps.
    text = max(candidates, key=lambda item: (item[0] or datetime.min, item[1]))[2].read_text(encoding="utf-8")
    verdict = "NONE"
    for line in text.splitlines():
        s = line.strip().strip("*# ").upper()
        if "VERDICT:" in s:
            v = s.split("VERDICT:", 1)[1].strip().strip("*# ")
            if v.startswith("APPROVE_WITH_CHANGES"):
                verdict = "APPROVE_WITH_CHANGES"
            elif v.startswith("APPROVE"):
                verdict = "APPROVE"
            elif v.startswith("BLOCK"):
                verdict = "BLOCK"
            elif v.startswith("FAIL"):
                verdict = "FAIL"
            elif v.startswith("PASS"):
                verdict = "PASS"
            else:
                # A later but unrecognized explicit verdict must fail closed.
                verdict = "NONE"
    return verdict


def re_review_after_remediation(run_id: str) -> bool:
    """Require a real reviewer execution after every logged run-local remediation."""
    d = run_dir(run_id)
    remediation_files = [path.resolve() for path in d.glob("remediation*.md")]
    if not remediation_files:
        return True

    executions = execs(run_id)
    remediation_times = []
    for artifact_path in remediation_files:
        matching = []
        for execution in executions:
            if (execution.get("role") not in ("implementer", "integrator")
                    or execution.get("status") != "completed"):
                continue
            artifact = execution.get("output_artifact", "")
            path = (REPO / artifact).resolve() if artifact else None
            if path == artifact_path:
                timestamp = parse_ts(execution.get("completed_at", ""))
                if timestamp is None or not path.is_file():
                    return False
                matching.append(timestamp)
        if not matching:
            return False
        remediation_times.append(max(matching))

    reviewer_executions = [e for e in executions
                           if e.get("role") == "reviewer"
                           and e.get("status") == "completed"]
    if not reviewer_executions:
        return False
    reviewer_times = []
    for execution in reviewer_executions:
        timestamp = parse_ts(execution.get("completed_at", ""))
        artifact = execution.get("output_artifact", "")
        path = (REPO / artifact).resolve() if artifact else None
        if timestamp is None or path is None or not path.is_file():
            return False
        reviewer_times.append(timestamp)

    return max(reviewer_times) > max(remediation_times)


def cmd_gate(a) -> None:
    """Fail-closed stage gate. Exit 0 = may proceed; exit 1 = blocked."""
    run = load_run(a.id)
    cls = run["classification"]
    d = run_dir(a.id)
    done = roles_completed(a.id)
    stage = a.stage
    need_plan = cls in ("MEDIUM", "LARGE", "ARCHITECTURAL")

    def block(reason: str) -> None:
        print(f"GATE BLOCKED for stage '{stage}': {reason}")
        sys.exit(1)

    if stage not in STAGES:
        block(f"unknown stage: {stage}")

    if stage in ("implementing", "integrating", "qa", "review",
                 "remediation", "approved", "complete"):
        if need_plan and not (d / "plan.md").exists():
            block("MEDIUM+ requires development/runs/<id>/plan.md before implementation")
        if need_plan and not run.get("founder_plan_approved"):
            block("MEDIUM+ requires founder plan approval "
                  "(scripts/dev_run.py approve-plan --id <run>) before implementation")
    if stage in ("review", "remediation", "approved", "complete"):
        if "qa" not in done:
            block("cannot claim TESTED: no completed QA execution with artifact")
    if stage in ("approved", "complete"):
        qa_verdict = execution_verdict(a.id, "qa")
        if qa_verdict != "PASS":
            block(f"cannot claim {stage.upper()}: latest QA verdict is {qa_verdict}; PASS required")
        v = review_verdict(a.id)
        if v != "APPROVE":
            block(f"cannot claim {stage.upper()}: latest review verdict is {v}")
        if stage == "complete":
            missing = [role for role in REQUIRED_ROLES[cls] if role not in done]
            if missing:
                block("cannot claim COMPLETE: required completed executions/artifacts missing: "
                      + ", ".join(missing))
        if (stage == "complete" or cls in ("MEDIUM", "LARGE", "ARCHITECTURAL")) and "reviewer" not in done:
            block(f"cannot claim {stage.upper()}: reviewer execution missing")
    if stage in ("approved", "complete") and any(d.glob("remediation*.md")):
        if not re_review_after_remediation(a.id):
            block("remediation exists but no completed reviewer execution ran after every logged output")
    print(f"GATE OPEN for stage '{stage}'")
    save_maybe = dict(run)
    save_maybe["current_stage"] = stage
    if stage in ("approved", "complete"):
        save_maybe["status"] = stage
    save_run(a.id, save_maybe)


def cmd_trace(a) -> None:
    run = load_run(a.id)
    done = roles_completed(a.id)
    order = ["planner", "implementer", "integrator", "qa", "reviewer"]
    labels = {"planner": "Planning", "implementer": "Implementation",
              "integrator": "Integration", "qa": "QA", "reviewer": "Review"}
    print(f"Run {a.id} — {run['title']} [{run['classification']}] stage={run['current_stage']}")
    for r in order:
        if r == "implementer" and run["workers"]:
            for i, w in enumerate(run["workers"]):
                mark = "OK" if w["status"] == "completed" else ".."
                agent_name = w.get("agent") or w.get("worker_id") or "worker"
                print(f"  [{mark}] worker-{i + 1} ({agent_name}): {w['status']}")
        elif r in done:
            print(f"  [OK] {labels[r]}: completed")
        elif r in run.get("required_roles", []):
            cur = run["current_stage"]
            active = {"planner": "planning", "implementer": "implementing",
                      "integrator": "integrating", "qa": "qa",
                      "reviewer": "review"}.get(r)
            mark = ">>" if cur == active else "--"
            print(f"  [{mark}] {labels[r]}: waiting/running")
    v = review_verdict(a.id)
    if v != "NONE":
        print(f"  latest review verdict: {v}")


def cmd_matrix(a) -> None:
    rows = execs(a.id)
    print(f"ROLE | ACTUAL RUN? | AGENT | ARTIFACT")
    for e in rows:
        art = e.get("output_artifact", "")
        real = "YES" if (e.get("status") == "completed" and art
                         and (REPO / art).exists()) else "NO"
        print(f"{e['role']} | {real} | {e['agent']} | {art}")
    if not rows:
        print("(no executions recorded — delegation did not happen)")


def parse_ts(s: str):
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (AttributeError, ValueError, TypeError):
        return None


def cmd_parallelism(a) -> None:
    """Overlap proof from executions.jsonl timestamps. Honest REAL/NO."""
    recs = [e for e in execs(a.id)
            if e.get("worker_id")  # scheduler-launched parallel workers only;
            # planner/QA/reviewer are stage-ordered roles, not parallel workers
            and e.get("status") == "completed"
            and parse_ts(e.get("started_at", ""))
            and parse_ts(e.get("completed_at", ""))]
    for e in recs:
        e["_s"] = parse_ts(e["started_at"])
        e["_e"] = parse_ts(e["completed_at"])
    print("WORKER | START | END | DURATION | OVERLAPPED WITH")
    maxconc, parallel_n = 0, 0
    for e in recs:
        others = sorted({o.get("worker_id") or o["role"]
                         for o in recs if o is not e
                         and o["_s"] < e["_e"] and e["_s"] < o["_e"]})
        if others:
            parallel_n += 1
        dur = e["_e"] - e["_s"]
        name = e.get("worker_id") or e["role"]
        print(f"{name} | {e['started_at']} | {e['completed_at']} | "
              f"{dur} | {','.join(others) if others else '-'}")
    for t in [e["_s"] for e in recs] + [e["_e"] for e in recs]:
        live = sum(1 for e in recs if e["_s"] <= t < e["_e"])
        maxconc = max(maxconc, live)
    print(f"MAX CONCURRENCY: {maxconc}")
    print(f"PARALLEL WORKERS: {parallel_n}")
    print(f"PARALLELISM: {'REAL' if parallel_n >= 2 else 'NO'}")


def cmd_approve_plan(a) -> None:
    run = load_run(a.id)
    run["founder_plan_approved"] = True
    run["plan_approved_at"] = now()
    save_run(a.id, run)
    print(f"plan approved for {a.id} — orchestrator authorized through "
          f"implementation, QA, review, remediation, re-review")


def cmd_set_stage(a) -> None:
    if a.stage not in STAGES:
        sys.exit(f"unknown stage: {a.stage}")
    if a.stage in ("approved", "complete"):
        return cmd_gate(a)
    run = load_run(a.id)
    run["current_stage"] = a.stage
    run["status"] = a.stage
    save_run(a.id, run)
    print(f"stage -> {a.stage}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("--id", required=True)
    n.add_argument("--title", required=True)
    n.add_argument("--class", dest="class_", required=True)
    n.set_defaults(fn=cmd_new)
    l = sub.add_parser("log-exec")
    l.add_argument("--id", required=True)
    l.add_argument("--role", required=True)
    l.add_argument("--worker-id")
    l.add_argument("--agent", required=True)
    l.add_argument("--task", required=True)
    l.add_argument("--started", required=True)
    l.add_argument("--completed", required=True)
    l.add_argument("--status", required=True)
    l.add_argument("--input", required=True)
    l.add_argument("--output", required=True)
    l.add_argument("--parent", default="orchestrator-session")
    l.set_defaults(fn=cmd_log_exec)
    g = sub.add_parser("gate")
    g.add_argument("--id", required=True)
    g.add_argument("--stage", required=True)
    g.set_defaults(fn=cmd_gate)
    t = sub.add_parser("trace")
    t.add_argument("--id", required=True)
    t.set_defaults(fn=cmd_trace)
    m = sub.add_parser("matrix")
    m.add_argument("--id", required=True)
    m.set_defaults(fn=cmd_matrix)
    s = sub.add_parser("set-stage")
    s.add_argument("--id", required=True)
    s.add_argument("--stage", required=True)
    s.set_defaults(fn=cmd_set_stage)
    p = sub.add_parser("parallelism")
    p.add_argument("--id", required=True)
    p.set_defaults(fn=cmd_parallelism)
    ap_ = sub.add_parser("approve-plan")
    ap_.add_argument("--id", required=True)
    ap_.set_defaults(fn=cmd_approve_plan)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
