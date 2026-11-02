# Phase 4.5 — reusable marketing connectors

Research date: 2026-11-02. OMOS baseline: `8a5cce98a5fb5cbd3073af626aa5f810ff6c2559` (latest fetched main). Research precedes implementation. This document is a source inspection, not a live-provider acceptance report. No account was connected, post published, paid API invoked, or upstream server installed during research.

## Recommendation

Reuse the official Python MCP SDK for a small local protocol proof, with OMOS retaining its existing policy boundary. Use the already-installed `mcp==1.30.0` for the bounded proof rather than upgrading the application dependency graph. Exercise a real stdio initialize → tools/list → tools/call exchange with a deterministic local server. Prepare a draft payload only from the explicitly requested currently approved, project-scoped OMOS campaign revision; reject stale versions and newer unapproved edits. The payload is an offline artifact, not a provider submission.

For the next provider experiment, prefer **self-hosted Postiz as a separate service**, starting with discovery and channel/schema reads. Its source includes social providers, MCP transport, and both API-key and OAuth handling. Do not embed its server code into OMOS. Check the exact chosen revision's AGPL obligations, provider app approvals, and infrastructure requirements before any distribution or account connection. Activepieces is a useful later workflow service, but running its broad automation platform just to prove one campaign payload adds unnecessary scope.

Planable and Metricool remain optional commercial endpoints. An MCP endpoint or public skills repository does not make the underlying SaaS open source. DeepSeek Harness supplies useful plugin lifecycle ideas, but replacing OMOS's runtime or copying a general plugin manager is outside this phase.

## Comparison

| Candidate | Reuse and capabilities | License / self-hosting | Authentication | Fit and cost observation |
|---|---|---|---|---|
| Postiz | Existing social providers, scheduling, analytics, channels, schema discovery, API and MCP. MCP source exposes agent tools, including dangerous writes and optional AI generation. | Inspected root LICENSE is AGPL-3.0; full service can be self-hosted. Keep service boundary; do not casually copy source into OMOS. | MCP resolves `pos_` OAuth tokens or organization API keys; public API middleware also accepts both. Social channels require their own provider grants. | Best next open-source social-service candidate. Cloud starts $29/month; self-host has infrastructure, app-review and optional AI costs. |
| Activepieces | TypeScript pieces plus workflow engine and MCP exposure. Inspected LinkedIn piece has profile/page publishing and reads; Instagram Business piece has publishing, insights, comments and messaging. | MIT outside two enterprise directories; enterprise features have a separate commercial license. Self-hosted community platform is available. | Piece-specific: LinkedIn OAuth2; inspected Instagram piece uses Facebook OAuth and requires a Business/Creator account linked to a Page. MCP/API connection auth is a separate layer. | Good broad automation reuse, heavier than this proof. Cloud Free $0/1,000 monthly credits; Plus $20/month; commercial embedding advertised from $36k/year. |
| Planable MCP | Hosted workspace/page/content/approval/media/analytics tools. Public `smm-skills` repo contains workflows and prompts, not the service implementation. Draft-batch skill documents workspace/page selection. | Public skills are MIT. No open-source MCP server or supported self-host package was established from inspected sources. | Account login/consent; user/workspace permissions inherited. Treat exact OAuth/PKCE metadata as unverified until independently inspected. | Strong editorial workflow, SaaS dependency. Official guide says MCP included on paid plans without separate connector charge; analytics needs an add-on. Localized pricing output did not support a reliable numeric quote. |
| Metricool MCP | Hosted scheduling, brand management and analytics. `vicampuzano/metricool-mcp` contains Python implementation; `metricool/servers` is a fork of generic reference servers, not a Metricool connector. | No LICENSE present in inspected `vicampuzano` root listing: do not copy or redistribute assuming MIT. Generic `metricool/servers` fork's MIT license does not license another repository or the SaaS. Self-hostable Python source is technically visible, but reuse rights and upstream support need clarification. | Hosted README specifies OAuth2 authorization code + PKCE. Python source also accepts Bearer/API-key context and `MCP_API_KEY`; OAuth metadata points at Metricool's hosted authorization service. These are distinct deployment paths. | Consider later for analytics after provenance/license clarification. Pricing lists MCP on Free with limits; raw API access is Advanced. Annual/monthly and currency views differ. |
| Official MCP Python SDK | Client, server, schemas and transports; directly supplies discovery/call protocol machinery. No marketing providers. | MIT; runs locally. `v1.30.0` source supports Python >=3.10; current stable release API reports `v2.3.0` (2026-10-02), with a different API and Python >=3.11. | Protocol auth helpers exist for remote use; a local stdio proof needs no account or token. | Smallest competent proof dependency. SDK has no usage fee; external provider costs are separate. |
| Maintained LangChain MCP | Current `langchain.mcp.MCPAdapter` converts MCP tools into LangChain tools; its source uses FastMCP clients and accepts SDK servers. | LangChain MIT. Self-hostable library; not a hosted marketing connector. | Delegates transport/auth; does not replace OMOS credential scope or approval checks. | Use only if model-driven tool use is later needed. Old `langchain-mcp-adapters` README explicitly says maintenance moved to `langchain[mcp]`. No LLM is needed for deterministic discovery/payload prep. |
| DeepSeek Harness | Cordis services, typed events, scoped tool definitions, profile/bundle composition, plugin install/update lifecycle. | MIT; local Node runtime. Developer preview warns of breaking changes. | Model/provider credentials and plugin authority belong to the harness; not marketing-provider authorization. | Borrow explicit lifecycle and compatibility concepts only. Source declares Node >=24.9.0 and Windows x64 desktop package scripts. No need to install or call DeepSeek to use MCP in OMOS. |

