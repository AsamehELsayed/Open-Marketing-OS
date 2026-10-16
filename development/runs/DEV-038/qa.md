# DEV-038 independent QA — FAIL

**Lane:** general execution substituted for the unavailable dedicated `qa` lane under `docs/development-delegation-system.md` §10. This is a separate execution from the planner and implementer; no code changes or provider calls were made.

## Reproduction and result

- Source reviewed: clean `codex/dev-038-autonomous-e2e` at `4de629b284235987102b20a520859479e92c13e7`, descendant of approved `7136389a`; diff inspected from base. Production imports resolved under this isolated worktree (`app/services/campaign_production.py`, `app/database/sqlite.py`, `app/services/llm/model_router.py`).
- `python -m pytest development/runs/DEV-038/test_synthetic.py app/tests/test_dev033_campaign_validation.py -q` → **82 passed**.
- Fresh replay: import `development/runs/DEV-038/run_synthetic.py`, set `RUN` to a fresh `qa-output`, call `main()` → exit 0, `status=PASS`, fake provider calls 1, network requests 0, real provider requests 0, real inference POSTs 0. The output was moved after verifying both absolute paths remain inside the DEV-038 run, to ignored `synthetic-output/qa-output-old`; no source file was changed.
- Reopened persisted `marketing.db` through production `connect`: deliverable `del_46e06655d8664d929e022a85de95ffe7` has type `strategy_brief`; its content includes scoped citation `01cb9b37f0e7431c8b3e70a6dd424c15:c000` and `SYNTHETIC`. Export bytes exactly equal the title plus reread persisted `content_md`. SQLite `quick_check=ok`, schema version 14.
- Original source DB SHA-256 before/after: `f54a384efbbe789e0c7f2ca2eafa9563a4796e08fbd952cc7fe7f790f9eac507`; template DB SHA-256: `7805082672e4fb69a22bb4e7f4a944951bc3a49e61a071d18ceae77f08940f19`. Input and output DBs are distinct. Recomputed other-project fingerprint against the template and output; equal. Cross-project citation and fabricated price are rejected by focused tests; source guard dirty/wrong-base cases also pass.

## Blocking finding: synthetic Strategy Brief lacks usable strategy

The persisted/exported brief in `synthetic-output/qa-output-old/northstar-strategy-brief-SYNTHETIC.md` is only a short factual shell. Its entire recommendation is to introduce coffees with one product-focused post per day for seven days and consider tracking saves/profile visits. It contains no audience or positioning, messaging pillars, seven distinct daily approaches, visual guidance, or proposed success criteria. This does not satisfy the requested seven-day Instagram Strategy Brief even though the real validator accepts it and the database/export path works. The cause is the scripted fake-provider fixture in `run_synthetic.py`, not a database or factual-validation failure.

**Required in-scope remediation:** Expand the scripted fake response into a useful synthetic strategy with distinct daily ideas and explicit audience/positioning, messaging, visual, and measurement sections. Keep claims sourced or clearly proposed/unknown; preserve the unchanged real validator. Add a focused regression asserting these substantive sections and replay the full offline workflow in a new clean output directory. Independent QA must rerun after the fix.

**External status:** Real OpenRouter eligibility, connectivity, ZDR, and live inference remain unverified; no live traffic was authorized or used. This is separate from the local QA failure.
