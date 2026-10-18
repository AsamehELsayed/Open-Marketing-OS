# Changelog

Notable changes to Open Marketing OS are recorded here. This changelog covers the prepared `v0.1.0-beta.2` release and the earlier `v0.1.0-beta.1` release; it does not assert that either release has been published.

Project links: [repository](https://github.com/AsamehELsayed/Open-Marketing-OS) · [releases](https://github.com/AsamehELsayed/Open-Marketing-OS/releases) · [issues](https://github.com/AsamehELsayed/Open-Marketing-OS/issues).

## [Unreleased]

No entries yet.

## [0.1.0-beta.2] — PRE-RELEASE / BETA

### Changed

- Application identity synchronized to `0.1.0-beta.2`. The backend constant `app.paths.APP_VERSION` remains the single source of truth: the health route, the release manifest, the installer profile IDs (`omos-local-v0.1.0-beta.2`, `omos-openrouter-v0.1.0-beta.2`) and the private frontend package metadata now all agree with it, and the edition-profile defaults module derives its IDs from that constant instead of repeating a literal.
- The first-run UI no longer pins one release's profile ID. Edition-specific setup copy is selected from a version-tolerant match on the `omos-<edition>-v<release>` ID shape, so a profile stored by an earlier beta still resolves to the correct edition instructions. Unknown or unrecognized values continue to fall back to neutral copy.
- Edition defaults, credential policy, model catalog, pricing, offers, privacy handling, project isolation, validation and workflow behavior are unchanged from `0.1.0-beta.1`.

### Installation

- Installers remain unsigned. Windows may show an **Unknown Publisher** warning and Microsoft Defender SmartScreen may report an unrecognized publisher. Verify the download with `Get-FileHash -Algorithm SHA256` against `SHA256SUMS.txt` before running it, and do not disable Windows security protections to install.

### Known limitations

- The limitations listed for `0.1.0-beta.1` still apply, including loopback-proxy (not OS-wide) offline validation, the un-reobserved OpenRouter transport telemetry after its final fix, hardware-dependent performance, and beta status rather than production readiness.
- This is a pre-release beta, not a production-readiness claim.

## [0.1.0-beta.1] — PRE-RELEASE / BETA

### Added

- Two Windows editions: OMOS Local and OMOS OpenRouter.
- OMOS Local with explicit download and checksum verification for the pinned Qwen2.5-7B-Instruct Q4_K_M model, managed llama.cpp `b11429` runtime, and local account-manager inference after setup. Model weights are not bundled.
- OMOS OpenRouter with user-supplied credentials protected by Windows DPAPI and cloud inference through the user's OpenRouter account.
- Business Knowledge and project-scoped retrieval with source citations.
- Windows desktop launcher and per-user writable data layout.

### Privacy and security

- OpenRouter prompts and evidence selected for a turn are sent to OpenRouter. No provider key is bundled or shared by OMOS.
- The Local account-manager inference path runs on the PC after setup. Network use may still occur for features the user invokes and for first-time local RAG embedding-model setup.
- The backend listens on loopback. The beta installers are not code-signed.

### Known limitations

- Local offline-after-setup validation used a closed loopback proxy for application HTTP(S), not OS-wide physical network isolation.
- The single successful live OpenRouter acceptance request predates the final transport telemetry fix. The fix passed a focused fake-only regression; corrected lifecycle telemetry was not re-observed with another live request.
- Hardware performance varies; the published 16 GB RAM figure is a practical planning target, not a tested minimum or compatibility guarantee.
- Public links identify the founder-confirmed target `AsamehELsayed/Open-Marketing-OS`; they do not claim the repository or beta has been published.
- This is a pre-release beta, not a production-readiness claim.
