import { useEffect, useRef, useState } from "react";
import { api, saveDownloadedFile, type CampaignWorkPackage } from "../../api/client";
import StatusBadge from "../chrome/StatusBadge";

/**
 * Owner-readable campaign work package. It walks a small business owner
 * through one journey on a single screen:
 *
 *   1. what OMOS understood (and who said it)
 *   2. what is still missing (answer it right here)
 *   3. the proposed campaign (facts are the owner's; ideas are labelled)
 *   4. write the content
 *   5. review / approve what the AI wrote
 *   6. download only what is approved
 *
 * Evidence classes are kept apart so AI suggestions and unchecked search leads
 * are never shown as established facts.
 */
export const GOAL_PRESETS = [
  "Get more customer inquiries",
  "Promote a new product",
  "Increase awareness of an existing offer",
  "Prepare a two-week content campaign",
];

const CHANNEL_CHOICES: [string, string][] = [
  ["instagram", "Instagram"], ["facebook", "Facebook"], ["tiktok", "TikTok"],
  ["linkedin", "LinkedIn"], ["email", "Email"], ["newsletter", "Email newsletter"],
  ["google ads", "Google Ads"], ["meta ads", "Meta Ads"], ["blog", "Blog"],
];

const TEXT_FIELDS = new Set(["objective", "target_audience", "duration", "offer", "geography"]);

function Section({ title, children, testId }: { title: string; children: React.ReactNode; testId?: string }) {
  return (
    <div className="rounded border border-linedefault bg-surface px-4 py-3" data-testid={testId}>
      <h2 className="text-body font-semibold text-ink">{title}</h2>
      {children}
    </div>
  );
}

