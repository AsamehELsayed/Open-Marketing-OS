# Open Marketing OS

**Your open-source AI marketing team.**

Open Marketing OS (OMOS) is a free Windows app that gives you a dedicated AI
marketing account manager. You describe your business in plain language; it
researches your website and competitors, reviews your social profiles, challenges
your positioning and offers, plans campaigns and experiments, and keeps track of
what actually worked.

It runs on your own machine. Your projects, files and chats stay on your
computer. You bring your own AI provider account.

```
[ Download the installer ]  →  install  →  open  →  connect your AI  →  work
```

---

## Download

[![Windows](https://img.shields.io/badge/platform-Windows%20x64-0078D4?logo=windows&logoColor=white)](https://github.com/<OWNER>/Open-Marketing-OS/releases)
[![License](https://img.shields.io/badge/license-Apache--2.0-0078D4)](./LICENSE)
[![Beta](https://img.shields.io/badge/status-public%20beta-orange)](https://github.com/<OWNER>/Open-Marketing-OS/releases)

### 👉 Normal users: download `OpenMarketingOS-Quick-Setup.exe` from the latest release

<p align="center">
  <a href="https://github.com/<OWNER>/Open-Marketing-OS/releases/latest">
    <img alt="Download Open Marketing OS" src="https://img.shields.io/badge/%F0%9F%93%8D%20Download-latest%20release-0078D4?style=for-the-badge">
  </a>
</p>

> **Please do not download "Source code (zip)" or "Source code (tar.gz)"** unless
> you intend to build from source. Those are for developers. Ordinary users want
> `OpenMarketingOS-Quick-Setup.exe` from the [Releases page][releases].

[releases]: https://github.com/<OWNER>/Open-Marketing-OS/releases

After downloading, verify the file against `SHA256SUMS.txt` from the same release
page. See [Verifying a download](#verifying-a-download).

---

## Choose your version

### ⚡ OMOS Quick — available now

**Fast setup. Runs on almost any Windows PC. This is what you download today.**

- Works on Windows 10 and 11, 64-bit
- Needs 4 GB RAM and an internet connection
- No GPU, no local model, no big download
- **Cloud AI through [OpenRouter](https://openrouter.ai) (recommended) or
  [OpenAI](https://openai.com)** — you supply your own API key
- Your key is stored encrypted on your own machine — never in a config file, and
  never in your browser

### 🔒 OMOS Local — coming in a future beta

Running the marketing AI entirely on your own computer, with no AI API key at
all, is planned. It is **not** part of this beta, and this release deliberately
does not advertise it. Nothing is downloaded, and no multi-gigabyte model is
bundled in the installer.

There is no local model in this release to configure, install or download. The
Settings screen says so plainly rather than offering a control that cannot work.

---

## Getting started

1. Download and run `OpenMarketingOS-Quick-Setup.exe`.
2. On the welcome screen, pick **Cloud AI** (recommended for the beta).
3. Choose **OpenRouter** or **OpenAI** and paste your API key.
4. Press **Test connection**. OMOS verifies the key against the real provider.
5. Create your workspace — business name, website, and what you sell.
6. Start working. OMOS opens with concrete next steps, not an empty dashboard.

You can change provider, add another one, or disconnect entirely later in
**Settings → AI** without reinstalling anything.

---

## What it does

| | |
|---|---|
| **AI account manager** | A chat-based marketing manager that plans, challenges and prioritises — and shows its work |
| **Website research** | Point it at your site and get a real crawl-backed audit, not generic advice |
| **Instagram research** | Reviews a public profile through the provider you configure |
| **Competitor research** | Summarises how competitors position themselves |
| **Positioning and offer critique** | Pressure-tests your messaging and proposes sharper versions |
| **Campaigns and experiments** | Turns strategy into trackable work with success metrics and stop conditions |
| **Approvals** | Nothing is sent or published without your explicit sign-off |
| **Project memory** | Every project keeps its own context, so answers stay relevant and separate |
| **Files and knowledge** | Drop in briefs, decks and notes; OMOS retrieves from them when relevant |
| **Bring your own integrations** | OpenRouter, OpenAI, Apify, Bright Data, Meta, and MCP servers |
| **Real-time streaming** | Responses stream as they are produced |

Every answer is required to cite its sources. When OMOS does not know something,
it says so rather than inventing it.

---

## Screenshots

> Coming with the first public release. Check the
> [release notes][releases] for the current set — all screenshots are captured
> with no API keys, no client data and no personal file paths visible.

---

## Requirements

| | |
|---|---|
| **OS** | Windows 10 or 11, 64-bit |
| **RAM** | 4 GB minimum, 8 GB comfortable |
| **Disk** | ~500 MB |
| **Internet** | Required — for your AI provider, website research and Instagram research |
| **Not required** | Python, Node.js, Git, Docker, a terminal, a code editor, or a GPU |

You do **not** need to install any developer tooling. The installer ships
everything, including the Python runtime and the built web interface.

---

## Verifying a download

This beta is **not code-signed**, so Windows SmartScreen will show an
*"Unknown publisher"* warning. That is expected and honest — we would rather
tell you than have you discover it at a bad moment. Do not disable SmartScreen.

Instead, verify the download:

1. Download `OpenMarketingOS-Quick-Setup.exe` and `SHA256SUMS.txt` from the
   [releases page][releases].
2. In PowerShell:

   ```powershell
   Get-FileHash .\OpenMarketingOS-Quick-Setup.exe -Algorithm SHA256
   ```

3. Compare the hash to the matching line in `SHA256SUMS.txt`.

`release-manifest.json` on the same page records the build commit, build date
and per-artifact checksums.

---

## Privacy

OMOS is local software, but "local" has a precise meaning and it is worth being
straight about the edges:

**Stays on your machine**

- Your projects, chats, files and uploaded knowledge
- Your settings and workspace files
- Encrypted references to your provider credentials

**Leaves your machine**

- **AI requests go to the provider you chose.** In this beta OMOS sends your
  prompts and the relevant project context to OpenRouter or OpenAI to get an
  answer. The app is local; the AI inference is not.
- **Website research** fetches the sites you ask about.
- **Instagram research** contacts the provider you configured for it.

**Never sent, anywhere**

- OMOS has no analytics and no telemetry. Model usage, timing and cost are
  recorded **locally**, in your own database, so you can see your own spend.
- Your API keys are never written to a `.env` file, never placed in browser
  storage, and never stored in plain text. They are encrypted with Windows
  DPAPI, bound to your Windows user account.
- OMOS ships no API keys of its own. Every credential belongs to you.

Read the full [privacy statement](./docs/privacy.md).

---

## Where your data lives

Everything OMOS owns for you is in one folder:

```
%LOCALAPPDATA%\OpenMarketingOS\
```

Your projects, database, uploads, knowledge and logs are there — **not** in the
installation folder. That is why updating or uninstalling OMOS never costs you
your work, and why the portable ZIP does not scatter files across your
Downloads folder.

- **Backup and restore:** [docs/user-data-and-backup.md](./docs/user-data-and-backup.md)
- **Troubleshooting:** [docs/troubleshooting.md](./docs/troubleshooting.md)

Uninstalling removes the application only. Your data and credentials are left
alone, on purpose.

---

## Contributing

Development setup is documented separately in **[CONTRIBUTING.md](./CONTRIBUTING.md)** —
that document is for developers and is *not* how you install OMOS as a user.

---

## Architecture

For the curious and for contributors — deliberately below the user
documentation, because most people installing a marketing tool do not care how
it is built internally.

A Python [FastAPI](https://fastapi.tiangolo.com/) backend orchestrates each turn
through [LangGraph](https://langchain-ai.github.io/langgraph/): it assembles
labeled context, gates retrieval, classifies intent, executes tools when the
request genuinely needs them, and calls the model. Project memory and retrieval
run on SQLite (with optional hybrid vector search), scoped per project. A React
interface is served by the same process, which listens only on `127.0.0.1` and
is opened in your default browser.

More detail in [docs/architecture.md](./docs/architecture.md) and
[docs/distribution/windows-packaging-decision.md](./docs/distribution/windows-packaging-decision.md).

---

## Security

Please read **[SECURITY.md](./SECURITY.md)** before reporting anything
security-sensitive. Do not open a public issue for an unfixed vulnerability, and
never paste API keys into an issue, a log or a screenshot.

---

## License

**Apache License 2.0** — see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).

You can use it commercially, self-host it, and change it. If you redistribute
it, keep the license and the notice, and say what you changed.
