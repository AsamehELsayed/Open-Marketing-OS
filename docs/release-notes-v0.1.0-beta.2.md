# Open Marketing OS v0.1.0-beta.2

**Status: PRE-RELEASE / BETA**

Open Marketing OS is a Windows desktop application for project-based marketing work, an AI Account Manager, and Business Knowledge retrieval. This beta is prepared in two editions: OMOS Local and OMOS OpenRouter. These notes do not announce publication or production readiness.

Beta 2 aligns the application identity across the whole product. It changes no feature behavior: the AI editions, default routing, Business Knowledge retrieval, privacy handling and validation work exactly as they did in `0.1.0-beta.1`.

## What's new in Beta 2

- **One consistent version.** The version the app reports, the version stamped into the installer name and manifest, the installer edition profiles (`omos-local-v0.1.0-beta.2`, `omos-openrouter-v0.1.0-beta.2`) and the packaged frontend metadata all read from a single canonical value. An installer can no longer ship one version while the app reports another.
- **Setup copy that follows your edition across releases.** The first-run and AI-settings screens previously recognized only the profile ID of the beta that shipped them. A profile stored by an earlier beta could therefore fall back to neutral copy. Edition detection now matches the edition and the shape of the release segment, so an upgrade keeps the correct Local or OpenRouter setup instructions.
- **Beta 1 history is preserved.** The `0.1.0-beta.1` changelog entry and release notes remain available as a record of that release.

Nothing else changed. Edition defaults, the pinned model, credential storage, pricing and offers, project isolation and validation behavior are identical to Beta 1.

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

Download the installer for the desired edition (`OMOS-Local-v0.1.0-beta.2.exe` or `OMOS-OpenRouter-v0.1.0-beta.2.exe`) from the [Open Marketing OS releases page](https://github.com/AsamehELsayed/Open-Marketing-OS/releases).

**The installers are not code-signed.** Windows may display an **Unknown Publisher** warning, and Microsoft Defender SmartScreen may report that the publisher is unrecognized. This is expected for an unsigned beta build. Do not disable Windows security protections to install it. If you are not sure whether a warning is expected, stop and verify the file first.

### Verify the download (PowerShell)

Run this in the folder where you downloaded the installer, and compare the printed SHA-256 hash with the matching installer line in `SHA256SUMS.txt`:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath .\OMOS-Local-v0.1.0-beta.2.exe
Get-FileHash -Algorithm SHA256 -LiteralPath .\OMOS-OpenRouter-v0.1.0-beta.2.exe
```

Compare the `Hash` values character for character with the matching filename in `SHA256SUMS.txt`. If a hash does not match, delete that installer and download it again rather than running it. This unsigned beta has no signed checksum metadata, so a matching hash checks file integrity against the published list but does not establish publisher identity.

Launch OMOS from Windows; the launcher starts its local backend and opens the browser interface.

## Known limitations

- **Unsigned build.** Installers carry no code signature, so Unknown Publisher and SmartScreen warnings are the normal first-run experience. Signature verification is a release-process property, not something the application can suppress.
- **Local offline validation:** DEV-029 used a closed loopback proxy for application HTTP(S), not OS-wide physical network isolation.
- **OpenRouter transport telemetry:** the one successful live OpenRouter acceptance request predates the final transport telemetry fix. The fix passed a focused fake-only regression, but corrected transport lifecycle telemetry was not re-observed with another live request.
- **Hardware:** performance and memory use vary. The 16 GB RAM figure is a planning target, not a validated minimum.
- **Beta status:** this is pre-release software. Do not treat it as production-ready or code-signed.
- **Cross-release setup copy is shape-matched:** edition detection recognizes the documented `omos-<edition>-v<release>` ID shape. An unrecognized or hand-edited profile ID intentionally falls back to neutral copy rather than guessing an edition.
- The issue link below identifies the founder-confirmed public target; these notes do not announce publication.

## Checksums

Final release checksums will be supplied in `SHA256SUMS.txt` alongside the approved installer files after release-artifact validation. **Not yet populated — release handoff placeholder.** Do not treat this document alone as checksum approval.

## Reporting issues

For ordinary issues, use the [public issue tracker](https://github.com/AsamehELsayed/Open-Marketing-OS/issues). Include the OMOS version (this release reports `0.1.0-beta.2`), edition, Windows version/architecture, reproduction steps, and redacted diagnostics. Never attach keys, credential blobs, databases, real customer/project content, or unredacted logs. For security vulnerabilities, use the private reporting channel linked from the repository's [Security page](https://github.com/AsamehELsayed/Open-Marketing-OS/security) if available; do not post exploit details publicly. These links identify the intended target and do not assert that the repository or release has been published or that private reporting is enabled.
