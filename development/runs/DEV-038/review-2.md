# DEV-038 independent re-review after export repair

VERDICT: BLOCK

This separate reviewer execution inspected `review.md`, `remediation-2.md`, `workers/remediation-02.md`, `qa-3.md`, the export code and test diff since `7c911859`, and the previous persisted/exported evidence. No application code, input database, provider, or live inference was changed or contacted.

## Export repair: technically sound

The runner now calls the same scoped `campaign_deliverables.markdown_export` service as the application route, with the selected project, campaign, and persisted deliverable ID. It rejects a returned row with mismatched identity or body and writes exactly the returned UTF-8 Markdown bytes. The focused regressions verify both the call and fail-closed behavior. My rerun of `python -m pytest development/runs/DEV-038/test_synthetic.py app/tests/test_dev033_campaign_validation.py -q` passed: **86 passed**. QA independently used the production service on a prior persisted synthetic record and confirmed the exported byte hash. The prior review's code finding is resolved.

## Full acceptance remains blocked by changed input provenance

The required fresh complete run of the repaired runner cannot begin: its approved Northstar input DB was pinned at SHA-256 `f54a384efbbe789e0c7f2ca2eafa9563a4796e08fbd952cc7fe7f790f9eac507`, but my read-only hash is now `9c1aa99a905408c2a4efee5aea585bc365996560a2832a6165eb563ea997f99d`. QA reproduced the expected `Approved input database hash mismatch` before any copy, generation, persistence, or export. Keeping this guard is correct; the changed input has not been verified and must not be silently repinned. Earlier full synthetic runs establish generation, validation, and persistence at a prior commit; the repaired export was separately checked on that saved record. Together these are strong component evidence, but they are not the fresh source-guarded end-to-end run required by the plan and mission. QA-3 is therefore BLOCKED, and this reviewer cannot return APPROVE or mark DEV-038 complete.

## Shortest path to approval

Recover the approved immutable source DB with its pinned hash, then run the repaired `run_synthetic.py` in a new output directory. If that snapshot is unavailable, obtain explicit founder approval for a newly verified Northstar source and update the source manifest/guard only after checking identity, scoped evidence, integrity, and unrelated-project state. Then have independent QA verify a fresh persisted row and exact production export bytes, followed by another separate review. Do not invoke a real provider or alter the original evidence to force a pass.

Real OpenRouter eligibility, connectivity, ZDR, and live output remain unverified. Real provider requests: **0**; real inference POSTs: **0**.
