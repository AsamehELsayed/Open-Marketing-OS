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
          "remediation", "approved", "failed"]


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
    with open(run_dir(a.id) / "executions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    if a.role == "planner":
        run["planner"] = {"agent": a.agent, "status": a.status, "artifact": a.output}
    elif a.role == "implementer":
        run["workers"].append({"agent": a.agent, "status": a.status, "artifact": a.output})
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
            if art and (REPO / art).exists():
                done[e["role"]] = True
    return done


def review_verdict(run_id: str) -> str:
    """Latest review artifact verdict: APPROVE / APPROVE_WITH_CHANGES / BLOCK / NONE."""
    d = run_dir(run_id)
    cands = sorted(d.glob("review*.md"),
                   key=lambda p: (p.stat().st_mtime, p.name))
    if not cands:
        return "NONE"
    text = cands[-1].read_text(encoding="utf-8")
    for line in text.splitlines():
        s = line.strip().upper()
        if s.startswith("VERDICT:"):
            v = s.split(":", 1)[1].strip()
            if v.startswith("APPROVE_WITH_CHANGES"):
                return "APPROVE_WITH_CHANGES"
            if v.startswith("APPROVE"):
                return "APPROVE"
            if v.startswith("BLOCK"):
                return "BLOCK"
    return "NONE"


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

    if stage in ("implementing", "integrating", "qa", "review",
                 "remediation", "approved"):
        if need_plan and not (d / "plan.md").exists():
            block("MEDIUM+ requires development/runs/<id>/plan.md before implementation")
        if need_plan and not run.get("founder_plan_approved"):
            block("MEDIUM+ requires founder plan approval "
                  "(scripts/dev_run.py approve-plan --id <run>) before implementation")
    if stage in ("review", "remediation", "approved"):
        if "qa" not in done:
            block("cannot claim TESTED: no completed QA execution with artifact")
    if stage in ("approved",):
        v = review_verdict(a.id)
        if v != "APPROVE":
            block(f"cannot claim APPROVED: latest review verdict is {v}")
        if cls in ("MEDIUM", "LARGE", "ARCHITECTURAL") and "reviewer" not in done:
            block("cannot claim APPROVED: reviewer execution missing")
    if stage == "approved" and (d / "remediation.md").exists():
        revs = sorted(d.glob("review*.md"),
                      key=lambda p: (p.stat().st_mtime, p.name))
        import os
        rem_mtime = os.path.getmtime(d / "remediation.md")
        if not any(os.path.getmtime(r) > rem_mtime for r in revs):
            block("remediation exists but no re-review ran after it")
    print(f"GATE OPEN for stage '{stage}'")
    save_maybe = dict(run)
    save_maybe["current_stage"] = stage
    if stage == "approved":
        save_maybe["status"] = "approved"
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
    from datetime import datetime
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
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
    run = load_run(a.id)
    if a.stage not in STAGES:
        sys.exit(f"unknown stage: {a.stage}")
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
