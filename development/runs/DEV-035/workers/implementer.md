# DEV-035 Implementer Record

**Role:** Implementer (`general` lane)
**Base:** Public `main` commit `3c84e33a81a1a3333a0b131503a3bbae9b8635f5`

## Work completed

- Reproduced the pre-change rejection with a direct call to `_validate_persisted_facts()`: `**Business name:** Northstar Coffee` failed `persisted_fact_source_match` against the allowed `Northstar Coffee` value. Details are in `reproduction.md`.
- Added Markdown field-label normalization for the known Business Profile labels only. The parser removes label delimiters/colon formatting and leaves the text after the label intact for the existing source match.
- Added deterministic regression coverage for all four requested business-name formats; invented business names; unsupported location, price, and certification claims; supported-name plus invented-location claims; unrecognized Markdown labels; and retention of the original generated content.
- Left citation, project-scope, numeric/high-risk, batch-write, and existing-deliverable logic untouched.

## Files changed

- `app/services/campaign_production.py`
- `app/tests/test_dev033_campaign_validation.py`

## Tests run

- `python -m pytest app/tests/test_dev033_campaign_validation.py -q` — **43 passed**.
- `git diff --check` — **passed**.
- Test interpreter: existing repository `.venv`; no packages or settings changed.

No real model/provider calls were made. Broader campaign suites and independent QA remain pending.
