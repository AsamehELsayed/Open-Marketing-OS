# DEV-038 independent review

VERDICT: APPROVE_WITH_CHANGES

This is a separate general-lane reviewer execution after the independent post-remediation QA. Reviewed the plan, both worker reports, failed and passing QA reports, full diff from `7136389a`, runner and regression tests, and the persisted synthetic evidence. No code, source database, or provider was modified by this review.

## Confirmed

- `python -m pytest development/runs/DEV-038/test_synthetic.py app/tests/test_dev033_campaign_validation.py -q` passed: **84 passed**.
- The runner uses production `ModelRouter`, `generate_batch`, unchanged factual validation, and production persistence with one scripted local provider call. Its source guard checks a clean descendant of the pinned DEV-037 tree, exact branch, and production module origins. Socket creation is blocked for the execution portion; real provider requests and inference POSTs are zero.
- The QA replay has one synthetic `strategy_brief` row in its isolated SQLite database. Its cited Northstar fact uses a selected-project evidence ID. The brief has substantive proposed audience, messaging, seven distinct days, visual guidance, measures, and explicit unknowns. Source database hash and unrelated-project fingerprint remain unchanged; SQLite `quick_check` passes.

## Required change: use the production Markdown export seam

`run_synthetic.py` currently reads the persisted row through `production.list_deliverables`, then constructs a file with `f'# {created["title"]}\n\n{created["content_md"]}\n'`. The application's export route, `app/routes/campaign_deliverables.py::export_markdown`, instead calls `app.services.campaign_deliverables.markdown_export`, which performs the scoped read and returns the stored Markdown body. The current acceptance run never calls that export service and its file bytes differ from the application's export response. The requested end-to-end export is therefore not yet verified.

Within the approved scope, export the persisted ID through `markdown_export(conn, PROJECT_ID, CAMPAIGN_ID, created["id"])`, assert the returned row ID and body match the persisted record, and write exactly the returned Markdown body. Add a focused regression that proves the runner calls this scoped export path. Replay in a fresh output directory, then have independent QA verify the exported bytes against the scoped service result and file hash before a separate re-review.

## Remaining limit

The passing synthetic path does not establish current OpenRouter eligibility, connectivity, ZDR status, or live inference. Those remain outside the present authorization. No merge or deployment is approved by this review.
