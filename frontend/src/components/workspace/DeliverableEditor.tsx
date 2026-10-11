import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  ApiRequestError,
  saveDownloadedFile,
  DELIVERABLE_TYPES,
  type DeliverableStatus,
  type DeliverableType,
  type SpaCampaignDeliverable,
  type SpaDeliverableRevision,
} from "../../api/client";
import { isCurrentWorkspaceSelection } from "../../routes/workspaceSelection";
import StatusBadge from "../chrome/StatusBadge";
import {
  canSubmitDraft,
  deliverableStatusLabel,
  deliverableTypeLabel,
  draftFromDeliverable,
  draftIsDirty,
  emptyDraft,
  nextDeliverableStatus,
  normalizeDeliverableList,
  normalizeRevisionList,
  summarizeRevision,
  type DeliverableDraft,
} from "./deliverables";

/**
 * DEV-032 W3 — the campaign deliverable workspace.
 *
 * Everything it shows or sends is namespaced by BOTH `projectId` and
 * `campaignId`, and every async continuation re-checks the selection epoch
 * before touching state, so a user who switches client mid-flight never sees
 * the previous client's copy land in the new client's page.
 *
 * Editorial status is INTERNAL. DRAFT → IN_REVIEW → APPROVED records that a
 * human signed the copy off inside OMOS; nothing here publishes, schedules,
 * posts, or spends, and the copy says so plainly rather than implying a
 * distribution step the product does not have.
 */

type Mode = "create" | "edit";

