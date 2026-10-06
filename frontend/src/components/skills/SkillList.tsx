import { useMemo, useState } from "react";
import { useSkillRegistry } from "../../hooks/useSkillRegistry";
import type { SpaSkillRecord } from "../../api/client";

/**
 * DEV-008-SKILLS-OPS â€” the marketing playbook list for Settings â†’ Skills.
 *
 * Renders the registry the server measured, and nothing more:
 * - the counts (`total` / `valid` / `missing` / `invalid`) are the loader's
 *   measured numbers for this request, printed as received. There is no
 *   expected-size constant here, so re-pinning the library changes the numbers
 *   without touching this file.
 * - the pin itself (upstream, version, commit, licence, `library_checksum`) is
 *   shown so a reader can tell WHICH library is on disk, not just that one is.
 * - a disabled playbook stays listed and visibly disabled rather than
 *   disappearing. A hidden row would make "I turned it off" indistinguishable
 *   from "it is gone", and only the first is true.
 * - `unresolved_related` is rendered as a visible note, not swallowed: a
 *   cross-reference that does not resolve is a real defect in the pinned
 *   library and the operator should see which ones.
 *
 * The manifest view (`GET /api/skills/manifest`) is read through the same
 * client, and the pin line below falls back to the registry's own
 * `hash_scheme` / `library_checksum` when the manifest endpoint is not
 * deployed.
 *
 * Tokens only (`tokens.css` / `tailwind.config.ts`), single fixed dark theme,
 * no new colour literals, no light mode.
 */

function categoryLabel(category: string): string {
  const c = category.trim();
  if (!c) return "Uncategorised";
  return c.charAt(0).toUpperCase() + c.slice(1);
}

function shortHash(hash: string): string {
  const h = hash.trim();
  if (!h) return "unknown";
  return h.length > 12 ? `${h.slice(0, 12)}â€¦` : h;
}

function RecordRow({
  record,
  busy,
  onToggle,
}: {
  record: SpaSkillRecord;
  busy: boolean;
  onToggle: (skillId: string, enabled: boolean) => void;
}) {
  const unresolved = record.unresolved_related ?? [];
  return (
    <li className="border-t border-linesubtle px-4 py-3 first:border-t-0">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-bodysm font-medium text-ink">{record.name || record.skill_id}</span>
            <span className="rounded-sm border border-linedefault bg-elevated px-1.5 py-0.5 text-meta text-inksecondary">
              {categoryLabel(record.category)}
            </span>
            <span className="text-meta text-inkmuted">v{record.version}</span>
            {record.enabled ? null : (
              <span className="rounded-sm border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-meta text-warn">
                Disabled
              </span>
            )}
            {record.validation_status && record.validation_status !== "valid" ? (
              <span className="rounded-sm border border-err/40 bg-err/10 px-1.5 py-0.5 text-meta text-err">
                {record.validation_status}
                {record.validation_reason ? ` · ${record.validation_reason}` : ""}
              </span>
            ) : null}
          </div>
          <p className="mt-1 break-words text-meta text-inksecondary">
            {record.description}
            {record.description_truncated ? "â€¦" : ""}
          </p>
          <p className="mt-1 text-meta text-inkmuted">
            {record.path} · {record.eval_count} eval{record.eval_count === 1 ? "" : "s"} ·{" "}
            {record.triggers?.length ?? 0} trigger
            {(record.triggers?.length ?? 0) === 1 ? "" : "s"} · {shortHash(record.checksum)}
          </p>
          {unresolved.length > 0 ? (
            <p className="mt-1 text-meta text-warn">
              Unresolved cross-references: {unresolved.join(", ")}
            </p>
          ) : null}
        </div>
        <label className="flex shrink-0 cursor-pointer items-center gap-2 text-meta text-inksecondary">
          <input
            type="checkbox"
            checked={record.enabled}
            disabled={busy}
            onChange={(e) => onToggle(record.skill_id, e.target.checked)}
            className="rounded border-linedefault"
            aria-label={`${record.enabled ? "Disable" : "Enable"} ${record.skill_id}`}
          />
          <span>{busy ? "Savingâ€¦" : record.enabled ? "Enabled" : "Off"}</span>
        </label>
      </div>
    </li>
  );
}

