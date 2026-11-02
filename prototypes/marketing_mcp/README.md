# Offline marketing MCP proof

This optional prototype uses the official **mcp==1.30.0** SDK and existing OMOS controls. It is not imported by application startup or included as a production integration. No LLM or provider SDK is needed. The SDK is MIT-licensed; its package retains its own license. Research and provider/license boundaries are in [the comparison](../../docs/research/marketing-connectors.md).

From the repository root, using Python 3.11+:

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m prototypes.marketing_mcp.demo
python -m pytest app/tests/test_phase45_marketing_mcp.py -q
```

The dedicated SDK pin lives in `prototypes/marketing_mcp/requirements.txt`; dev requirements include it so CI cannot silently skip the protocol tests. Production requirements and release lock remain unchanged. A missing or different SDK version fails acceptance. The SDK's newer v2 API is a separate compatibility task.

The demo creates a temporary synthetic SQLite workspace under ignored `.scratch/`, then removes it. It uses existing project/campaign repositories and real deliverable creation and editorial transitions. Only fixture creation fixes generated identities; real preparation uses persisted content and does not generate payload UUIDs or timestamps. The demo prints discovery/schema/protocol receipts, default-deny and unapproved refusals, two equal approved draft payloads, sanitized gateway audit, and real SQLite `tool_runs` rows. Audit timestamps and tool-run IDs vary; the canonical draft and digest remain identical for identical approved input.

Public demo output omits the synthetic idempotency digest and summarizes generated run IDs after the real equality and digest checks. The host and server still generate and validate the full SHA-256 value; tests assert raw-payload determinism before evidence sanitization. Committed evidence uses relative paths, algorithm/length/match facts and test outcomes rather than high-entropy digest literals or machine-specific paths.

The only discovered tool is `format_campaign_draft`. Its fixed local subprocess runs the pinned official FastMCP server via stdio, using `sys.executable`, isolated Python mode and argument arrays. There is no configurable command, URL, account, token or provider endpoint. No publish, schedule, send or connect tool exists. The result is an **OMOS offline draft envelope**, not a Postiz/Planable/Metricool API payload or submitted draft.

OMOS remains the authority: the host checks trusted project identity, current `APPROVED` status, exact requested version, matching approved history and valid final copy. It repeats the check inside the registered handler and after the protocol call. Direct Registry invocation with a forged approval string does not bypass these checks. Pending edits invalidate preparation even when older approved text exists; the production ZIP export retains its existing last-approved behavior. Only final copy reaches the formatter; internal audit sections stay in SQLite.

Both the Registry and gateway deny execution until the host explicitly opts this synthetic project's formatter in. A gateway execution grant is temporary and revoked after every attempt. Registry permission approval is distinct from editorial approval. The prototype registers one valid `ToolRecord` itself because the legacy gateway automatic bridge uses incompatible cost metadata; it does not alter production code. Real registry telemetry and gateway audit are reused; gateway audit is exported evidence, not a new durable production audit subsystem. No real vault is read. The existing vault-reference validation is exercised, and Windows DPAPI remains the production credential store.

Network operations are denied in the host's protocol context and the child server. Windows Python creates private loopback IPC while constructing its asyncio event loop; guards activate after that initialization and before MCP work. The child guard self-test proves denial before connecting. This is an application-level guard for trusted fixture code, not an operating-system sandbox for hostile plugins. Timeout covers SDK process launch/handshake/call; SDK shutdown can add its bounded grace period. Tests observe real process exit on success, error and timeout. A restrictive Windows execution sandbox can obstruct local IPC/subprocess startup; run the same local test command in a normal terminal if necessary.

Installed Electron/frozen-interpreter packaging, Linux execution, provider OAuth, provider draft schemas, refresh/revoke and live tenant mapping are not established by this Windows Python proof. Keep every future integration default-denied. Next recommendation: separately authorized self-hosted Postiz discovery/channel-schema reads, pinned revision/license review and isolated account/project mapping before any draft write. Publication and scheduling remain outside this phase.