export default function WorkPackagePanel({ projectId, campaignId, onReview }: {
  projectId: string; campaignId: string; onReview?: () => void;
}) {
  const [pkg, setPkg] = useState<CampaignWorkPackage | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [writing, setWriting] = useState(false);
  const writingRef = useRef(false);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!writing) return;
    const started = Date.now();
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [writing]);
  const [goal, setGoal] = useState("");
  const [goalSaved, setGoalSaved] = useState("");
  const [reload, setReload] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [picked, setPicked] = useState<string[]>([]);

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
  }, [projectId, campaignId, goalSaved, reload]);

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

  async function saveAnswers() {
    const body: Record<string, string | string[]> = {};
    for (const [k, v] of Object.entries(answers)) if (v.trim()) body[k] = v;
    if (picked.length) body.channels = picked;
    if (!Object.keys(body).length) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setPkg(await api.saveCampaignAnswers(projectId, campaignId, body));
      setAnswers({});
      setPicked([]);
      setNotice("Saved. Your plan is updated.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn’t save those details. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function writeContent() {
    if (writingRef.current) return;
    writingRef.current = true;
    setBusy(true);
    setWriting(true);
    setError("");
    setNotice("");
    try {
      const out = await api.generateCampaignContent(projectId, campaignId);
      setNotice(`${out.created.length} draft(s) written${out.model ? ` using ${out.model}${out.provider ? ` (${out.provider})` : ""}` : ""}. Nothing is approved yet - review each one below.`);
      setReload((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The content couldn’t be written. Nothing was saved.");
    } finally {
      setBusy(false);
      setWriting(false);
      writingRef.current = false;
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
  const u = pkg.understanding;
  const p = pkg.proposal;
  const noContent = pkg.deliverables.length === 0;
  const checked = ev.web_sources.filter((s) => s.verification === "inspected");
  const leads = ev.web_sources.filter((s) => s.verification !== "inspected");
  // Goal, audience, channels and duration are shown in the understanding list;
  // list only the remaining facts the owner supplied.
  const shownElsewhere = new Set(["goal", "audience", "channels", "duration", "offer", "location"]);
  const provided = Object.entries(ev.user_provided).filter(([k]) => !shownElsewhere.has(k));

  return (
    <section aria-label="Campaign work package" className="mb-6 space-y-4">
      <div className="rounded border border-accent/40 bg-surface px-4 py-3" data-testid="wp-stage" data-stage={pkg.stage.id}>
        <p className="text-meta uppercase tracking-wide text-inksecondary">Where this campaign is</p>
        <p className="text-body font-semibold text-ink">{pkg.stage.label}</p>
        <p className="text-bodysm text-inksecondary">{pkg.stage.next}</p>
      </div>

      <Section title="What OMOS understood" testId="wp-understood">
        {u.facts.length ? (
          <dl className="mt-1 divide-y divide-linesubtle">
            {u.facts.map((f) => (
              <div key={f.field} className="flex flex-wrap gap-x-3 py-1.5 text-bodysm" data-fact={f.field}>
                <dt className="w-32 shrink-0 text-inksecondary">{f.label}</dt>
                <dd className="text-ink">{f.value}</dd>
                <dd className="text-meta text-inkmuted">{f.source_label}</dd>
              </div>
            ))}
          </dl>
        ) : <p className="mt-1 text-bodysm text-inkmuted">Nothing specific was picked up from your message yet.</p>}
        <p className="mt-2 text-meta text-inkmuted">Only what you wrote (or your business profile) appears here. Nothing is guessed.</p>
      </Section>

      {u.missing.length > 0 && (
        <Section title="What is still missing" testId="wp-missing">
          <form className="mt-2 space-y-3" onSubmit={(e) => { e.preventDefault(); void saveAnswers(); }}>
            {u.missing.map((m) => (
              <div key={m.field}>
                <label className="block text-bodysm font-medium text-ink" htmlFor={`wp-${m.field}`}>
                  {m.question}{" "}
                  <span className="font-normal text-inkmuted">{m.essential ? "(needed to write content)" : "(optional)"}</span>
                </label>
                {m.field === "channels" ? (
                  <div className="mt-1 flex flex-wrap gap-3" role="group" aria-label="Channels">
                    {CHANNEL_CHOICES.map(([value, label]) => (
                      <label key={value} className="flex items-center gap-1.5 text-bodysm text-ink">
                        <input
                          type="checkbox"
                          checked={picked.includes(value)}
                          onChange={(e) => setPicked((cur) => e.target.checked ? [...cur, value] : cur.filter((c) => c !== value))}
                        />
                        {label}
                      </label>
                    ))}
                  </div>
                ) : TEXT_FIELDS.has(m.field) ? (
                  <input
                    id={`wp-${m.field}`}
                    maxLength={300}
                    value={answers[m.field] ?? ""}
                    onChange={(e) => setAnswers((cur) => ({ ...cur, [m.field]: e.target.value }))}
                    className="mt-1 w-full rounded-sm border border-linedefault bg-base px-3 py-1.5 text-bodysm"
                  />
                ) : null}
              </div>
            ))}
            <button type="submit" disabled={busy} className="rounded-sm bg-accent px-3 py-1.5 text-bodysm font-semibold text-white disabled:opacity-50">
              Save details
            </button>
          </form>
        </Section>
      )}

      <Section title="Proposed campaign" testId="wp-proposal">
        <dl className="mt-1 divide-y divide-linesubtle text-bodysm">
          <div className="flex gap-3 py-1.5"><dt className="w-32 shrink-0 text-inksecondary">Audience</dt><dd className="text-ink">{p.audience || <span className="text-inkmuted">Not set yet</span>}</dd></div>
          <div className="flex gap-3 py-1.5"><dt className="w-32 shrink-0 text-inksecondary">Channels</dt><dd className="text-ink">{p.channels.length ? p.channels.join(", ") : <span className="text-inkmuted">Not set yet</span>}</dd></div>
          {p.duration && <div className="flex gap-3 py-1.5"><dt className="w-32 shrink-0 text-inksecondary">Duration</dt><dd className="text-ink">{p.duration}</dd></div>}
          <div className="flex gap-3 py-1.5"><dt className="w-32 shrink-0 text-inksecondary">Message focus</dt><dd className="text-ink">{p.message_focus || <span className="text-inkmuted">None yet</span>}<span className="block text-meta text-inkmuted">{p.message_focus_note}</span></dd></div>
        </dl>
        <h3 className="mt-3 text-bodysm font-medium text-ink">Content OMOS would write</h3>
        {p.deliverables.length ? (
          <ul className="list-disc pl-5 text-bodysm text-ink">
            {p.deliverables.map((d) => <li key={d.type}>{d.label}{d.platform && d.label !== "Email" ? ` (${d.platform})` : ""} <span className="text-meta text-inkmuted">- {d.reason}</span></li>)}
          </ul>
        ) : <p className="text-bodysm text-inkmuted">Choose at least one channel to see what would be written.</p>}
        {p.recommendations.length > 0 && (
          <>
            <h3 className="mt-3 text-bodysm font-medium text-ink">Ideas from OMOS (suggestions, not facts)</h3>
            <ul className="list-disc pl-5 text-bodysm text-ink">
              {p.recommendations.map((r) => <li key={r.channel}><span className="text-inksecondary">{r.channel}:</span> {r.idea}</li>)}
            </ul>
          </>
        )}
        {noContent && (
          <div className="mt-3">
            <button
              type="button"
              disabled={busy || !p.ready_to_generate}
              onClick={() => void writeContent()}
              className="rounded-sm bg-accent px-3 py-1.5 text-bodysm font-semibold text-white disabled:opacity-50"
            >
              {writing ? "Writing…" : "Write this content"}
            </button>
            {writing && <p role="status" aria-live="polite" className="mt-1 text-meta text-inksecondary">Writing content · {elapsed}s elapsed. Free models can be slow or unavailable; no completion time is guaranteed.</p>}
            {!p.ready_to_generate && <p className="mt-1 text-meta text-inksecondary">Answer the needed questions above first.</p>}
            <p className="mt-1 text-meta text-inkmuted">Writes drafts only. Nothing is published, sent or paid for.</p>
          </div>
        )}
      </Section>

      <Section title="Review and approve" testId="wp-deliverables">
        {pkg.deliverables.length === 0 ? (
          <p className="mt-1 text-bodysm text-inksecondary">Nothing has been written yet.</p>
        ) : (
          <ul className="mt-2 divide-y divide-linesubtle">
            {pkg.deliverables.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 py-1.5 text-bodysm" data-deliverable={d.id}>
                <span className="text-ink">{d.type_label}: {d.title}{d.platform ? ` (${d.platform})` : ""}{d.written_by_ai ? " · written by AI" : ""}</span>
                <StatusBadge status={d.status_label} />
              </li>
            ))}
          </ul>
        )}
        {!noContent && onReview && (
          <button type="button" onClick={onReview} className="mt-2 rounded-sm bg-accent px-3 py-1.5 text-bodysm font-semibold text-white">
            Review, change and approve
          </button>
        )}
        <p className="mt-2 text-meta text-inksecondary">{a.approved} of {a.total} approved. Earlier versions are kept when you edit.</p>
      </Section>

      <Section title="Ready to download" testId="wp-download">
        {a.approved === 0 ? (
          <p className="mt-1 text-bodysm text-inksecondary">Nothing is approved yet, so there is nothing to download.</p>
        ) : (
          <ul className="mt-1 list-disc pl-5 text-bodysm text-ink">
            {pkg.download.approved.map((t) => <li key={t}>{t} <span className="text-meta text-inkmuted">- included</span></li>)}
          </ul>
        )}
        {pkg.download.not_included.length > 0 && a.approved > 0 && (
          <p className="mt-1 text-meta text-inksecondary">Not included until approved: {pkg.download.not_included.join(", ")}.</p>
        )}
        <button type="button" disabled={busy || a.approved === 0} onClick={() => void downloadApproved()} className="mt-2 rounded-sm border border-linedefault px-3 py-1.5 text-bodysm text-ink hover:bg-elevated disabled:opacity-50">
          Download approved package
        </button>
      </Section>

      <Section title="Where this comes from" testId="wp-evidence">
        <p className={`mt-1 text-bodysm ${ev.research.state === "available" ? "text-ink" : "text-warn"}`}>{ev.research.message}</p>
        <p className="mt-1 text-bodysm text-ink">{p.research.note}</p>
        <h3 className="mt-3 text-bodysm font-medium text-ink">Told to us by you</h3>
        {provided.length ? <ul className="list-disc pl-5 text-bodysm text-ink">{provided.map(([k, v]) => <li key={k}><span className="text-inksecondary">{k}:</span> {v}</li>)}</ul> : <p className="text-bodysm text-inkmuted">See “What OMOS understood” above.</p>}
        <h3 className="mt-3 text-bodysm font-medium text-ink">From your own documents</h3>
        {ev.own_documents.length ? <ul className="list-disc pl-5 text-bodysm text-ink">{ev.own_documents.map((d) => <li key={d.label}>{d.label}</li>)}</ul> : <p className="text-bodysm text-inkmuted">None used.</p>}
        <h3 className="mt-3 text-bodysm font-medium text-ink">Web pages OMOS opened and read</h3>
        {checked.length ? (
          <ul className="list-disc pl-5 text-bodysm text-ink" data-testid="wp-sources-checked">
            {checked.map((s) => (
              <li key={s.url}>
                <a href={s.url} target="_blank" rel="noopener noreferrer" className="underline">{s.title || s.url}</a>
                <span className="block break-all text-meta text-inkmuted">{s.url}</span>
                <span className="block text-meta text-ink">{s.verification_label} · {s.scope_label}</span>
              </li>
            ))}
          </ul>
        ) : <p className="text-bodysm text-inkmuted">None.</p>}
        <h3 className="mt-3 text-bodysm font-medium text-ink">Search results to check (leads, not facts)</h3>
        {leads.length ? (
          <ul className="list-disc pl-5 text-bodysm text-ink" data-testid="wp-sources-leads">
            {leads.map((s) => (
              <li key={s.url}>
                <a href={s.url} target="_blank" rel="noopener noreferrer" className="underline">{s.title || s.url}</a>
                <span className="block break-all text-meta text-inkmuted">{s.url}</span>
                <span className="block text-meta text-warn">{s.verification_label} · {s.use} · {s.scope_label}</span>
              </li>
            ))}
          </ul>
        ) : <p className="text-bodysm text-inkmuted">None.</p>}
        <h3 className="mt-3 text-bodysm font-medium text-ink">Suggested by AI (ideas to check, not facts)</h3>
        {ev.ai_suggested.length ? <ul className="list-disc pl-5 text-bodysm text-ink">{ev.ai_suggested.map((t) => <li key={t}>{t}</li>)}</ul> : <p className="text-bodysm text-inkmuted">None.</p>}
      </Section>

      <Section title="Your business goal" testId="wp-goal">
        <p className="mt-1 text-bodysm text-ink">{pkg.goal || "You haven’t said what you want to achieve yet."}</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {GOAL_PRESETS.map((preset) => (
            <button key={preset} type="button" disabled={busy} onClick={() => void saveGoal(preset)} className="rounded-sm border border-linedefault px-2.5 py-1 text-meta text-ink hover:bg-elevated disabled:opacity-50">
              {preset}
            </button>
          ))}
        </div>
        <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); void saveGoal(goal); }}>
          <input aria-label="Describe your goal" maxLength={500} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Or describe it in your own words" className="flex-1 rounded-sm border border-linedefault bg-base px-3 py-1.5 text-bodysm" />
          <button type="submit" disabled={busy || !goal.trim()} className="rounded-sm bg-accent px-3 py-1.5 text-bodysm font-semibold text-white disabled:opacity-50">Save goal</button>
        </form>
      </Section>

      {notice && <p role="status" className="text-bodysm text-ink" data-testid="wp-notice">{notice}</p>}
      {error && <p role="alert" className="text-bodysm text-err" data-testid="wp-error">{error}</p>}
    </section>
  );
}
