# DEV-038 implementer remediation after QA FAIL

## Root cause and changes

Independent QA correctly found the first fake-provider response was only a factual shell. I expanded `run_synthetic.py` into a clearly synthetic Strategy Brief with audience positioning, messaging pillars, distinct Day One through Seven Instagram concepts, visual guidance, and proposed measurement. The cited product claim is asserted against the actual selected-project retrieved evidence before generation. The brief keeps unverified details in the unknowns section. No production factual validator, application database code, pricing, privacy, or provider routing was changed.

Added content-contract regressions in `test_synthetic.py` for the required strategy components, seven distinct days, synthetic label, scoped citation, and acceptance by the unchanged production validator. The complete run still uses production `generate_batch`, `ModelRouter` with one scripted local provider call, production storage, fresh copied SQLite workspace, and export from the persisted row.

## Input drift classification

The previously pinned prepared template DB changed outside this worker's edits: its earlier SHA-256 was `7805082672e4fb69a22bb4e7f4a944951bc3a49e61a071d18ceae77f08940f19`, while the fresh read gave `242b2e03a20bf8e0698f5f7eb91de2d595dd31a005944994dc9d3723bf217503`. The runner's hash guard stopped before using it. I did not repin or edit that template. The approved original Northstar source DB retained SHA-256 `f54a384efbbe789e0c7f2ca2eafa9563a4796e08fbd952cc7fe7f790f9eac507`, passed read-only `quick_check`, and contained the project profile and scoped evidence, so the runner now copies that unchanged, hash-pinned DB directly into a separate workspace. Its hash remained identical after execution. This is input/template drift, not a reproduced application initialization defect.

## Verification

- `python -m pytest development/runs/DEV-038/test_synthetic.py app/tests/test_dev033_campaign_validation.py -q`: **84 passed**.
- Fresh full synthetic workflow at clean source commit `9a032640c13de27ed75308662de07a4be26e0aeb`: **PASS**. Evidence: `development/runs/DEV-038/synthetic-output/remediation-03/verification.json`.
- Persisted Strategy Brief ID: `del_46e06655d8664d929e022a85de95ffe7` in `synthetic-output/remediation-03/marketing.db`; DB SHA-256 `7ad2a03f8e0b71a5c23ba6171e58fd2bbffd99bc416f782012e7d92f35fa0420`. Markdown export: `synthetic-output/remediation-03/northstar-strategy-brief-SYNTHETIC.md`; SHA-256 `9683d6efb7285ddef0e7e60a19d37621d5d50c49e07e73aa5f14618858f81a0a`.
- Reopened DB: record exists, type `strategy_brief`, `quick_check=ok`, hash matches verification. The Markdown contains the persisted `content_md` when read as UTF-8. Source DB unchanged; other-project fingerprint unchanged. Scoped source ID: `01cb9b37f0e7431c8b3e70a6dd424c15:c000`.
- Network socket dispatch is blocked. Fake provider calls **1**; network requests **0**; real provider GET/POST **0**; real inference POSTs **0**.

Independent QA and review must rerun after this remediation. Real OpenRouter eligibility, connectivity, ZDR, and live output remain unverified without separate live authorization.
