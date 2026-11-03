# Local Postiz read-only experiment

Disabled by default; never imported by application startup. Uses the existing OMOS
McpGateway, Registry, project-scoped credential vault and audit, with official
Python MCP SDK 1.30.0. NO DELEGATION: direct tests/self-checks do not constitute
independent QA or reviewer approval. PR #19 remains Draft and unmerged.

The [sanitized live receipt](evidence/live-local.json) records an actual successful
Postiz run, including empty and nonempty tenant-isolation phases. SDK negotiation
used 2025-11-25, with 20 upstream tools discovered and only three exposed/enabled.
The disposable test stack was subsequently removed; recreate it to rerun the demo.

The public source export includes this package's code, requirements, static setup
configuration and upstream license. Local credentials, generated Compose overrides,
the linked acceptance receipt and storage inventories are deliberately withheld.

## Exact policy

Only `groupList({})`, `integrationList({})`, and
`integrationSchema({"platform":"linkedin","isPremium":false})` may run after
discovery and explicit `enable()`. Unknown/dynamically discovered names, aliases,
case variants and every other tool are denied. This includes both scheduling names,
post writes/deletion, uploads, media generation, `triggerTool` and `ask_postiz`.
`tool-policy.json` contains the known inventory plus the catch-all denial.
The Registry has only three records; the Gateway receives only three normalized
tools; the transport independently checks exact names/arguments before dispatch.
An approval string cannot override those checks.

The HTTP boundary permits only the fixed literal-loopback `/api/mcp` endpoint,
POST initialize/initialized/tools-list/allowlisted-tools-call and bounded GET
streaming. It rejects batches, other MCP methods, redirects, compressed/oversized
responses and DELETE. The sole setup REST request is a fixed GET
`/api/public/v1/me` before each session: its authenticated organization must match
the trusted project/org/vault binding. Its raw API-key header differs from MCP's
Bearer header. No caller can supply an organization, URL, headers or token in args.
No generic REST agent tool or protocol-validation bypass exists.

Discovery never enables calls. A failed rediscovery disables the Registry and
transport and clears the Gateway allowlist. All SDK sessions/clients close under
the total deadline; no retries or HTTP DELETE cleanup. Error bodies and credentials
never enter exported receipts. Results are untrusted data, including helper metadata
and picture URLs; nothing follows those URLs or invokes the helpers.

## Reproduction

Use a normal Python 3.12 terminal with an existing compatible environment:

```powershell
python -m pip install -r requirements-dev.txt -r prototypes/postiz_readonly/requirements.txt
python -m pytest app/tests/test_postiz_readonly.py -q
python prototypes/postiz_readonly/environment/prepare.py
Set-Location prototypes/postiz_readonly/environment
docker compose -p dev050 -f compose.json config --quiet
docker compose -p dev050 -f compose.json pull
docker compose -p dev050 -f compose.json up -d
```

The test module is **OFFLINE CONTRACT ONLY**, using the actual SDK and an in-memory
HTTP fixture. It is never a live Postiz receipt. Restricted Windows environments
can block asyncio's internal socket pair before the deadline starts; run from a
normal terminal instead of weakening SDK validation or the network policy.

Review actual container network/port/DNS settings before provisioning or tests.
Postiz/DB/cache/Temporal use only `dev050_isolated`; IPv6 and external DNS are disabled.
Docker drops published ports on an internal-only network, so a fixed nginx ingress
reuses the same pinned image without starting Node or receiving app credentials.
Only ingress also joins `dev050_ingress`, with kernel forwarding disabled and a
single `127.0.0.1:5199` binding. Its config proxies only the exact MCP and identity
paths to Postiz; it is not an arbitrary HTTP proxy. No DB/cache/Temporal host ports.
Run `python prototypes/postiz_readonly/environment/check_isolation.py --receipt isolation.json`
from the repository root. Wait for backend readiness: an unauthenticated GET to
`http://127.0.0.1:5199/api/mcp` must return 401, not the transient startup 502.
Verify no default external route from Postiz's actual namespace. A failed external
connectivity probe must use a reserved documentation IP, never a social provider.
Do not attach another network, configure a proxy or add provider credentials to
make startup succeed. Capture containment/observed-traffic evidence and stop on
provider attempts, tenant mixing, secret leakage or social-domain mutations.

Provision synthetic organizations, nonadmin users and groups only after the official
image initializes its disposable schema. From the repository root:

```powershell
python prototypes/postiz_readonly/environment/provision.py seed
python prototypes/postiz_readonly/environment/provision.py snapshot
python -m prototypes.postiz_readonly.demo --receipt live-empty.json
python prototypes/postiz_readonly/environment/provision.py snapshot
# Optional tenant data-isolation phase: invalid, disabled local rows, no OAuth.
python prototypes/postiz_readonly/environment/provision.py channels
python prototypes/postiz_readonly/environment/provision.py snapshot
python -m prototypes.postiz_readonly.demo --receipt live-inert.json
python prototypes/postiz_readonly/environment/provision.py snapshot
```

Use `provision.py snapshot --receipt <private-file>` before and after a read phase
to compare full canonical domain-row fingerprints as well as counts. Fingerprint
values stay in private local evidence; public summaries report equality only.
`demo` always targets the real endpoint and writes an explicit failure receipt on
error. It uses a temporary DPAPI vault and in-memory SQLite refs; temporary vault
files created by that invocation are removed on exit. The Compose `.env` contains
only newly generated synthetic local secrets, is ignored, and must not be committed
or copied into evidence. The demo receipt contains fixture IDs/counts, never API keys.
No service connects real social accounts, and seeded channel rows have no valid
provider tokens. Compare post/media/integration counts before/after each read phase;
fixture provisioning writes are distinct from read-operation evidence.

## Environment provenance and limits

Official Postiz v2.25.3 source revision:
[a5affe6](https://github.com/gitroomhq/postiz-app/tree/a5affe62c74e39c1e7ac960d50d8418b807d532c).
`environment/upstream-compose.yaml` and `dynamicconfig/development-sql.yaml` are
unmodified upstream resources. `prepare.py` derives scoped overrides and pins
linux/amd64 digests in `images.lock.json`: local names/secrets, internal network,
fixed loopback nginx ingress, offline-only pnpm startup, disabled Elasticsearch
GeoIP downloads, no optional monitoring/admin/UI containers. Postgres17, Redis7.2,
Temporal1.28.1 with Postgres16 and Elasticsearch7.17.27 are retained.

Upstream resources are AGPL-3.0; see the
[upstream license](https://github.com/gitroomhq/postiz-app/blob/a5affe62c74e39c1e7ac960d50d8418b807d532c/LICENSE)
and retain notices/corresponding source when conveying them. Postiz remains a
separate unmodified process; this experiment does not bundle it into OMOS.
Production use, packaging, real provider authorization and independent security
acceptance are outside the demonstrated scope. Actual image source/revision and
runtime initialization must be checked, not inferred solely from a tag.

## Scoped shutdown and disposal

From `environment/`, stop only this project's services:

```powershell
docker compose -p dev050 -f compose.json stop
```

After retaining needed DEV-050 receipts, verify `docker compose -p dev050 -f
compose.json ps -a` and the `dev050.owner=DEV-050` labels on the exact containers,
two networks and six named volumes. These are disposable DEV-050-owned resources.
Only after that check, `docker compose -p dev050 -f compose.json down --volumes`
removes this stack's containers/network/volumes; it keeps images and does not prune
unrelated resources. Never use system prune, wildcard volume deletion, forced image
removal, or deletion of preexisting worktrees/data. Shared image caches are separate
from project-directory storage and must be measured separately.
