# DEV-031 SOL architecture planning brief

## Role and boundary

You are the gated strategic/architecture planner for DEV-031. Inspect the repository and return a concise, reviewable Phase A implementation plan. Do not edit code, tests, configuration, or product documentation. Do not begin Phase B. The orchestrator will save your plan and seek the required founder plan approval before implementation.

## Objective

Implement Phase A of the supplied OMOS product direction: a single human operator can manage multiple client workspaces by reusing existing Projects, with an editable project-associated Business Profile and a persistent, editable AI Business Brief grounded in the profile and that project's existing Knowledge/RAG. Do not build a multi-user agency platform or duplicate Projects, Campaigns, or Knowledge.

## User-approved product constraints

- Use the latest public main of https://github.com/AsamehELsayed/Open-Marketing-OS, including merged PR #3. The clean isolated target checkout is at public main `efbcb7582ca43ef35db70f462ee52a1d9136789b` (PR #3 `Add chat model selection and full Markdown export` was verified merged).
- Preserve Account Manager, project isolation, RAG/Knowledge, automatic tool selection, campaign draft creation, model selection, Markdown chat export, and approval/permission guards.
- Prefer one Client Profile per existing Project. Support partial profiles and never invent missing business facts.
- A Business Brief should label user-provided facts, retrieved evidence, AI suggestions, and unknown information; support editing and persistence. Failed generation must not delete existing profile/brief data.
- Reuse current UI/navigation and project selection. No CRM, no redesign, no billing, no client portal, no publishing integrations, no external publishing.
- Phase A acceptance: fictional client; create workspace and save profile; upload knowledge through existing UI; generate brief; edit/save; restart app and verify persistence; verify another project cannot access the client's private information.
- Two focused PRs total, one per phase. Only Phase A is authorized now. Phase A must be committed and merged to public `main` after checks pass, as the product direction specifies.
- Keep the implementation simple. No unnecessary agents. No paid model calls in tests; use deterministic local fakes. Record actual operations and partial failures honestly.

## Repository inspection facts to verify

- Current checkout is clean public `main` at `efbcb7582ca43ef35db70f462ee52a1d9136789b`.
- Stack: FastAPI (`fastapi>=0.115`), Python, SQLite schema version 12; React 18.3.1, React Router 6.23.1, Vite 5.x.
- Existing `projects` has name, website, goal, status; `companies` has name, website, markets, languages, services. `company_id_for(project_id)` aliases company ID to project ID, and onboarding already associates project/company data.
- Existing project API is in `app/routes/api_spa.py`; campaign and knowledge listing are there too. Existing file upload/index routes are project-scoped. Frontend has ProjectSwitcher, Knowledge, Campaigns, Start Here/Create Business, but no dedicated client workspace/profile/brief UI.
- Inspect relevant route registration, auth/project-boundary checks, repository/migration conventions, knowledge retrieval interfaces, model routing, and tests directly before recommending changes.

## Required output

Write a decision-ready Markdown plan in your final response (the planner lane is read-only). Save no files. The plan must include:

1. Smallest compatible architecture: specifically decide whether to extend the existing Companies/Profile row or introduce a new table, how to persist the Brief, and why. Do not duplicate project or knowledge systems.
2. API and UI surfaces, project-boundary enforcement, generation inputs/evidence labels, failure behavior, and migration/backward-compatibility approach.
3. Ordered implementation packets with exclusive file ownership, dependency graph, and only justified parallelism. Keep worker count minimal.
4. Acceptance criteria matching every Phase A requirement; focused and regression checks discoverable in this repository; a real UI smoke path; no Phase B work.
5. Key risks, open product decisions (if any), and approval-sensitive actions. Do not invent facts about the product.

If the inspected code invalidates any listed repository fact or reveals that Phase A is already implemented, state the evidence and adjust the plan rather than guessing.
