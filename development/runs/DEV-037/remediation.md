# DEV-037 Remediation Plan — Percentage-Point Unit Distinction

## Finding
Independent review reproduced that `4 percentage points` and `4%` collapse to one numeric fact key, allowing an unsupported percent baseline. See `review.md`.

## Fix task and owner
- Owner: separate implementer execution (general lane).
- Scope: `app/services/campaign_production.py` and `app/tests/test_dev033_campaign_validation.py` only, plus this run artifact.
- Parse percentage-point units distinctly before generic percent units. Preserve existing `%` and `percent` equivalence.
- Add both negative directions (`4 percentage points` must not support `4%`; `4%` must not support `4 percentage points`) and positive `%`/`percent` equivalence coverage.
- Keep the change offline and deterministic. No provider/model calls or excluded-scope edits.

## Completion gates
Run focused numeric and validator/integration tests, then independent QA on the updated diff. Re-review in a new reviewer execution must produce `APPROVE` before PR creation. If either gate finds a blocking issue, remediate within this scope and repeat the affected independent gate.
