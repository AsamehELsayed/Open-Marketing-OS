# Privacy

What Open Marketing OS does with your information, in plain terms.

The short version: **the app and your data live on your machine. AI inference
does not.** In this beta, OMOS Quick sends your prompts and the context it needs
to the cloud provider you chose. Everything else stays local.

## What stays on your machine

These never leave your computer:

- **Your projects** — names, websites, goals, notes, and settings.
- **Your files** — everything you attach to a project, plus the extracted text
  and the local search index built from them.
- **Your conversations** — every message you send and every response you get.
- **The database** — campaigns, tasks, approvals, experiments, learnings, and
  usage records all live in a local SQLite file.
- **Your settings** — provider choices, model choice, appearance, and
  preferences.
- **References to your credentials** — which providers you connected and
  whether a credential is present. The database stores only a reference, not the
  secret.

All of this lives under `%LOCALAPPDATA%\OpenMarketingOS\`. See
[user-data-and-backup.md](user-data-and-backup.md) for the exact paths.

The backend listens on `127.0.0.1` only, on a port chosen at launch. It is not
reachable from your LAN or the internet.

## What leaves your machine in the Quick edition

This is the part worth reading carefully. OMOS Quick has no local-model edition,
so a model call is a network call to a provider.

| What | Where it goes | Why |
| --- | --- | --- |
| **Your prompts and the context they need** — your message, the project context retrieved for it, and the tool results included in the request | **OpenRouter** or **OpenAI**, whichever you connected in Settings | This is how the model produces an answer |
| **Website content** | The website you asked OMOS to research, and the AI provider above | Research fetches the page, then the model reasons about it |
| **Instagram profile data** | The Instagram data provider you connected (Apify or Bright Data) | Public profile data comes from that provider, not from OMOS |
| **Attachments you send for vision analysis** | **OpenAI** | Vision analysis requires their models |

Practical consequences:

- **Anything you type into a conversation leaves your machine**, along with the
  project context attached to that turn. Do not paste data you are not allowed
  to send to a third party.
- **Anything you attach to a project for analysis leaves your machine** when it
  is analyzed.
- **Website and Instagram research require internet access** and involve third
  parties you do not control.

A local-model edition is planned. It would let the model call happen on your own
hardware. It does not exist in this beta — do not plan around it.

## What OMOS never sends

- **Your credential values.** OMOS reads a secret from the Credential Vault
  locally and uses it only for the request to the provider that credential
  belongs to. Your OpenRouter key is not sent to OpenAI. Your Apify key is not
  sent to Bright Data. Your OpenAI key is not sent to OpenRouter.
- **Your credentials to the OMOS project.** There is no OMOS account and nothing
  is sent to us.
- **Your files or projects, as a batch.** Files are only transmitted when a
  specific request needs them, and only to the provider handling that request.

## How credentials are stored

Every provider credential — OpenRouter, OpenAI, Apify, Bright Data, Meta — is
stored through the Credential Vault:

- Secret values are **encrypted with Windows DPAPI** and written as
  individual encrypted files under
  `%LOCALAPPDATA%\OpenMarketingOS\credentials\`.
- **DPAPI is bound to your Windows user account.** Only that account on that
  machine can decrypt them.
- The database stores only a `vault://` reference — a pointer, not a secret.
- Plaintext secrets are never written to the database, to `.env` files, to
  browser storage, or to logs.

One consequence to be aware of: if you move your data to a different machine or a
different Windows user, your projects restore but your credentials will not
decrypt. You will need to reconnect them in Settings. That is a security
property, not a bug — it means a copied backup file is not a usable credential.

## Telemetry and usage records

To be precise about what "telemetry" means here:

- **The app records model usage, timing, and cost in its own local database.**
  This is what powers the Results view and the cost views. Those records live in
  your database, on your machine, alongside everything else.
- **No analytics or tracking data is sent to the OMOS project.** There is no
  usage ping, no crash reporting service, and no telemetry endpoint. If the app
  is not talking to a provider you configured, it is not talking to us.

## Third-party providers have their own policies

Once a request reaches a provider, that provider's privacy policy applies to it.
OMOS cannot speak for them.

- **OpenRouter** — see OpenRouter's privacy policy on their site
  (openrouter.ai).
- **OpenAI** — see OpenAI's privacy policy on their site (openai.com).
- **Apify** and **Bright Data** — if you connect them for Instagram research,
  their policies apply to those requests.

Review those policies before sending anything sensitive through a provider.

## What OMOS does not do

- **No telemetry to us.** Nothing is sent to the OMOS project.
- **No selling of your data.** Not to anyone, for any reason.
- **No advertising or ad tracking.**
- **No OMOS-owned API keys shipped to you.** You bring your own provider
  credentials; the app runs with none of its own, which means there is no shared
  key that could leak your prompts along with someone else's.

## In one paragraph

Your projects, files, conversations, and database stay on your machine, and your
credentials are encrypted at rest and never leave except to the provider they
belong to. In the Quick edition, your prompts and relevant context do go to the
cloud AI provider you configured, and research tools contact the sites and
providers they need to. There is no analytics, no tracking, and no data leaving
for the OMOS project. A local-model edition is planned, and would remove the AI
provider from that list.

## Related

- [SECURITY.md](../SECURITY.md) — reporting a vulnerability, and our security posture
- [user-data-and-backup.md](user-data-and-backup.md) — exact data locations and backup/restore
- [troubleshooting.md](troubleshooting.md) — if a provider connection fails
