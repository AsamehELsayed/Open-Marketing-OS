# DEV-031 W1 Backend Handoff

**Role:** Backend implementer (general execution substituting for unavailable OpenCode CLI worker; OpenCode could not open protected local log state). **Status:** implementation handed off; no QA/review claim.

## Files changed

- `app/database/sqlite.py` — additive schema v13 migration creates project-keyed `business_profiles`.
- `app/main.py` — registers the new API router.
- `app/routes/business_workspace.py` — client creation and profile/brief GET/PUT/generate endpoints. Project path is authoritative; conflicting IDs are rejected. Creation requires only business name and writes an isolated-workspace marker. Regeneration requires `overwrite_confirmed` when a saved brief exists. Profile and brief saves use expected revisions; generation snapshots both revisions and writes atomically only when they remain current.
- `app/services/business_workspace.py` — profile and brief persistence with targeted SQL updates (no replace-style repository write).
- `app/services/rag/indexing_service.py` — isolated client projects skip shared root workspace files during rebuild; project upload indexing remains in the existing upload path.
- `app/tests/test_dev031_business_workspace.py` — migration, partial profile/revision, overwrite protection, and shared-root isolation checks.

## Checks

- `python -m compileall -q app/routes/business_workspace.py app/services/business_workspace.py app/services/rag/indexing_service.py app/database/sqlite.py app/main.py app/tests/test_dev031_business_workspace.py` — passed.
- Direct migration smoke: `connect()` reported user_version 13 — passed.
- `python -m pytest app/tests/test_dev031_business_workspace.py -vv -s --basetemp=./.pytest-tmp -o faulthandler_timeout=12` — did not reach test assertions. It stalled while `TestClient` initialized its AnyIO portal; faulthandler showed a worker thread in `socket._fallback_socketpair` / `asyncio.windows_events` `new_event_loop`. Interrupted after capturing the timeout. Earlier pytest attempts also showed the default sandbox temp directory could not create its numbered pytest directory. This is local runner/process evidence, not a test failure verdict; parent should reproduce independently.

## Integration points / limits

- Generation calls the existing `graph_runtime._get_model_router()` and `retrieve_scoped(..., project_id=path_id, mode="lexical")`; it makes one `AUTO` call with empty tools and bounded `max_tokens`.
- Generation output source IDs are `document_id:chunk_id`; bracketed citations are checked against this exact retrieved set. The prompt requests distinct facts/evidence/suggestions/unknown sections and labels insufficient Knowledge.
- API payloads use the existing `{ok:true,data:...}` envelope. Profile fields are `business_name`, `website`, `industry`, `description`, `audience`, `location`, `offer`, and `differentiators`. Revision request fields are `expected_revision`; regeneration takes `overwrite_confirmed: true`.
- Provider errors return 503 with status/provider/model/error detail and do not overwrite saved brief data. Frontend should render `detail` as either a string or object.
- No real model call, QA, independent review, frontend edits, package installation, or commit was performed.
