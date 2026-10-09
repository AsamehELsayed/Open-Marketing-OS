# DEV-031 Phase A Independent QA

**Execution:** separate general QA execution substituting for the unavailable OpenCode `qa` lane, as allowed by `docs/development-delegation-system.md §10`. This was read-only QA; no implementation files were changed.

**Started:** 2026-10-09 16:45:19 UTC
**Completed:** 2026-10-09 16:52:10 UTC
**Verdict: FAIL**

## Scope reviewed

Read the supplied workspace operating contract, `docs/development-delegation-system.md`, `plan.md`, `acceptance.md`, and `qa-brief.md`. The target checkout has no local `AGENTS.md`; the workspace-level contract supplied for this task was applied.

Inspected the complete tracked diff and all untracked files in the DEV-031 worktree, including the migration, service and route, frontend workspace/create flow, changed regressions, and run artifacts. Inspected the browser acceptance record but did not personally run that browser scenario.

## Findings

### F1 — FAIL: profile project key is nullable in SQLite

`app/database/sqlite.py::_migrate_v12_to_v13` declares `project_id TEXT PRIMARY KEY` without `NOT NULL`. In SQLite, a non-integer primary key does not itself imply `NOT NULL`; `PRAGMA table_info(business_profiles)` reports `notnull=0`. A profile row without a project ID can therefore be inserted, violating the project-scope invariant and allowing unscoped profile data.

Reproduced by:

```text
python -m pytest -q app/tests/test_dev008g_publish_gate.py::test_every_project_id_column_default_is_generic
FAILED: business_profiles.project_id has no DEFAULT and is nullable
1 failed in 2.07s
```

This is an independent code failure, not a sandbox limitation. The migration should declare this key `NOT NULL` while retaining the intended no-cascade behavior.

### F2 — citation validation only checks one notation

`app/routes/business_workspace.py::generate_brief` rejects unsupported source IDs only when they match the square-bracket pattern `[document:chunk]`. The generation prompt asks for source IDs but does not specify that canonical notation. A citation-like reference such as `Source: invented:reference` or `(invented:reference)` is not checked by this validator. The existing invalid-citation test covers only the bracketed form. Pin a canonical citation syntax in the prompt and validation, or reject unsupported citation-like IDs in other forms, before claiming all generated references resolve.

## Checks that passed

- `python -m pytest -q app/tests/test_dev031_business_workspace.py`: **4 passed**. Covers v13 startup, name-only creation, profile revision conflicts, isolated root reindex/reimport, generated section labels, bracketed invalid-citation rejection, overwrite confirmation, provider failure preservation, and no-provider error detail.
- Broad focused regression batch (17 files: DEV-011/012/013/014/015/030, DEV-031, automatic tools, tool permissions, and publish gate): **167 passed, 1 skipped, 2 failed**. F1 is the code failure above. The other failure is `test_allowed_website_read_runs_with_campaign_suggestion_and_no_write`, which requires a successful live `https://example.com` audit; it did not get a successful tool run in this environment. Treat that result as network-dependent, not as evidence of a DEV-031 regression.
- `npm run typecheck`: **passed**.
- `npm run build`: **passed** when rerun with elevated access. The initial sandboxed attempt failed at Vite `realpath` with Windows `EPERM` for `frontend/src/main.tsx`; elevation resolved that environment restriction.
- `git diff --check`: **passed**.

## Requirement assessment

- Name-only client creation and optional remaining fields are reflected in the create flow and profile UI; existing onboarding remains on its original route and API path. The recorded browser acceptance reports the two-client scenario, but I did not rerun it.
- Generation status and provider/model fields are surfaced when supplied; errors are translated to plain-language messages, and the brief remains editable after a failed generation.
- Regeneration has an explicit browser confirmation. The API also refuses to replace a persisted brief unless `overwrite_confirmed` is set, then persists with a revision compare-and-swap.
- Required brief section headings are checked; the focused tests confirm missing sections and a bracketed invented citation are rejected without replacing the saved brief. Citation-format gap F2 remains.
- Profile and brief writes use expected revisions and conditional updates. Generation snapshots both revisions and conditionally saves only if neither changed. The focused tests cover stale profile revisions and preservation on generation failure; they do not simulate two simultaneous writers.
- New client workspaces carry the isolation marker. Workspace-root reindex skips shared root evidence, shared workspace import is skipped for marked clients, and generation calls scoped retrieval with the path project ID. The focused tests cover root reindex and reimport isolation.
- The migration is additive, has no cascading project foreign key, and the migration/profile tests pass. The nullability invariant F1 fails.
- No CRM, Phase B, installer changes, or new campaign/agent system were found in the implementation diff.

## Scope and evidence notes

The worktree also contains untracked smoke data under `development/runs/DEV-031/smoke/data/data` (SQLite and Chroma databases plus an uploaded synthetic file). These are local acceptance artifacts, not implementation changes; keep them out of any PR if they are not intended as fixtures. The actual browser acceptance evidence is documented in `acceptance.md`; real AI generation was unavailable there, so its provider/model behavior is supported by deterministic tests rather than a real model run.
