# Architecture

Developer-facing. For how the *product* is installed, where your data lives, or
what is sent where, see the [README](../README.md),
[user-data-and-backup.md](user-data-and-backup.md), and
[privacy.md](privacy.md).

## High level

```text
                     ┌──────────────────────────────────────┐
  browser  ────────► │  React SPA  (Vite + TypeScript)      │
  (127.0.0.1)         └──────────────────┬───────────────────┘
                                        │  HTTP + SSE
                     ┌──────────────────▼───────────────────┐
                     │  FastAPI  (uvicorn, 127.0.0.1:dynamic)│
                     │  routes/ = presentation only          │
                     └──────────────────┬───────────────────┘
                                        │
                     ┌──────────────────▼───────────────────┐
                     │  LangGraph StateGraph                 │
                     │  load_project → load_conversation →   │
                     │  understand → route → tool/RAG →      │
                     │  synthesize → approval_gate → respond │
                     └──────┬───────────────┬───────────────┘
                            │               │
        ┌───────────────────▼──┐      ┌─────▼─────────────────────┐
        │ Tool Registry        │      │ Credential Vault (DPAPI)  │
        │ Integration Registry │      │ refs in SQLite,           │
        │ MCP gateway          │      │ ciphertext in the vault   │
        └───────────┬──────────┘      └───────────────────────────┘
                    │
        ┌───────────▼──────────────────────────────────────────┐
        │ services/  (llm, rag, files, social, vision, jobs)   │
        └───────────┬──────────────────────────────────────────┘
                    │
        ┌───────────▼──────────────────────────────────────────┐
        │ SQLite  +  Chroma (optional)     │
        └──────────────────────────────────────────────────────┘
```

Three layering rules, enforced by tests, keep this shape honest:

1. **`app/routes/` is presentation only.** Business logic lives in
   `app/services/`. Routes do not touch `sqlite3` or spawn subprocesses — they
   go through `app/deps.py`.
2. **`app/graphs/` never imports FastAPI or React.** The platform dependency is
   injected, so the graph is testable in isolation.
3. **`app/database/sqlite.py` and `app/database/repos.py` are the only modules
   that touch `sqlite3`.** Everything else calls a repository.

## The web tier

A single FastAPI application, created by `create_app()` in `app/main.py`.

- **It serves the SPA.** `app/routes/spa.py` and `app/routes/api_spa.py` serve
  the built frontend and the API it consumes from the same origin, so there is
  no CORS surface and no second server in production. In development, Vite
  serves the frontend and proxies to the backend.
- **It binds `127.0.0.1` only, on a dynamically chosen free port.** The
  distribution launcher finds a free port and opens the default browser at it.
  The backend is never meant to be reachable from the network.
- **Startup is idempotent.** `init_db()` on startup creates and upgrades the
  schema. Safe to run on every launch.
- **Errors are translated, not leaked.** `safe_error_message` in
  `app/contracts/events.py` converts exceptions into user-facing text and
  scrubs secrets, env values, and sensitive paths. Raw exceptions must never
  reach a response body.

## Orchestration

The product path is a LangGraph `StateGraph` built in
`app/graphs/account_manager_graph.py`. The state contract is a `TypedDict` in
`app/graphs/state.py`, and its field names are normative — other modules code
against them. List fields use append/union reducer semantics so parallel branches
can fan in without clobbering each other.

### The shape of a turn

```text
load_project → load_conversation → understand → route_selector
                                                      │
        ┌──────────────┬──────────────┬───────────────┼───────────┐
        ▼              ▼              ▼               ▼           ▼
   state_only     knowledge    external_research  social_...  campaign_...
                                                          │
                                                     deep_planner
                                                          │
                                            deep_fanout → deep_worker*
                                            (fans out via Send)
                                                          │
                                                     deep_fuse
                                                          ▼
                                                    aggregate
                                                          ▼
                                                    synthesize
                                                          ▼
                                                   approval_gate
                                                          ▼
                                                      respond
```

`route_selector` is a conditional edge: it picks **exactly one** branch set per
turn. Deep research fans out further with `Send` and reduces back through
`deep_fuse`, then rejoins the main path at `aggregate`.

### Why tools and retrieval are gated, not bypassed

`understand` classifies the turn and sets the route; `tool_capability` resolves
what a tool is actually allowed to do. Two things follow from that, and both are
deliberate:

- **The model does not get to reach a tool directly.** Every external action
  goes through the Tool Registry, which carries a capability record: side-effect
  class, permission level, cost type, required credential scope, retry policy.
  There is no path from a model response straight to a provider call.
- **Retrieval is part of the route, not an optimization the model can skip.** If
  the route needs evidence, the graph retrieves it. The model receives evidence
  that was gathered and cited, not whatever it decided to include.

If you are tempted to add a "just call the tool here" shortcut in a node, that
is the thing this design is built to prevent.

