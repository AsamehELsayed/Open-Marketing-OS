# DEV-050 storage findings

Direct read-only audit on 2026-11-02; no preexisting file/resource cleanup.
Full internal evidence includes per-directory/file tables, access-error inventory,
external worktrees, shared caches and conditional path-specific recommendations.
Machine-specific raw inventories stay local rather than enter the public PR.

| Measurement | Decimal GB |
|---|---:|
| First readable project logical bytes | 39.386 |
| Follow-up readable project logical bytes | 39.394 |
| Other registered worktrees outside project | 18.189 |
| Shared pip cache | 0.668 |
| Shared npm cache | 9.617 |
| Shared Playwright binaries | 2.029 |
| Baseline Docker WSL VHDX logical length | 5.820 |
| Actual reclaimed preexisting project data | 0 |

Project scans excluded 85 access failures and 209 reparse points. These are
readable logical file lengths/lower bounds, not NTFS allocated bytes. External
Docker/cache figures are not part of the project total; Docker VHDX and engine
image/volume accounting overlap and must not be added together. DEV-050 image
downloads are new external storage, measured separately at closeout.

After scoped teardown, Docker engine accounting returned to baseline: 2 images
(1.485 GB), 2 unrelated stopped containers and 6 retained volumes (474.2 MB).
All six image references fetched for DEV-050 were verified absent. The shared
WSL VHDX files still measured **15.014 GB logical length**, versus **5.820 GB**
before provisioning. Host free space measured **6.278 GB** at that check.
No physical host-space reclamation is claimed. Manual shared-VM compaction was
not performed; it needs a separately reviewed maintenance/backup procedure.
Do not delete the VHDX or prune retained volumes to address this growth.

Main consumers are retained model/test/build copies: DEV-029 13.499 GB,
DEV-015 7.033 GB, DEV-028 4.253 GB, nested .claude worktrees 6.879 GB,
development/worktrees 5.487 GB. These parent totals overlap the run breakdown;
they are not a list to sum. Two separately stored Qwen model trees total about
9.366 GB. Their names/version/sizes match; full hash equality/use is unverified.
Git itself is approximately 0.205 GB. Unreachable Git objects are preserved.

49 registered worktrees were inspected; 21 had changes. 32 worktree heads are
not ancestors of the PR #19 baseline, which is not a proof of abandonment or a
complete merge assessment. No branches/worktrees are approved for removal.

Disjoint build/node_modules review candidates total up to **13.634 GB**. A
conditional scenario removing every candidate would leave about **25.760 GB**.
This is not verified-safe savings: require exact source/lockfile/toolchain,
ownership/nonuse checks, preservation of unique evidence/manifests and explicit
path-specific approval first. Dist/installers, models, Python environments,
databases, vaults, original assets, source and uncertain files are excluded.

Prevention: reuse existing package caches and pinned toolchains, prefer pnpm's
content-addressed store, avoid an environment per evidence run, reuse a canonical
model cache where test isolation allows, configure existing artifact/test/log
retention, and record owned temporary resources at closeout. Existing .gitignore
already excludes most dependencies/build/state; ignoring does not limit disk use.
No custom cleanup daemon or blanket deletion policy is introduced.
