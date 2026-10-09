# DEV-031 Phase A Independent QA — Final After Remediation

**Execution:** Separate general QA execution substituting for the unavailable OpenCode `qa` lane, as allowed by `docs/development-delegation-system.md §10`. Read-only implementation review; no implementation files were changed.

**Started:** 2026-10-09 17:01:47 UTC
**Completed:** 2026-10-09 17:06:58 UTC
**Verdict: FAIL**

## Scope reviewed

Read `qa-brief.md`, the approved `plan.md`, `acceptance.md`, `qa-initial.md`, `remediation.md`, the supplied workspace operating contract, and `docs/development-delegation-system.md`. The isolated checkout has no local `AGENTS.md`; the supplied contract was applied.

Reviewed the full tracked diff and untracked implementation files, plus the relevant run artifacts, including the SQLite migration, profile/brief service and routes, project isolation changes, frontend workspace/create flow, regression tests, browser acceptance record, and smoke launch configuration. Inspected but did not personally rerun the browser scenario. No implementation files were modified.

## Initial QA findings

### F1 — CLOSED: nullable profile project key

The v13 migration now declares `business_profiles.project_id TEXT PRIMARY KEY NOT NULL`. The project-ID schema audit explicitly checks non-nullability for this default-free key. The requested test batch passed, including `test_every_project_id_column_default_is_generic`.

### F2 — CLOSED for the reported citation formats

The generation prompt specifies canonical `[document_id:chunk_id]` citations. The API rejects unknown bracketed IDs, noncanonical `Source: invented:reference` labels, and parenthesized `(invented:reference)` IDs. The DEV-031 regression test exercises `[invented:source]`, `Source: invented:reference`, and `(invented:reference)`; for each rejected response it verifies that the previously saved manual brief and its revision remain unchanged.

## Finding

### F3 — FAIL: regeneration confirmation misstates when the saved brief is replaced

In `frontend/src/routes/Workspace.tsx:77`, the confirmation says: “Your saved brief will remain unchanged unless you save the result.” On successful generation, `app/routes/business_workspace.py:159` immediately updates the persisted brief and increments its revision; the route then commits before returning. The UI also reports that the generated brief is saved.

The confirmation therefore tells the user their saved brief is protected until a separate save, while confirming regeneration actually replaces it as soon as generation succeeds. A user can lose manually edited text after relying on that statement.

Change the confirmation copy to clearly state that a successful regeneration immediately replaces the saved brief, or keep the generated response as an unsaved draft until the user explicitly saves it. This conflicts with the founder-approved requirement for an informed replacement confirmation, so the final QA verdict is FAIL even though F1 and F2 are fixed.

## Checks and evidence

- Ran `python -m pytest --basetemp=.pytest-tmp/basetemp -p no:cacheprovider -q app/tests/test_dev008g_publish_gate.py app/tests/test_dev031_business_workspace.py app/tests/test_dev014_integration.py app/tests/test_dev030_chat_backend.py`: **39 passed, 1 skipped, 13 warnings** in 13.31 seconds. Used the approved elevated routine test execution because Windows TestClient socketpair access is blocked inside the workspace sandbox.
- `git diff --check HEAD`: passed.
- The tests verify that a saved brief cannot be regenerated without `overwrite_confirmed`, stale profile revisions are rejected, and invalid model output/provider failures preserve the previously saved brief and revision. Generation also writes conditionally on both the snapshotted profile and brief revisions.
- The recorded browser acceptance documents name-only Northstar/Harbor workspaces, persistence across restart, isolation of files/briefs, and explicit cancellation of regeneration without changing the saved text/revision. This QA execution did not operate the browser itself.
- Real AI generation was blocked because no provider was ready. The acceptance record says the local model runtime was unavailable and OpenAI/OpenRouter credentials were not configured; the smoke backend script uses a disposable credentials directory and removes OpenAI credential environment variables. The smoke logs are empty. No real model output or provider route is claimed. The recorded acceptance reports frontend typecheck and production build passed; they were not rerun in this final QA execution.
- The worktree contains untracked `.pytest-tmp/` and disposable smoke data under `development/runs/DEV-031/smoke/data/data`. Keep those generated databases out of the code change unless intentionally retained as run evidence.
