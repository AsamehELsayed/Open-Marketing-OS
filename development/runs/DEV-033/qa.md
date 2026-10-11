# DEV-033 Independent QA

**Verdict: PASS with an environment limitation.** No code correctness failure was reproduced. The direct route-handler checks passed; the TestClient integration test still cannot complete in this Python 3.12 Windows environment because it hangs before request handling.

## Execution

- **Role:** `role=qa, agent=general`. The OpenCode QA lane was unavailable because its CLI could not write the required user-level log.
- **Review method:** independent diff review using correctness, readability, architecture, security, and performance axes. No product code or tests were modified.
- **Provider calls:** none.
- **Baseline evidence:** DEV-032 acceptance records identify the persisted-fact exact-value rule as the failure. The raw rejected draft was not retained, so whether the real failure was an unsupported model claim or a matcher false negative remains **undetermined**.
- **Diagnostic artifact:** reviewed `development/runs/DEV-033/diagnostic-evidence.md`. It contains a deterministic fixture, explicitly not the lost DEV-032 draft or a real model response. The bounded excerpt is redacted, source IDs are limited, and project scope is represented by a hash. It retains no provider payload.

## Verification

All pytest temporary files were placed under `C:\Users\User\.codex\visualizations\2026\10\11\01a12b11-24d9-7c23-bcdf-63f9a04bf37a`.

| Command / selection | Result |
|---|---:|
| `app/tests/test_dev033_campaign_validation.py` | **16 passed** |
| `app/tests/test_dev032_campaign_ai.py` | **62 passed** |
| `test_dev032_deliverables_api.py::test_generated_batch_is_atomic_idempotent_and_preserves_later_edits` | **1 passed** |
| Campaign generation scope selections: `test_generation_reads_only_the_turn_project_profile_brief_and_evidence`, `test_explicit_request_generates_and_persists_requested_types` | **2 passed** |
| Scoped markdown/ZIP/cross-project TestClient case | **Timed out at 30 seconds** with no pytest output; interrupted. The supplied faulthandler stack shows Python 3.12 Windows asyncio self-pipe `socket.accept` before request handling. |
| Direct route-handler verification using an isolated SQLite database | **5 passed:** create, Markdown export, ZIP export, cross-project deliverable 404, cross-project campaign 404. This bypassed TestClient and ASGI dispatch. |

The full DEV-032 suite also exercised retry idempotency, existing-row preservation, project isolation, citation/provenance validation, and revisions of approved deliverables; it passed.

## Five-axis review

- **Correctness:** New matching is bounded to explicit business-name/flavor-note phrase rewrites and deterministic comma-group / metric-mass normalization. Decimal arithmetic is used for mg/g/kg conversions. Unsupported claims remain guarded, and the tests cover wrong mass values/units, unsupported business claims, invented prices/statistics/testimonials/certifications/discounts/delivery promises, and batch rejection.
- **Readability:** Helpers are named for their specific purpose. Error construction and normalization stay localized in campaign production.
- **Architecture:** The change preserves the existing generation → validate whole batch → W1 atomic save flow. No new service layer or dependency was introduced.
- **Security:** Rejection excerpts pass through the existing sanitizer plus email/phone redaction and are capped at 160 characters. At most three source IDs are retained, each capped at 120 characters; the diagnostic carries a project-scope hash instead of the raw project ID. The user-facing error is capped at 900 characters. I found no private provider payload or hidden reasoning persistence in this diff.
- **Performance:** Normalization scans bounded generated content and uses standard-library regex and Decimal operations. No material hot-path concern found.

## Remaining limitation

The scoped TestClient test does not verify ASGI dispatch in this environment because it stalls before the route is called. Direct handler checks exercise the route functions and scoped service behavior successfully, but they do not replace an HTTP-level rerun on a working TestClient environment. This is an environment verification limitation, not a demonstrated product defect.

No code defect was confirmed. The real campaign failure's root cause remains undetermined until a safely retained rejected claim can be compared with its scoped sources.
