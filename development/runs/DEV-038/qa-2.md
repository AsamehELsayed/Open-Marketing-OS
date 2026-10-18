# DEV-038 independent QA after remediation — PASS

**Lane substitution:** This independent `general` execution performed the full QA rubric because the dedicated `qa` lane was unavailable, per `docs/development-delegation-system.md` §10. It is separate from planner, implementer, and prior QA execution. No code or input database was edited and no provider request was made.

## Reproduction

- Reviewed `remediation.md`, `workers/remediation-01.md`, and the code/test diff since the earlier QA failure. The branch was clean at `256ba3f95d5fd76ffaa8dd317969b2128d8ed5b6` when the source guard ran. Production imports originated within this isolated worktree. The actual path is `ModelRouter` with a scripted `LOCAL` provider → production `generate_batch` → unchanged `validate_generated_items` → production `list_deliverables` → Markdown export from the reread row.
- `python -m pytest development/runs/DEV-038/test_synthetic.py app/tests/test_dev033_campaign_validation.py -q` → **84 passed**. These cover content sections, seven distinct daily ideas, real factual validation, dirty/wrong source guards, cross-project citation rejection, and fabricated price rejection.
- Fresh QA replay: imported `run_synthetic.py`, set `RUN` to `development/runs/DEV-038/synthetic-output/qa-2`, called `main()` → **PASS**. Evidence: `synthetic-output/qa-2/verification.json`. One fake provider call; socket dispatch blocked; real provider requests **0**, real inference POSTs **0**. No live service was contacted.

## Deliverable and content

- Reopened `synthetic-output/qa-2/marketing.db` and found exactly one matching `strategy_brief` row for project `6c4aaf8b966d43f1ae87b63707b3ed2c` and campaign `9b9afc7746cb52ccaccac865ffa8025d`: `del_46e06655d8664d929e022a85de95ffe7`. The Markdown export `synthetic-output/qa-2/northstar-strategy-brief-SYNTHETIC.md` exactly equals the persisted title and content body when read as UTF-8; its SHA-256 is `9683d6efb7285ddef0e7e60a19d37621d5d50c49e07e73aa5f14618858f81a0a`.
- Read the entire export. It now includes proposed audience and positioning for Cairo home brewers, three messaging pillars, seven distinct day concepts (orientation, product focus, setup, choice, ritual, questions, recap), visual guidance, and proposed measures (saves, replies, question themes). It is prominently labeled `SYNTHETIC`. The one factual product/price claim cites selected-project source `01cb9b37f0e7431c8b3e70a6dd424c15:c000`. Baselines, results, second-product details, and imagery approval are explicitly unknown. These are proposed creative ideas, not invented business performance facts.

## Integrity and limits

- Original source DB SHA-256 before and after: `f54a384efbbe789e0c7f2ca2eafa9563a4796e08fbd952cc7fe7f790f9eac507`. Isolated output DB SHA-256: `0dde399dd9bf1e2510b232cac4abbfd9435654fde8437df6822b942af75f204a`. Source and output `quick_check=ok`, schema version 14. Independently recomputed an unrelated project's project-bearing table fingerprint against source and output; identical.
- The previously pinned prepared template hash drifted from `7805082672e4fb69a22bb4e7f4a944951bc3a49e61a071d18ceae77f08940f19` to `242b2e03a20bf8e0698f5f7eb91de2d595dd31a005944994dc9d3723bf217503` outside this execution. The remediated runner does not use or write it. It copies the unchanged, hash-pinned original Northstar source DB to a separate output DB. This is external input drift, not a reproduced application database initialization defect.
- The brief proves the deterministic synthetic application path. Current OpenRouter eligibility, connectivity, ZDR status, and live model output remain unverified and require separate authorization before live requests. Merge/deployment and required independent review remain pending.