### Project isolation

Isolation is **fail-closed**. `app/services/rag/scoped_retrieval.py` requires a
non-empty, known `project_id` *before* any store query is issued, and filters
by project inside the SQL (joining `documents` before `LIMIT`) rather than
filtering a global top-k in Python. Chroma gets the same constraint as a
server-side `where` filter plus a contract-level re-check. A blank or unknown
project id raises; it never degrades to "search everything".

## Registries

Three separate registries, because capability, provider, and transport are
different questions.

| Registry | Module | Responsibility |
| --- | --- | --- |
| **Tool Registry** | `app/services/tools/registry.py` | What a tool *is*: capability metadata, safety vocabulary, permission level, cost type, credential scope, retry policy. Execution binds by string id. |
| **Integration Registry** | `app/services/integrations/registry.py` | Which *provider* supplies a capability for a project, and its non-secret config plus `vault://` ref. Resolution fails closed: no row means not configured, never a silent default. |
| **MCP gateway** | `app/services/mcp/manager.py` + `gateway.py` | External MCP servers as an *extension* mechanism. Discovered tools are classified into a side-effect category that maps to a frozen approval level. |

The split matters: the registry layer never picks a vendor, and the tool layer
never holds a secret. `integration_status()` returns presence-only UI cards —
labels and booleans, never `secret_ref` values or secret material.

The MCP approval matrix is frozen in code:

```text
READ            -> GREEN   (runs autonomously)
WRITE           -> YELLOW  (approval required)
EXTERNAL_ACTION -> YELLOW  (approval required)
DESTRUCTIVE     -> RED     (approval required)
```

### Built-in tools

`app/services/tools/` holds the native implementations: `web_tools.py`
(website research), `instagram_tools.py`, `knowledge_tools.py` (project memory),
`state_tools.py` (proposals and state writes), `propose_tools.py`, and
`delegate_tools.py`. They are registered into the Tool Registry by
`build_default_registry()`.

## The Credential Vault

The seam is `app/services/credentials/`, and it is deliberately two layers.

**`vault.py` — the seam.** It stores **references only**, in a
`credentials_refs` table:

```text
vault://<scope>/<32-hex>
```

Scopes are `installation`, `project`, and `user`, with a strict ownership matrix
that fails closed — a project-scoped secret requires a project id, an
installation-scoped one rejects one. `resolve()` is the only function that
returns plaintext, and it is server-side only.

**`store.py` — the backends.** It implements a `SecretBackend` protocol
(`put`/`get`/`delete` of opaque bytes). The default is `DpapiFileBackend`: one
ciphertext file per secret under
`%LOCALAPPDATA%\OpenMarketingOS\credentials\<scope>\<hex>.bin`, encrypted with
`CryptProtectData` under `CRYPTPROTECT_UI_FORBIDDEN` so decryption can never
pop a UI dialog. Writes are atomic (temp file plus `os.replace`).

`store.py` is the only module that needs a platform. Other OS backends — macOS
Keychain, Linux libsecret, Windows Credential Manager — implement the same
protocol and register via `set_default_backend()`. The reasoning behind
preferring the DPAPI file store over Credential Manager is documented at the top
of that module; read it before proposing a change.

Invariants worth protecting in review:

- Secret values never touch the database, logs, status payloads, exports,
  prompts, retrieval chunks, or error text.
- `integration` config rejects keys that look like secrets
  (`_FORBIDDEN_CONFIG_TOKENS`) — config is non-secret by construction.
- `.env` is bootstrap and dev only. Real users configure credentials through the
  UI.

## Data model

**SQLite** is the system of record, at
`%LOCALAPPDATA%\OpenMarketingOS\data\marketing.db` in the distribution. Schema
lives in `app/database/schema.sql`, with additive in-place upgrades in
`app/database/sqlite.py`.

Core tables: `projects`, `conversations`, `messages`, `turns`, `memories`,
`documents`, `chunks`, `tool_runs`, `model_calls`, `campaigns`, `tasks`,
`approvals`, `experiments`, `learnings`, `background_jobs`, `integrations`,
`credentials_refs`, `mcp_servers`, `settings`.

Three rules for changes here:

- **Additive DDL only.** `CREATE TABLE IF NOT EXISTS`, in a path that is safe on
  every startup.
- **Project-owned tables carry a non-null `project_id`.** Installation- and
  user-scoped rows park at `''`. The `credentials_refs` and `integrations` DDL
  lives in the modules that own those tables, and a test asserts the shipped
  migrations and the in-code DDL stay in sync.
- **Telemetry tables are fixed-column and value-free.** `tool_runs` and
  `model_calls` record ids, timings, status, and cost. They never store
  arguments, request bodies, or response bodies.

### Retrieval

Two tiers, with a hard guarantee that retrieval always works.

