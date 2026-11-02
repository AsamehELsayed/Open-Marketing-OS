# DEV-048-PHASE45 — research-first offline marketing MCP prototype

Planner: independent in-session general execution (`technical_plan`). External OpenCode execution was rejected by automatic approval review for private source-code egress; this is the disclosed safe local fallback, not an OpenCode execution. No subdelegation. Classification: MEDIUM, bounded multi-file research/prototype; no production architecture replacement and no SOL planning/review gate currently triggered.

## Goal and sequence

First deliver `docs/research/marketing-connectors.md` from inspected upstream evidence. Then demonstrate real MCP tool discovery and deterministic formatting of an approved campaign draft payload using the official SDK and existing OMOS security/state components. The prototype runs against an isolated synthetic SQLite workspace, without real accounts or external provider requests.

Research is an implementation prerequisite. This plan requires the founder's single explicit plan approval before modifying product/prototype code. After approval the orchestrator records `python scripts/dev_run.py approve-plan --id DEV-048-PHASE45`, then checks the implementing gate. Original mission authorization is not recorded as a later plan approval. Routine implementation, QA, review, remediation and re-review inside this scope are then autonomous.

## Research/reuse decision

Use existing `app.services.mcp.McpGateway`, `app.services.tools.registry.Registry`, campaign deliverable/version/history APIs, credential reference contract and audit mechanisms. Keep all prototype composition outside normal app startup. Research determines the recommended next provider integration; no provider implementation is needed now.

The installed Windows Python 3.12 environment has official `mcp==1.30.0`. Pin that version in a prototype-only requirements file and inspect its official tagged source/API before implementation. Upstream SDK v2 uses a different API; do not silently upgrade the application or copy v2 examples into v1 code. LangChain is unnecessary for deterministic discovery/formatting; the old adapters repository is not the maintained support route. Document the maintained route from research without installing or upgrading the application's LangGraph stack.

## Bounded implementation

Create a small `prototypes/marketing_mcp/` package with an SDK FastMCP stdio fixture server, a host/demo, prototype requirements and a README. The server exposes one deterministic formatter tool, such as `format_campaign_draft`, and no publishing/scheduling/account/auth/HTTP tool. Its result is an OMOS draft envelope, not a claim of provider-schema conformance or a successful provider draft creation. It accepts already-selected content for formatting; approval authority belongs to the host, not an attacker-supplied boolean.

The host uses a fixed local script/module command launched with `sys.executable` and argument arrays (no shell string, arbitrary executable, configurable endpoint or inherited provider secrets). A narrow SDK transport collaborator implements the existing gateway's synchronous discovery/call shape. It performs real SDK initialization, `tools/list` and `tools/call`, translates `inputSchema` to the gateway's `parameters`, enforces a bounded timeout and handles `isError`/invalid result/unknown tool fail-closed. It supports exactly this fixture, not arbitrary connector installation. If an SDK in-memory transport is chosen instead, genuine protocol handshake/discovery/call must still be tested and Windows subprocess compatibility must be reported untested; stdio is preferred for this task.

Use the real Registry explicitly with valid `ToolRecord` vocabulary (`source_type=mcp`, `cost_type=free`, no retry). Existing gateway auto-bridging currently constructs an incompatible `cost_type=external_call`, so inject no registry into that legacy bridge and register the one known local tool in the prototype composition itself. This is a few-line bounded bridge, not a gateway/registry rewrite. Discovered tools remain disabled/default-denied until the demo explicitly permits the one formatter for its synthetic project. Exercise calls through the real Registry and gateway so tool-run telemetry and gateway audit are real.

Persist synthetic project, campaign and deliverable rows using existing repositories/services. Create content with `create_manual` or `save_generated_batch`; approve only through the existing `DRAFT -> IN_REVIEW -> APPROVED` transition API in the demo setup. Before formatting, read via `get_deliverable(conn, trusted_project_id, campaign_id, deliverable_id)` and require current status `APPROVED`, matching `expected_version` and a matching approved history snapshot. Reuse `validate_final_copy`/`extract_deliverable_parts`; send only final copy, never private audit/provenance notes. Require a supported `social_post` type/platform for this bounded fixture. Re-read immediately before the local formatter call; immutable/versioned payload output is the security boundary, not eventual publication. All preparation is read-only with respect to campaign content.

Semantics are intentionally stricter than `approved_package_export`: that existing ZIP export can include a last-approved revision while the current row is a newer draft. This prototype prepares only the explicitly requested currently approved version and rejects stale/newer edits. Do not change ZIP export behavior. Registry `approval_id` being nonempty and gateway per-tool `grant_approval` are execution capabilities, not evidence of campaign content approval. Any such capability is derived only after the persisted content check, and direct tool invocation cannot bypass that host check.

Credentials: register fixture with `secret_ref=None`; do not read the real vault, invent OAuth, copy credentials into env/config, or create a new secret store. Reuse the existing vault reference validation contract and demonstrate that inline credential registration is rejected. Preserve DPAPI as the production vault; live authentication and provider-scoped vault resolution remain a later authorized integration. Use temporary data roots and no default workspace database.

