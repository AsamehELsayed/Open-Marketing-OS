# DEV-031 Phase A independent SOL review

**VERDICT: BLOCK**

**Execution:** Separate `gpt-6.1-sol` review execution, independent of the implementation workers and QA. Applied `C:/Users/User/.agents/skills/code-review-and-quality/SKILL.md`, the supplied operating contract, and `docs/development-delegation-system.md`. No delegation or implementation edits.

**Started:** 2026-10-09 17:12:56 UTC
**Completed:** 2026-10-09 17:17:35 UTC
**Baseline:** public main `efbcb7582ca43ef35db70f462ee52a1d9136789b`

## Required changes

### F1 — P1: Unsupported citations outside the narrow ID regex are saved

**Evidence:** `app/routes/business_workspace.py:128-145` recognizes only bracketed IDs composed of letters, digits, underscores and hyphens. An unknown ID with a period, such as `[invented.source:reference]`, matches none of those checks. Having the four required headings then lets the response reach the persistence at lines 159-164.

**Independent reproduction:** Called the actual generation route with an in-memory SQLite database and a deterministic fake ModelRouter. Retrieval returned zero hits. Output with all four headings and `A made-up fact [invented.source:reference].` succeeded and persisted that text: `brief_revision=2`, `sources_json=[]`. No external provider or network request was made.

**Impact:** Violates the founder requirement to never invent citations and the plan's allowlisted-reference contract. The prior remediation closes its three reported formats but does not close the broader unsupported-reference gap.

**Required remedy:** Validate citation candidates comprehensively against the supplied evidence IDs, rejecting noncanonical or unknown source references before persistence. Use a focused validation helper rather than adding more format-specific conditional branches. Add regression coverage for unknown IDs containing punctuation and verify that rejection preserves the previous brief, revision and provenance.

### F2 — P2: Client creation silently discards supplied optional profile values

**Evidence:** `app/routes/business_workspace.py:35-50` accepts an unrestricted payload but inserts six literal empty strings for industry, description, audience, location, offer and differentiators at lines 47-48. The frontend API advertises those optional properties in `frontend/src/api/client.ts:224-233`. Creation also bypasses the type/length validation used by profile updates in `app/services/business_workspace.py:35-43`, converting a supplied non-text website to text.

**Independent reproduction:** Called the actual creation route in an in-memory database with nonempty values for all six optional fields. Every one came back empty: `{'industry': '', 'audience': '', 'description': '', 'location': '', 'offer': '', 'differentiators': ''}`.

**Impact:** Name-only creation works, but partial profiles supplied at creation do not round-trip. Callers receive success despite lost founder-provided information.

**Required remedy:** Reuse a single profile-field validator for creation and update. Persist supplied valid optional values, retain blanks for absent fields, and validate all fields before inserting either project or profile. Cover optional-field round-trips and invalid creation without partial rows.

### F3 — P2: A→B→A switching admits stale save/generation responses

**Evidence:** `frontend/src/routes/Workspace.tsx:55-85` captures the project ID and checks only whether it equals `selectedProjectId.current` when handling action success, error and finalization. The load path already uses an invalidation token at lines 36-50, but actions do not capture it. `frontend/src/App.tsx:41-47` intentionally keeps routes mounted across project selection.

**Failure sequence:** Start a save or generation in A; switch to B; switch back to A; load current A and make an unsaved edit; let the old A action response arrive. Its ID again matches, so lines 61, 71 or 83 replace the current editor and clear its dirty state. Old errors/finalizers can similarly affect the new selection session. This follows directly from the retained component and ID-only guards; it was not exercised in a browser in this execution.

**Impact:** Breaks the approved requirement to ignore late action responses from a previous selection and can discard current unsaved work.

**Required remedy:** Capture a selection/load epoch with every action and require both that epoch and the project ID to remain current before applying success, error or finalization. Add a behavioral delayed-response check covering A→B→A for save and generation.

### F4 — P2: Concurrent first loads of an existing project can fail initialization

**Evidence:** `app/services/business_workspace.py:13-23` performs SELECT, then an unconditional INSERT when no profile exists. The Workspace itself launches profile and brief GETs concurrently at `frontend/src/routes/Workspace.tsx:43`; both call `get_profile` through the read routes or `get_brief` (`app/services/business_workspace.py:62-66`). Existing projects have no profile until this first access.

**Independent reproduction:** With an in-memory existing project lacking a profile, simulated the legitimate interleaving in which both callers observe no profile and the first inserts/commits immediately before the second caller's INSERT. The actual service raised `IntegrityError: UNIQUE constraint failed: business_profiles.project_id`. The read endpoints do not handle it.

