# Changelog

All notable changes to Open Marketing OS are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Beta releases carry a pre-release suffix, so `1.1.0-beta.1` sorts before
`1.1.0`.

## [Unreleased]

Nothing yet.

## [1.1.0-beta.1] - 2026-09-26

The first public beta of OMOS Quick for Windows.

### Added

- **First public Windows beta.** OMOS is an AI marketing account manager: you
  chat with it, and it does real marketing work on your own machine.
- **OMOS Quick edition** with cloud AI through your own account:
  - **OpenRouter** — recommended, widest model choice.
  - **OpenAI** — direct access to OpenAI models.
- **Two ways to install:**
  - Single-click installer `OpenMarketingOS-Quick-Setup.exe`.
  - Portable build `OpenMarketingOS-Portable.zip` — no installer, just unzip and
    run.
- **First-run setup wizard** that walks you through connecting a provider and
  creating your first project.
- **Settings → AI**, where you choose your provider, pick a model, and test the
  connection.
- **Credential Vault** for every provider credential, encrypted at rest with
  Windows DPAPI and bound to your Windows user account.
- **Project memory** and **project-scoped retrieval**, so the app recalls what it
  learned about each project instead of starting cold every time.
- **Website research tool** — point it at a site and it reads and analyses it.
- **Instagram research tool** — analyse public profiles through your own Apify
  or Bright Data account.
- **Files and vision** — attach documents and images to a project and ask
  questions about them.
- **MCP integrations** — connect Model Context Protocol servers to extend what
  the app can reach.
- **Real-time streaming responses**, so you see work as it happens instead of
  waiting for a finished answer.
- **Apache-2.0 licensing** for the source.

### Changed

- The app **no longer requires Python, Node.js, or Git to run**. Those are only
  needed to build from source.
- The backend now **binds `127.0.0.1` on a dynamically chosen free port**
  instead of a fixed port, so it will not collide with anything else you are
  running. The launcher opens your default browser to it.
- **All user data now lives under a single folder**,
  `%LOCALAPPDATA%\OpenMarketingOS\`, instead of being spread across the install
  directory.

### Security

- **Credentials are encrypted at rest with Windows DPAPI.** Secret values are
  never written to the database, `.env` files, browser storage, or logs — the
  database holds only references.
- **Local-only by default.** The backend listens on `127.0.0.1` only and is
  never exposed to your LAN or the internet.
- **SHA256 checksums are published with every release** in `SHA256SUMS.txt`, so
  you can verify what you downloaded.
- **No OMOS-owned API keys are shipped.** You bring your own provider
  credentials.

### Known limitations

- **Cloud AI only.** There is no local-model edition in this beta. A local-model
  edition is planned.
- **Not code-signed yet.** Windows SmartScreen will warn about an **Unknown
  publisher**. This is expected for a beta — verify the download against
  `SHA256SUMS.txt` rather than disabling SmartScreen.
- **Windows x64 only.**
- **Internet access is required** for AI providers, and for website and
  Instagram research. The app is local, but the AI inference is not.
- **This is a beta.** Expect rough edges: clunky spots in the interface,
  occasional retries, and behavior that may change before the final release.
  Back up your projects before big changes — see
  [docs/user-data-and-backup.md](docs/user-data-and-backup.md).