Canonical draft JSON contains synthetic project/campaign/deliverable IDs, approved version, platform, final text, `status=draft`, `publish=false` and a stable digest/idempotency key derived from canonical approved content. No generated timestamp/UUID enters the deterministic payload. Operational audit timestamps/tool-run IDs may vary and are excluded from equality assertions. Save sanitized evidence that separates draft content from audit metadata, and record protocol/package/test facts without tokens, arguments or content in telemetry.

## Packets and dependencies

| worker_id | depends_on | files_or_scope | can_run_parallel |
|---|---|---|---|
| R1 | [] | `docs/research/marketing-connectors.md`, research handoff/evidence only | true (planning inspection only) |
| P1 | [] | this plan and `technical-inspection.md` only | true (research only) |
| W1 | [R1, P1, founder_plan_approval] | new `prototypes/marketing_mcp/`; new `app/tests/test_phase45_marketing_mcp.py`; worker handoff and sanitized demo/test evidence | false |
| QA1 | [W1] | independent test execution, `qa.md`, QA evidence only | false |
| REVIEW1 | [QA1] | independent diff/requirements/evidence review, `review.md` only | false |
| FIX1 (conditional) | [REVIEW1] | fixes within W1 scope and `remediation.md` | false |
| REREVIEW1 (conditional) | [FIX1, fresh_QA_as_needed] | independent `review-2.md` and fresh evidence | false |

One coding worker avoids overlapping shared-file edits. No coding worktrees beyond the isolated phase branch are required. Research/planning overlap must not be described as parallel coding; report `PARALLELISM: NO` unless recorded actual scheduler worker intervals establish otherwise. No nested delegation. Every role counts only after its separate real execution, on-disk artifact and accurate `executions.jsonl` record.

## Tests and acceptance

1. Real official SDK stdio initialization, discovery of the one formatter with its schema, and call through the local composition complete on Windows. A fake `list_tools` response alone is insufficient.
2. Repeated preparation of the same approved synthetic version yields byte-identical canonical payload; process cleanup occurs on success/error/timeout.
3. DRAFT, IN_REVIEW, arbitrary approval strings, missing/malformed final copy, wrong type/platform, stale expected version and edits after approval are rejected before formatting; no calls occur on rejected requests.
4. Project B cannot prepare project A's campaign/deliverable even if identifiers/argument fields are supplied. Trusted project identity comes from host invocation, not tool arguments.
5. Discovery does not enable tools; unallowlisted, unknown, publish-like and malformed tools/schema are rejected. Formatter errors (`isError`), malformed output and wrong fixed endpoint fail closed. The command set contains no publishing/scheduling/account-connect method.
6. Existing Registry records actual successful/refused calls in isolated SQLite `tool_runs`; gateway audit records contain operation metadata/argument keys without final-copy text, fake credential sentinels or real secrets. Inline secrets are refused by the existing gateway contract. No external sockets/HTTP requests or provider calls occur during prototype tests; guard outbound networking, including the subprocess fixture, rather than infer this only from a flag.
7. Run targeted new tests plus relevant existing MCP, campaign/version/export and registry/vault regressions. Report exact commands, counts, skips and exit codes. Missing SDK must fail the explicitly documented POC acceptance command rather than produce a falsely passing skipped integration test. Preserve sanitized command output and package versions.
8. Independent QA inspects current source/diff and executes tests. Independent reviewer reads plan, research, handoff, diff and QA; completion requires latest real verdict `APPROVE`, including re-review after remediation.

## Risks, non-goals and stop conditions

No custom universal framework, production transport rewrite, UI, account connection, OAuth flow, provider SDK installation, public posting, paid provider request, merge, tag or deploy. No Electron installer claim: this phase proves Windows Python protocol execution only; bundled Python executable/resource paths, subprocess policy, signing and OAuth redirects need separate installed-app acceptance. SDK1 pin is deliberate debt with a documented v2 migration check, not a recommendation to ignore newer security fixes. No broad dependency update or core lock-file churn.

Stop and report if source/license/auth evidence is inaccessible, real SDK execution cannot run, isolation/approval/audit tests fail or resolving an issue requires production architecture/security semantics, paid/external access or a material scope change. Do not silently broaden the prototype or claim completion. Any provider selected for a later integration must pass independent license/auth/capability review and a sandbox read-only discovery test under separate account authorization before draft-write tests. That future provider integration must bind provider account/workspace to OMOS project and exact approved payload/version; scheduling/publishing remains default-denied.

## Delivery and approval-sensitive actions

Prepare a separate draft PR on `codex/phase-4-5-marketing-connectors`, based on fetched `origin/main` commit `8a5cce98a5fb5cbd3073af626aa5f810ff6c2559`. The user explicitly requested the draft PR; after plan approval and passing gates, commit only this phase's artifacts and create that draft PR, with no merge/tag/deploy. Include actual tests, risks, next integration recommendation and compact proof block (run, planner/workers/QA/reviewer/remediation/re-review/tests). Keep the existing dirty original checkout untouched. Final next measurement is whether the selected provider's separately authorized discovery and exact-version draft mapping satisfy these boundaries; otherwise stop before integration.
