# DEV-031 Phase A Independent QA — Final After Remediation

**Execution:** Separate general QA execution substituting for the unavailable OpenCode `qa` lane, as permitted by `docs/development-delegation-system.md §10`. Read-only review; implementation files were not changed.

**Started:** 2026-10-09 17:09:33 UTC
**Completed:** 2026-10-09 17:12:10 UTC
**Verdict: PASS**

## Scope reviewed

Re-read `qa-brief.md`, the approved `plan.md`, `qa-initial.md`, the prior final QA report and its remediation report, `remediation.md`, and the updated `acceptance.md`. Applied the supplied workspace operating contract; the isolated checkout has no local `AGENTS.md`. Reviewed the full tracked diff and untracked implementation changes, including the latest regeneration confirmation and `frontend/tests/dev031-workspace-confirmation.test.mjs`. The recorded browser scenario was inspected, not personally rerun.

## Remediation verification

- **F1 — CLOSED:** `business_profiles.project_id` is declared `TEXT PRIMARY KEY NOT NULL`; the schema audit asserts the default-free key is non-nullable.
- **F2 — CLOSED for the reported formats:** generation specifies canonical `[document_id:chunk_id]` citations. The API rejects unsupported bracketed IDs, `Source: invented:reference`, and `(invented:reference)`. The regression test verifies those rejected outputs leave the saved manual brief and its revision unchanged.
- **F3 — CLOSED:** the confirmation now says successful generation saves over current saved content and discards unsaved editor edits. The new frontend assertion checks the wording, the confirmation guard, the `overwrite_confirmed` request flag, and that successful generation replaces the displayed editor state. The browser follow-up reports the no-provider failure preserved the saved brief and kept manual editing available.

No new findings or out-of-scope changes were identified in this pass. The implementation remains within Phase A: project-specific profiles and briefs, isolated client knowledge, existing ModelRouter use, and the requested navigation/create flow. The reviewed diff does not add CRM, Phase B, multi-user authentication, billing, a portal, publishing integration, installer changes, or a new agent/provider system.

## Requirement and regression assessment

Name-only client creation is supported while first-run onboarding remains on its prior path. Profile and brief writes check expected revisions; generation snapshots both revisions and updates conditionally. Generation uses the path project ID for scoped retrieval, validates required fact/evidence/suggestion/unknown sections, and preserves prior content on malformed output or provider failure. The workspace clears content while switching projects and ignores stale responses by selected project ID. Existing regression coverage includes migration/onboarding, publishing guards, project file scope, model selection, and Markdown export.

## Checks and evidence

- Focused backend batch: `python -m pytest --basetemp=.pytest-tmp/basetemp -p no:cacheprovider -q app/tests/test_dev008g_publish_gate.py app/tests/test_dev031_business_workspace.py app/tests/test_dev014_integration.py app/tests/test_dev030_chat_backend.py` — **39 passed, 1 skipped, 13 warnings** in 9.85 seconds. Elevated execution was used because Windows TestClient socketpair access is blocked in the workspace sandbox.
- `node frontend/tests/dev031-workspace-confirmation.test.mjs` — passed: `DEV-031 Regenerate confirmation: saved replacement and discarded edits are stated explicitly`.
- `git diff --check HEAD` — passed. The new frontend test also has no trailing whitespace.
- The updated acceptance and prior QA reports record frontend typecheck and production build as passed; this execution inspected that evidence but did not rerun those commands after the copy-only remediation. The frontend worker’s earlier handoff says dependencies were unavailable at worker time, while the later QA/acceptance records report successful checks. The browser acceptance additionally records the manual follow-up after the wording change.
- Real model generation remains blocked because no provider was ready: the acceptance server had no configured OpenAI/OpenRouter credentials and its local model runtime was unavailable. The smoke script uses an isolated credentials directory and removes provider credential environment variables. No real model output is claimed; deterministic fake-provider coverage is test-only.
- The worktree contains generated `.pytest-tmp/` files and disposable smoke databases under `development/runs/DEV-031/smoke/data/data`; these are test/acceptance artifacts, not implementation changes.
