# DEV-033 Investigation Evidence

## Real acceptance artifacts
- Reviewed `development/acceptance/DEV-032-REAL-AI-E2E/acceptance-report.md`, attempt `campaign-attempt.md`, `conversation.md`, and `run-state.json` from attempt 02, plus the earlier report/state and attempt-local brief, database, and source notes.
- Accepted assistant text identifies the failed rule as a persisted fact not matching an exact value from the user turn, project profile, business brief, or scoped evidence.
- SQLite is read-only inspected. `campaign_deliverables`, generated batches, and revisions each contain 0 rows; the database has 2 model-call metadata rows, 2 messages, and no model response column. The assistant message is the sanitized failure text. The rejected draft cannot be recovered from safe persisted evidence.
- Historical real inference count: 2; historical observed cost: $0.00. No new campaign provider call has been made in this repair.

## Baseline deterministic validator behavior
All cases call `validate_generated_items` directly with a scripted strategy block; no provider is invoked.

| Case | Deterministic input | Baseline result | Meaning |
|---|---|---|---|
| A | Exact supported offer text | PASS | Exact grounded facts work. |
| B | `The business is named Northstar Coffee.` against `Business name: Northstar Coffee.` in the brief | REJECT | Harmless field paraphrase is not recognized. |
| C1 | `EGP 1000` against source `EGP 1,000` | REJECT | Safe thousands formatting equivalent is missed. |
| C2 | `0.25 kg` against source `250 g` | REJECT | Safe metric mass equivalent is missed by fact and numeric checks. |
| D | `Northstar Coffee is a certified organic roaster.` with no support | REJECT | Unsupported business claim remains guarded. |
| E | Invented price/statistic/testimonial/certification/discount/delivery promise | REJECT | High-risk factual protection remains active. |
| F | `The flavor notes for Northstar House Blend are cocoa and caramel.` against project evidence `Northstar House Blend has tasting notes of cocoa and caramel.` | REJECT | Supported project-knowledge paraphrase is not recognized. |
| E-gap | `Promote our certified organic beans.` in AI suggestions, with no supporting source | PASS (baseline) | Certification language was not included in the high-risk vocabulary, so a fabricated certification could pass outside persisted facts. |

## Post-fix deterministic results
- A exact supported fact: PASS.
- B business-name field paraphrase: PASS through a narrow explicit field alias.
- C1 grouped thousands formatting (`EGP 1,000` / `EGP 1000`): PASS.
- C2 equivalent mass (`250 g` / `0.25 kg`): PASS; different mass and volume values remain rejected.
- D unsupported business claim: REJECT.
- E invented price, statistic, testimonial, certification, discount, and delivery promise: REJECT.
- F flavor/tasting-note wording with identical product and values: PASS through a narrow explicit alias.
- Diagnostics include no assigned deliverable ID, ordinal/type, rule, a bounded redacted claim excerpt, up to three available scoped source IDs, a hashed project scope, revision/retry guidance, and `persisted: false`. Rejection leaves existing deliverables unchanged.
- The historical rejected model text was not retained, so the real rejection cause remains UNDETERMINED even though deterministic matcher false positives and a separate certification-claim gap were reproduced.

## Focused verification
- `pytest -q app/tests/test_dev033_campaign_validation.py`: 16 passed.
- `pytest -q app/tests/test_dev032_campaign_ai.py --basetemp=<approved visualization temp>`: 62 passed.
- Atomic API test: 1 passed.
- W1 persistence, project-scoped API, isolation, and retry idempotency integration test: 1 passed.
- A broader TestClient/API invocation hung in this environment and was canceled; targeted persistence and integration checks passed.

## Real verification preflight
- The current official OpenRouter model page lists `apodex/apodex-1.1-mini:free` at zero prompt/completion cost and one NovitaAI endpoint: https://openrouter.ai/apodex/apodex-1.1-mini%3Afree
- The authenticated read-only catalog/endpoint/ZDR request failed before returning data (`URLError`). Therefore the exact live endpoint and privacy eligibility could not be verified for this account. Per the explicit stop rule, no inference request was sent; cumulative call count remains 2 and observed spend remains $0.00.
- Full details are in `development/runs/DEV-033/real-verification.md`.

## Narrow repair direction
- Canonicalize only unambiguous thousands grouping and metric mass units (mg/g/kg) before fact and numeric comparisons.
- Match a small explicit set of field/value paraphrases (`business name` and `tasting/flavor notes`) only when the entire extracted value matches the same field in an allowed source. No general semantic or model self-assessment matching.
- Tell the generator to copy supported facts verbatim; keep all existing rejection and batch persistence gates active.
- Return a structured sanitized diagnostic with deliverable ordinal/type, no-assigned-ID status, rule, excerpt, available scoped evidence IDs, classification, and a safe revise/retry instruction.
