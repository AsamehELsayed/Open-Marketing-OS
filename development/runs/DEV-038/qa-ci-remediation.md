# Independent QA — DEV-038 CI Remediation

**Verdict: PASS (focused scope), with validation limits noted below.**

## Findings

- **Terminal job persistence and lifecycle events — PASS.** `app/services/jobs.py::_run` uses one `BEGIN IMMEDIATE` transaction for the terminal `background_jobs` upsert, `worker_completed`/`worker_failed` insertion, then `job_status_changed`. The terminal event is inserted first; both inserts and the state row commit together. The existing `ExecutionEvents.for_job` query preserves row-ID ordering and filters by project. Each new event receives the same job, project, and conversation IDs. `ExecutionEvents.insert(..., commit=False)` and `BackgroundJobs.upsert(..., commit=False)` preserve normal commit behavior by default while allowing this transaction to own its commit.
- **Transaction error path — PASS by inspection.** If either upsert/insert or `commit()` raises, the exception handler rolls back and re-raises; no terminal state or partial terminal event set is committed. If `BEGIN IMMEDIATE` itself fails, writes have not begun. The worker thread will terminate with an exception and the job may remain `running`; the atomicity invariant is preserved, but recovery from persistent DB write failure is outside this remediation. No injected commit/insert failure regression was present or run.
- **Bounded regression — PASS.** The lifecycle test uses `time.monotonic()` with a 15-second deadline and 20 ms polling, reads the final persisted job, derives the expected terminal event from its persisted status, requires the corresponding terminal status event after that event, and checks that another project receives no rows. The requested test passed. The polling timeout is bounded; an actual timeout fails on the persisted terminal-state assertion.
- **Synthetic batch ID rename — PASS.** `SYNTHETIC_BATCH_ID` retains the original literal `35cd5bf40fbad25b5c87610aa1f74144`; the DB lookup still uses the unchanged `idempotency_key` column and same row predicate. The rename is semantic only and no scanner suppression was introduced.
- **Source guard — PASS by inspection and negative regressions.** The runner pins both the approved DEV-037 commit/tree and the clean-main commit. It requires their full tree hashes to equal the approved tree, requires `HEAD` to descend from the pinned clean-main commit, and retains branch-name, clean-checkout, repository-root, and imported-module-origin checks. Synthetic tests exercise rejection of a dirty checkout, wrong ancestry, and a non-equivalent clean-main tree. Because this QA worktree contains the uncommitted remediation diff and QA output, the positive `verify_source()` run correctly cannot pass here; positive-path execution on the eventual clean committed CI checkout remains unverified.
- **Diff hygiene — PASS.** `git diff --check` produced no output. Test basetemp is under the DEV-038 ignored `synthetic-output/` directory. No application or test code was modified by this QA execution.

## Commands and results

Working directory: `C:\Users\User\Documents\open-marketing-os\development\worktrees\DEV-038-ci-repair`

```powershell
python --version
# Python 3.12.10

python -c "import sys; assert sys.version_info[:2] == (3,12), sys.version"
# exit 0

python -m pytest -q app/tests/test_v021_streaming.py::test_job_lifecycle_events_persist_and_isolate development/runs/DEV-038/test_synthetic.py app/tests/test_v021_streaming.py --basetemp=development/runs/DEV-038/synthetic-output/qa-ci-remediation-basetemp
# 19 passed in 8.57s

git diff --check
# exit 0; no output
```

## Limits

This is focused independent QA, not full CI. It does not execute the synthetic end-to-end runner, a positive clean-checkout source-guard run, or an injected SQLite transaction-failure test. Tests made no real provider calls and no network access was used. The worktree was not committed or otherwise modified beyond this QA report and ignored test basetemp output.
