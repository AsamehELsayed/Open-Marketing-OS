# DEV-038 technical plan — Northstar synthetic end-to-end acceptance

## Goal and source

On an isolated branch/worktree based on DEV-037 equivalent commit `7136389aed293c152fb387de2a2f97bc1cd2dd47`, prove the complete Northstar workflow: use or create an isolated client, read project-scoped evidence, generate one Strategy Brief through a deterministic fake provider, apply the real factual validator, persist the accepted deliverable, and export Markdown. Preserve safe diagnostic evidence and classify each failure before fixing a reproducible defect. This is MEDIUM because the application and acceptance harness may both require coordinated changes. One DEV-038 run covers bounded diagnosis, repairs, QA, review, and remediation; do not create a phase for each failure.

## Current evidence and uncertainty

The canonical untracked acceptance harness in the main repository is separate from this clean source worktree. Its `acceptance_source_guard.py` pins the exact DEV-037 commit and tree. Its documented `--offline-fault-test` injects a failed fake generation and checks crash-safe sanitized output; it does **not** prove persistence and Markdown export. The original Northstar source DB is read-only and hash-pinned; a prepared workspace is copied per run. A copied prepared database has already connected using current `sqlite3.connect`, with `user_version=14`, so a database initialization defect is **not established**. Reproduce and classify any failure before changing DB code. Treat the previous harness's fixed commit guard as a provenance issue to resolve explicitly for a DEV-038 descendant without relaxing clean checkout, approved ancestor/tree, or module-origin verification.

## Scope and exclusions

Affected areas, only if diagnosis warrants: acceptance harness and focused tests; application SQLite initialization, generation/persistence, validation, and export seams; focused regression tests and safe run evidence. Import the untracked canonical harness into this branch only through a deliberate, reviewable file selection; never copy bulk historic acceptance artifacts or secrets. Keep the original Northstar evidence and client data untouched. Use a fresh run-specific workspace database and output directory. Do not make real provider GET/POST requests, live inference calls, use paid/fallback models, weaken factual checks, alter pricing/privacy rules, disable project isolation, deploy, merge, or modify real client data. No SOL strategic gate is triggered by a technical repair.

## Implementation packet (one bounded worker; no nested delegation)

1. Capture source commit/tree, import origins, input file hashes, SQLite `quick_check` and schema version, and sanitized baseline failure with exact command, exit status, and stage. Read canonical runner implementation and relevant production seams. Classify each failure as application, harness, environment, or external; do not assume the database is faulty.
2. Build an offline, deterministic fake-provider path that traverses production generation, factual validation, persistence, and Markdown export. Use known synthetic facts tied to allowed project-scoped source IDs; label generated content and artifacts `SYNTHETIC`. Exercise the real validator, with no special acceptance bypass. Ensure network calls are disabled or fail closed.
3. Repair the smallest reproducible in-scope defect. For every confirmed software defect, add a regression that fails on the original behavior and passes after repair. Address acceptance source provenance for the DEV-038 descendant with an explicit allowlist/manifest or equivalent verified descendant policy, retaining clean checkout, trusted DEV-037 base, module-origin checks, and immutable original evidence hashes. Do not silently repin to arbitrary HEAD.
4. Rerun focused regressions and the full synthetic workflow. Persist a real DB deliverable in the isolated workspace, then export and inspect Markdown from that record. Record database and export paths, IDs, hashes, source links, validation result, row counts, and before/after source and other-project fingerprints without revealing private row contents. Stop only on a genuine external blocker or after bounded effort (one full diagnosis, up to three targeted repair cycles, then report remaining reproducible failure).
5. Write `workers/implementer.md` with commands, root-cause classifications, changes, tests, diagnostic artifact paths, zero provider traffic evidence, and unresolved limits. Worker must not delegate.

## Dependencies and execution order

One worker packet is appropriate because the source guard, harness path, and app behavior depend on one another; parallel coding would overlap files and obscure attribution. Worker starts **only after founder plan approval is recorded** via `scripts/dev_run.py approve-plan --id DEV-038` and the implementing gate passes. Independent QA follows implementation; independent reviewer follows QA. If QA/review finds a fixable in-scope issue, the implementer remediates within DEV-038 and a separate reviewer execution re-reviews. Record every role in `executions.jsonl`; no role is inferred from prose.

## Acceptance and verification

- Deterministic offline command returns success and records exactly one synthetic Strategy Brief produced via fake provider, accepted by the unchanged factual validator, persisted in a fresh SQLite workspace, and exported as Markdown read back from persistence.
- Project and campaign references resolve to Northstar; source IDs belong to the selected project. A second project's counts/hashes stay unchanged. Original source DB bytes/hash stay unchanged, and source/workspace files are distinct; `quick_check` passes before and after.
- Source checkout and imported production modules have verified origins. A dirty checkout, wrong source, cross-project evidence, rejected factual content, and accidental network dispatch fail closed in focused regressions.
- Safe evidence includes run ID, commands, stage transitions, sanitized error classification, schema version, DB/deliverable/export paths and hashes, and request counters. No secret, raw rejected prose, or private database row dump enters artifacts.
- Focused regressions and relevant existing deterministic tests pass. QA independently replays the workflow and inspects isolation, factual guard, export, failure modes, and zero traffic. Reviewer issues `VERDICT: APPROVE` only after inspecting plan, diff, tests, QA, and evidence. CI gates remain required before merge/deploy.
- Final report distinguishes synthetic success from real OpenRouter readiness. Real provider eligibility/connectivity/ZDR and live execution remain unverified without separate authorization; counts remain real provider requests **0** and real inference POSTs **0**.

## Risks and stop conditions

The source snapshot and prepared DB are untracked outside this branch, so capture hashes and only promote needed harness code/fixtures; preserve approved source evidence. A fake-provider response can accidentally bypass actual generation or validation: QA must trace the production call chain and inspect the persisted record and exported file. If an external provider state blocks real acceptance, report that dependency separately without attempting live traffic. If a defect would require pricing, privacy, provider routing, or broad architecture changes, stop for a material scope decision. No production merge or deployment is authorized by this plan.
