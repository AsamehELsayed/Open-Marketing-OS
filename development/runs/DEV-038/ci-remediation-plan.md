# DEV-038 CI remediation addendum

## Authorization and source baseline

This remediation continues the already approved DEV-038 run; it does not create a new development run. The replacement branch is based on the freshly fetched `origin/main` commit `3b6dff1a9fd589105c944ff52e918dfcdc6c0e50`. Before source edits, Git confirmed that the full tree hash at that commit (`88881ace7c42781e7db3d538d3bc84263d5c62b0`) exactly matches the approved DEV-037 source commit `7136389aed293c152fb387de2a2f97bc1cd2dd47`; the `app/` subtree hash also matches (`c0ed202e1f6dcf5fb00d1b054516720ed6d1b350`). The original DEV-038 branch and dirty primary checkout remain untouched.

## Findings

- Gitleaks 8.24.3 flagged the synthetic idempotency value because its variable name ended in `_KEY`; the identifier is a fixed synthetic database row identity used to select exactly one previously approved synthetic batch row. Preserve the literal and matching behavior, rename the semantic variable to identify it as a batch ID, and do not add scanner exclusions.
- The Python 3.12 failure is a production race: `BackgroundJobs.upsert` commits terminal state, then terminal execution events are inserted using another connection. A reader can observe the terminal job before either terminal event exists. Commit terminal state and both terminal lifecycle events in one SQLite transaction; keep test synchronization bounded and assert the persisted project/job scope.
- `run_synthetic.py` pins the historical DEV-037 commit and branch name. On the clean branch, pin the approved source tree and clean base commit; require exact full-tree equivalence between them, require HEAD to descend from the pinned clean base, and keep clean checkout and module-origin checks.

## In-scope work and acceptance

1. Repair atomic persistence for terminal background-job state and lifecycle events.
2. Add a focused regression that verifies each terminal state is observed with its matching lifecycle event and existing project isolation.
3. Rename the flagged synthetic batch identifier without changing its literal database identity or verification behavior.
4. Adapt the synthetic source guard to the clean `origin/main` ancestry while proving approved full-tree equivalence.
5. Run the lifecycle regression repeatedly on Python 3.12, focused and full backend tests, synthetic provenance/E2E, Gitleaks, frontend, packaging, and Windows checks as available. Do not make real provider calls or change real client/source data.
6. Obtain independent QA and review after the fixes; repair and re-review any in-scope findings.
