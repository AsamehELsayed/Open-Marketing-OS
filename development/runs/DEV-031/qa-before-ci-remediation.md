# DEV-031 Phase A Independent QA — Final After SOL Remediation

**Execution:** Separate general QA execution substituting for the unavailable OpenCode `qa` lane, as permitted by `docs/development-delegation-system.md §10`. Read-only review; implementation files were not changed.

**Started:** 2026-10-09 17:22:26 UTC
**Completed:** 2026-10-09 17:27:06 UTC
**Verdict: PASS**

## Scope reviewed

Read the updated `qa-brief.md`, approved `plan.md`, latest `remediation.md`, updated `acceptance.md`, `qa-initial.md`, `qa-remediation-1.md`, the prior `qa.md`, `review-initial.md`, and the supplied workspace contract. Applied the development delegation policy; this isolated checkout has no local `AGENTS.md`. Reviewed the complete tracked diff and untracked implementation/test files, including the shared profile validator, citation parser, conflict-safe initialization, workspace selection helper, and both frontend regressions. The recorded browser evidence was inspected; I did not personally operate the browser.

## SOL review finding verification

- **F1 — citations: CLOSED.** `has_unsupported_citations` validates bracketed colon references against exact IDs from scoped retrieval, then scans source-like colon tokens across other punctuation and bare forms. Unknown or noncanonical references are rejected before the profile update that writes the brief, source list, or generation metadata. Regression cases include bracketed punctuation, a bare punctuated ID, a source label, and a parenthesized ID. Tests assert the prior brief and revision remain unchanged; the route’s pre-write rejection path leaves provenance untouched.
- **F2 — client creation fields: CLOSED.** Creation and update use the same `validate_profile_fields` normalization and type/length checks. Creation validates before opening its insert transaction, persists each supplied supported field, and leaves omitted fields blank. Tests cover optional field round-trip and confirm invalid creation creates no project row.
- **F3 — selection races: CLOSED.** Each of `saveProfile`, `saveBrief`, and `generate` captures the project ID and `requestGeneration` epoch. Success, error, and finalization updates require both to match the current selection. The frontend regression simulates A→B→A epoch changes and checks all three handlers use the guard.
- **F4 — legacy profile initialization: CLOSED.** `get_profile` uses `INSERT OR IGNORE` for the project-keyed profile, then reloads the winning row. The synchronized two-reader test verifies both reads succeed and only one profile row exists.

The earlier QA findings are also closed: the v13 project key is explicitly `NOT NULL`; the originally reported citation forms remain rejected; and regeneration confirmation now accurately says successful generation immediately replaces saved content and discards unsaved editor changes.

## Product and scope assessment

Name-only client creation and existing first-run onboarding remain supported. Provider/model status and plain-language errors are surfaced, with manual editing available after generation failure. Profile and brief revisions are checked; generation snapshots both revisions and writes conditionally. The selected project ID scopes Knowledge retrieval, and isolated clients skip shared root indexing/import. The UI labels user facts, retrieved evidence, AI suggestions, and missing information. Existing regression coverage still exercises onboarding, publishing guards, model selection, and Markdown export.

No new scope issue was found. The reviewed changes remain within Phase A and do not add CRM, campaign deliverables, Phase B, multi-user authentication, billing, a portal, publishing integration, installers, or an agent/provider stack.

## Checks and limitations

- Backend: `python -m pytest --basetemp=.pytest-tmp/basetemp -p no:cacheprovider -q app/tests/test_dev008g_publish_gate.py app/tests/test_dev031_business_workspace.py app/tests/test_dev014_integration.py app/tests/test_dev030_chat_backend.py` — **40 passed, 1 skipped, 13 warnings** in 14.68 seconds. Used elevated access because Windows TestClient socketpair access is blocked in the workspace sandbox.
- From `frontend`: `node tests/dev031-workspace-confirmation.test.mjs` — passed; `node --experimental-strip-types tests/dev031-workspace-selection.test.mjs` — passed.
- From `frontend`: `npm run typecheck` — passed; `npm run build` — passed with elevated access (Vite built 97 modules).
- `git diff --check HEAD` — passed.
- The A→B→A regression checks the selection helper and asserts handler guards in source; it does not run a mounted React component or a real browser delay sequence. Browser evidence records the two-client scenario, restart persistence, isolation, and the provider-failure follow-up, but this QA execution did not rerun that scenario.
- Real AI generation was blocked in the acceptance environment because the local model runtime was unavailable and no OpenAI/OpenRouter credentials were configured. No real model output is claimed; deterministic fake-provider tests are test evidence only.
