# DEV-032 Focused QA After Review Remediation 4

**Verdict: PASS for the remediation-4 frontend scope.** The deliverables frontend tests, typecheck, production build, both requested real-browser regressions, and `git diff --check` passed. QA did not edit source or test files.

## Execution and environment

- QA execution window (UTC): **2026-10-10T16:04:50Z–2026-10-10T16:05:56.5733336Z**.
- Checkout: `development/worktrees/DEV-032/integrated`.
- Windows PowerShell; Python **3.12.10**, Node **v24.19.0**, npm **11.17.0**. The frontend checks ran locally on Node 24; CI uses Node 20.
- Browser tests required approved local localhost access and used synthetic API fixtures only. They used no live campaign data or external provider.
- The scope in [`workers/review-remediation-4.md`](workers/review-remediation-4.md) is the deliverables editor/helper and frontend regressions; no backend source/tests changed since QA-4. Therefore I cite the unchanged backend evidence in [`qa-final-4.md`](qa-final-4.md): full suite **2,672 passed, 99 skipped, 0 failed**; focused DEV-032 modules **68 passed, 0 failed**. Those backend suites were not repeated in this frontend-only pass.

## Frontend commands

Commands ran from the integration checkout root:

| Command | UTC window | Exit | Outcome |
|---|---|---:|---|
| `npm.cmd run test:dev032-deliverables --prefix frontend` | 16:05:05.8700599Z–16:05:08.1640714Z | 0 | Both Node suites passed: helper/editor/status/history/export/stale-scope checks and workspace stale-guard/CAS/client-scope checks. |
| `npm.cmd run typecheck --prefix frontend` | 16:05:08.1819069Z–16:05:15.5467454Z | 0 | `tsc --noEmit` passed. |
| `npm.cmd run build --prefix frontend` | 16:05:15.5589386Z–16:05:28.4575370Z | 0 | Vite production build passed. |

Logs and exact command/UTC/exit markers are in [`qa-final-5/`](qa-final-5/).

## Deferred-history browser regression

Exact command: `python frontend/tests/dev032_history_race.py`.

- UTC window: **2026-10-10T16:05:35.4008932Z–2026-10-10T16:05:43.9555646Z**
- Exit: **0**. The browser reported that delayed success and error responses from campaign A were discarded after switching to campaign B.

The regression uses the same deliverable ID across campaigns and fulfills both stale A outcomes after the switch; it passed.

## Unsaved create-draft discard browser regression

Exact command: `python frontend/tests/dev032_create_draft_discard.py`.

- UTC window: **2026-10-10T16:05:43.9710409Z–2026-10-10T16:05:49.8598155Z**
- Exit: **0**. Browser output: `DEV-032 create draft guard: blank create, Cancel retention, and accepted saved-row selection passed.`

Assertion review and the live React SPA run verified:

- Opening a blank create form does not prompt.
- With a dirty create draft, invoking `startCreate()` prompts; Cancel retains type, platform, title, Markdown, and the form contents.
- Selecting a saved row with that dirty draft prompts; Cancel retains all four fields, leaves the form open, and does not select the row.
- Accepting the saved-row confirmation closes the form, marks the saved row current, and displays its persisted content.

The browser fixture uses synthetic API responses and stubs confirmation locally. Output and timestamps are in `qa-final-5/browser-create-draft-discard.*`.

## Diff check and disposition

Exact command: `git diff --check`.

- UTC window: **2026-10-10T16:05:56.1615654Z–2026-10-10T16:05:56.5733336Z**
- Exit: **0**. Git emitted only LF-to-CRLF normalization warnings for `app/graphs/account_manager_graph.py` and `development/runs/DEV-032/plan.md`.

**Final focused QA verdict: PASS.** All remediation-4-specific checks passed. The run remains at the remediation stage; this QA execution does not claim review approval or change the stage.
