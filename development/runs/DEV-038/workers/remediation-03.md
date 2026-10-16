# DEV-038 implementer remediation: approved logical-equivalence source guard

## Change and authorization

The founder approved the narrow source-provenance path recorded in `development/runs/DEV-038/source-approval.md`. `run_synthetic.py` retains the original exact-byte hash path (`f54a384efbbe789e0c7f2ca2eafa9563a4796e08fbd952cc7fe7f790f9eac507`) and adds a second fail-closed path. It requires the approved-derived isolated snapshot bytes SHA-256 `7ad2a03f8e0b71a5c23ba6171e58fd2bbffd99bc416f782012e7d92f35fa0420`, read-only `quick_check`, Northstar project/campaign identity, equal schema identity and SQL definitions, and full row-multiset equality across all 40 tables after excluding exactly four identified prior DEV-038 synthetic rows (one campaign batch, deliverable, revision, and model call) from the derived copy. Their IDs, project/campaign, type, version, turn, provider, and model are checked. No current-source-only row is accepted. Input WAL must be empty; source and derived bytes are rehashed after comparison; source bytes are checked after workspace copy and after the full run. The factual validator and application code remain unchanged.

## Regression results

`python -m pytest development/runs/DEV-038/test_synthetic.py app/tests/test_dev033_campaign_validation.py -q`: **91 passed**. New tests cover the preserved exact-byte path and rejection of changed Northstar evidence, an extra source row, extra schema, and a tampered derived snapshot. These mutations use disposable copied databases under ignored run output; no original client DB was edited.

## Fresh complete offline run

At clean source commit `57153fd5db5927c3fc7b5fb362326aff6fabc581`, a fresh run at `development/runs/DEV-038/synthetic-output/logical-equivalence-01/` returned **PASS**, provenance mode `logical_equivalence`. The current source DB SHA-256 before and after was `9c1aa99a905408c2a4efee5aea585bc365996560a2832a6165eb563ea997f99d`; approved-derived snapshot remained `7ad2a03f8e0b71a5c23ba6171e58fd2bbffd99bc416f782012e7d92f35fa0420`. Schema identity: user version 14, schema version 68, application ID 0. Other-project fingerprint stayed unchanged.

Production `generate_batch` used one scripted local provider call and the unchanged factual validator, then persisted one synthetic Strategy Brief (`del_46e06655d8664d929e022a85de95ffe7`) in isolated `marketing.db`, SHA-256 `324f122c95d4c6b51c8447d66c51e0cd9b9e7ff438e9c9face725fe8a952aba4`. Production scoped `markdown_export` returned that persisted row/body; the runner wrote exactly those UTF-8 bytes to `northstar-strategy-brief-SYNTHETIC.md`, SHA-256 `9a1bfbe840591c1c81069e6fd93c88585b20942dc2cad06783c565991abbed90`. A separate read-only reopen verified row ID, exact export bytes, both artifact hashes, and `PRAGMA quick_check=ok`. Evidence: `synthetic-output/logical-equivalence-01/verification.json`.

Network sockets were blocked. Real provider requests **0**; real inference POSTs **0**. No merge or deployment occurred. Independent QA and re-review remain required.
