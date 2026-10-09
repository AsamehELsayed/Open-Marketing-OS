# DEV-031 — Phase A implementation plan

**Classification:** ARCHITECTURAL
**Baseline:** public `main` at `efbcb7582ca43ef35db70f462ee52a1d9136789b`
**Scope:** Phase A only — project-based client workspaces, editable Business Profile, persistent editable AI Business Brief, and project isolation.

## Architecture decision

Reuse Projects as client workspaces. Add an additive SQLite v13 `business_profiles` table keyed by `project_id`, holding validated partial profile fields, the editable `brief_md`, source references, generation metadata, separate profile/brief revisions, and timestamps. Keep project name and website authoritative and update them with targeted `UPDATE`s. Do not extend the historically conflated `companies` table, put client content in shared root company files, or add a second campaign/knowledge system.

The current “Create business” onboarding edits the existing default business/project and shared files; it is not a safe multi-client creation path. Add real project creation and keep first-run onboarding behavior compatible. New client projects receive an explicit isolated-workspace marker in project settings. Their Knowledge rebuild uses project uploads only; workspace root sync/import must not copy legacy shared company or knowledge files into them. Existing projects retain legacy ingestion behavior. Use no cascading foreign key that could erase profile rows when existing project writes use `INSERT OR REPLACE`.

## Backend contract

- `POST /api/projects`: create a distinct project and partial profile; require a business name, allow other profile fields to be blank, and return stable generated IDs.
- `GET|PUT /api/projects/{id}/business-profile`: read/update only the named project’s profile and project identity fields. Validate fields and `expected_revision`; return 409 on a stale edit.
- `GET|PUT /api/projects/{id}/business-brief`: read or save editable Markdown, references, and revisions. Saving the brief must not alter profile fields or provenance.
- `POST /api/projects/{id}/business-brief/generate`: use only this project’s profile plus scoped Knowledge retrieval. Use the existing configured ModelRouter, one bounded call with tools disabled, and existing AUTO/model settings. Do not add a provider stack or agent orchestration.

The path project ID is the authority; reject conflicting body IDs and never fall back to a globally active/default project. Briefs must distinguish persisted user facts, retrieved evidence with real source references, model suggestions, and unknown or unsupported information. Validate cited evidence IDs against retrieved evidence. Snapshot revisions before generation and persist atomically only if neither profile nor brief changed. Provider, validation, timeout, or save failures preserve prior profile, brief, and provenance; return success only after persistence. Keep existing project selection, Campaign Draft, automatic tools, model selector, Markdown chat export, and approval guards intact.

## Frontend contract

Add `/app/workspace` with a client overview, project-specific editable profile and brief, Generate/Regenerate and Save actions, source labels, and clear loading/saving/error/dirty/stale states. Link to existing Account Manager, Campaigns, Knowledge, and scoped Files UI. Add one “Client workspace” navigation entry. Change “+ Create project…” to open client creation, reusing onboarding only with an explicit create-client mode or a new workspace creation route; preserve existing first-run setup.

On project change, clear old project content immediately, capture the selected ID for each action, and ignore late load/save/generation responses from a previous selection. Do not show project A data while project B loads.

## Execution packets and gates

Freeze the API contract above, then run two minimal workers in parallel with disjoint ownership and isolated worktrees where available:

1. **Backend worker:** schema/migration/repository; profile and brief service/router; route registration; isolated-client reindex/sync behavior; backend focused and migration tests. Scope: `app/database/`, new backend modules, `app/main.py`, `app/services/knowledge_index.py`, relevant project state/API modules, backend tests.
2. **Frontend worker:** API types/client, routes, navigation, project switcher, workspace/profile/brief and create-client UI, focused UI acceptance. Scope: `frontend/src/` and frontend-specific acceptance files only.

No nested delegation. Integrate the two outputs, resolve API mismatches, run tests and UI smoke, then have a separate independent QA execution and a separate SOL review execution. The review gate runs only after QA. If remediation is requested, record it and obtain a new real APPROVE re-review. Record every role, artifact, and execution in `development/runs/DEV-031/`; claim parallelism only if execution timestamps overlap and `dev_run.py parallelism` proves it. The first configured SOL relay failed before a response due protected Codex state access; the completed read-only planner execution used a separate `gpt-6.1-sol` agent and is recorded independently.

## Acceptance and verification

1. Name-only client creation creates a new stable project; existing onboarding remains compatible.
2. Partial profile values round-trip, blank optional fields stay blank, validation errors do not damage saved data, and revisions prevent lost updates.
3. v12→v13 migration and repeated startup preserve existing conversations, campaigns, Knowledge, model selection, and profile/brief data.
4. Generation uses only the selected profile and that project’s retrieved evidence; all references resolve and facts/suggestions/unknowns are visibly separated.
5. Generated and manually edited briefs persist independently; provider errors, empty/malformed output, and revision conflicts leave the prior data intact.
6. A second project cannot read the first project’s profile, brief, upload sentinel, or citations; reindex/sync cannot import shared root evidence into isolated clients.
7. Delayed requests during project switching cannot display or write to the wrong project.
8. Regressions preserve automatic tool selection, Campaign Draft idempotency, publishing/approval guards, model selection, and Markdown export.

Run focused new backend tests, relevant DEV-011, DEV-014/015, DEV-030, automatic-tool, write-constraint, and permission-guard regressions, frontend typecheck/build, browser acceptance, and required GitHub Actions. Update migration assertions that hardcode v12. Real UI smoke: create fictional Northstar Coffee, upload a Northstar-only fact via existing Files UI, generate and edit/save its brief, restart against the same disposable data directory, then create Harbor Studio and verify Harbor cannot see Northstar’s profile, brief, upload, or root legacy evidence. If no real model route is usable, mark real generation smoke blocked; fakes count only for deterministic tests.

## Boundaries, risks, and release

No Phase B, CRM, multi-user authentication, billing, portal, redesign, publishing integration, or installer changes. The app has no multi-user auth boundary; this phase isolates project context in the single-operator installation. Main risks are legacy shared-file indexing, project IDs historically conflated with Companies, project switch races, and replacement-style repository writes; the architecture and acceptance checks address each.

After independent QA and reviewer APPROVE plus required CI success, create one focused Phase A PR, commit and merge it to public `main` as requested in the product plan, then verify the merge commit is on public `main`. No work on Phase B before that merge is verified.

## Founder-approved UX requirements

- Client creation requires only the business name; every other profile field is optional, with a short flow instead of a questionnaire.
- Show generation state and actual provider/model when available. Explain missing credentials, unavailable models, and provider errors in plain language. Keep editing available when generation cannot run.
- Never silently replace a manually edited brief. Regenerate prompts for explicit replacement confirmation while retaining revision/concurrency checks.
- Label user facts, retrieved evidence, AI suggestions, and missing information separately. Never invent citations; clearly say when project Knowledge is insufficient.

These requirements were explicitly approved by the founder on 2026-10-09 and are part of Phase A.
