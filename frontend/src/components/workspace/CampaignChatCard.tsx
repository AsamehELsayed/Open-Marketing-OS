import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type CampaignWorkPackage } from "../../api/client";

/**
 * Inline card shown under an assistant reply that mentions a campaign. It
 * answers, in plain words: what OMOS understood, what it still needs, what it
 * proposes to write, and what to do next - without leaving the chat.
 */
export default function CampaignChatCard({ projectId, campaignId }: { projectId: string; campaignId: string }) {
  const [pkg, setPkg] = useState<CampaignWorkPackage | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const writingRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getCampaignWorkPackage(projectId, campaignId)
      .then((d) => !cancelled && setPkg(d))
      .catch(() => !cancelled && setError("This campaign summary could not be loaded."));
    return () => {
      cancelled = true;
    };
  }, [projectId, campaignId, reload]);

  async function write() {
    if (writingRef.current) return;
    writingRef.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const out = await api.generateCampaignContent(projectId, campaignId);
      const timeStr = out.elapsed_s ? ` (${out.elapsed_s}s)` : "";
      const modelStr = out.model ? ` using ${out.model}` : "";
      setNotice(`Generated ${out.created.length} deliverable(s)${modelStr}${timeStr}.`);
      setReload((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The content couldn’t be written. Nothing was saved.");
    } finally {
      setBusy(false);
      writingRef.current = false;
    }
  }

  if (!pkg) {
    return error ? <p className="mt-2 text-meta text-err">{error}</p> : null;
  }
  const noContent = pkg.deliverables.length === 0;
  const essentialMissing = pkg.understanding.missing.filter((m) => m.essential);
  return (
    <div className="mt-3 rounded border border-linedefault bg-surface px-3 py-2.5" data-testid="campaign-chat-card" data-stage={pkg.stage.id}>
      <p className="text-meta uppercase tracking-wide text-inksecondary">Campaign · {pkg.stage.label}</p>
      <p className="text-bodysm font-semibold text-ink">{pkg.campaign.title}</p>
      <ul className="mt-1 text-bodysm text-ink">
        {pkg.understanding.facts.filter((f) => f.field !== "content_requirements").map((f) => (
          <li key={f.field}><span className="text-inksecondary">{f.label}:</span> {f.value}</li>
        ))}
      </ul>
      {essentialMissing.length > 0 && (
        <p className="mt-1 text-bodysm text-warn">Still needed: {essentialMissing.map((m) => m.question).join(" ")}</p>
      )}
      {noContent && pkg.proposal.deliverables.length > 0 && (
        <p className="mt-1 text-bodysm text-ink">
          Would write: {pkg.proposal.deliverables.map((d) => d.label + (d.platform && d.label !== "Email" ? ` (${d.platform})` : "")).join(", ")}
        </p>
      )}
      {!noContent && (
        <p className="mt-1 text-bodysm text-ink">
          {pkg.approval.approved} of {pkg.approval.total} approved · {pkg.stage.next}
        </p>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        {noContent && (
          <button
            type="button"
            disabled={busy || !pkg.proposal.ready_to_generate}
            onClick={() => void write()}
            className="rounded-sm bg-accent px-3 py-1.5 text-bodysm font-semibold text-white disabled:opacity-50"
          >
            {busy ? "Writing…" : "Write this content"}
          </button>
        )}
        <Link to={`/app/campaigns/${encodeURIComponent(campaignId)}`} className="rounded-sm border border-linedefault px-3 py-1.5 text-bodysm text-ink hover:bg-elevated">
          {noContent ? "Review the plan" : "Review and approve"}
        </Link>
      </div>
      {notice && <p className="mt-1 text-meta text-inksecondary">{notice}</p>}
      {error && <p role="alert" className="mt-1 text-meta text-err">{error}</p>}
    </div>
  );
}
