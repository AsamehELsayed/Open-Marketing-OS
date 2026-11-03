# DEV-051 storage revalidation

Revalidated the **37 existing DEV-050 candidates only** on 2026-11-03; no repeated
full project audit and no existing-data deletion. Machine-specific exact paths,
sizes, lockfile hashes and process evidence remain in the local DEV-051 run.

| Observation | Decimal GB |
|---|---:|
| Last full readable project observation, 2026-11-02 | 39.397861092 |
| Remeasured 37-candidate upper bound, 2026-11-03 | 13.633216248 |
| Verified deletion-ready candidates | 0 |
| Existing data actually reclaimed | 0 |

The current full project footprint is **not remeasured**. The historical figure
must not be presented as a fresh current scan. Logical readable bytes are not
NTFS allocated or physical VHDX space.

All candidate paths were checked for retained lockfiles/package manifests,
installed/build shape, worktree HEAD/status, tracked files and current executable
or process command-line references. No active exact-path process reference was
observed. This does **not** establish absence of open handles or process working
directories. No dependency tree was reinstalled or treated as clean just because
it was ignored by Git.

Bounded reference checks inspected 457 top-level run artifacts: 25 candidates had
evidence references. 35 candidate-owner HEADs were not ancestors of the local
origin/main snapshot; that is preservation evidence, not proof of abandonment.
Build uniqueness and source/toolchain reproduction remain unresolved. Installed
dependencies have retained lockfiles but local patch/extra-file absence is unproven.
All candidates therefore remain HOLD. **13.633 GB is conditional potential, not
verified-safe savings or reclaimed space.** No exact-path deletion approval exists.

Local manifest ranks paths by risk/size and lists remaining proof. Before cleanup,
confirm owner/nonuse, preserve patches and unique QA/release evidence, then obtain
founder approval for the exact generated child paths. Preserve all source,
unmerged worktrees, databases, vaults, models, original assets and unique evidence.

Growth prevention applied: one source-only DEV-051 worktree, existing Python/MCP
and package caches, no duplicated venv/node_modules, no image pulls/volumes/models,
no custom cleanup daemon. Existing build/npm/CI retention mechanisms remain the
route for future retention. Docker VHDX compaction is not authorized or performed.
