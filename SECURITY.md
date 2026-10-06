# Security Policy

## Release status

Open Marketing OS `v0.1.0-beta.1` is a **PRE-RELEASE / BETA**. It is not code-signed and is not represented as production-ready. Windows may show an Unknown Publisher warning. Obtain installers only from the public release channel announced by the maintainers and verify their SHA-256 checksums against the accompanying checksum file. Do not disable operating-system security protections.

## Reporting a security issue

The founder-confirmed public target is [AsamehELsayed/Open-Marketing-OS](https://github.com/AsamehELsayed/Open-Marketing-OS). Use the private vulnerability-reporting channel linked from its [Security page](https://github.com/AsamehELsayed/Open-Marketing-OS/security) if that feature is available. Until maintainers publish a private channel, do not post exploit steps, credentials, private data, or other sensitive vulnerability details in a public issue. These links identify the intended target; this document does not assert that the repository has been published or that private vulnerability reporting is enabled.

For a non-sensitive product issue, use the [public issue tracker](https://github.com/AsamehELsayed/Open-Marketing-OS/issues). Include the affected OMOS version and edition, Windows version/architecture, concise reproduction steps, and redacted diagnostics. Do not attach user databases, credential files, raw logs containing private paths, API keys, tokens, passwords, or real company/project content.

## Security behavior and privacy boundaries

- **Local listener:** the packaged launcher binds the backend to `127.0.0.1` on a dynamically selected port. This prevents direct LAN binding; it is not a claim that the entire application is offline or immune to local-machine threats. Do not port-forward or reverse-proxy the listener.
- **OMOS Local:** account-manager inference runs on the managed local llama.cpp runtime after the explicitly initiated model download has completed, checksums have passed, and the model is active. The Qwen model weights are not bundled. Other network-enabled features remain capable of using the network. First semantic RAG use may fetch a pinned embedding model into the local user cache; its embedding code does not send document text to its model host.
- **OMOS OpenRouter:** the user supplies the OpenRouter key. Prompts and evidence selected for a turn are sent to OpenRouter for cloud inference. Review the provider's own terms and retention controls before sending sensitive material.
- **Credential storage:** on Windows, provider secrets are stored as DPAPI-protected blobs bound to the current Windows user. SQLite stores references, not the plaintext secret value. Do not manually copy credential blobs to another account or machine.
- **Data at rest:** packaged builds keep writable state under `%LOCALAPPDATA%\OpenMarketingOS\`, including workspace/project data, the application database, indexes, logs, credentials, and model caches. Anyone with access to your Windows account or unlocked device may be able to access ordinary workspace content.
- **Diagnostics:** launcher details and logs can contain local paths and runtime metadata. Review and redact them before sharing.

## Known validation limits

DEV-029 Local offline-after-setup validation used a closed loopback proxy for application HTTP(S), not an OS-wide physical network disconnect. The successful live OpenRouter generation predates the final transport telemetry fix. That fix passed a focused fake-only regression, but its corrected transport lifecycle telemetry was not re-observed with an additional live provider request.

## Safe use

- Keep Windows and the app's dependencies updated from trusted release sources.
- Do not expose the local listener to other devices.
- Do not submit confidential content to OpenRouter unless permitted by your organization and acceptable under the provider's current terms.
- Keep backups of important project/workspace files in a location you control.
- Redact credentials and private data from issue reports and screenshots. If a key is accidentally disclosed, revoke or rotate it with the provider.
