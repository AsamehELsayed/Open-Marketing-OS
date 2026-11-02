# Direct implementation handoff

Executor: primary agent, direct execution after the founder prohibited delegation. This is not an independent delegated worker. Work observed from 2026-11-02T12:01:42Z; implementation and initial direct review completed by 2026-11-02T12:17:08Z. No external coding service was used.

Implemented the optional `prototypes/marketing_mcp/` package, official SDK pin, fixed stdio server/client collaborator, existing gateway/registry composition, exact current APPROVED revision checks, synthetic demo and self-contained acceptance tests. Default discovery enables nothing. Only LinkedIn text formatting is supported. The envelope is offline, `status=draft`, `publish=false`; no provider endpoint or submission exists.

The host uses the real Registry, gateway project allowlists, temporary tool approval grants, scoped campaign services/history/final-copy validation, vault-reference validation and existing audit mechanisms. Real vault contents are never accessed. Payloads bypass metadata telemetry; raw final copy is absent from gateway audit and tool-run rows. No production startup, provider, credential-store, campaign or gateway source was changed.

Supporting reproducibility changes: dev requirements include the prototype's pin so clean CI cannot skip/miss the SDK. Public export narrowly includes the prototype source, package initializer and research document, with `.scratch` excluded. These changes keep exported tests/dependency paths resolvable; they do not add the prototype to a production runtime or installer.

Actual validation: combined protocol/campaign/registry/vault run **204 passed, 24 skipped in 191.27s**, exit 0; all 26 prototype cases collected in that run executed. Separate new public-export acceptance plus existing exporter suite **13 passed in 17.72s**, exit 0 (one additional prototype case, 12 existing exporter cases). A real demo exited 0 and recorded one discovery plus two initialize/list/call exchanges, identical canonical payloads, four real tool-run rows and zero active sessions afterward. See `qa-direct.md` and raw evidence.

Resolved in-scope failures: restrict temporary SQLite to workspace scratch (sandbox temp ACLs), supply all fixture UUID calls, initialize Windows event-loop IPC before activating network guards, return a safe refusal for the existing `DeliverableNotFound` LookupError, and include public-export dependencies. The predecessor prototype run had 1 failed/24 passed before the LookupError fix; it is retained as historical evidence, not the final verdict. Earlier guard failures prevented operations; no security-boundary breach was observed.

No publishing, scheduling, account connection, provider request, paid call, merge, tag or deploy. No new framework. No independent QA/reviewer is claimed; subsequent technical review is primary-agent self-review, per founder steering.

Final follow-up: strengthened socket guard without changing scope or production code. Final combined suite, now including exporter regressions and all **27** prototype cases: **217 passed, 24 skipped in 215.61s, exit 0**. See `tests-acceptance.txt`. Primary direct work continued through this follow-up; the first completion timestamp above bounds the initial implementation only.
