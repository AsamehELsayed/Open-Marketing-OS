# DEV-038 implementer remediation after export review

## Code change

`run_synthetic.py` now calls production `app.services.campaign_deliverables.markdown_export(conn, PROJECT_ID, CAMPAIGN_ID, persisted_id)`, the same service used by the application route. It verifies the returned row ID, project, campaign, and Markdown body against the persisted row, then writes exactly the returned UTF-8 bytes. The prior manual title/body serialization was removed. `test_synthetic.py` adds a regression proving the exact scoped service call and byte output, plus a fail-closed mismatch test. No application code or factual validator changed. Commit: `25c6476`.

`python -m pytest development/runs/DEV-038/test_synthetic.py app/tests/test_dev033_campaign_validation.py -q` passed: **86 tests**.

## Full replay blocker: external input drift

Fresh full offline replay stopped at the pinned input hash guard before copying any DB or generating a deliverable. The original Northstar source DB, previously approved at SHA-256 `f54a384efbbe789e0c7f2ca2eafa9563a4796e08fbd952cc7fe7f790f9eac507`, now hashes `9c1aa99a905408c2a4efee5aea585bc365996560a2832a6165eb563ea997f99d`. The separately prepared template, previously `7805082672e4fb69a22bb4e7f4a944951bc3a49e61a071d18ceae77f08940f19`, now hashes `242b2e03a20bf8e0698f5f7eb91de2d595dd31a005944994dc9d3723bf217503`. Neither changed input was repinned, copied, restored, or edited in this execution. This is **input provenance drift**, not a reproduced export-service or application bug. The approved source snapshot was not found among 22 `marketing.db` files in the acceptance tree. A new full replay cannot honestly pass until the approved immutable source snapshot is recovered or a founder-approved new source is established.

## Read-only production export verification

Against the prior isolated persisted synthetic DB at `synthetic-output/remediation-03/marketing.db` (SHA-256 `7ad2a03f8e0b71a5c23ba6171e58fd2bbffd99bc416f782012e7d92f35fa0420`), a read-only immutable SQLite connection called production `markdown_export` with the Northstar project, campaign, and deliverable ID `del_46e06655d8664d929e022a85de95ffe7`. Its returned row matched the scope and persisted body; `PRAGMA quick_check=ok`. Exactly the service-returned UTF-8 body was written to `synthetic-output/export-verification-01/northstar-strategy-brief-SYNTHETIC.md`, SHA-256 `9a1bfbe840591c1c81069e6fd93c88585b20942dc2cad06783c565991abbed90`. Evidence: `synthetic-output/export-verification-01/verification.json`. This verifies the export seam on an existing persisted deliverable; it is **not** a fresh full end-to-end replay of the changed runner.

No live provider call or inference occurred: real provider requests **0**, real inference POSTs **0**. Independent QA/review and a full replay remain pending the pinned input provenance issue.
