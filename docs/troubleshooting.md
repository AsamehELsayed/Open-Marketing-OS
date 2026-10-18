# Troubleshooting

Problems, causes, fixes. Find your symptom in the table and jump to the section
for detail.

> **A note on error messages.** Every error OMOS shows you is written in plain
> language and cleaned of anything sensitive. **If you see a raw Python
> traceback, that is a bug in our error handling — please report it** using
> [Reporting a bug](#reporting-a-bug).

## Quick reference

| What you see | Likely cause | Fix |
| --- | --- | --- |
| SmartScreen: "Unknown publisher" | Beta is not code-signed | [Verify the download](#smartscreen-unknown-publisher) |
| Not sure which installer to use | Two editions ship | [Which installer did I install?](#which-installer-did-i-install) |
| Nothing happens when I launch it | Install or runtime problem | [The app will not start](#the-app-will-not-start) |
| "Port already in use" / startup failure | The local backend could not bind | [Startup fails with a port error](#startup-fails-with-a-port-error) |
| The browser never opened | Browser launch was blocked | [The browser did not open](#the-browser-did-not-open) |
| The setup wizard keeps starting over | No working cloud credential | [The first-run wizard loops](#the-first-run-wizard-loops-back-to-setup) |
| "OpenRouter isn't connected" | Credential missing or not tested | [The AI provider is not connected](#the-ai-provider-is-not-connected) |
| "OpenAI isn't connected" | Credential missing or not tested | [The AI provider is not connected](#the-ai-provider-is-not-connected) |
| 401 / authentication error | Key wrong, revoked, or expired | [A provider returns 401 or an auth error](#a-provider-returns-401-or-an-authentication-error) |
| Requests time out | VPN, proxy, or firewall blocking the provider | [Requests time out](#requests-time-out) |
| Website research fails | Site blocked, private, or you are offline | [Website research fails](#website-research-fails) |
| Instagram research fails | No Apify/Bright Data key, or a private profile | [Instagram research fails](#instagram-research-fails) |
| Search returns nothing | Index is empty or out of date | [Search returns no results](#search-returns-no-results) |
| The app feels slow | Large project or first-run index build | [The app feels slow](#the-app-feels-slow) |
| I need detail | — | [Find the logs](#find-the-logs) |

---

## Which installer did I install?

A release contains **two** installers. Both install OMOS to the same folder, so
only one of them is installed at a time — and **Settings → About** tells you
which edition you are running.

| File | Edition | What it needs |
| --- | --- | --- |
| `OMOS-Local-v<version>.exe` | **Local** | Nothing to connect. Account-manager inference runs on your PC through the managed llama.cpp runtime after you download and verify the model in Settings. No provider key. |
| `OMOS-OpenRouter-v<version>.exe` | **OpenRouter** | Your own OpenRouter or OpenAI API key. |
| `OpenMarketingOS-Portable.zip` | Portable (optional) | No installer. This archive is a convenience build and may be absent from a release; if it is there, extract it and run `OpenMarketingOS.exe`. |

Replace `<version>` with the version number in the file name. The installed
version appears in **Settings → About**.

## SmartScreen: "Unknown publisher"

**Cause.** OMOS is not code-signed yet. Windows SmartScreen does not recognize
the publisher and warns you before it will run the file. This is expected for a
beta, and it is not a sign that anything is wrong with your copy.

**Fix.** Do **not** disable SmartScreen, and do not add an exclusion for the
downloaded file. Verify what you downloaded instead:

1. Find the `SHA256SUMS.txt` file published alongside the release download.
2. In PowerShell, hash the file you actually downloaded — use the line for the
   edition you chose:

   ```powershell
   Get-ChildItem -File -Path '.\OMOS-Local-v*.exe' | Get-FileHash -Algorithm SHA256
   Get-ChildItem -File -Path '.\OMOS-OpenRouter-v*.exe' | Get-FileHash -Algorithm SHA256
   ```

   Run the command for the edition you downloaded, from the folder containing
   the installer.
3. Compare the hash PowerShell prints with the line for that same file name in
   `SHA256SUMS.txt`.

If they match, your download is the one that was published. If they do not
match, delete the file and download it again. `release-manifest.json` records
the same expected hashes if you would rather not read the text file.

## The app will not start

**Cause.** Usually one of: the install did not finish, security software
quarantined a file, or a required runtime is missing.

**Fix.**

1. Close and reopen it once — a first launch after install can be slow.
2. If your security software shows a quarantine notice, review it. OMOS is not
   code-signed, which is what triggers these prompts.
3. If you used the portable archive (`OpenMarketingOS-Portable.zip`), confirm
   you extracted the whole archive before running it. Running the `.exe` from
   inside the zip will not work.
4. On the Local edition, the first launch after setup needs the model download
   to have finished and verified in **Settings → Local Model**; until then the
   app starts but cannot answer.
5. Check [Find the logs](#find-the-logs) for a clue.

## Startup fails with a port error

**Cause.** The local backend could not start its HTTP server, often with a
message about the port being in use. OMOS picks a free port automatically, so
this usually means something is preventing it from binding at all — security
software blocking the process, or a leftover instance still running.

**Fix.**

1. Close every OMOS window, then check whether the process is still running and
   end it if so.
2. Restart your machine — this clears any leftover instance holding the port.
3. If your antivirus or firewall asks about OMOS, allow it to run on private
   networks and retry. OMOS only ever listens on `127.0.0.1`.
4. Try again.

## The browser did not open

**Cause.** OMOS started the local server but the automatic browser launch did not
happen.

**Fix.** Look at the OMOS launcher window — it displays the local address it
served on. Paste that address into your browser's address bar manually. It looks
like `http://127.0.0.1:<port>`.

Any modern browser works.

## The first-run wizard loops back to setup

**Cause.** In almost every case, the AI route this edition needs is not ready
yet.

- On the **OpenRouter** edition there is no working cloud credential. It needs an
  AI provider to talk to, and it keeps returning you to setup until one is
  connected and working.
- On the **Local** edition the pinned model has not finished downloading, or has
  not passed checksum verification and activation.

**Fix (OpenRouter edition).**

1. In **Settings → AI**, connect your provider — **OpenRouter** (recommended) or
   **OpenAI**.
2. Press **Test Connection** and wait for it to succeed before leaving the
   screen.
3. If the test fails, fix the problem first (see the 401 and timeout sections
   below), then return to the wizard.

**Fix (Local edition).**

1. In **Settings → Local Model**, select **Download Model** and let it finish.
   The download does not start on its own.
2. Wait for the files to verify and the model to become active. Do not interrupt
   OMOS while activation is running.
3. Return to the wizard.

If you are not sure which edition you installed, see
[Which installer did I install?](#which-installer-did-i-install).

## The AI provider is not connected

**Symptoms.** "OpenRouter isn't connected." or "OpenAI isn't connected."

**Cause.** The credential is not stored, was cleared, or was stored but never
tested successfully.

**Fix.**

1. Open **Settings → AI**.
2. Press **Connect** for your provider and enter your key.
3. Press **Test Connection**.
4. Only leave the screen once the test reports success.

Your key is stored in the Credential Vault, encrypted with Windows DPAPI. If you
restore a backup on a different machine or a different Windows user, stored
credentials will not decrypt — reconnect them. See
[user-data-and-backup.md](user-data-and-backup.md).

## A provider returns 401 or an authentication error

**Cause.** The provider rejected the key. Either it was typed incorrectly, it was
revoked or rotated at the provider, or the account it belongs to is inactive.

**Fix.**

1. Go to your provider's dashboard and confirm the key is still active.
2. Copy the key again and paste it fresh into **Settings → AI → Connect**.
3. Press **Test Connection**.

If a key was ever pasted somewhere public, revoke it at the provider first. See
[SECURITY.md](../SECURITY.md).

## Requests time out

**Cause.** The request to your AI provider never completed. Usually a VPN,
corporate proxy, or firewall is blocking the connection.

**Fix.**

1. If you are on a VPN, try turning it off and retrying.
2. Check whether your firewall or network allows outbound HTTPS.
3. If you are behind a proxy, configure it for the app, or test the connection
   from a network that does not require one.
4. Confirm your machine has working internet by opening a normal web page.

## Website research fails

**Cause.** Usually one of these:

- The site is **private or behind a login**, so there is nothing public to read.
- The URL points at a **local or loopback address** (such as `localhost` or
  `127.0.0.1`) or an internal network address OMOS cannot reach.
- The site **blocks automated requests** and refused the fetch.
- You have **no internet connection** at the time.

**Fix.** Check the URL. Use a public `https://` address. If the site is behind a
login or actively blocks automated access, OMOS cannot read it — save the page as
a file, drop it into the project's files, and ask your question about that
instead.

## Instagram research fails

**Cause.** Instagram research needs a third-party data provider, and OMOS does
not ship provider credentials of its own.

**Fix.**

1. Go to **Settings → Integrations** and add your own **Apify** or **Bright
   Data** key. Without one, Instagram research cannot run.
2. Confirm the profile is **public**. Private profiles cannot be researched, and
   the app will tell you so.
3. Make sure you have internet access and that your provider account has credit
   or an active plan — research consumes their quota.

## Search returns no results

**Cause.** The project has nothing indexed yet, or the index is older than the
files you added.

**Fix.**

1. Open **Settings** and re-index the project. The search index is built from
   your files and project memory; a fresh project starts empty, which is why
   search returns nothing before the first index build.
2. Wait for the index build to finish — on a large project it takes a moment.
3. Try again with fewer, more specific words.

If Chroma is unavailable, search falls back to keyword-only mode and still
works; it will be less thorough.

## The app feels slow

**Cause.** Almost always the size of the project, or work happening in the
background.

**Fix.**

- **First launch in a project** builds the search index. That is the slow part,
  and it does not repeat.
- **Large projects** take longer to search and to load context. Consider
  splitting unrelated work into separate projects — they are isolated from each
  other on purpose.
- **Streaming is on by default.** If you are on a long task, watch the response
  stream in rather than assuming it has hung.
- **Background research** (website or Instagram) runs against a third-party
  provider and takes as long as the provider takes.

## Find the logs

Logs are plain files in:

```text
%LOCALAPPDATA%\OpenMarketingOS\logs\
```

Press `Win + R`, paste `%LOCALAPPDATA%\OpenMarketingOS\logs`, and press Enter.
The newest file is the current session.

Logs are useful when reporting a bug. Read them before you delete them, and never
post a file containing credentials.

## Reporting a bug

Open an **Issue** on the repository. Include:

- the OMOS version **and edition** (Settings → About),
- your Windows version,
- the exact installer file name you downloaded, for example
  `OMOS-Local-v<version>.exe`,
- what you did, what you expected, and what happened instead,
- the relevant log excerpt.

**Never paste an API key, token, or password into an Issue, a log excerpt, or a
screenshot.** Redact it first, and rotate it at the provider if you pasted it by
accident.

If you found a security problem, do not open a public Issue — follow
[SECURITY.md](../SECURITY.md) instead.
