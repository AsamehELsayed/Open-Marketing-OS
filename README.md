# Open Marketing OS

**Open Marketing OS (OMOS)** is a Windows desktop beta for organizing marketing work, chatting with an AI account manager, and retrieving information from project knowledge. It runs on your PC and opens in your default browser. The backend binds to loopback (`127.0.0.1`); it is not intended to be exposed to your LAN.

This pre-release offers two editions. Choose the inference route that fits your privacy and hardware needs:

**Project:** [source repository](https://github.com/AsamehELsayed/Open-Marketing-OS) · [releases](https://github.com/AsamehELsayed/Open-Marketing-OS/releases) · [issue tracker](https://github.com/AsamehELsayed/Open-Marketing-OS/issues)

| Edition | Inference | Setup |
| --- | --- | --- |
| **OMOS Local** | After setup, account-manager inference runs on your PC through OMOS-managed llama.cpp. | You explicitly download the pinned model in Settings. No provider key is needed. |
| **OMOS OpenRouter** | Inference is not local; prompts and selected evidence go to OpenRouter for cloud inference. | You supply your own OpenRouter API key; OMOS stores it using Windows DPAPI. |

## OMOS Local

The selected model is **Qwen2.5-7B-Instruct Q4_K_M**, pinned to a source revision. Its two model files total **4,683,073,632 bytes** (about **4.36 GiB**). The model is not included in the installer. Review its source and license before downloading it. OMOS does not download these model weights silently: use **Settings → Local Model → Download Model** to start the download. Starting the runtime does not download the model. OMOS verifies the pinned files before activation and manages the llama.cpp `b11429` runtime.

The pinned profile reserves **9,366,147,264 bytes** (about **8.72 GiB**) of temporary disk space for staging and activation. Keep additional free space for Windows, OMOS, project files, and indexes. As a practical planning target, 16 GB of system RAM is a reasonable starting point for a 7B Q4 model; it is not a tested minimum or a guarantee of speed. Actual memory use and response time depend on the PC and workload. The shipped runtime profile is Windows x64 CPU; no GPU requirement is claimed.

After setup, Local account-manager prompts and responses are processed by the local inference runtime. Other features can still use the network when you invoke them. On first semantic Business Knowledge/RAG use, OMOS may download a pinned local embedding model to the user cache. The embedding implementation does not send document text to its model host. Set up that cache before relying on semantic retrieval offline.

## OMOS OpenRouter

OMOS does not include or share an OpenRouter key. Enter your own key in the OMOS settings UI. On Windows, the credential value is stored as DPAPI-protected data bound to the current Windows user; the application database holds a credential reference. The key is not intended to be written to plaintext files, logs, prompts, or telemetry.

OpenRouter is cloud inference. Prompts and the project knowledge, conversation attachments, or other evidence selected for a turn are sent to OpenRouter so it can produce a response. Review the provider's terms and privacy settings, and do not submit material you are not comfortable sending to that provider.

## Install and first run

1. Download the installer from the [Open Marketing OS releases page](https://github.com/AsamehELsayed/Open-Marketing-OS/releases). Verify its SHA-256 against the release's `checksums-sha256.txt`.
2. Run the Windows installer and launch OMOS. The launcher starts the local backend and opens the app in your default browser.
3. Create or select a project and review its Business Knowledge before asking questions.
4. For **Local**, open **Settings → Local Model**, review the model details and disk requirement, then explicitly select **Download Model**. Wait for checksum verification and activation before starting the runtime or selecting Local inference.
5. For **OpenRouter**, enter your own key in the credential settings and use the connection check. Choose the provider/model and routing mode in AI settings.
6. Start an Account Manager conversation. Add relevant files to the correct project when you want them indexed as Business Knowledge.

See [SETUP.md](SETUP.md) for more detail.

## Business Knowledge and RAG

OMOS can retrieve relevant text from a project's knowledge and supported files and include that evidence in a conversation. This is retrieval-augmented generation (RAG). Add information to the intended project, allow it to be indexed, and check the citations in the answer. A citation shows which retrieved source informed a response; it is not a guarantee that the response is complete or correct. Retrieval is project-scoped. Do not upload data you lack permission to use.

Supported file types and size limits are shown in the app. Image OCR is not supported. Retrieval behavior can degrade to lexical search if semantic embeddings are unavailable. The first semantic index use may need an internet connection to obtain the pinned local embedding model; application text is not sent to that model host.

## Data and privacy

In a packaged Windows installation, writable app data is stored under `%LOCALAPPDATA%\OpenMarketingOS\`, separate from the installation directory. This includes the SQLite database at `data\marketing.db`, project and workspace data, search indexes and backups under `data\`, encrypted credential files under `credentials\`, logs under `logs\`, and model/runtime caches under `models\`. The launcher and app also expose local diagnostic details that may include your local file paths; review diagnostics before sharing them.

Local inference keeps the account-manager model call on your PC after setup. OpenRouter sends turn prompts and selected evidence to the cloud provider. Network access may also be used by features you invoke, such as website research, and by first-time retrieval embedding setup. The backend's loopback binding is not a claim that all application traffic stays local.

## Beta status and limitations

- This is **PRE-RELEASE / BETA** software. It is not represented as production-ready.
- The Windows installer is not code-signed. Windows may display an Unknown Publisher warning. Do not disable Windows security controls; obtain the installer and checksum from the announced official release channel.
- Local offline-after-setup acceptance used a closed loopback proxy for application HTTP(S), not an OS-wide physical network disconnect.
- The one successful live OpenRouter acceptance request predates the final transport telemetry fix. The fix passed a focused fake-only regression; corrected transport lifecycle telemetry was not re-observed with another live request.
- Local generation performance and memory use vary by hardware. The RAM planning figure above is not a hardware compatibility guarantee.
- Public release and issue links use the founder-confirmed target `AsamehELsayed/Open-Marketing-OS`. Publication is not claimed by these documents.

## Help and issue reporting

Use the [public issue tracker](https://github.com/AsamehELsayed/Open-Marketing-OS/issues). Include the OMOS version, edition, Windows version/architecture, concise reproduction steps, and relevant redacted diagnostics. Do not attach API keys, credential files, real client or project material, database files, or unredacted logs. For a suspected security vulnerability, do not post exploit details publicly; use the private reporting channel linked from the repository's [Security page](https://github.com/AsamehELsayed/Open-Marketing-OS/security) if available, or wait for a maintainer-published private contact channel. These links identify the intended target and do not assert that the repository or release has been published.

## License and notices

OMOS source is distributed under the Apache License 2.0 as shown in [LICENSE](LICENSE), with attribution details in [NOTICE](NOTICE). Third-party software and Local model terms are separate; see the included notices and the model details shown in Settings before use. The release remains pre-production; applicable production/CI licensing decisions are not claimed as confirmed.
