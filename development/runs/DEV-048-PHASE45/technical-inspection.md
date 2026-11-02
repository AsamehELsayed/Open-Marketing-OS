# DEV-048-PHASE45 technical inspection

Independent planner: in-session `technical_plan`, safe general-execution fallback after external OpenCode source-egress rejection. Read-only code inspection and dependency metadata inspection; no prototype/product implementation or live-provider test executed.

## Environment evidence

Observed Windows Python executable `sys.executable`, Python 3.12.10 64-bit. Metadata: `mcp==1.30.0`, `langchain-core==1.6.5`, `pytest==9.1.1`, `anyio==4.15.1`; `langchain` and `langchain-mcp-adapters` are not installed. A second metadata-only check used `importlib.metadata.version`/`PackageNotFoundError`. Presence is not an integration test. Current runtime requirements do not declare `mcp`; add a prototype-only pin, avoiding a core dependency migration.

## Existing reusable boundaries

- `app/services/mcp/gateway.py`: `McpGateway` accepts injected transport, registry, integration registry and vault. Discovery leaves allowlists denied. Execution checks `(server, project, tool)` allowlist and approval mapping before vault resolution or transport. `secret_ref` accepts a vault reference or `None`; no fixture credential is needed. Audit records scrub details and retain argument names rather than values. Default HTTP transport is network-disabled; its handwritten request sequence must not be claimed to prove official SDK/session compatibility.
- `app/services/mcp/manager.py`: classification/approval/redaction helpers are already present. The fixture exposes a known read-only formatter only; deny all other names instead of relying solely on provider annotations or naming heuristics.
- `app/services/credentials/store.py`: existing Windows `DpapiFileBackend` uses user-bound `CryptProtectData`/`CryptUnprotectData`, encrypted files and atomic replace. Do not copy or replace it. `vault.py` preserves project/scoped `vault://` references. This prototype uses no secrets, so no live vault read is required; later provider auth must reuse these APIs.
- `app/services/campaign_deliverables.py`: `get_deliverable` and history validate campaign/project ownership. `transition` implements versioned DRAFT -> IN_REVIEW -> APPROVED; `revise` increments version and reopens approved content as DRAFT. `validate_final_copy` and `extract_deliverable_parts` separate final text from audit content. `approved_package_export` intentionally returns a prior approved snapshot while edits are pending, so the POC needs explicit current-approved expected-version semantics, not accidental ZIP reuse.
- `app/services/tools/registry.py`: real `Registry`/`ToolRecord`, enabled flag, trusted call-local execution constraints, approval gate and SQLite `tool_runs` telemetry exist. A nonempty `approval_id` is only an execution capability; it does not query persisted campaign approval. Proof must exercise the real registry, not a dict fake.

## Compatibility gaps to contain

The gateway auto-bridge builds `cost_type=external_call`, outside the real registry's supported `free|metered|byok` values. Its fallback uses legacy registration with a dict. The prototype must avoid claiming this bridge works unchanged: use explicit valid `ToolRecord` registration in prototype composition, with the gateway's optional registry collaborator omitted. This prevents unrelated production changes while proving actual registry reuse. SDK `inputSchema` needs normalization to the gateway's `parameters`. SDK error results need explicit treatment as failure, because the gateway otherwise defaults dict responses to success.

Gateway audit is an in-memory list in this inspected path, while real registry tool runs persist to SQLite. Preserve a sanitized gateway-audit snapshot with run evidence and verify the real SQLite rows; do not imply a new durable production audit pipeline was built. Discovered tools should be visible as disabled records until the fixture explicitly enables its only known formatter for its synthetic project.

## Validation status and handoff

Source inspection complete; metadata inspection complete. No product/prototype code changed, tests not yet run, no provider compatibility established. Research and founder plan approval precede W1. Implementer should use the plan's fixed local SDK server/client, approved-current-version host check, real registry/gateway/SQLite and bounded tests. Independent QA/reviewer remain required separate executions. Planner does not grant approval and does not claim OpenCode execution.

Parent-provided baseline: the existing MCP/registry/vault command recorded 45 passed and 24 skipped (17.85 seconds, exit 0); the entire legacy MCP test module is skipped because private DEV-007 fragments are absent in public main. This is baseline evidence only. The new self-contained SDK/approval/isolation integration tests must execute without those internal fixtures and must not silently skip for missing `mcp`. See `baseline-tests.md` for the parent's actual command/evidence. Windows also printed an ignored `pyreadline` destructor invalid-handle message after tests; do not misattribute that to prototype behavior.
