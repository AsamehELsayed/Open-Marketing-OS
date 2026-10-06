# FOR DEVELOPERS

**This document is not how you install Open Marketing OS as a user.** If you
downloaded OMOS to run it, you want the [README](README.md) — it covers the
installer, the portable build, and first-run setup. This file is only for people
who want to work on the code.

If you are trying to *run* OMOS from source, you are in the right place; jump to
[Set up a development environment](#set-up-a-development-environment).

## What OMOS is

OMOS is an AI marketing account manager: you chat with it in a browser and it
does real marketing work — website research, Instagram research, positioning and
offer analysis, campaign and experiment planning, approvals, and measurement.
It is a local-first Windows desktop app (Python FastAPI backend, React frontend,
SQLite) where all of your projects and data stay on your own machine.

## Prerequisites

| Requirement | Version | Needed for |
| --- | --- | --- |
| Python | 3.11+ | Backend |
| Node.js | 20+ | Frontend build and dev server |
| Git | any recent | Cloning |
| Windows | x64 | Supported platform for released builds |

These are **development** prerequisites. End users installing OMOS Quick need
none of them — the installer bundles its own runtime.

## Set up a development environment

### Recommended: the helper scripts

The scripts below are the recommended path because they set up the virtual
environment, install dependencies, and start both sides with one command.

```powershell
git clone https://github.com/AsamehELsayed/Open-Marketing-OS.git
cd Open-Marketing-OS
.\scripts\setup-dev.ps1     # create .venv, install backend + frontend deps
.\scripts\run-dev.ps1        # start backend and frontend dev server
```

`setup-dev.ps1` is idempotent — re-run it after pulling to refresh
dependencies.

### Manual alternative

If you would rather drive it yourself:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

The backend serves on `http://127.0.0.1:8000` in development.

The frontend is a separate Vite dev server:

```powershell
cd frontend
npm ci
npm run dev
```

Vite proxies API calls to the backend, so run both.

## Project layout

| Path | What lives there |
| --- | --- |
| `app/` | Python backend: FastAPI app, graphs, services, database |
| `app/routes/` | HTTP routes (presentation only — no business logic) |
| `app/graphs/` | LangGraph orchestration: account manager graph, approvals |
| `app/services/` | Domain services: tools, RAG, credentials, integrations, MCP, social |
| `app/database/` | SQLite connection, schema, repositories |
| `app/contracts/` | Shared typed contracts and event/error contracts |
| `app/tests/` | pytest suite |
| `frontend/` | React + Vite + TypeScript SPA |
| `scripts/` | Developer and maintenance scripts |
| `docs/` | Architecture, product, and process documentation |
| `data/` | Local dev database and index (gitignored) |

Two layering rules the tests enforce, so don't route around them:

- `app/routes/` is presentation only. Business logic belongs in `app/services/`.
- `app/graphs/` must not import FastAPI or React modules. Platform dependencies
  are injected from `app/services/`.

## Run the tests

```powershell
python -m pytest
```

`pytest.ini` sets `testpaths = app/tests`. Useful variations:

```powershell
python -m pytest -k vault          # one topic
python -m pytest app/tests/test_dev007_mcp.py -v
```

Tests must not require network access or real provider credentials. If a test
needs a secret, it should use an injected fake backend, not a live key.

## Frontend checks

```powershell
cd frontend
npm run typecheck    # tsc --noEmit
npm run build        # typecheck + production build
```

Run both before opening a PR.

## Coding conventions

- **Match the surrounding code.** The codebase is the reference. Follow the file
  you are editing.
- **No new dependencies without discussion.** Open an issue or PR describing
  what the dependency buys you and why the standard library or an existing
  dependency is not enough. Adding a dependency changes every user's install.
- **Secrets never get committed.** No API keys, tokens, or `.env` files. Use the
  Credential Vault for real credentials, and test doubles in tests. See
  [SECURITY.md](SECURITY.md).
- **Modules are small and single-purpose.** `app/database/sqlite.py` and
  `app/database/repos.py` are the only modules that touch `sqlite3`; keep it
  that way.
- **New SQLite tables are additive.** Create them with `CREATE TABLE IF NOT
  EXISTS` in a migration-safe path, and give project-owned tables a
  `project_id` column that is never null.
- **Errors get translated.** Raw exceptions should not reach the UI. Route them
  through `safe_error_message` in `app/contracts/events.py`, which redacts
  secrets and sensitive paths.

## Propose a change

1. **Branch off `main`.** One logical change per branch.
2. **Make the change**, with tests for new behavior. Bug fixes should come with
   a test that fails before the fix.
3. **Verify locally:** `python -m pytest`, `npm run typecheck`, `npm run build`.
4. **Open a pull request** with:
   - what changed and why,
   - how you verified it,
   - anything a reviewer should be careful about (schema changes, migrations,
     new dependencies, credential handling).
5. Wait for review. A maintainer will either merge, ask for changes, or close
   with an explanation.

If your change touches security, credentials, or data handling, read
[SECURITY.md](SECURITY.md) first — some of those changes follow a different
route.

## License

By contributing, you agree that your contributions are licensed under
**Apache-2.0**, the same license as the project. See [LICENSE](LICENSE) and
[NOTICE](NOTICE).