## Evidence ledger

Each entry distinguishes observed source from the recommendation inferred from it. Branch URLs are mutable; file blob SHAs below identify the inspected content. The GitHub connector returned file content and SHAs. Full repositories were not built or security-audited.

### E1 — Postiz license and source

- STATUS: VERIFIED (source), INFERENCE (integration choice).
- SOURCE: [LICENSE](https://github.com/gitroomhq/postiz-app/blob/207eee0dae442a642844c1de9bd7a1574d17c95e/LICENSE), [repository tree](https://api.github.com/repos/gitroomhq/postiz-app/git/trees/207eee0dae442a642844c1de9bd7a1574d17c95e?recursive=1), [MCP bootstrap](https://github.com/gitroomhq/postiz-app/blob/207eee0dae442a642844c1de9bd7a1574d17c95e/libraries/nestjs-libraries/src/chat/start.mcp.ts), [public authentication](https://github.com/gitroomhq/postiz-app/blob/207eee0dae442a642844c1de9bd7a1574d17c95e/apps/backend/src/services/auth/public.auth.middleware.ts).
- OBSERVATION: root license is AGPL-3.0. MCP bootstrap resolves OAuth tokens or API keys and constructs tools through existing services. Public auth binds authorization to an organization and can enforce subscription presence. Provider files are present for Instagram, Facebook, LinkedIn, TikTok, X, YouTube and others.
- INTERPRETATION: reuse the service's public boundary; a permissioned token can still reach tools that publish or generate paid media. Discovery must not auto-enable execution. Indexed claims of a BSL change are not the inspected license evidence.
- CONFIDENCE: HIGH for inspected source; MEDIUM for deployment choice pending license/release selection and actual installation.

### E2 — Postiz hosting and pricing

- STATUS: VERIFIED (published terms/features), PARTIALLY VERIFIED (operational feasibility).
- SOURCE: [README](https://github.com/gitroomhq/postiz-app/blob/207eee0dae442a642844c1de9bd7a1574d17c95e/README.md), [pricing](https://postiz.com/pricing), [tool loader](https://github.com/gitroomhq/postiz-app/blob/207eee0dae442a642844c1de9bd7a1574d17c95e/libraries/nestjs-libraries/src/chat/load.tools.service.ts).
- OBSERVATION: self-host documentation names Docker, Postgres, Redis, storage and social developer apps. Cloud Standard is $29/month with MCP/API; annual pricing differs. Tool loader includes channel/schema, post scheduling, deletion and optional AI features.
- INTERPRETATION: free source is not zero operational cost. Self-host provider approvals and optional model/media costs remain separate. Do not call the agent/AI generation path for deterministic payload preparation.
- CONFIDENCE: HIGH for published information, MEDIUM for actual channel availability; no live test.

### E3 — Activepieces licensing and actual provider pieces

- STATUS: VERIFIED.
- SOURCE: [root LICENSE](https://github.com/activepieces/activepieces/blob/main/LICENSE), [enterprise LICENSE](https://github.com/activepieces/activepieces/blob/main/packages/ee/LICENSE), [LinkedIn piece](https://github.com/activepieces/activepieces/blob/main/packages/pieces/community/linkedin/src/index.ts), [Instagram piece](https://github.com/activepieces/activepieces/blob/main/packages/pieces/community/instagram-business/src/index.ts), [Instagram authentication](https://github.com/activepieces/activepieces/blob/main/packages/pieces/community/instagram-business/src/lib/common/index.ts).
- OBSERVATION: root license excludes `packages/ee/` and `packages/server/api/src/app/ee`. LinkedIn source declares OAuth2 and publishing/admin scopes. Instagram source wires publishing and insights actions; its common module requests Facebook OAuth scopes.
- INTERPRETATION: reuse an existing piece/service if that capability is selected later, but do not treat all enterprise controls as MIT. Requested scopes exceed a read-only experiment; narrow permission review remains necessary.
- CONFIDENCE: HIGH for source; no grants or provider success tested.

### E4 — Activepieces hosting, auth limits and costs

- STATUS: VERIFIED (published offer), PARTIALLY VERIFIED (MCP deployment path).
- SOURCE: [README](https://github.com/activepieces/activepieces/blob/main/README.md), [self-host documentation](https://www.activepieces.com/docs/install/overview), [pricing](https://www.activepieces.com/pricing).
- OBSERVATION: platform offers community self-hosting and exposes pieces through MCP; cloud pricing differentiates free credits, paid tiers, enterprise controls and commercial embedding.
- INTERPRETATION: existing piece credentials, Activepieces project isolation and OMOS isolation are different boundaries. This research did not establish a tested MCP server edition/auth recipe for embedding; select and inspect that exact edition before using it. Do not lift enterprise MCP/security code based on the community label.
- CONFIDENCE: HIGH for advertised pricing, MEDIUM for self-host MCP fit.

### E5 — Planable: open instructions, hosted service

- STATUS: VERIFIED (skills and public workflow), NOT ACCESSIBLE (service implementation).
- SOURCE: [skills LICENSE](https://github.com/seranking-planable/smm-skills/blob/main/LICENSE), [draft-batch skill](https://github.com/seranking-planable/smm-skills/blob/main/skills/draft-post-batch/SKILL.md), [official MCP guide](https://planable.io/guides/planable-mcp/), [connection help](https://help.planable.io/hc/en-us/articles/27538577098780-How-to-connect-Planable-MCP-to-your-AI-tools), [pricing](https://planable.io/pricing/).
- OBSERVATION: MIT license applies to skills. Official help supplies `https://mcp.planable.io/mcp` and account login/authorization. Guide describes draft creation, approvals subject to member permissions, paid-plan availability and analytics add-on. The hosted implementation was not available in the inspected repository.
- INTERPRETATION: can reuse editorial concepts and later connect the SaaS; cannot claim an open-source/self-host server. Native provider approvals do not replace OMOS approval for an external write. Exact numeric plan price and OAuth metadata are intentionally unverified here.
- CONFIDENCE: HIGH for published workflow and license distinction; MEDIUM for future client interoperability.

### E6 — Metricool provenance, auth and license distinction

- STATUS: VERIFIED (inspected files), PARTIALLY VERIFIED (vendor ownership/support), NOT ACCESSIBLE (redistribution permission).
- SOURCE: [generic fork tree](https://api.github.com/repos/metricool/servers/git/trees/73de17352c10d659d9d96f6dd17c137d0a7d1f0f?recursive=1), [Python repository root](https://api.github.com/repos/vicampuzano/metricool-mcp/contents), [server.py](https://github.com/vicampuzano/metricool-mcp/blob/main/server.py), [auth.py](https://github.com/vicampuzano/metricool-mcp/blob/main/auth.py), [oauth.py](https://github.com/vicampuzano/metricool-mcp/blob/main/oauth.py), [README](https://github.com/vicampuzano/metricool-mcp/blob/main/README.md).
- OBSERVATION: generic fork has reference tools, no Metricool implementation. Python server imports SDK FastMCP; auth module supplies request-context Bearer/API-key fallback. OAuth module declares hosted authorization/token/registration endpoints. README advertises OAuth2 PKCE and `https://mcp.metricool.ai/mcp`. No root LICENSE was listed.
- INTERPRETATION: visible source is not a license grant. Verify maintainer/vendor linkage and explicit reuse terms before adopting code. Hosted OAuth and local API-key operation must not be conflated.
- CONFIDENCE: HIGH for source observations; MEDIUM for supported deployment identity.

### E7 — Metricool economics and hosting

- STATUS: VERIFIED (published pricing), INFERENCE (Windows suitability).
- SOURCE: [pricing](https://metricool.com/pricing/), [requirements](https://github.com/vicampuzano/metricool-mcp/blob/main/requirements.txt), [deployment files](https://github.com/vicampuzano/metricool-mcp).
- OBSERVATION: Free includes MCP with possible limits; API access appears in Advanced. Page shows both annual and monthly prices: USD Starter $20/$25, Advanced $53/$67 per month in the rendered views; VAT and add-ons apply. Python repository includes uvicorn/requests dependencies and systemd/nginx/shell deployment files.
- INTERPRETATION: API-key connector costs can differ from hosted MCP eligibility. Python transport could run on Windows, but Linux deployment scripts are not a native Windows install path. Self-hosting the wrapper still depends on Metricool accounts/APIs.
- CONFIDENCE: HIGH for page/source; MEDIUM for platform inference; no installation tested.

### E8 — Official SDK reuse and version boundary

- STATUS: VERIFIED (upstream); local test evidence must be recorded by implementation/QA separately.
- SOURCE: [v1.30.0 pyproject](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/pyproject.toml), [v1.30.0 stdio client](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/stdio/__init__.py), [MIT LICENSE](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE), [v2.3.0 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0), [current client source](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/client/client.py).
- OBSERVATION: 1.30.0 has explicit Windows process helpers and safe inherited environment selection; Python requirement is >=3.10. Current stable release reports 2.3.0; main requires >=3.11 and offers `MCPServer`/`Client`, including in-process dispatch.
- INTERPRETATION: pin 1.30.0 for the existing environment and stdio wire proof; do not combine v2 examples with v1 imports. Upgrade to v2 only as a separate compatibility task. SDK availability does not validate OAuth or provider contracts.
- CONFIDENCE: HIGH for versioned source; installed application/Electron compatibility remains untested.

### E9 — Maintained LangChain MCP support

- STATUS: VERIFIED.
- SOURCE: [old adapters maintenance notice](https://github.com/langchain-ai/langchain-mcp-adapters/blob/main/README.md), [maintained adapter source](https://github.com/langchain-ai/langchain/blob/master/libs/langchain_v1/langchain/mcp/adapter.py), [LangChain LICENSE](https://github.com/langchain-ai/langchain/blob/master/LICENSE).
- OBSERVATION: maintenance moved to `langchain.mcp` / `langchain[mcp]`. Adapter wraps FastMCP clients. Its URL validation distinguishes explicit local-file targets from remote URLs, including Windows drive paths.
- INTERPRETATION: future model integration should use maintained support after package compatibility checks. Do not add the abandoned adapter package or introduce an LLM into deterministic payload preparation. Explicit trusted server launch configuration is safer than accepting arbitrary model-supplied commands.
- CONFIDENCE: HIGH for source, MEDIUM for future integration; no adapter runtime tested.

### E10 — DeepSeek Harness plugin approach

- STATUS: VERIFIED (source), INFERENCE (lessons for OMOS).
- SOURCE: [architecture](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md), [tool registry source](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/index.ts), [plugin manager source](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/boot/plugin-manager/src/index.ts), [package manifest](https://github.com/deepseek-ai/deepseek-harness/blob/master/package.json), [README/license](https://github.com/deepseek-ai/deepseek-harness).
- OBSERVATION: Cordis contributions have scoped services/effects; profiles compose bundles; plugin manager checks compatibility and lifecycle operations. Manifest has Windows x64 Electron packaging commands and Node >=24.9.0. README calls it a breaking-change-prone preview.
- INTERPRETATION: useful principles are explicit manifests, version compatibility, bounded tool registration and unload cleanup. Copying its plugin runtime would create the custom framework this mission excludes. Windows packaging scripts are evidence of intent, not OMOS compatibility proof.
- CONFIDENCE: HIGH for source; MEDIUM for architectural lessons.

## Inspected source identities

Blob hashes identify file content, not necessarily a repository commit. Postiz and generic Metricool tree commits are pinned above. Other branch reads may move after this research date.

| Repository / source | Inspected blob SHA |
|---|---|
| Postiz `start.mcp.ts` | `cd992daf5d7be4ae3cb8ac68d3ad38b8ca0e2b02` |
| Postiz `public.auth.middleware.ts` | `526757b68452c70e35aef52264800b9098f5b657` |
| Postiz `load.tools.service.ts` | `4496aeaf7d18d0ab34b04f4649f17a18873a061d` |
| Activepieces LinkedIn `src/index.ts` | `1841f951abe34e5003e51a1e4eee2c59ba0b639a` |
| Activepieces Instagram `src/index.ts` | `0a208f9d54bd45e6f15f79c468070d2598c01b74` |
| Activepieces Instagram common auth | `cd90a51e1e51b8528bae528e324fa4c29861c4e3` |
| Activepieces enterprise LICENSE | `1c68c6b3bbc00e1a41a78b488da61a0c983faab4` |
| Planable draft-batch SKILL.md | `856169de4e5c0f6c73765e1dd8ed0483e084b1e8` |
| Planable skills LICENSE | `2dd07878b778fafb02ff2d048fbe840bf99495cc` |
| Metricool Python `server.py` | `96ee2fc6580aa6ae588f9c8383134ffa3278b9a6` |
| Metricool Python `auth.py` | `627b32c540102ee9bafa0c6489fd0bcd09d60e08` |
| Metricool Python `oauth.py` | `80890afef5d64804383b16b31e2d8cb47186efc1` |
| SDK current `src/mcp/client/client.py` | `099dcbb874fe1b49137bc2fe5667b0c81338d33b` |
| LangChain maintained `mcp/adapter.py` | `b1c87e1f68fe0beb11406cc774e42167afbf879c` |
| Harness `packages/core/tools/src/index.ts` | `d2d10d6b5be2d77696470e82164bda47322c2112` |
| Harness plugin manager `src/index.ts` | `ceb82aac476df7c7d928f7f111f114c118f2bd81` |
| Harness root `package.json` | `9dccd6008bc8baa11863c2cab544eac2c786808e` |

## OMOS reuse boundary and minimal proof contract

Local source inspection found these existing controls; no replacement framework is warranted:

| Existing component | Required reuse |
|---|---|
| `app/services/mcp/gateway.py` | Existing per-project allowlist and action approval gate; inject a narrowly bounded SDK transport. Current auto-bridge uses incompatible `cost_type=external_call`; register one explicit valid `ToolRecord` (`cost_type=free`) in prototype composition instead of changing production code. Discovery must remain default-deny. |
| `app/services/tools/registry.py` | Existing tool records, execution context, permission gates and tool-run telemetry; preserve registered capability metadata. |
| `app/services/credentials/vault.py` and `store.py` | Keep opaque vault references and OS DPAPI backend; synthetic proof requires no credentials, so no vault read or second secret store. |
| `app/services/campaign_deliverables.py` | Reuse project/campaign ownership checks, editorial revisions, APPROVED transitions and final-copy validation. Require the requested current approved revision and reject newer unapproved edits; preserve the existing ZIP export semantics, which can select a last-approved revision. |
| Existing gateway/DB audit records | Record discovery/deny/prepare events with identifiers and argument key names, never token values or full campaign content in metadata. |

Minimal deterministic behavior: launch only a fixed local server using the current Python executable; expose a tiny fixed set of nonpublishing tools; discover via the SDK; obtain an approved synthetic campaign revision from an isolated fixture database; prepare a canonical JSON draft with revision/hash identifiers; retain an audit record. There must be no publish/schedule/send tool, arbitrary endpoint/command option, account login, secret resolution, model generation, or network dependency in the proof execution path.

Acceptance evidence should demonstrate: actual protocol handshake/list/call; exact discovered schemas; stable payload for identical input; refusal of unapproved, mismatched-project, stale-version and unknown-tool inputs; discovery alone does not enable tools; revisions cannot smuggle unapproved text; no credential/real-account access; no outbound connections; audit metadata contains no secret/content values; child process terminates. Fixed synthetic fixture approvals demonstrate code behavior, not founder authorization for a live campaign.

The existing gateway's action approval set is scoped to server/project/tool, whereas campaign editorial approval is revision-specific. Neither should be presented as the other. The proof must enforce both where relevant, and must not silently promote a persistent broad tool grant into permission to publish future revisions.

## Windows / Electron compatibility assessment

| Route | Source-based assessment | Still requires real verification |
|---|---|---|
| Python SDK stdio | Explicit Windows process helper code and `sys.executable` fit an isolated backend proof. | Packaged interpreter/resource paths, spaces/Unicode, process cleanup and frozen-bundle dependencies. |
| Remote Postiz/Planable/Metricool | Backend HTTP/MCP transport can keep credentials outside the renderer; hosted mode avoids shipping Linux services. | Complete MCP handshake/session/version handling, OAuth browser redirect and PKCE, refresh/revoke, TLS, tenant mapping. Current custom HTTP gateway must not be assumed fully compatible from plain JSON-RPC requests alone. |
| Self-host Postiz / Activepieces | Separate Docker/Linux service is plausible on a Windows host with Docker Desktop/WSL2. | Actual installation, resource requirements and provider approvals; neither is a drop-in Electron package. |
| LangChain MCP | Python library can live in backend; source considers Windows path ambiguity. | Version/extra compatibility with OMOS lockfile and packaged app. |
| Harness | Upstream has Windows Electron build scripts. | No binary/runtime/plugin compatibility with OMOS was tested; no adoption recommended here. |

## Risks, next integration and stop conditions

1. Complete the offline proof before selecting a live provider. Stop if a test requires a real account, network call, paid model, new credential store or publication path.
2. Next bounded integration proposal: self-hosted Postiz discovery plus read-only channel/schema listing, isolated test project, explicit provider app/account authorization, rechecked source license and version pin. Require a separate approved plan before connecting any account.
3. Before a draft-only external write, bind approval to exact client, channel, content hash/revision and operation; enforce safe provider draft semantics rather than merely trusting tool names or annotations. Ambiguous tools remain disabled.
4. Measure integration viability with handshake/discovery success, supported schema coverage, tenant-isolation denials, credential-redaction checks and draft-state verification. Stop on unknown publication semantics, license uncertainty, excessive scopes, account cross-talk or unsupported packaging.
5. No provider uptime, social results, analytics accuracy, production performance or installed Electron acceptance is established by this research. Implementation and independent QA/review must append their actual evidence; do not label this phase complete from the comparison alone.

Approval state at research delivery: internal research only; implementation awaits the repository's founder plan gate. No live integration is authorized by this document.

## Offline prototype evidence delivered after research

The founder subsequently approved DEV-048-PHASE45, then prohibited delegation. The optional proof in `prototypes/marketing_mcp/` uses official `mcp==1.30.0` and the existing OMOS controls. Final direct acceptance: **217 passed, 24 skipped in 215.61s (exit 0)**, including all **27** self-contained prototype cases with genuine SDK stdio discovery/calls and process-cleanup checks. Skips are existing withheld-fixture regressions, not prototype tests. Synthetic demo evidence and exact commands are in `development/runs/DEV-048-PHASE45/qa-direct.md` and `demo-evidence.json`.

Direct technical validation and self-review passed; independent QA/reviewer executions were not performed under the no-delegation instruction. The legacy independent-role approval gate remains unmet and was not altered. No account, provider call or publication was used. This evidence does not change the recommendation to make the next Postiz pilot read-only, separately authorized, revision-pinned and default-denied.
