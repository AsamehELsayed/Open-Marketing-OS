# Setup Guide

OMOS is a Windows desktop application whose interface opens in your default browser. The app backend listens on `127.0.0.1` at a dynamically selected local port. Do not port-forward it or expose it through a reverse proxy.

## Before installing

- Use a Windows x64 computer. Keep adequate free disk space for the installer, app data, project files, and indexes.
- Choose an edition: Local performs account-manager inference on the PC after its model is set up; OpenRouter sends selected turn content to OpenRouter.
- Download only from the [Open Marketing OS releases page](https://github.com/AsamehELsayed/Open-Marketing-OS/releases). Verify the downloaded installer with the SHA-256 entry in `checksums-sha256.txt` before opening it.
- The beta installer is not code-signed, so Windows may show an Unknown Publisher warning. Do not disable Windows security controls to proceed.

## Install and launch

1. Run the edition's Windows installer and follow the Windows setup prompts.
2. Launch **Open Marketing OS** from the installed shortcut.
3. The launcher prepares writable user directories, starts the local backend, waits for its health check, and opens the app in your default browser.
4. If startup fails, use the launcher-provided details/log option. Review and redact local paths or other sensitive information before sharing diagnostics.
5. Create a project and enter only information you are authorized to use. Projects and knowledge are kept in the local user-data area.

## Set up OMOS Local

The Local edition uses the pinned **Qwen2.5-7B-Instruct Q4_K_M** model, totaling **4,683,073,632 bytes** (about **4.36 GiB**). The model is not bundled with the installer.

1. Open **Settings → Local Model** and review the model identity, source, license, and disk-space estimate.
2. Ensure approximately **9,366,147,264 bytes** (about **8.72 GiB**) of free disk space is available for the catalog's temporary staging and activation requirement, plus additional space for the app, Windows, workspace data, and indexes.
3. Select **Download Model** to explicitly start downloading the pinned files. The download is not started by opening Settings or starting the runtime.
4. Wait for all model files to finish downloading, pass checksum verification, and become active. Do not interrupt OMOS while it is activating the verified model.
5. Start or select the Local runtime and wait until it reports ready before sending an account-manager prompt.

The OMOS-managed llama.cpp runtime profile is `b11429` for Windows x64 CPU. As a practical planning target, 16 GB system RAM is a reasonable starting point for a 7B Q4 model, but it is not an established minimum. Memory demand and speed vary with other applications, context length, and hardware.

The first use of semantic Business Knowledge/RAG can fetch the pinned `multilingual-e5-small` embedding model into `%LOCALAPPDATA%\OpenMarketingOS\models\`. This is separate from the explicit Qwen model download. The embedding model runs locally and its implementation does not send document text to the model host. Complete this initial cache setup before relying on semantic retrieval while offline.

## Set up OMOS OpenRouter

1. Open the credential/provider controls in Settings → AI.
2. Enter your own OpenRouter API key into the OMOS UI. Do not put it in a project file, issue report, screenshot, or source-code `.env` file.
3. Use the connection check if you want to confirm the credential. This check contacts OpenRouter.
4. Select OpenRouter and configure the provider/model and routing mode.

OMOS stores the secret using Windows DPAPI-backed local storage bound to your Windows user. The application database stores a reference to the secret. OMOS does not bundle a shared key.

## Business Knowledge and project files

Use the Knowledge/Business Knowledge area and project file controls to add relevant business information to the correct project. OMOS extracts supported text, indexes it for project-scoped retrieval, and can cite retrieved source chunks in an answer. Review the citation and the underlying source. Retrieval can return incomplete or irrelevant evidence; verify important decisions independently.

Supported formats, file-size limits, and current indexing state are presented in the app. Image OCR is not supported. If semantic embeddings are unavailable, retrieval may fall back to lexical search. Keep project knowledge current and remove material that should no longer be used.

For OpenRouter turns, the prompt and the evidence chosen for that turn, including relevant retrieved knowledge or selected attachments, are transmitted to OpenRouter. For Local turns, the account-manager model call runs using the local runtime after setup. Other features may still use network services when invoked.

## Where information is stored

Packaged Windows builds use `%LOCALAPPDATA%\OpenMarketingOS\` for writable user state:

- `data\marketing.db` — application database and metadata.
- `company\`, `knowledge\`, `production\`, `strategy\`, and `state\` — workspace files where used.
- `data\projects\` — project files/attachments; `data\chroma\` and database indexes — retrieval data.
- `credentials\` — DPAPI-encrypted credential blobs. These files are bound to the Windows user account.
- `logs\` — launcher and backend logs.
- `models\` — Local inference and retrieval model/runtime data.

Make a backup of important workspace material before moving the user-data folder or uninstalling. Do not share database, credential, or project-data folders when reporting a problem.

## Choose an edition

| | OMOS Local | OMOS OpenRouter |
| --- | --- | --- |
| Account-manager inference | On this PC after model setup | OpenRouter cloud |
| Model requirement | Explicit Qwen model download; about 4.36 GiB | No local generation model required |
| Credential | No provider API key | Your own OpenRouter key, stored with DPAPI |
| Turn privacy | Account-manager prompt stays with local inference; invoked network features still use their services | Prompt and selected evidence for the turn are sent to OpenRouter |
| Offline use | Local generation can work after setup; semantic RAG also needs its embedding cache ready | Cloud generation requires network access |

## Troubleshooting and issue reports

If startup or a model action fails, capture the displayed status and relevant redacted diagnostics. Do not share secrets, credential blobs, database files, customer data, or private business documents. Report ordinary issues through the [public issue tracker](https://github.com/AsamehELsayed/Open-Marketing-OS/issues). Use the maintainers' published private channel for security concerns; do not post unpatched vulnerability details publicly. These links identify the intended target and do not assert that the repository or release has been published.
