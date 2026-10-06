# Open Marketing OS v0.1.0-beta.1

**Status: PRE-RELEASE / BETA**

Open Marketing OS is a Windows desktop application for project-based marketing work, an AI Account Manager, and Business Knowledge retrieval. This beta is prepared in two editions: OMOS Local and OMOS OpenRouter. These notes do not announce publication or production readiness.

## What's included

- A Windows launcher that starts the local app backend and opens its interface in your default browser.
- Business Knowledge and project-scoped retrieval with citations to retrieved sources.
- **OMOS Local:** local account-manager inference after an explicit model download and verification.
- **OMOS OpenRouter:** cloud inference through the user's own OpenRouter account and credential.

## OMOS Local

The Local profile uses **Qwen2.5-7B-Instruct Q4_K_M**, totaling **4,683,073,632 bytes** (about **4.36 GiB**), at a pinned model-source revision. The model is not bundled. Download it from **Settings → Local Model → Download Model** after reviewing the source and license. OMOS verifies the files before activation; the runtime does not silently download model weights. OMOS manages the llama.cpp `b11429` Windows x64 CPU runtime.

The catalog reserves **9,366,147,264 bytes** (about **8.72 GiB**) of temporary disk space for staging and activation. Keep additional free space for the app, workspace, indexes, and Windows. A 16 GB system-RAM configuration is a practical planning target, not a tested minimum; actual requirements and speed depend on the PC and workload.

Initial semantic Business Knowledge retrieval may fetch a pinned local embedding model into the user cache. The embedding implementation does not send document text to its model host. Complete this setup before expecting semantic retrieval to work offline.

## OMOS OpenRouter and privacy

OMOS does not bundle or share an OpenRouter API key. The user enters their own key through the app UI; Windows DPAPI-backed storage protects the credential at rest and binds it to the Windows user. The app database stores a reference rather than the plaintext key.

OpenRouter performs inference in the cloud. The prompt and evidence selected for a turn, such as retrieved Business Knowledge or selected attachments, are sent to OpenRouter. Do not submit content unless you are authorized to share it with the provider and have reviewed its current policies.

## Business Knowledge and citations

Add material to the intended project so OMOS can index and retrieve relevant text during a conversation. Answers may cite retrieved sources. Check those sources: citations show which evidence was returned, but do not guarantee answer accuracy or completeness. Image OCR is not supported. Semantic retrieval can fall back to lexical retrieval if its local embedding runtime is unavailable.

## Installation

Download the installer for the desired edition from the [Open Marketing OS releases page](https://github.com/AsamehELsayed/Open-Marketing-OS/releases). Verify its SHA-256 using `checksums-sha256.txt` before running it. Launch OMOS from Windows; the launcher starts its local backend and opens the browser interface. The installer is not code-signed, so Windows may display an Unknown Publisher warning. Do not disable Windows security protections. The URL identifies the intended target and does not assert that the repository or release has been published.

## Known limitations

- **Local offline validation:** DEV-029 used a closed loopback proxy for application HTTP(S), not OS-wide physical network isolation.
- **OpenRouter transport telemetry:** the one successful live OpenRouter acceptance request predates the final transport telemetry fix. The fix passed a focused fake-only regression, but corrected transport lifecycle telemetry was not re-observed with another live request.
- **Hardware:** performance and memory use vary. The 16 GB RAM figure is a planning target, not a validated minimum.
- **Beta status:** this is pre-release software. Do not treat it as production-ready or code-signed.
- The issue link below identifies the founder-confirmed public target; these notes do not announce publication.

## Checksums

Final release checksums will be supplied in `checksums-sha256.txt` alongside the approved installer files after release-artifact validation. **Not yet populated — release handoff placeholder.** Do not treat this document alone as checksum approval.

## Reporting issues

For ordinary issues, use the [public issue tracker](https://github.com/AsamehELsayed/Open-Marketing-OS/issues). Include the OMOS version, edition, Windows version/architecture, reproduction steps, and redacted diagnostics. Never attach keys, credential blobs, databases, real customer/project content, or unredacted logs. For security vulnerabilities, use the private reporting channel linked from the repository's [Security page](https://github.com/AsamehELsayed/Open-Marketing-OS/security) if available; do not post exploit details publicly. These links identify the intended target and do not assert that the repository or release has been published or that private reporting is enabled.
