# Parallel Agent Execution

> Extension of `docs/development-delegation-system.md` (unchanged).
> Proven by run `development/runs/DEV-002/` + `app/tests/test_dev_parallel.py`.

## Core rule

Multiple executions are parallel only when their real time intervals
overlap. Evidence: `started_at` / `completed_at` in `executions.jsonl`,
read by `python scripts/dev_run.py parallelism --id DEV-XXX`. Sequential
executions report `PARALLELISM: NO`. Never claim otherwise.

## Dependency graph

The planner declares worker dependencies in `plan.md` and
`development/runs/<id>/graph.json`. Per packet: `worker_id`, `role`,
`task`, `depends_on`, `files_or_scope`, `can_run_parallel`, plus launch
fields (`command`, `stdin_text`, `output_artifact`, `worktree`, `timeout`).
Example: W1+W2 `depends_on: []` run concurrently; W3 `depends_on: [W1]`
waits. Stages still never parallelize across dependencies: QA starts only
after required workers + integration finish; reviewer starts only after the
QA artifact exists. Existing gates enforce this.

## Scheduler (`scripts/dev_sched.py`, stdlib only)

- `init`: validates the graph (unique ids, known deps).
- `run`: asyncio loop — computes `ready_workers`, launches up to
  `max_concurrency` (default 4, env `MAX_PARALLEL_WORKERS`), awaits
  `FIRST_COMPLETED`, repeats. Real concurrent processes
  (`asyncio.create_subprocess_exec`), not sequential loops.
- Worker default: `opencode run` processes with the packet on stdin.
  Windows-shim resolution included (bare `opencode` fails with WinError 2
  under CreateProcess; fixed during DEV-002 after an honest spawn failure).
- Failure: a failed worker's dependents are recorded `blocked` and never
  launched; independent workers continue; the integrator/PM gets completed +
  failed + unknowns and decides retry / partial / block.
- Every launch/completion/block is appended to `executions.jsonl` with
  timestamps and mirrored into `run.json` (so `trace` stays true).
- Timestamps use fractional seconds (`%S.%fZ`); `parse_ts` reads both
  formats (second-resolution cannot resolve sub-second overlap).

## No nested recursion

Workers are launched by the external orchestrator only
(Orchestrator → Worker A/B/C processes simultaneously). Packets forbid
sub-delegation; a worker process has no handle on the scheduler, so
Worker → Worker recursion is structurally impossible.

## Worktree isolation

`ensure_worktree` / `clean-worktrees` create isolated git worktrees at
`development/worktrees/<run_id>/<worker>`, tested with real git operations
(isolated paths, per-worker files invisible to siblings, clean removal).
Each worker edits only its assigned scope and runs its own tests; the
integrator merges and runs integration tests.

DEV-002 verdict: **NOT REQUIRED** (documented reason): the repo base
contains large uncommitted v0.2 work, so HEAD worktrees would lack the
packets' files; packets used guaranteed-disjoint single-owner files
(`repos.py` / `chat.html` / new test file — zero shared edits), the
DEV-001-proven safe pattern. Mechanism verdict: **PASS** (13 tests).

## File ownership

The planner declares exclusive `files_or_scope` per worker. Same file
needed twice → serialize via `depends_on`, or isolate via worktrees +
integrator resolution. Uncontrolled simultaneous edits in one tree are
forbidden; QA/review verify scope discipline by file contents.

## Parallelism proof

`python scripts/dev_run.py parallelism --id DEV-XXX` prints per-worker
START/END/DURATION/OVERLAPPED-WITH (scheduler workers only — planner/QA/
reviewer are stage-ordered roles), then MAX CONCURRENCY, PARALLEL WORKERS,
and `PARALLELISM: REAL` (≥2 overlapping) or `NO`.

## Founder trace

`trace` shows real state only, concurrency included (multiple `● running`
workers, then completions, then QA). No chain-of-thought, no fake
percentages — percentages are forbidden everywhere (worker labels derive
from real job fields; see jobs audit below).

## Marketing runtime parallelism audit (2026-09-18, code-read)

- Real concurrent execution: `app/services/jobs.py:18`
  `ThreadPoolExecutor(max_workers=2)` runs up to 2 persistent
  `opencode run` jobs as real subprocesses (`_run`, `run_task`).
- Real persisted events per worker: `worker_started` / `worker_completed` /
  `worker_failed` + `job_status_changed` are emitted on actual state
  transitions (`jobs.py:114-152`) with labels from real job fields
  (`_worker_label`, no invented progress) and per-job `job_id` scoping.
- Fan-out path: `delegate_to_marketing_pm` / `request_persistent` create one
  real row per worker; multiple delegate requests in a turn run with max 2
  concurrent; follow-ups read persisted rows + result JSON, never chat text.
- Verdict: **REAL** (bounded: max 2 concurrent; queued jobs wait honestly).
- Background job UI: Live Activity renders these real per-job events
  (`chat.html` generic branch) and the Jobs page shows persisted states —
  no percentage fields exist anywhere. Multi-worker display works today;
  no UI change was needed.

## DEV-002 proof run (MEDIUM, approved plan, one approval)

Task: latency follow-ups pack (failed-turn reload, one panel entry per
turn, integration tests). W1 (repos.py) + W2 (chat.html) launched as
simultaneous `opencode run` processes (same-second start, 45 s overlap);
W3 (tests) started only after both completed (`depends_on: [W1, W2]`).
Honest incidents preserved in `executions.jsonl`: 2 spawn failures from
the shim bug (fixed, retried per policy), 1 blocked cascade, 1 W3 process
that wrote its product file but no handoff (recovered by a separate
verification execution that wrote `w3.md`). QA PASS (own 170+2 full-suite
run exposing scheduler flakes) → post-QA infra repair (fractional
timestamps) → full suite 172/0 → reviewer APPROVE with 0 new findings
(own 17-test re-run).

```
WORKER | START | END | OVERLAP
W1 backend  | 16:53:35 | 16:54:25 | W2
W2 frontend | 16:53:35 | 16:54:20 | W1
W3 tests    | 16:54:25 | 16:55:10 | - (dependency respected)
MAX CONCURRENCY: 2 | PARALLEL WORKERS: 2 | PARALLELISM: REAL
```

## Founder approval / autonomy policy

One approval point for MEDIUM/LARGE/ARCHITECTURAL: after the plan is
complete, present goal, classification, packets, parallel plan,
dependencies, risks, acceptance criteria, SOL need, and approval-sensitive
actions — then ask once: "Approve this plan and allow the development team
to execute it autonomously through implementation, QA, review, remediation
and re-review?" After approval the orchestrator autonomously launches
(in parallel where allowed), creates worktrees, tests, integrates, QAs,
reviews, remediates, re-reviews, retries workers, and resolves normal
engineering choices — status updates, never permission questions.
Remediation after FAIL/BLOCK/APPROVE_WITH_CHANGES is pre-authorized within
the approved scope. Re-ask the founder ONLY for: material scope change,
product decision, destructive action, external cost, secret/access need,
SOL strategic choice, invalidated plan, or founder-only information.
PAUSE/STOP/CANCEL/CHANGE PLAN are always respected immediately. Completion
is automatic when gates pass; report the proof block.
