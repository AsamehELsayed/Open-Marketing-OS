# Where your data lives, and how to back it up

This is the definitive guide to where Open Marketing OS stores everything on
your Windows machine, and how to copy it somewhere safe.

## The one folder that matters

Everything OMOS owns lives in a single folder:

```text
%LOCALAPPDATA%\OpenMarketingOS\
```

On a typical Windows install, `%LOCALAPPDATA%` is:

```text
C:\Users\<your-username>\AppData\Local
```

So the folder is usually:

```text
C:\Users\<your-username>\AppData\Local\OpenMarketingOS
```

To open it without typing a path: press `Win + R`, paste
`%LOCALAPPDATA%\OpenMarketingOS`, and press Enter.

## What is inside

| Path | What lives there |
| --- | --- |
| `%LOCALAPPDATA%\OpenMarketingOS\` | The workspace root. Everything below is inside it. |
| `%LOCALAPPDATA%\OpenMarketingOS\data\` | The data directory. |
| `%LOCALAPPDATA%\OpenMarketingOS\data\marketing.db` | The database: your projects, conversations, messages, tasks, campaigns, approvals, experiments, learnings, settings, and cost/usage records. This is the file that matters most. |
| `%LOCALAPPDATA%\OpenMarketingOS\data\chroma\` | The optional vector search index. Optional only in the sense that it can be rebuilt — if it is missing, the app falls back to keyword search. |
| `%LOCALAPPDATA%\OpenMarketingOS\credentials\` | Encrypted credential files, one per secret, protected with Windows DPAPI. Never delete these by accident; see the restore warning below. |
| `%LOCALAPPDATA%\OpenMarketingOS\logs\` | Application logs. Safe to delete; useful when reporting a bug. |
| `%LOCALAPPDATA%\OpenMarketingOS\data\backups\` | Backups the app itself creates. |

## Why data is not in the app folder

The application files and your data are deliberately kept apart.

Application files live wherever you installed them (for example
`C:\Program Files\OpenMarketingOS\`). Your data lives in `%LOCALAPPDATA%`.

This means:

- **Updating OMOS never touches your data.** Installing a new version replaces
  application files only. Your projects, database, and credentials are
  untouched.
- **Uninstalling OMOS does not delete your data.** The uninstaller removes
  application files only — your projects, the database, and credentials stay
  where they are. Deleting user data is a separate, deliberate action that you
  have to take yourself.
- **Reinstalling is safe.** Install again, launch, and your projects are still
  there.
- **Nothing outside your machine can reach it.** OMOS's local server listens on
  `127.0.0.1` only, so it is not reachable from your LAN or the internet. Do not
  port-forward or reverse-proxy it.

## How to back up

**Close OMOS first.** Copying the database while the app is running can produce
an inconsistent copy. The app may be running in the background after you close
the browser tab — quit it from the tray, or confirm the process has exited.

Then copy the whole folder. In File Explorer: open the path from the section
above, select everything inside, and copy it somewhere safe — an external drive,
a network share, or a cloud-synced folder.

### One-liner

This copies the folder to a timestamped sibling folder, so you keep every
backup instead of overwriting the last one:

```powershell
$src = "$env:LOCALAPPDATA\OpenMarketingOS"; $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'; Copy-Item -Path $src -Destination "$env:USERPROFILE\Documents\OMOS-backup-$stamp" -Recurse -Force
```

Check the result before you delete anything.

## How to restore

1. Close OMOS.
2. Copy your backup folder back over
   `%LOCALAPPDATA%\OpenMarketingOS\`, replacing what is there.
3. Launch OMOS.

Your projects, conversations, files, and search index come back.

### Read this before you restore on a different machine

**Credentials are encrypted with Windows DPAPI, which is bound to the Windows
user account that created them.** The encryption key belongs to that one user
profile on that one machine.

What this means in practice:

- **Restoring as the same Windows user on the same machine** — credentials work.
- **Restoring on a different machine** — the database and projects restore fine,
  but **your credentials will not work**. The app cannot decrypt them. You will
  need to reconnect each one by hand in Settings.
- **Restoring as a different Windows user** — same result. The projects restore;
  the credentials do not.

This is intentional. It means a stolen backup file is not a stolen credential.

If credentials fail after a restore, the fix is simple: open **Settings**, reconnect
your provider accounts, and test the connection. Nothing else is lost.

## There is no automatic cloud backup

OMOS does not upload your data anywhere, and there is no cloud sync. That is
part of the deal, and it is also your responsibility: **copy the folder
yourself**, on a schedule you choose, to wherever you trust.

If a full copy is too heavy, the two things worth protecting are
`%LOCALAPPDATA%\OpenMarketingOS\data\marketing.db` and
`%LOCALAPPDATA%\OpenMarketingOS\credentials\`. The Chroma index and the logs can
be discarded — the index is rebuilt by re-indexing in Settings.
