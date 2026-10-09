# DEV-031 Phase A QA Remediation

## Initial QA findings

- **F1 — project scope key nullable:** SQLite does not make a text primary key `NOT NULL` implicitly. The v13 `business_profiles.project_id` declaration is now explicitly `PRIMARY KEY NOT NULL`. The project ID audit lists `business_profiles` as intentionally default-free because each profile must name its client project explicitly; it also checks the key is non-nullable.
- **F2 — citation notation gap:** the generation prompt now requires the exact `[document_id:chunk_id]` notation copied from supplied evidence. The API rejects unknown bracketed IDs, uncited source labels such as `Source: invented:reference`, and parenthesized IDs such as `(invented:reference)` before saving. Regression coverage verifies the existing brief and revision stay unchanged on rejection.
- **F3 — overwrite confirmation copy:** the confirmation now says generated content is saved over the current saved brief on success and unsaved editor changes are discarded. This matches the API's immediate persistence behavior.

All QA remediations are within the approved Phase A scope. Initial FAIL evidence is preserved in `qa-initial.md` and `qa-remediation-1.md`.

## SOL 6.1 review findings

- **Unsupported citation candidates:** replaced the narrow citation regex with validation of every colon-delimited source-like token. Only exact bracketed IDs from scoped retrieval are accepted; unknown punctuation, bare IDs, and parenthesized IDs fail before persistence. Regression checks cover punctuation, source labels, parentheses, and bare references and verify the existing brief and revision are preserved.
- **Optional profile data lost at creation:** profile creation and update now share validation and normalization. Every supplied supported field is stored; absent fields remain blank. Invalid values are rejected before project or profile rows are inserted. DEV-031 tests cover round-trip and no partial creation.
- **A→B→A late response race:** every save and generation captures the workspace selection epoch as well as the project ID. Success, error, and finalization handling require both to remain current. A frontend regression simulates A→B→A and verifies all three actions reject the stale epoch.
- **Concurrent lazy profile initialization:** legacy profile creation now uses `INSERT OR IGNORE` on the project-key constraint and reloads the winning row. A synchronized two-reader test verifies both requests succeed and only one profile exists.

The pre-review QA PASS is preserved in `qa-before-sol-remediation.md`. Final independent QA and SOL re-review are required on this latest result; no approval is implied here.
