# DEV-033 Real Campaign Verification

## Decision

**BLOCKED before inference.** No additional provider inference request was sent.

## Preflight evidence

- Requested model: `apodex/apodex-1.1-mini:free`.
- The current official OpenRouter page lists its input and output prices as free and shows one NovitaAI provider endpoint: [Apodex 1.1 Mini (free)](https://openrouter.ai/apodex/apodex-1.1-mini%3Afree).
- Official OpenRouter docs expose authenticated model endpoint listings and a separate ZDR endpoint list; request-level controls include provider allow-listing, `allow_fallbacks: false`, `zdr: true`, and `data_collection: "deny"`: [model endpoints API](https://openrouter.ai/docs/api/api-reference/endpoints/list-endpoints), [ZDR endpoint API](https://openrouter.ai/docs/api/api-reference/endpoints/list-endpoints-zdr), [privacy and routing controls](https://openrouter.ai/docs/guides/get-started/sovereign-ai).
- A live authenticated, read-only request to the model catalog, exact model endpoint listing, and ZDR endpoint listing failed before receiving a response (`URLError`). It did not call `/chat/completions`.
- Exact live endpoint identity, endpoint pricing, and its account-eligible ZDR/privacy status therefore remain unverified. The user’s instruction requires stopping without inference if any check fails.

## Call and cost accounting

- Historical real inference calls before this repair: 2.
- Additional inference calls in this verification: 0.
- Cumulative real inference calls: 2 (below the maximum of 8).
- Observed spend: $0.00; this verification added no inference spend.
- No retry or paid fallback was attempted.

## Rejection evidence boundary

The original rejected model draft was not retained in DEV-032, and this verification did not produce a new rejection. No claim is attributed to the historical model. A sanitized synthetic fixture diagnostic is retained in `development/runs/DEV-033/diagnostic-evidence.md`; it is explicitly not presented as the historical model claim. Deterministic redaction and diagnostic behavior is also covered in `test_dev033_campaign_validation.py` and the focused regression suite.
