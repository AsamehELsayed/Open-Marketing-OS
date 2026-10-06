# Security Policy

## Supported versions

Only the **latest published release** receives security fixes. When a new
version ships, the previous one stops being supported.

| Version | Supported |
| --- | --- |
| Latest release | Yes |
| Anything older | No |

If you are on an older version, update to the latest release before reporting
anything — the issue may already be fixed.

## Reporting a vulnerability

**Use GitHub's private vulnerability reporting.** On the repository, open the
**Security** tab and choose **Report a vulnerability**. This opens a private
advisory visible only to you and the maintainers, and it is the primary channel.

If that option is unavailable to you, you can file a GitHub Issue and select the
option to report it as a **security advisory** — that keeps the conversation
private as well.

> **Do not open a public Issue for an unfixed vulnerability.** A public issue
> announces the bug to everyone before a fix exists. Use one of the private
> channels above.

If the project later publishes a security contact email, it will be listed in
this file. Until then, the private advisory flow above is the only sanctioned
channel — please do not guess an address or contact a third party.

### What to include

- **Affected version** — the exact version string from Settings → About (or the
  release you downloaded).
- **Edition** — OMOS Quick is the only edition in this beta; say so explicitly if
  you are unsure.
- **Windows version and architecture** — e.g. Windows 11 x64.
- **Reproduction** — the smallest set of steps that shows the problem.
- **Impact** — what an attacker gains, and what access they need first.
- **Suggested mitigation** — if you have one, include it.

### What not to include

- **API keys, tokens, or passwords of any kind.** Not in the report body, not
  in screenshots, not in attached files.
- **Personal data**, customer data, or your marketing content.
- **Raw credential files** from `%LOCALAPPDATA%\OpenMarketingOS\credentials\`.

If you accidentally paste a key or token, **redact it and rotate it immediately**
at the provider. Treat it as compromised the moment it exists in a public place.
A redacted report is still fully useful.

## Response expectations

These are targets, not guarantees:

| Stage | Target |
| --- | --- |
| Acknowledgement that the report was received | 3 business days |
| Initial assessment (severity, reproducibility, affected versions) | 10 business days |
| Fix and release, if a fix is warranted | Depends on severity; communicated in the advisory |
| Disclosure | Coordinated with you, after a fix is available |

You will be credited in the release notes if you want to be. If you would rather
stay anonymous, that is fine — the advisory will credit "a responsible
researcher" or omit the credit.

You will not be sued or retaliated against for good-faith research that stays
within the scope above: no social engineering, no denial-of-service, no access
to data belonging to anyone other than yourself.

## Security posture

What the product actually does today, stated plainly:

- **Credentials are encrypted at rest with Windows DPAPI**, bound to your
  Windows user account. Each secret is a separate encrypted file under
  `%LOCALAPPDATA%\OpenMarketingOS\credentials\`. The database stores only
  `vault://` references, never secret values. Secrets are not written to
  `.env`, browser storage, logs, or the database.
- **The backend binds `127.0.0.1` only**, on a dynamically chosen free port. It
  is not exposed to your LAN or the internet, and the launcher opens your
  default browser to it. Do not port-forward, reverse-proxy, or otherwise expose
  this port. If you need to reach it from another machine, do not.
- **Installations are not code-signed.** Windows SmartScreen will show an
  **Unknown publisher** warning. This is expected for a beta. Do not disable
  SmartScreen. Instead, verify your download against the published
  `SHA256SUMS.txt` and confirm the hashes match.
- **AI inference is not local in the Quick edition.** Prompts and context are
  sent to the cloud provider you configured (OpenRouter or OpenAI). The app is
  local; the inference is not. See [docs/privacy.md](docs/privacy.md).
- **No OMOS-owned API keys are shipped.** You bring your own provider
  credentials, and the app runs with none of its own.

## Security-relevant documentation

- [docs/privacy.md](docs/privacy.md) — what stays local, what leaves the machine
- [docs/user-data-and-backup.md](docs/user-data-and-backup.md) — where data and
  credentials live on disk
- [docs/troubleshooting.md](docs/troubleshooting.md) — if you hit an error