export default function SkillList() {
  const { data, loading, error, savingSkillId, reload, setEnabled } = useSkillRegistry();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");

  const categories = useMemo(() => {
    const found = new Set<string>();
    for (const record of data?.skills ?? []) {
      if (record.category) found.add(record.category);
    }
    return [...found].sort();
  }, [data]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data?.skills ?? []).filter((record) => {
      if (category && record.category !== category) return false;
      if (!q) return true;
      return (
        record.skill_id.toLowerCase().includes(q) ||
        record.name.toLowerCase().includes(q) ||
        record.description.toLowerCase().includes(q)
      );
    });
  }, [data, query, category]);

  if (loading && !data) {
    return (
      <section className="rounded border border-linedefault bg-raised p-5">
        <h3 className="text-h3 font-semibold text-ink">Marketing Skills</h3>
        <p className="mt-1 text-bodysm text-inksecondary">Loading the pinned playbook libraryâ€¦</p>
      </section>
    );
  }

  return (
    <div className="space-y-4">
      <section className="rounded border border-linedefault bg-raised p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-h3 font-semibold text-ink">Marketing Skills</h3>
          <button
            type="button"
            onClick={reload}
            className="rounded border border-linedefault px-3 py-1 text-meta text-ink hover:bg-elevated"
          >
            Reload
          </button>
        </div>
        <p className="mt-1 text-bodysm text-inksecondary">
          Playbooks the Account Manager may load for a turn. Disabling one removes it from
          routing on the next request; it stays listed here so the change is visible and
          reversible.
        </p>

        {error ? (
          <div className="mt-3 rounded border border-err/40 bg-err/10 px-3 py-2 text-bodysm text-err">
            {error}
          </div>
        ) : null}

        {data ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-4">
            <div className="rounded border border-linedefault bg-elevated p-3">
              <span className="block text-meta text-inkmuted">Playbooks</span>
              <span className="mt-0.5 block text-bodysm font-bold text-ink">{data.total}</span>
            </div>
            <div className="rounded border border-linedefault bg-elevated p-3">
              <span className="block text-meta text-inkmuted">Valid</span>
              <span className="mt-0.5 block text-bodysm font-bold text-ok">{data.valid}</span>
            </div>
            <div className="rounded border border-linedefault bg-elevated p-3">
              <span className="block text-meta text-inkmuted">Missing</span>
              <span className="mt-0.5 block text-bodysm font-bold text-warn">{data.missing}</span>
            </div>
            <div className="rounded border border-linedefault bg-elevated p-3">
              <span className="block text-meta text-inkmuted">Invalid</span>
              <span className="mt-0.5 block text-bodysm font-bold text-err">
                {data.invalid.length}
              </span>
            </div>
          </div>
        ) : null}

        {data ? (
          <div className="mt-3 space-y-1 text-meta text-inkmuted">
            <p>
              Pin · {data.library_root} · {data.hash_scheme} ·{" "}
              {shortHash(data.library_checksum)} · {data.file_count} files
            </p>
            <p>{data.summary}</p>
            {data.invalid.length > 0 ? (
              <ul className="list-disc pl-5 text-err">
                {data.invalid.map((row) => (
                  <li key={row.skill_id}>
                    {row.skill_id} · {row.reason_code}
                  </li>
                ))}
              </ul>
            ) : null}
            {data.warnings.length > 0 ? (
              <ul className="list-disc pl-5 text-warn">
                {data.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </section>

      {data && data.skills.length > 0 ? (
        <section className="rounded border border-linedefault bg-raised p-5">
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex-1 text-bodysm text-ink">
              Search
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter by name, id, or description"
                className="mt-1 w-full rounded border border-linedefault bg-elevated px-3 py-2 text-bodysm text-ink focus:border-accent focus:outline-none"
              />
            </label>
            <label className="text-bodysm text-ink">
              Category
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="mt-1 w-full rounded border border-linedefault bg-elevated px-3 py-2 text-bodysm text-ink focus:border-accent focus:outline-none"
              >
                <option value="">All categories</option>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {categoryLabel(c)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="mt-2 text-meta text-inkmuted">
            Showing {visible.length} of {data.skills.length} loaded playbooks.
          </p>
          <ul className="mt-3 rounded border border-linedefault">
            {visible.map((record) => (
              <RecordRow
                key={record.skill_id}
                record={record}
                busy={savingSkillId === record.skill_id}
                onToggle={(id, enabled) => {
                  void setEnabled(id, enabled);
                }}
              />
            ))}
            {visible.length === 0 ? (
              <li className="px-4 py-3 text-meta text-inkmuted">
                No playbook matches this filter.
              </li>
            ) : null}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