export default function DeliverableEditor({
  projectId,
  campaignId,
}: {
  projectId: string | null;
  campaignId: string;
}) {
  const selectedProjectId = useRef(projectId);
  selectedProjectId.current = projectId;
  const selectedCampaignId = useRef(campaignId);
  selectedCampaignId.current = campaignId;
  const requestGeneration = useRef(0);

  const [items, setItems] = useState<SpaCampaignDeliverable[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selectedId;
  const [mode, setMode] = useState<Mode | null>(null);
  const [draft, setDraft] = useState<DeliverableDraft>(emptyDraft());
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [transitioning, setTransitioning] = useState<DeliverableStatus | null>(null);
  const [exporting, setExporting] = useState<"md" | "zip" | null>(null);
  const [history, setHistory] = useState<SpaDeliverableRevision[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);

  // Keep async continuations aware of selection changes immediately, before
  // React commits the next render.
  function chooseSelectedId(id: string | null) {
    selectedIdRef.current = id;
    setSelectedId(id);
  }

  // Selection scope. `scope` is null whenever the workspace has no project:
  // with no project there is no scope to be correct about, so nothing loads.
  const scope = useMemo(
    () => (projectId ? { projectId, campaignId } : null),
    [projectId, campaignId],
  );

  useEffect(() => {
    const token = ++requestGeneration.current;
    setItems([]);
    setError("");
    setNotice("");
    chooseSelectedId(null);
    setMode(null);
    setDraft(emptyDraft());
    setDirty(false);
    setSaving(false);
    setTransitioning(null);
    setExporting(null);
    setHistory([]);
    setHistoryOpen(false);
    setHistoryLoading(false);
    if (!scope) return;
    const isCurrent = () =>
      isCurrentWorkspaceSelection(scope.projectId, token, selectedProjectId.current, requestGeneration.current);
    setLoading(true);
    api
      .getCampaignDeliverables(scope.projectId, scope.campaignId)
      .then((rows) => {
        if (!isCurrent()) return;
        const scoped = normalizeDeliverableList(rows, scope);
        setItems(scoped);
        chooseSelectedId(scoped[0]?.id ?? null);
      })
      .catch((e: unknown) => {
        if (!isCurrent()) return;
        setError(e instanceof Error ? e.message : "Deliverables could not be loaded.");
      })
      .finally(() => {
        if (isCurrent()) setLoading(false);
      });
    return () => {
      if (requestGeneration.current === token) requestGeneration.current++;
    };
  }, [scope]);

  const selected = useMemo(
    () => items.find((item) => item.id === selectedId) ?? null,
    [items, selectedId],
  );

  /** True while the scope this effect captured is still the live selection. */
  const scopedNow = (): boolean => {
    if (!scope) return false;
    return selectedProjectId.current === scope.projectId && selectedCampaignId.current === scope.campaignId;
  };

  function startEdit() {
    if (!selected) return;
    setDraft(draftFromDeliverable(selected));
    setMode("edit");
    setNotice("");
    setError("");
  }

  function startCreate() {
    if (!confirmDiscardDraft()) return;
    setDraft(emptyDraft());
    setDirty(true);
    setMode("create");
    chooseSelectedId(null);
    setHistory([]);
    setHistoryOpen(false);
    setNotice("");
    setError("");
  }

  function cancelEdit() {
    setMode(null);
    setDirty(false);
    if (selected) setDraft(draftFromDeliverable(selected));
    else setDraft(emptyDraft());
  }

  function select(id: string) {
    if (!confirmDiscardDraft()) return;
    chooseSelectedId(id);
    setMode(null);
    setDirty(false);
    setHistory([]);
    setHistoryOpen(false);
    setNotice("");
    setError("");
  }

  function confirmDiscardDraft(): boolean {
    if (mode === null || !dirty) return true;
    const baseline = mode === "create" ? null : selected;
    if (!draftIsDirty(draft, baseline)) return true;
    return window.confirm("Discard unsaved changes and continue?");
  }

  function updateDraft(patch: Partial<DeliverableDraft>) {
    setDraft((prev) => ({ ...prev, ...patch }));
    setDirty(true);
  }

  /** Replace one item in place after a write, without refetching the list. */
  function applySaved(saved: SpaCampaignDeliverable, message: string) {
    setItems((prev) => {
      const next = prev.some((item) => item.id === saved.id)
        ? prev.map((item) => (item.id === saved.id ? saved : item))
        : [saved, ...prev];
      return [...next].sort((a, b) => a.title.localeCompare(b.title));
    });
    chooseSelectedId(saved.id);
    setDraft(draftFromDeliverable(saved));
    setDirty(false);
    setMode(null);
    setNotice(message);
    setError("");
  }

  async function save() {
    if (!scope || saving || !dirty || !canSubmitDraft(draft)) return;
    if (mode === "edit" && !selected) return;
    const editing = mode === "edit";
    const pid = scope.projectId;
    const cid = scope.campaignId;
    const epoch = requestGeneration.current;
    const selectionAtStart = selectedIdRef.current;
    const isCurrent = () =>
      isCurrentWorkspaceSelection(pid, epoch, selectedProjectId.current, requestGeneration.current);
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const body = {
        title: draft.title.trim(),
        platform: draft.platform.trim() || null,
        content_md: draft.content_md,
      };
      const saved = editing && selected
        ? await api.saveCampaignDeliverable(pid, cid, selected.id, {
            expected_version: selected.current_version,
            type: draft.type,
            ...body,
          })
        : await api.createCampaignDeliverable(pid, cid, { type: draft.type, ...body });
      if (!isCurrent() || !scopedNow() || selectedIdRef.current !== selectionAtStart) return;
      const reopened = saved.status === "DRAFT" && selected?.status === "APPROVED";
      applySaved(
        saved,
        reopened
          ? "Saved as a new draft version. This deliverable is back in DRAFT and needs sign-off again."
          : editing
            ? "Saved. A new revision was recorded."
            : "Created. A first revision was recorded.",
      );
    } catch (e) {
      if (!isCurrent() || selectedIdRef.current !== selectionAtStart) return;
      setError(conflictMessage(e, "The deliverable could not be saved."));
    } finally {
      if (isCurrent()) setSaving(false);
    }
  }

  async function transition(target: DeliverableStatus) {
    if (!scope || !selected || transitioning) return;
    const pid = scope.projectId;
    const cid = scope.campaignId;
    const deliverableId = selected.id;
    const epoch = requestGeneration.current;
    const isCurrent = () =>
      isCurrentWorkspaceSelection(pid, epoch, selectedProjectId.current, requestGeneration.current);
    setTransitioning(target);
    setError("");
    setNotice("");
    try {
      const saved = await api.setCampaignDeliverableStatus(pid, cid, deliverableId, {
        expected_version: selected.current_version,
        status: target,
      });
      if (!isCurrent() || !scopedNow() || selectedIdRef.current !== deliverableId) return;
      applySaved(saved, `Status is now ${deliverableStatusLabel(saved.status)}.`);
    } catch (e) {
      if (!isCurrent() || selectedIdRef.current !== deliverableId) return;
      setError(conflictMessage(e, "The status change was rejected."));
    } finally {
      if (isCurrent()) setTransitioning(null);
    }
  }

  async function openHistory() {
    if (!scope || !selected) return;
    const pid = scope.projectId;
    const cid = scope.campaignId;
    const deliverableId = selected.id;
    const epoch = requestGeneration.current;
    const isCurrent = () =>
      isCurrentWorkspaceSelection(pid, epoch, selectedProjectId.current, requestGeneration.current);
    setHistoryOpen(true);
    setHistoryLoading(true);
    try {
      const rows = await api.getCampaignDeliverableHistory(pid, cid, deliverableId);
      if (!isCurrent() || !scopedNow() || selectedIdRef.current !== deliverableId) return;
      setHistory(
        normalizeRevisionList(rows, { projectId: pid, campaignId: cid }, deliverableId),
      );
    } catch (e) {
      if (!isCurrent() || !scopedNow() || selectedIdRef.current !== deliverableId) return;
      setError(e instanceof Error ? e.message : "Revision history could not be loaded.");
    } finally {
      if (isCurrent() && scopedNow() && selectedIdRef.current === deliverableId) {
        setHistoryLoading(false);
      }
    }
  }

  async function exportMarkdown() {
    if (!scope || !selected || exporting) return;
    const pid = scope.projectId;
    const cid = scope.campaignId;
    const epoch = requestGeneration.current;
    const isCurrent = () =>
      isCurrentWorkspaceSelection(pid, epoch, selectedProjectId.current, requestGeneration.current);
    setExporting("md");
    setError("");
    setNotice("");
    try {
      const file = await api.downloadDeliverableMarkdown(pid, cid, selected.id);
      if (!isCurrent() || !scopedNow()) return;
      saveDownloadedFile(file);
      setNotice("Markdown export downloaded.");
    } catch (e) {
      if (!isCurrent()) return;
      setError(e instanceof Error ? e.message : "Markdown export failed.");
    } finally {
      if (isCurrent()) setExporting(null);
    }
  }

  async function exportPackage() {
    if (!scope || exporting) return;
    const pid = scope.projectId;
    const cid = scope.campaignId;
    const epoch = requestGeneration.current;
    const isCurrent = () =>
      isCurrentWorkspaceSelection(pid, epoch, selectedProjectId.current, requestGeneration.current);
    setExporting("zip");
    setError("");
    setNotice("");
    try {
      const file = await api.downloadCampaignDeliverablesPackage(pid, cid);
      if (!isCurrent() || !scopedNow()) return;
      saveDownloadedFile(file);
      setNotice("Campaign package downloaded.");
    } catch (e) {
      if (!isCurrent()) return;
      setError(e instanceof Error ? e.message : "Campaign package export failed.");
    } finally {
      if (isCurrent()) setExporting(null);
    }
  }

  if (!projectId) {
    return (
      <p className="rounded border border-linedefault bg-surface px-4 py-3 text-bodysm text-inksecondary">
        Select a client workspace to work on this campaign&apos;s deliverables. Every deliverable
        read and write is scoped to one client.
      </p>
    );
  }

  const nextStatus = selected ? nextDeliverableStatus(selected.status) : null;
  const showForm = mode !== null;
  const busy = saving || transitioning !== null || exporting !== null;
  // `dirty` records that a field was touched; `draftIsDirty` re-checks the
  // actual bytes. Typing and then undoing back to the saved text leaves the
  // button disabled, so no version number is spent on a no-op save.
  const editableChanged = dirty && draftIsDirty(draft, mode === "create" ? null : selected);

  return (
    <section aria-labelledby="deliverables-heading" className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="deliverables-heading" className="text-title font-semibold text-ink">
            Deliverables
          </h2>
          <p className="mt-1 text-meta text-inksecondary">
            Markdown working documents for this campaign. Status is an internal sign-off
            (draft → in review → approved); it does not publish, schedule, or spend anything.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={startCreate}
            disabled={busy}
            className="rounded-sm bg-accent px-3 py-1.5 text-bodysm font-semibold text-white disabled:opacity-50"
          >
            New deliverable
          </button>
          <button
            type="button"
            onClick={() => void exportPackage()}
            disabled={busy || items.length === 0}
            title={
              items.length === 0
                ? "Create at least one deliverable before exporting a package."
                : "Download every deliverable in this campaign as one ZIP."
            }
            className="rounded-sm border border-linedefault px-3 py-1.5 text-bodysm text-ink disabled:opacity-50"
          >
            {exporting === "zip" ? "Preparing…" : "Export campaign (.zip)"}
          </button>
        </div>
      </div>

      {error && (
        <p role="alert" className="rounded border border-err/40 bg-surface px-3 py-2 text-bodysm text-err">
          {error}
        </p>
      )}
      <p role="status" aria-live="polite" className="min-h-[1.25rem] text-meta text-inksecondary">
        {notice}
      </p>

      {loading ? (
        <p className="text-bodysm text-inksecondary">Loading deliverables…</p>
      ) : items.length === 0 && !showForm ? (
        <div className="rounded border border-linedefault bg-surface px-4 py-8 text-center">
          <p className="text-bodysm font-medium text-ink">No deliverables yet</p>
          <p className="mx-auto mt-1 max-w-md text-bodysm text-inksecondary">
            Create the campaign&apos;s working documents here, or ask the Account Manager to draft
            them from this client&apos;s profile and Knowledge.
          </p>
          <button
            type="button"
            onClick={startCreate}
            className="mt-3 rounded-sm bg-accent px-3 py-1.5 text-bodysm font-semibold text-white"
          >
            New deliverable
          </button>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
          <ul className="grid content-start gap-2">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => select(item.id)}
                  aria-current={item.id === selectedId && !showForm ? "true" : undefined}
                  className={[
                    "w-full rounded border px-3 py-2 text-left",
                    item.id === selectedId
                      ? "border-accent bg-raised"
                      : "border-linedefault bg-surface hover:border-accent/50",
                  ].join(" ")}
                >
                  <span className="block text-bodysm font-medium text-ink">
                    {item.title || "Untitled deliverable"}
                  </span>
                  <span className="mt-1 flex flex-wrap items-center gap-2 text-meta text-inksecondary">
                    <span>{deliverableTypeLabel(item.type)}</span>
                    <span aria-hidden="true">·</span>
                    <span>v{item.current_version}</span>
                    <StatusBadge status={deliverableStatusLabel(item.status)} />
                  </span>
                </button>
              </li>
            ))}
          </ul>

          <div className="rounded border border-linedefault bg-surface p-4">
            {showForm ? (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void save();
                }}
              >
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="grid gap-1 text-bodysm font-medium text-ink">
                    Type
                    <select
                      value={draft.type}
                      onChange={(event) => updateDraft({ type: event.target.value as DeliverableType })}
                      className="rounded-sm border border-linedefault bg-base px-2 py-1.5 font-normal"
                    >
                      {DELIVERABLE_TYPES.map((type) => (
                        <option key={type} value={type}>
                          {deliverableTypeLabel(type)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="grid gap-1 text-bodysm font-medium text-ink">
                    Platform <span className="font-normal text-inksecondary">Optional</span>
                    <input
                      value={draft.platform}
                      onChange={(event) => updateDraft({ platform: event.target.value })}
                      placeholder="e.g. LinkedIn, Instagram"
                      className="rounded-sm border border-linedefault bg-base px-3 py-1.5 font-normal"
                    />
                  </label>
                </div>
                <label className="mt-3 grid gap-1 text-bodysm font-medium text-ink">
                  Title
                  <input
                    value={draft.title}
                    onChange={(event) => updateDraft({ title: event.target.value })}
                    required
                    className="rounded-sm border border-linedefault bg-base px-3 py-1.5 font-normal"
                  />
                </label>
                <label className="mt-3 grid gap-1 text-bodysm font-medium text-ink">
                  Content (Markdown)
                  <textarea
                    value={draft.content_md}
                    onChange={(event) => updateDraft({ content_md: event.target.value })}
                    rows={16}
                    required
                    aria-label="Deliverable Markdown content"
                    className="w-full resize-y rounded-sm border border-linedefault bg-base p-3 font-mono text-bodysm"
                  />
                </label>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="submit"
                    disabled={saving || !editableChanged || !canSubmitDraft(draft)}
                    className="rounded-sm bg-accent px-3 py-1.5 text-bodysm font-semibold text-white disabled:opacity-50"
                  >
                    {saving ? "Saving…" : mode === "create" ? "Create deliverable" : "Save revision"}
                  </button>
                  <button
                    type="button"
                    onClick={cancelEdit}
                    disabled={saving}
                    className="rounded-sm border border-linedefault px-3 py-1.5 text-bodysm text-ink disabled:opacity-50"
                  >
                    Cancel
                  </button>
                  {!editableChanged && mode === "edit" && <span className="text-meta text-inksecondary">No changes to save</span>}
                </div>
              </form>
            ) : selected ? (
              <div>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="text-body font-semibold text-ink">{selected.title || "Untitled deliverable"}</h3>
                    <p className="mt-1 text-meta text-inksecondary">
                      {deliverableTypeLabel(selected.type)}
                      {selected.platform ? ` · ${selected.platform}` : ""} · v{selected.current_version}
                      {selected.updated_at ? ` · updated ${selected.updated_at}` : ""}
                    </p>
                  </div>
                  <StatusBadge status={deliverableStatusLabel(selected.status)} />
                </div>

                <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-sm border border-linedefault bg-base p-3 font-mono text-bodysm text-ink">
                  {selected.content_md || "This deliverable has no content yet."}
                </pre>

                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={startEdit}
                    disabled={busy}
                    className="rounded-sm border border-linedefault px-3 py-1.5 text-bodysm text-ink disabled:opacity-50"
                  >
                    Edit content
                  </button>
                  {nextStatus && (
                    <button
                      type="button"
                      onClick={() => void transition(nextStatus)}
                      disabled={busy}
                      className="rounded-sm border border-accent/50 px-3 py-1.5 text-bodysm text-accenthover disabled:opacity-50"
                    >
                      {transitioning === nextStatus
                        ? "Updating…"
                        : nextStatus === "APPROVED"
                          ? "Approve internally"
                          : "Send to review"}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => void exportMarkdown()}
                    disabled={busy}
                    className="rounded-sm border border-linedefault px-3 py-1.5 text-bodysm text-ink disabled:opacity-50"
                  >
                    {exporting === "md" ? "Preparing…" : "Export (.md)"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void openHistory()}
                    disabled={busy}
                    aria-expanded={historyOpen}
                    className="rounded-sm border border-linedefault px-3 py-1.5 text-bodysm text-ink disabled:opacity-50"
                  >
                    Revision history
                  </button>
                </div>

                <p className="mt-2 text-meta text-inkmuted">
                  {selected.status === "APPROVED"
                    ? "Approved copy is locked to this version. Editing it creates a new draft that needs sign-off again."
                    : "Every save and every status change records an immutable revision you can read below."}
                </p>

                {historyOpen && (
                  <div className="mt-3 rounded-sm border border-linedefault bg-raised p-3">
                    <h4 className="text-bodysm font-semibold text-ink">Revision history</h4>
                    {historyLoading ? (
                      <p className="mt-2 text-meta text-inksecondary">Loading history…</p>
                    ) : history.length === 0 ? (
                      <p className="mt-2 text-meta text-inksecondary">No revisions recorded yet.</p>
                    ) : (
                      <ol className="mt-2 grid gap-2">
                        {history.map((revision) => {
                          const summary = summarizeRevision(revision);
                          return (
                            <li key={revision.revision_id} className="rounded-sm border border-linedefault bg-surface p-2">
                              <p className="text-meta text-inksecondary">
                                v{summary.version} · {summary.operation} · {summary.statusLabel}
                                {summary.createdAt ? ` · ${summary.createdAt}` : ""}
                              </p>
                              {summary.excerpt && (
                                <p className="mt-1 text-bodysm text-ink">{summary.excerpt}</p>
                              )}
                            </li>
                          );
                        })}
                      </ol>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-bodysm text-inksecondary">
                Select a deliverable on the left, or create a new one.
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

/**
 * A 409 is the backend refusing a stale write. Say so plainly and keep the
 * user's text on screen — silently re-saving or re-fetching would either lose
 * the edit or hide that someone else moved first.
 */
function conflictMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiRequestError && error.status === 409) {
    return "This deliverable changed since you opened it, so your save was rejected and nothing was overwritten. Reload to read the newer version, then re-apply your edit.";
  }
  if (error instanceof ApiRequestError && error.status === 404) {
    return "That deliverable is not in this campaign, or this campaign does not belong to the selected client.";
  }
  return error instanceof Error ? error.message : fallback;
}
