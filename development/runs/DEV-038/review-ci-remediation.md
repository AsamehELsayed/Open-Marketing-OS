# Independent review — DEV-038 CI remediation

**VERDICT: APPROVE**

## Scope

Reviewed the uncommitted replacement-branch changes against `origin/main` and DEV-038’s approved CI-remediation scope:

- Atomic persistence of terminal `background_jobs` state and terminal execution events.
- The bounded lifecycle regression.
- The synthetic batch identifier rename and unchanged data identity.
- The synthetic runner's approved-tree / clean-main provenance guard.
- DEV-038 run evidence and diff hygiene.

This is an independent review of the code and artifacts, not a statement that GitHub CI or the full desktop acceptance has passed.

## Review findings

No blocking correctness, architecture, security, or performance findings.

- `app/services/jobs.py::_run` writes the terminal job row, matching worker terminal event, and terminal status event using one connection and one `BEGIN IMMEDIATE` transaction. The event order is explicit. If either insert or commit raises, the handler rolls back and re-raises, so readers cannot observe a partially committed terminal transition. Initial `BEGIN IMMEDIATE` failure occurs before these writes.
- `app/database/repos.py` adds opt-in `commit=False` behavior while preserving existing commit-by-default behavior for callers. SQL values remain parameterized; table names are existing internal constants.
- The lifecycle test uses a monotonic bounded deadline, derives the expected worker event from the persisted terminal state, checks event ordering and project isolation. It no longer relies on an unbounded 60-second wall-clock loop.
- `SYNTHETIC_BATCH_ID` keeps the original fixed row identity and lookup predicate. No Gitleaks rule, suppression, or allowlist was added.
- `run_synthetic.py::verify_source` pins the approved commit and clean-main commit, requires matching full-tree hashes, clean-main ancestry, the intended branch, clean checkout state, and production-module origins within the checkout. The new negative test covers a mismatched clean-main tree.
- Reviewed the new CI-remediation plan, implementer handoff, QA artifact, and existing DEV-038 run records. `git diff --check` was clean. The run’s `.gitignore` excludes `synthetic-output/`; no generated database or temporary test output appeared in Git status.

## Verification performed

Working directory: `C:\Users\User\Documents\open-marketing-os\development\worktrees\DEV-038-ci-repair`

```text
Python 3.12 focused lifecycle + synthetic source tests: 15 passed in 4.07s
`git diff --check`: passed, no output
```

The separate QA artifact reports 19 focused passes including the full streaming test module. This review independently ran the lifecycle regression and synthetic runner tests; it did not repeat the entire app suite.

## Nonblocking verification limits

- The positive `verify_source()` path and full synthetic replay require a clean committed checkout, so they were not run from this dirty review checkout. Run them after the initial remediation commit.
- No injected SQLite insert/commit failure test was run. Rollback behavior was reviewed by inspection; a persistent database write failure can leave the job in `running`, while preserving the atomic terminal-state/event invariant. Recovery from that condition is outside this remediation.
- Full backend verification was not completed locally; the implementer reports the Python 3.12 full suite stalled without a failure and was interrupted. Replacement-branch GitHub CI remains authoritative for the matrix and Gitleaks.
- This review does not verify the later desktop acceptance workflow or any GitHub CI result.

## Verdict rationale

The changes address the two observed CI failures and strengthen clean-branch provenance without widening DEV-038 scope or weakening scanning. The remaining items are execution gates for the clean committed checkout and GitHub CI, not code defects requiring changes before review approval.
