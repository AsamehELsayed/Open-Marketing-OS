# Controlled local Postiz drafts — DEV-051

Working **offline-tested, disabled-by-default prototype**, stacked on DEV-050.
No application startup registration or production route. Existing campaign/editorial
services and Approvals records supply the local workflow; `local.py` shows the exact
payload, accepts the operator's separate decision and displays persisted results.
The production campaign UI has not been wired to dispatch writes.

**Actual Postiz writes performed in DEV-051: NO.** No account connection, provider
request, Docker startup, deletion, deployment or merge. NO DELEGATION: author tests
and self-review do not satisfy independent QA/reviewer gates. Do not enable actual
writes before separate founder authorization and the outstanding independent gates.

## Verified official contract

Pinned server: Postiz **v2.25.3**, commit
`a5affe62c74e39c1e7ac960d50d8418b807d532c`; image digest inherited unchanged from
DEV-050. Official Python MCP SDK **1.30.0**, existing installed environment.
Source inspection is not live draft acceptance. File hashes and evidence statuses
are in `evidence/official-contract.json`.

- [Official tool](https://github.com/gitroomhq/postiz-app/blob/a5affe62c74e39c1e7ac960d50d8418b807d532c/libraries/nestjs-libraries/src/chat/tools/integration.schedule.post.ts):
  createTool ID `schedulePostTool`, class/loader name `integrationSchedulePostTool`.
  Only the exact `schedulePostTool` wire name is accepted. Discovery fails closed if
  it differs. No alias probing or fallback write. Input is `{socialPost:[...]}`;
  required fields: `integrationId`, `isPremium`, `date`, `shortLink`, `type`,
  `postsAndComments:[{content,attachments}]`, `settings:[{key,value}]`.
  Success is `{output:[{postId,integration,previewUrl}]}`; errors use output.errors.
- A real organization-owned integration **row is required**. No zero-channel draft
  path exists here. An inert synthetic LinkedIn row can supply the local fixture
  without OAuth or a real account: official lookup uses organizationId/id and
  validation uses local LinkedIn checks. This is a static-source conclusion;
  successful live inert-channel draft creation remains unverified.
- [Posts service](https://github.com/gitroomhq/postiz-app/blob/a5affe62c74e39c1e7ac960d50d8418b807d532c/libraries/nestjs-libraries/src/database/prisma/posts/posts.service.ts)
  validatePosts 1005–1101 performs provider-specific validation. LinkedIn text-only
  checkValidity/maxLength are local. createPost 1148–1227 calls persistence and
  startWorkflow; startWorkflow 936–998 **queries Temporal** for existing workflows
  before returning for DRAFT. It does not start a publishing workflow for DRAFT.
  Do not describe this as having no infrastructure activity. `shortLink:false`
  avoids link shortening. No media, comments, helper tools or provider calls are
  needed on this constrained path. Runtime absence of provider egress is pending.
- [Repository](https://github.com/gitroomhq/postiz-app/blob/a5affe62c74e39c1e7ac960d50d8418b807d532c/libraries/nestjs-libraries/src/database/prisma/posts/posts.repository.ts)
  createOrUpdatePost 606+ stores state DRAFT and organization/integration ownership.
  The server generates post/group IDs. The inspected MCP input has **no client
  idempotency key** and is explicitly non-idempotent.
- [Readback](https://github.com/gitroomhq/postiz-app/blob/a5affe62c74e39c1e7ac960d50d8418b807d532c/libraries/nestjs-libraries/src/chat/tools/posts.list.tool.ts)
  `postsListTool({startDate,endDate})` reads organization-scoped local posts,
  including DRAFT and QUEUE. It is exposed only inside this separate composition,
  for a fixed two-day window; the DEV-050 three-tool read allowlist is unchanged.

## Authority and outcomes

Trusted operator configuration fixes project, organization, channel, endpoint and
vault reference. Model-callable args contain only operation/approval IDs. Registry,
Gateway and HTTP policy separately constrain dispatch. No raw credentials in config,
URL, audit or receipt. Every SDK session checks authenticated `/me` organization and
that `integrationList` contains the exact LinkedIn channel. No remote URL is followed.

Preparation reuses PR19's exact current APPROVED revision/history and final-copy
validator. Only extracted final copy is HTML-escaped into paragraphs. Private audit
sections never enter the payload. One text-only post, no comments, attachments or
settings; no shortening. Date is frozen required metadata `2026-01-01T00:00:00Z`,
not a scheduling input. All other dates/modes/fields are denied before transport.

The operation SHA-256 binds project, org, endpoint, vault ref, channel, revision,
content, payload and tool. Existing `approvals` stores a separate pending decision
and exact payload preview. A forged nonempty Registry approval string grants nothing:
the handler verifies the persisted approved decision, kind, payload binding and
decision metadata. The operation ledger uniquely binds that approval to the intent.

`prepared -> in_flight` is committed atomically before network access. Preflight
readback saves baseline post IDs and the revision is checked again before writing.
Any in-flight/ambiguous operation is never dispatched again, including after a crash
or simultaneous duplicate click. Success preserves remote ID; late local edits
produce `succeeded_stale` with the remote ID rather than hiding the created draft.
Timeouts, malformed results and failures are conservatively ambiguous and consume
the approval even if the server may not have received the request.

Reconciliation reads exact content/channel/date/DRAFT matches absent from the
baseline. It returns candidate IDs for manual review, never authorizes retry. If
baseline is missing, zero/multiple candidates exist, or another actor created
identical content, identity cannot be proved. No exactly-once server guarantee is
claimed. Do not delete/recreate remotely to resolve uncertainty. Stop for a human.
Receipts and reconciliation also verify the persisted trusted binding; changing
operator configuration cannot reinterpret an old operation under another org.

The local composition root/CLI and OS-owned config/database/vault are trusted.
They are not exposed as LLM tools. General application authentication and production
UI wiring are intentionally not added by this opt-in prototype. Use a dedicated
committed SQLite connection; outer transactions are refused before dispatch.

## Offline reproduction

Reuse the current Python environment; do not install/copy environments for DEV-051:

```powershell
python -m pytest -q app/tests/test_postiz_drafts.py --basetemp=development/runs/DEV-051/tmp-offline
python -m prototypes.postiz_drafts.local --help
```

Tests use synthetic campaign services, real SQLite approvals, real SDK protocol
serialization and httpx.MockTransport. They never contact Postiz. Synthetic timeout
after remote-commit simulation is not evidence of an actual remote draft.

## Prepared disposable acceptance — NOT executed

1. Obtain separate founder approval for **one actual text-only draft write** on the
   pinned disposable Postiz environment, naming org/channel/revision/payload. Obtain
   independent QA and reviewer approval first. No real accounts or provider calls.
2. Reuse DEV-050's pinned official Compose/toolchain and inert fixture procedure in
   `../postiz_readonly/environment/`. Inspect existing named resources before reuse;
   do not recreate another environment or overwrite existing data. Its prior stack
   was removed, so resource recreation/storage cost requires the acceptance approval.
   Keep its internal Docker network, loopback ingress and egress restrictions. Seed
   synthetic orgs/inert disabled LinkedIn channels only; no OAuth or real tokens.
3. Snapshot Postiz domain rows, workflows and provider-egress evidence before any
   tool call. Verify image/version, `/me` identity, exact wire tool name/schema, local
   channel and egress boundary. Stop on drift; never widen permissions to pass.
4. Create a synthetic OMOS SQLite DB with the existing campaign seed/services. Review
   and approve its exact editorial revision. Store the synthetic local API key using
   the existing project Vault backend. Operator config contains only the DB path,
   project, organization, channel, loopback MCP endpoint and returned vault reference.
   Preserve that database across invocations: it is the durable idempotency ledger.
5. Local review commands below do not contact Postiz. Inspect the complete printed
   payload and organization/channel before the separate approval decision:

```powershell
python -m prototypes.postiz_drafts.local --config trusted-local.json prepare CAMPAIGN DELIVERABLE VERSION
python -m prototypes.postiz_drafts.local --config trusted-local.json approve OPERATION_ID
python -m prototypes.postiz_drafts.local --config trusted-local.json status OPERATION_ID
```

6. **Only after the separate approval in step 1**, the prepared write command is:

```powershell
python -m prototypes.postiz_drafts.local --config trusted-local.json execute OPERATION_ID --founder-authorized-local-acceptance
```

7. Verify exactly one new DRAFT with expected content/channel/org, zero QUEUE or
   PUBLISHED rows, zero uploads/media/token refresh/provider egress and no newly
   running publication workflow. Observe Temporal queries separately. Compare before
   and after domain evidence. Repeat the same operation: it must return the saved
   receipt without creating a second draft. Check foreign-org/project and stale
   revisions without dispatch. Stop immediately on any unexpected side effect.
8. On uncertainty, `reconcile OPERATION_ID --founder-authorized-local-acceptance`
   performs only the constrained readback. No write retry or automatic deletion.
   Preserve sanitized receipts and private snapshots separately. Teardown only the
   specifically owned disposable resources within approved acceptance scope.

Before first actual write: separate founder acceptance authorization is still
**NOT RECEIVED**. Production enablement/merge and existing-data cleanup remain
separate approvals. Author testing is not independent review.
