# Development Delegation System

> Corrective action for `docs/delegation-integrity-audit.md` (verdict: FAIL).
> File-based orchestration. No new infrastructure. Enforced by
> `scripts/dev_run.py` gates (fail-closed) + execution records.

## 1. Pipeline

```
FOUNDER REQUEST → DEVELOPMENT PM (orchestrator) → classify
  → PLANNER (MEDIUM+) → IMPLEMENTATION WORKERS → INTEGRATION (LARGE)
  → QA (independent) → REVIEW (independent)
  → REMEDIATION (if changes requested) → FINAL RE-REVIEW → APPROVED
```

Not every task needs every role. Minimum per classification (§2).
Planning and review cannot be silently skipped on MEDIUM+ — the gate script
blocks `implementing` without `plan.md` and blocks `approved` without a
completed reviewer execution ending in `APPROVE`.

## 2. Task classification

| Class | Shape | Required roles |
|---|---|---|
| TINY | typo / trivial copy / obvious one-line fix | implementer + tests |
| SMALL | bounded bug or local UI fix | mini-plan + implementer + QA |
| MEDIUM | multi-file feature | planner + 1+ workers + QA + reviewer |
| LARGE | multi-system feature | planner + multiple workers + integrator + QA + reviewer (+remediation if needed) |
| ARCHITECTURAL | provider/DB/security architecture, major milestone | SOL planning gate + workers + cheap QA + SOL milestone/final review |

Classification is recorded in `run.json` at creation and never downgraded
to dodge gates (upgrades allowed with a note).

## 3. Role → agent mapping (this runtime)

Independent execution = a separate `Task`-lane call (or a separate
`opencode run` process) with its own execution record. Prose is not execution.

| Role | Agent | Rule |
|---|---|---|
| Development planner (technical) | `general` lane | Writes `plan.md`. NOT the SOL `planner` lane — that lane is strategic and SOL-gated per `AGENTS.md`. |
| Implementer / worker | `general` lane, narrow packet | One packet per worker, disjoint files where possible. Max 4 parallel. No nested delegation (packets forbid sub-delegation). |
| Integrator (LARGE) | `general` lane | Merges worker outputs, resolves conflicts. |
| QA | `qa` lane | Separate execution from implementer. Never repeats implementer claims; inspects behavior, regressions, tests, edge cases, isolation, approval safety, UI. Writes `qa.md`. |
| Reviewer (routine MEDIUM/LARGE) | `general` lane acting as reviewer, or second `qa` execution | Reads plan + handoffs + diff + tests + QA report. Verdict `APPROVE` / `APPROVE_WITH_CHANGES` / `BLOCK` in `review.md` with concrete findings. Must be a different execution from implementer AND from QA. |
| Reviewer (gated) | `review` lane (SOL) | ONLY when the SOL Review Gate is met (positioning/pricing/offer change, real spend/launch risk, major brand direction, LOW confidence after QA, explicit founder request). |
| Planner (gated) | `planner` lane (SOL) | ONLY when the SOL Planning Gate is met (see `AGENTS.md`). |
| Browser | `browser` lane | ONLY on Antigravity escalation per the browser gate. |

SOL policy: SOL plans/reviews; **SOL never implements**. No SOL lane usage
is recorded as `ENFORCED` only when zero `planner`/`review`-lane executions
occur without a gate trigger noted in `run.json`.

## 10. Lane availability fallback (proven in DEV-001)

The binding constraint is **execution independence**, not the lane name. If a
designated lane is unavailable in the environment (DEV-001: the `qa` lane
failed with a free-tier entitlement error), the orchestrator may substitute
the `general` lane **provided**: (a) it is a separate execution from the
implementer(s) and from any other role in the run, (b) it applies the full
role rubric, and (c) the substitution is disclosed in the artifact header and
in the execution record (`role=qa, agent=general`). Never merge two roles
into one execution.

## 11. Gate implementation notes

- `review_verdict()` resolves the *latest* `review*.md` by file mtime, not by
  filename sort (`review-2.md` sorts before `review.md` lexicographically —
  fixed during DEV-001 after the gate misread the verdict as BLOCK).

## 4. Artifacts (`development/runs/<run_id>/`)

```
run.json            run_id, title, classification, status, planner/workers/qa/reviewer,
                    required_roles, created_at, updated_at, current_stage
executions.jsonl    one JSON record per delegated role execution:
                    run_id, parent_run_id, role, agent, task,
                    started_at, completed_at, status, input_artifact, output_artifact
plan.md             goal, scope, non-goals, files/systems affected, implementation
                    packets, dependencies, risks, test plan, acceptance criteria
workers/<w>.md      task received, files changed, tests run, known issues, handoff
qa.md               independent findings: PASS / PASS_WITH_CHANGES / FAIL + evidence
review.md           VERDICT: APPROVE / APPROVE_WITH_CHANGES / BLOCK + concrete findings
review-2.md …       re-review artifact(s); mandatory after any remediation
remediation.md      review findings → fix tasks → dispositions (only if review requested changes)
trace.md            frozen visible trace at completion
matrix.md           frozen delegation matrix at completion
```

`run.json` statuses: `planning | implementing | integrating | qa | review |
remediation | approved | failed`.

## 5. Gates (fail-closed, `scripts/dev_run.py gate --id X --stage <s>`)

- `implementing` on MEDIUM+: blocked unless `plan.md` exists.
- `review` and beyond: blocked unless a completed QA execution with an
  on-disk `qa.md` exists — no `TESTED` claim without it.
- `approved`: blocked unless the latest `review*.md` verdict is `APPROVE`
  from a completed reviewer execution with artifact on disk.
- `approved` with `remediation.md` present: blocked unless a `review*.md`
  newer than `remediation.md` exists (no auto-approve after fixes).
- Allowed transitions are recorded via `set-stage`; the founder-visible
  `trace` command shows only actual role executions, never chain-of-thought.

## 6. Remediation loop

`APPROVE_WITH_CHANGES`/`BLOCK` → orchestrator writes `remediation.md`
(finding → fix task → owner) → implementer fixes → reviewer runs AGAIN
(new execution record + `review-2.md`). A fix without re-review is not
approval; the gate enforces this by mtime comparison.

## 7. Visible trace

`python scripts/dev_run.py trace --id X` prints per-role `[OK]/[>>]/[--]`
lines plus the latest review verdict. No hidden reasoning is exposed —
only which roles actually executed.

## 8. Delegation proof

`python scripts/dev_run.py matrix --id X` prints
`ROLE | ACTUAL RUN? | AGENT | ARTIFACT`. `ACTUAL RUN?` is `YES` only when a
`completed` execution record exists AND the artifact file exists on disk.
Anything else is `NO` — and must not be claimed as delegated.

## 9. Provenance of this system

- Audit: `docs/delegation-integrity-audit.md` (FAIL on prior work).
- Proof run: `development/runs/DEV-001/` (MEDIUM test task, full pipeline
  with real lane executions, remediation + re-review).
