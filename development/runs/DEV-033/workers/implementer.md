# DEV-033 Implementer Record

## Execution

- Classification: SMALL.
- Execution lane: general Codex execution. The configured OpenCode agent-list command was unavailable because it could not open its user-level log file, so the general execution substitution permitted by `docs/development-delegation-system.md` §10 was used. No SOL, Antigravity, paid model, or new agent lane was used for implementation.
- Baseline: `a08acb0d14c9267290afc797ca41ac7e757d4fd8` in a clean isolated branch `codex/fix-real-campaign-generation`.
- The main checkout was not changed.

## Findings

- DEV-032 identified the failed rule as an exact normalized substring check in `_validate_persisted_facts`.
- The rejected output was not retained, so it is not possible to classify the actual failure as a model error, validator error, or both. Root cause remains UNDETERMINED.
- Deterministic baseline reproduction found false rejections for explicit business-name paraphrase, thousands separators, equivalent metric mass, and project-supported tasting/flavor notes. It also found that unsupported certification language in suggestions was not guarded.

## Change

- Added a small explicit fact alias set and safe numeric normalization for unambiguous grouped numbers and metric mass units.
- Added certification/organic vocabulary to the existing high-risk source check.
- Tightened generation instructions to quote sourced facts exactly.
- Added bounded, redacted, project-scoped validation diagnostics with an honest no-ID/no-persistence state and retry/revision guidance.
- Kept scoped source selection, citation checks, atomic W1 writes, and existing deliverable preservation intact.

## Verification

- New deterministic suite: 16 passed.
- Existing campaign generation suite: 62 passed.
- Atomic campaign API test: 1 passed.
- W1 persistence/scoping/isolation/retry integration test: 1 passed.
- Independent QA result is recorded in `qa.md` and in `executions.jsonl`.
- Live one-call preflight is blocked because authenticated read-only endpoint/ZDR requests failed with `URLError`; no inference was sent. See `real-verification.md`.

## Acceptance evidence boundary

- DEV-032’s original rejected claim is unavailable; this report does not invent or infer it.
- The diagnostic test uses a deterministic synthetic example and confirms email redaction, bounded excerpt, source IDs, project-scope hash, and `persisted: false`.
- No private provider payload, credential, or hidden reasoning was stored.
