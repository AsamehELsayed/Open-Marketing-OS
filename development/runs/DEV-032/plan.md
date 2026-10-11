# DEV-032 — OMOS Solo Agency: Campaign Deliverables

**Classification:** LARGE — cross-cutting SQLite, API, account-manager graph, React, and isolation work. This adds campaign deliverables to the existing campaign system. It does not introduce a new campaign or provider architecture.

**Base:** public main at 385c989698432406c287399e71a002ec014ed13b (DEV-031 Phase A).  
**Feature branch:** codex/dev-032-campaign-deliverables-integrated.
**Approval state:** Founder approved the plan at 2026-10-10T10:31:49Z, including autonomous implementation, integration, QA, independent review, remediation/re-review, one PR to public `main`, and merge only after green CI and review approval.

## Current architecture observed

- Campaigns already persist by project_id in campaigns, and CampaignDetail has Overview, Content, Activity, Results, and Files tabs. Content is currently a placeholder. Campaign proposals already have turn-based idempotency.
- Business Profile and Business Brief are stored against the existing project. Brief generation uses ModelRouter and project-scoped retrieval.
- The account-manager graph detects explicit campaign write intent, persists proposals through the existing propose_campaign tool, and carries immutable turn project_id, conversation_id, turn_id, provider, and model. ModelRouter is selected per conversation/turn.
- retrieve_scoped fails closed and filters by project. Existing external approval handling guards publishing/spend actions. Deliverable approval is an internal editorial status and must not trigger external actions.
- Current database schema version is 13. Additive, idempotent migration conventions exist. Existing Markdown chat export provides the attachment precedent.

## Goal and boundaries

Extend existing campaign records with objective, target audience, and channels; persist the five requested Markdown deliverable types; support manual create/edit, version history, status transitions, export, natural-language creation and targeted revision. Keep each campaign attached to its existing project/client.

Out of scope: client duplication, a second campaign system, a new AI provider/agent system, external publishing, paid advertising, third-party writes, release/tag/installer changes, and broad product redesign.

## Implementation packets

The three workers may overlap because their owned files are disjoint and the service/API contract below is fixed. Each worker uses a separate execution and isolated worktree. The integration packet begins after all workers complete. No worker sub-delegates.

### W1 — Persistence and API

Owns: app/database/schema.sql, app/database/sqlite.py, app/database/repos.py, new app/services/campaign_deliverables.py, new app/routes/campaign_deliverables.py, app/main.py, and new app/tests/test_dev032_deliverables_api.py.

Add schema v14 tables for campaign deliverables and immutable revision snapshots. Expose project-and-campaign-scoped CRUD, history, status, and Markdown/package export operations. Publish the service contract for W2: save_generated_batch(conn, project_id, campaign_id, idempotency_key, items, provenance), plus scoped manual-save, revise, transition, and history operations. Do not edit graph or frontend files.

### W2 — Natural-language AI production and revision

Owns: app/graphs/account_manager_graph.py, app/routes/graph_runtime.py only if needed for safe callback injection, new app/services/campaign_production.py, and new app/tests/test_dev032_campaign_ai.py.

Retain the current explicit-write-intent and propose_campaign gates. Use the turn project_id and selected provider/model with the existing ModelRouter. Read only that project Business Profile, optional Brief, and retrieve_scoped knowledge. Validate generated content and evidence references before W1 batch persistence. Extend the existing campaign metadata with objective, channels, duration, and request facts. Report persisted output IDs/types or a precise campaign-only partial failure. Resolve chat revisions to one scoped deliverable and save a version; ask one focused clarification when ambiguous. Do not edit W1 or frontend files.

### W3 — Campaign workspace UI

Owns: frontend/src/api/client.ts, frontend/src/routes/CampaignDetail.tsx, focused components under frontend/src/components/workspace/ as needed, and focused frontend tests.

Replace the Content placeholder with the campaign overview and deliverable workspace. Support manual create/edit/save, revision history, DRAFT to IN_REVIEW to APPROVED, single Markdown export, and a campaign ZIP package. Pass project_id on every request. Guard against stale reads or writes after project switching using existing workspace-selection patterns. Do not edit backend files.

### Integration

A separate integrator execution verifies API, graph, database, and UI contracts together; owns only integration glue and app/tests/test_dev032_integration.py. Then run independent QA, followed by independent review. Any remediation receives a new independent re-review.

## Data and safety design

- Use stable deliverable IDs and project_id/campaign_id ownership. Validate campaign ownership before every child read, mutation, or export. Scope all queries by project and campaign.
- Store type, title, platform, Markdown, status, current version, optional generation idempotency key, and created/updated timestamps. Store immutable snapshots for every content revision and status transition.
- Use expected_version compare-and-swap updates in a transaction. Stale writes return conflict without overwriting newer content. Editing APPROVED content creates a new DRAFT revision. Only DRAFT to IN_REVIEW to APPROVED is allowed.
- Generated batch items receive deterministic turn/campaign/type/ordinal keys. Enforce uniqueness and atomic persistence. Retries return existing records without overwriting user edits or approval. Validate all output before saving; on model or validation failure preserve existing deliverables and report the exact failure.
- Generation reads profile, brief, and RAG evidence only for the immutable turn project. Clearly separate profile facts, supported evidence, recommendations, and missing information. No invented prices, claims, research, budgets, metrics, or testimonials.
- Internal editorial approval never publishes or spends. No publishing tool or route is added. Do not log or show secrets.

## Acceptance and verification

Automated tests use deterministic ModelRouter fakes and synthetic projects. Cover v13-to-v14 and repeated migration; create/edit/history/version conflicts; approved-edit reset; allowed/invalid status transitions; per-project isolation for detail/revision/export; idempotent generation retries; selected provider/model propagation; scoped profile, brief, and RAG inputs; malformed/unavailable provider preservation and honest failure; explicit intent and targeted/ambiguous revision; and current approval/chat-export regressions.

Run the requested Northstar/Harbor scenario: create the campaign and requested deliverables, edit a specific post, inspect history, approve a deliverable, export, restart, and confirm persistence. Verify Harbor cannot read or surface Northstar content. Run one local browser/Playwright flow using deterministic AI for the UI path. Inspect real provider readiness without printing credentials. If a concrete provider/model is ready, make one genuine end-to-end generation and verify persisted rows and provider/model telemetry; otherwise report exactly REAL AI GENERATION: BLOCKED — NO MODEL AVAILABLE. Never treat fake output as a real call.

Run focused tests, the full required backend suite, frontend typecheck/build, and all required GitHub Actions. Open one PR targeting public main. Merge only after CI is green and independent review approves; verify the resulting public main merge commit. Do not modify release tags or installers.

## Risks and mitigations

- Invalid model output or unsupported citations: validate before persistence; preserve existing records and report partial campaign-only success.
- Concurrent edits and retries: CAS, transaction-bound revisions, and unique idempotency keys.
- Wrong-client navigation or data access: immutable turn scope, project_id on requests, backend ownership checks, stale-selection guards, and Harbor canaries.
- Provider unavailable: full manual CRUD/export remains available and generation failure is reported without claiming persistence.
- Worktree metadata: the managed clean worktree required git worktree repair before Git status worked through the approved host operation; branch now points to the requested public-main base.
