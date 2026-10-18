# DEV-038 implementer execution

## Classification and repairs

- **Harness provenance defect:** The canonical untracked acceptance launcher pins exactly DEV-037 commit `7136389a`, so it cannot accept a clean DEV-038 descendant. The new scoped offline runner verifies the approved base tree `88881ace7c42781e7db3d538d3bc84263d5c62b0`, descendant ancestry, exact DEV-038 branch, clean checkout, and production module origins. It fails closed on wrong base or dirty state. The old live runner was not copied or altered.
- **Harness import ordering defect:** The first synthetic attempt locked sockets before the lazily imported canonical citation validator loaded; generation correctly failed with `output_validation`. Preloading `app.routes.business_workspace.diagnose_unsupported_citations` before the socket lock preserves the real validator and zero network access.
- **Synthetic fixture defects:** An uncited fragment and then unsupported metric wording were rejected by the real validator. The final scripted response uses exact supported Northstar source wording with its scoped citation and labels recommendations/unknowns. No factual validation rule was changed.
- **Database initialization:** No application defect reproduced. The copied prepared DB opened through production `app.database.sqlite.connect`, retained schema `user_version=14`, and passed `PRAGMA quick_check` before and after generation. No app DB code changed.

## Implementation and evidence

- Runner: `development/runs/DEV-038/run_synthetic.py`; focused regressions: `development/runs/DEV-038/test_synthetic.py`.
- Approved source commit: `7136389aed293c152fb387de2a2f97bc1cd2dd47`; accepted descendant: `9a058e225573342821da939036691228c9babeb8`; tree: `86bce6031a2e5fe13b55d60cc1269ff0634d940c`.
- Original Northstar DB SHA-256 before and after: `f54a384efbbe789e0c7f2ca2eafa9563a4796e08fbd952cc7fe7f790f9eac507`. Prepared input workspace SHA-256: `7805082672e4fb69a22bb4e7f4a944951bc3a49e61a071d18ceae77f08940f19`. Both inputs were read or copied, never modified.
- Synthetic output: `development/runs/DEV-038/synthetic-output/verification.json`; persisted DB: `synthetic-output/marketing.db` (SHA-256 `2783f2594eacbe235b14cf8671cfcf683ff2534392accf19c6fba3d4a73f719f`); export: `synthetic-output/northstar-strategy-brief-SYNTHETIC.md` (SHA-256 `ce57a1759b4f3a15a9294c1cac42afb55f271cfcf9e7c5f0d2b4993ae5335f6d`). Artifacts are deliberately ignored by Git and remain in the isolated worktree.
- Production `generate_batch` invoked one `ModelRouter` with a scripted `LOCAL` provider; the unchanged factual validator accepted one Strategy Brief; real storage persisted `del_46e06655d8664d929e022a85de95ffe7`; the export was built from the reread persisted row. The cited scoped source ID is `01cb9b37f0e7431c8b3e70a6dd424c15:c000`.
- The other project's project-bearing table fingerprint was unchanged. Separate SQLite reopen confirmed the record, export content, DB hash, and `quick_check=ok`.
- `python -m pytest development/runs/DEV-038/test_synthetic.py app/tests/test_dev033_campaign_validation.py -q`: **82 passed**. This includes dirty checkout, wrong ancestor, cross-project citation, and fabricated price rejection.
- `python development/runs/DEV-038/run_synthetic.py`: **PASS** with a clean source checkout at invocation. Socket dispatch was fail closed; fake provider calls **1**; network requests **0**; real provider GET/POST requests **0**; real inference POSTs **0**.

## Limits

The synthetic pass does not establish current OpenRouter eligibility, connectivity, ZDR, or live model output. Those need separate authorization for live provider requests. No merge or deployment occurred. Independent QA and review remain required by the run policy.
