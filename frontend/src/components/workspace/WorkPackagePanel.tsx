import { useEffect, useState } from "react";
import { api, saveDownloadedFile, type CampaignWorkPackage } from "../../api/client";
import StatusBadge from "../chrome/StatusBadge";

/**
 * Owner-readable campaign work package: goal, audience, offer, channels,
 * deliverables with approval state, and where each fact came from. Evidence
 * classes are kept apart so AI suggestions are never shown as market facts.
 */
export const GOAL_PRESETS = [
  "Get more customer inquiries",
  "Promote a new product",
  "Increase awareness of an existing offer",
  "Prepare a two-week content campaign",
];

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3 py-1.5 text-bodysm">
      <dt className="w-36 shrink-0 text-inksecondary">{label}</dt>
      <dd className="text-ink">{value || <span className="text-inkmuted">Not set yet</span>}</dd>
    </div>
  );
}

export default function WorkPackagePanel({ projectId, campaignId }: { projectId: string; campaignId: string }) {
  const [pkg, setPkg] = useState<CampaignWorkPackage | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [goal, setGoal] = useState("");
  const [goalSaved, setGoalSaved] = useState("");

  useEffect(() => {
    let cancelled = false;
    setPkg(null);
    setError("");
    api
      .getCampaignWorkPackage(projectId, campaignId)
      .then((d) => !cancelled && setPkg(d))
      .catch(() => !cancelled && setError("We couldn’t load the campaign summary. Please refresh to try again."));
    return () => {
      cancelled = true;
    };
  }, [projectId, campaignId, goalSaved]);

  async function saveGoal(text: string) {
    const clean = text.trim();
    if (!clean) return;
    setBusy(true);
    try {
      await api.saveProjectGoal(projectId, clean);
      setGoalSaved(clean);
      setGoal("");
    } catch {
      setError("We couldn’t save your goal. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function downloadApproved() {
    setBusy(true);
    setError("");
    try {
      saveDownloadedFile(await api.downloadApprovedCampaignPackage(projectId, campaignId));
    } catch {
      setError("The package couldn’t be downloaded. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (error && !pkg) return <p role="alert" className="mb-4 text-bodysm text-err">{error}</p>;
  if (!pkg) return <p className="mb-4 text-bodysm text-inksecondary">Loading campaign summary…</p>;

  const ev = pkg.evidence;
  const a = pkg.approval;
  const provided = Object.entries(ev.user_provided);
  return (
    <section aria-label="Campaign work package" className="mb-6 space-y-4">
      <div className="rounded border border-linedefault bg-surface px-4 py-3">
        <h2 className="text-body font-semibold text-ink">Your goal</h2>
        <p className="mt-1 text-bodysm text-ink">{pkg.goal || "You haven’t said what you want to achieve yet."}</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {GOAL_PRESETS.map((p) => (
            <button key={p} type="button" disabled={busy} onClick={() => void saveGoal(p)} className="rounded-sm border border-linedefault px-2.5 py-1 text-meta text-ink hover:bg-elevated disabled:opacity-50">
              {p}
            </button>
          ))}
        </div>
        <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); void saveGoal(goal); }}>
          <input aria-label="Describe your goal" maxLength={500} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Or describe it in your own words" className="flex-1 rounded-sm border border-linedefault bg-base px-3 py-1.5 text-bodysm" />
          <button type="submit" disabled={busy || !goal.trim()} className="rounded-sm bg-accent px-3 py-1.5 text-bodysm font-semibold text-white disabled:opacity-50">Save goal</button>
        </form>
      </div>

      <dl className="divide-y divide-linesubtle rounded border border-linedefault bg-surface px-4 py-2">
        <Row label="Target audience" value={pkg.target_audience} />
        <Row label="Offer" value={pkg.offer} />
        <Row label="Channels" value={pkg.channels.join(", ")} />
        <Row label="Duration" value={pkg.duration} />
      </dl>

      <div className="rounded border border-linedefault bg-surface px-4 py-3">
        <h2 className="text-body font-semibold text-ink">Deliverables</h2>
        {pkg.deliverables.length === 0 ? (
          <p className="mt-1 text-bodysm text-inksecondary">Nothing has been written yet. Ask your Account Manager to draft content for this campaign.</p>
        ) : (
          <ul className="mt-2 divide-y divide-linesubtle">
            {pkg.deliverables.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 py-1.5 text-bodysm">
                <span className="text-ink">{d.type_label}: {d.title}{d.platform ? ` (${d.platform})` : ""}{d.written_by_ai ? " · written by AI" : ""}</span>
                <StatusBadge status={d.status_label} />
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-meta text-inksecondary">{a.approved} of {a.total} approved. Earlier versions are kept when you edit.</p>
        <button type="button" disabled={busy || a.approved === 0} onClick={() => void downloadApproved()} className="mt-2 rounded-sm border border-linedefault px-3 py-1.5 text-bodysm text-ink hover:bg-elevated disabled:opacity-50">
          Download approved package
        </button>
        {a.approved > 0 && !a.ready_to_export && <p className="mt-1 text-meta text-inksecondary">Only approved items are included. The rest are listed as not included.</p>}
      </div>

      <div className="rounded border border-linedefault bg-surface px-4 py-3">
        <h2 className="text-body font-semibold text-ink">Where this comes from</h2>
        <p className={`mt-1 text-bodysm ${ev.research.state === "available" ? "text-ink" : "text-warn"}`}>{ev.research.message}</p>
        <h3 className="mt-3 text-bodysm font-medium text-ink">Told to us by you</h3>
        {provided.length ? <ul className="list-disc pl-5 text-bodysm text-ink">{provided.map(([k, v]) => <li key={k}><span className="text-inksecondary">{k}:</span> {v}</li>)}</ul> : <p className="text-bodysm text-inkmuted">Nothing yet.</p>}
        <h3 className="mt-3 text-bodysm font-medium text-ink">From your own documents</h3>
        {ev.own_documents.length ? <ul className="list-disc pl-5 text-bodysm text-ink">{ev.own_documents.map((d) => <li key={d.label}>{d.label}</li>)}</ul> : <p className="text-bodysm text-inkmuted">None used.</p>}
        <h3 className="mt-3 text-bodysm font-medium text-ink">Checked online</h3>
        {ev.web_sources.length ? <ul className="list-disc pl-5 text-bodysm text-ink">{ev.web_sources.map((s) => <li key={s.url}>{s.url}</li>)}</ul> : <p className="text-bodysm text-inkmuted">No online sources were checked.</p>}
        <h3 className="mt-3 text-bodysm font-medium text-ink">Suggested by AI (ideas to check, not facts)</h3>
        {ev.ai_suggested.length ? <ul className="list-disc pl-5 text-bodysm text-ink">{ev.ai_suggested.map((t) => <li key={t}>{t}</li>)}</ul> : <p className="text-bodysm text-inkmuted">None.</p>}
      </div>

      {pkg.needs_owner_input.length > 0 && (
        <div className="rounded border border-warn/40 bg-surface px-4 py-3">
          <h2 className="text-body font-semibold text-ink">Needs your input</h2>
          <ul className="mt-1 list-disc pl-5 text-bodysm text-ink">
            {pkg.needs_owner_input.map((n) => <li key={n.field}>{n.question}</li>)}
          </ul>
        </div>
      )}
      {error && <p role="alert" className="text-bodysm text-err">{error}</p>}
    </section>
  );
}