1. **SQLite FTS5 — always available.** `chunks_fts` is an FTS5 virtual table
   maintained alongside `chunks`. Keyword retrieval cannot fail, because it is
   the same database the app already depends on.
2. **Chroma — optional.** A vector store under
   `%LOCALAPPDATA%\OpenMarketingOS\data\chroma\`, used for dense and hybrid
   retrieval when it is installed. If it is missing or broken, the app degrades
   to keyword mode and says so in the UI rather than failing.

Fusion is deterministic Reciprocal Rank Fusion (`RRF_K = 60`) in
`app/services/rag/fusion.py`, with an additive source-hierarchy boost — founder
decisions and durable learnings outrank snapshots and drafts. Every hit carries
provenance: path, chunk id, header, file hash, status tag.

Retrieval is never authoritative for structured state. `ContextRouter` combines
retrieval hits with direct SQLite reads; structured facts come from the
database, not from a ranked document.

## Streaming and approvals

**Streaming** is Server-Sent Events. `app/routes/chat.py` returns a
`StreamingResponse` with `text/event-stream`, replaying persisted
`execution_events` for the turn so a reconnecting client catches up rather than
losing the stream. Events are normalized at the event boundary into a typed
`GraphExecutionEvent` (see `app/contracts/events.py`) with a
`to_sse()` method, so every event crossing the wire has the same shape. Text is
sanitized and truncated at the boundary, not by each producer.

**Approvals** are a separate graph in `app/graphs/approval_flow.py`. A yellow or
red action pauses via LangGraph `interrupt()` with a serializable payload, and
resumes only through `Command(resume=...)`. Green actions pass straight through
with no interrupt.

The important property: **side effects run after the interrupt and are keyed by
an idempotency key**, so a replayed resume is a no-op. A double-clicked approval
cannot execute twice.

The approval decision itself lives in `app/contracts/approvals.py` and is
persisted in the `approvals` table, so a decision survives a restart.

## The two runtimes

There are two orchestration runtimes in the tree, and this matters when you are
reading old code or an old doc:

- **LangGraph — the shipped default.** `app/graphs/`. Selected by
  `get_ai_runtime()` in `app/contracts/runtime.py`, which fails closed to
  `langgraph` when `AI_RUNTIME` is unset or invalid.
- **Legacy — rollback only.** The lighter runners in
  `app/graphs/account_manager.py` and the pure helpers in
  `app/graphs/approvals.py`, retained for tests and for a rollback path. Not a
  second supported product path.

Do not add features to the legacy runtime. If you need the legacy path, keep it
building; if you need a capability, add it to the graph.

`MAX_AGENT_CONCURRENCY` (default 4, hard cap 4) bounds parallel turn execution,
clamped fail-closed to the default when out of range.

## Model providers

`app/services/llm/` holds a provider interface (`base.py`), a routing layer
(`router.py` and `model_router.py`), and two concrete cloud providers:
`openrouter_provider.py` and `openai_provider.py`. A `fake_provider.py` stands
in for tests.

**In this beta, cloud providers are the only path.** A local provider
(`local_llama.py`, `local_config.py`) exists in the tree and is reachable in
development, but no local-model edition ships in the Quick beta. Do not document
local models as an available edition.

Vision (`app/services/vision/`) runs through OpenAI, with a `local_provider.py`
seam for the planned local path. Attachments are scrubbed before they leave the
machine (`scrub.py`) and checked on ingress (`ingress.py`).

## Where things live

| Concern | Path |
| --- | --- |
| App factory, router mounting, static serving | `app/main.py` |
| Service wiring (the route→service boundary) | `app/deps.py` |
| HTTP routes | `app/routes/` |
| Orchestration graphs | `app/graphs/` |
| Domain services | `app/services/` |
| Tool / integration / MCP registries | `app/services/{tools,integrations,mcp}/` |
| Credential Vault seam and backends | `app/services/credentials/` |
| Retrieval (FTS5, Chroma, fusion, scoping) | `app/services/rag/` |
| Model providers | `app/services/llm/` |
| Instagram providers and routing | `app/services/social/instagram/` |
| File ingestion and extraction | `app/services/files/` |
| SQLite connection, schema, repositories | `app/database/` |
| Shared contracts (events, approvals, state) | `app/contracts/` |
| Tests | `app/tests/` |
| Frontend | `frontend/` |

## Conventions for changes

- Prefer additive DDL. Never rewrite a shipped table in place.
- Keep secrets behind `vault.resolve()`, and keep the result out of every
  outbound surface: logs, events, status payloads, retrieval, prompts, error
  text.
- Route all errors through `safe_error_message`.
- Scope every query by `project_id` at the SQL level, not in Python.
- Keep the frontend's `tsc --noEmit` clean; `npm run build` runs it too.

Start with [CONTRIBUTING.md](../CONTRIBUTING.md) for setup, tests, and the PR
process.
