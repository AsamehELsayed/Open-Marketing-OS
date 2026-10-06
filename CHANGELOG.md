# Changelog

Notable changes to Open Marketing OS are recorded here. This beta changelog describes the prepared `v0.1.0-beta.1` release; it does not assert that the release has been published.

Project links: [repository](https://github.com/AsamehELsayed/Open-Marketing-OS) · [releases](https://github.com/AsamehELsayed/Open-Marketing-OS/releases) · [issues](https://github.com/AsamehELsayed/Open-Marketing-OS/issues).

## [Unreleased]

No entries yet.

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