**Impact:** First opening a legacy workspace can produce a 500/load failure during the normal parallel GET flow.

**Required remedy:** Make lazy initialization idempotent and race-safe, for example with a targeted `ON CONFLICT(project_id) DO NOTHING` insert followed by reading the row. Preserve the winning row and its existing revisions. Cover competing initialization and ordinary legacy workspace loading.

## Five-axis assessment

| Axis | Assessment |
|---|---|
| Correctness | Changes required: F1-F4. Conditional profile/brief updates and generation's dual revision predicate otherwise provide sound persistence protection. |
| Readability | Small service, route and component modules remain understandable. Validation should become one explicit boundary helper as requested above; no cosmetic blocker. |
| Architecture | Reuses Projects, SQLite, existing scoped Knowledge and ModelRouter. Additive v13 table avoids replacement-style project-write cascades. F4 needs safe legacy initialization. No duplicate provider, campaign or agent stack. |
| Security | Path project ID is authoritative and conflicting body IDs are rejected. SQL values are parameterized; profile update columns come from a fixed whitelist. Isolated clients skip shared root ingestion. F1 fails the untrusted model-output/evidence boundary; F2 bypasses creation validation. No new multi-user/auth claims or dependency changes. |
| Performance | Retrieval is limited to eight hits and 1,200 characters per hit; the model request has tools disabled and a 1,800-token output limit. No unbounded new list endpoint or costly dependency. Apply creation field limits under F2 to keep persisted profile inputs bounded too. |

## Founder requirements and Phase A boundaries

| Requirement | Review result |
|---|---|
| Create a distinct client with name only; short flow | Supported by UUID project creation, isolated marker, and explicit create-client mode. Optional API values require F2. |
| Editable partial Business Profile | Implemented; update validation and revision checks inspected. Creation path incomplete under F2. |
| Persistent editable Business Brief independent of profile | Implemented with separate revisions. Manual saves preserve generation metadata; generation writes only after validation and conditional persistence. |
| Generate from selected profile and scoped Knowledge | Existing scoped retrieval and ModelRouter reused. Unsupported citation validation requires F1. |
| Separate facts, evidence, suggestions and unknowns | Required headings validated; UI labels all four categories. This is structural validation, not a guarantee of semantic model accuracy. |
| Show generation state and actual provider/model; understandable failures | Success metadata and structured errors are displayed. Friendly no-provider/auth/model/timeout paths inspected. |
| Editing remains available after generation failure | Implemented and covered by recorded browser acceptance. |
| Explicitly confirm replacing an edited brief | Corrected confirmation accurately describes immediate persistence and discarded unsaved edits. Server overwrite guard retained. |
| Client isolation, switching and restart persistence | Backend project scope and shared-root skip supported; recorded two-client/restart smoke inspected. Switching requires F3; legacy first load requires F4. |
| Preserve onboarding, chat tools, campaign drafts, model selection, export and approval guards | Changes are additive and regression evidence inspected; existing first-run onboarding route retained. |
| Phase A only | No CRM, billing, portal, publishing integration, installer changes, multi-user auth or Phase B work identified. |

## Validation references and limits

Read the approved `plan.md`, `planner-brief.md`, both worker handoffs, `acceptance.md`, `remediation.md`, all three QA artifacts, `run.json`, and `executions.jsonl`. Reviewed the complete tracked baseline diff and all new implementation/test files, including files omitted from ordinary `git diff` because they are untracked.

- Final independent QA records **39 passed, 1 skipped** for the focused migration, publishing-guard, DEV-031, DEV-014 and DEV-030 batch; earlier acceptance records **124 passed**, typecheck and production build success.
- QA's frontend confirmation assertion passes, but it checks source text and does not cover delayed selection responses.
- This review independently ran `git diff --check` against the public-main baseline: passed.
- This review independently executed the two production routes against in-memory SQLite and the controlled lazy-initialization interleaving described above. These checks used deterministic fakes, no disk database and no provider call. No test or implementation files were added or edited.
- Real browser generation remains unavailable in the recorded acceptance environment because no provider was ready. Real generated output is **not verified**; fake-provider output is test evidence only. The browser scenario was read, not rerun by this reviewer.
- GitHub Actions have not been validated by this execution. Generated pytest/smoke artifacts should be excluded from the eventual implementation commit as appropriate.

Final QA PASS does not cover F1-F4. Remediate these findings within the approved scope, run independent QA on the corrected result, and obtain a separate real SOL re-review before the user-authorized PR/CI flow. No approval or completion claim is made here.
