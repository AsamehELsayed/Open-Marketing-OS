import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiRequestError, type BusinessBrief, type BusinessProfile } from "../api/client";
import PageHeader from "../components/chrome/PageHeader";
import { useProject } from "../components/shell/project-context";
import { isCurrentWorkspaceSelection } from "./workspaceSelection";

type ProfileDraft = Pick<BusinessProfile, "business_name" | "website" | "industry" | "description" | "audience" | "location" | "offer" | "differentiators">;
type GenerationAttempt = { status: "running" | "generated" | "failed"; provider?: string; model?: string; error?: string };
const blankProfile = (business_name = ""): ProfileDraft => ({ business_name, website: "", industry: "", description: "", audience: "", location: "", offer: "", differentiators: "" });

function parseJson<T>(value: string | undefined, fallback: T): T {
  try { return value ? JSON.parse(value) as T : fallback; } catch { return fallback; }
}

export default function Workspace() {
  const { current, projects, loading: projectsLoading, setProjectId } = useProject();
  const projectId = current?.id ?? null;
  const selectedProjectId = useRef(projectId);
  selectedProjectId.current = projectId;
  const requestGeneration = useRef(0);
  const [loadedId, setLoadedId] = useState<string | null>(null);
  const [profile, setProfile] = useState<BusinessProfile | null>(null);
  const [draft, setDraft] = useState<ProfileDraft>(blankProfile());
  const [brief, setBrief] = useState<BusinessBrief | null>(null);
  const [briefDraft, setBriefDraft] = useState("");
  const [profileDirty, setProfileDirty] = useState(false);
  const [briefDirty, setBriefDirty] = useState(false);
  const [loading, setLoading] = useState(false);
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingBrief, setSavingBrief] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generationAttempt, setGenerationAttempt] = useState<GenerationAttempt | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const token = ++requestGeneration.current;
    setLoadedId(null); setProfile(null); setBrief(null); setDraft(blankProfile(current?.name));
    setBriefDraft(""); setProfileDirty(false); setBriefDirty(false); setError(""); setNotice("");
    setSavingProfile(false); setSavingBrief(false); setGenerating(false); setGenerationAttempt(null);
    if (!projectId) return;
    setLoading(true);
    Promise.all([api.getBusinessProfile(projectId), api.getBusinessBrief(projectId)]).then(([nextProfile, nextBrief]) => {
      if (token !== requestGeneration.current) return;
      setProfile(nextProfile); setDraft({ business_name: nextProfile.business_name, website: nextProfile.website ?? "", industry: nextProfile.industry ?? "", description: nextProfile.description ?? "", audience: nextProfile.audience ?? "", location: nextProfile.location ?? "", offer: nextProfile.offer ?? "", differentiators: nextProfile.differentiators ?? "" });
      setBrief(nextBrief); setBriefDraft(nextBrief.brief_md ?? ""); setLoadedId(projectId);
    }).catch((e: unknown) => {
      if (token === requestGeneration.current) { setError(e instanceof Error ? e.message : "Workspace could not be loaded."); setLoadedId(projectId); }
    }).finally(() => { if (token === requestGeneration.current) setLoading(false); });
    return () => { if (requestGeneration.current === token) requestGeneration.current++; };
  }, [projectId, current?.name]);

  const visible = Boolean(projectId && loadedId === projectId && !loading);
  const updateDraft = (key: keyof ProfileDraft, value: string) => { setDraft((prev) => ({ ...prev, [key]: value })); setProfileDirty(true); };
  async function saveProfile() {
    if (!projectId || !profile) return;
    const pid = projectId; const epoch = requestGeneration.current;
    const isCurrent = () => isCurrentWorkspaceSelection(pid, epoch, selectedProjectId.current, requestGeneration.current);
    setSavingProfile(true); setError(""); setNotice("");
    try {
      const saved = await api.saveBusinessProfile(pid, { ...draft, expected_revision: profile.profile_revision });
      if (!isCurrent()) return;
      setProfile(saved); setDraft({ business_name: saved.business_name, website: saved.website ?? "", industry: saved.industry ?? "", description: saved.description ?? "", audience: saved.audience ?? "", location: saved.location ?? "", offer: saved.offer ?? "", differentiators: saved.differentiators ?? "" }); setProfileDirty(false); setNotice("Profile saved.");
    } catch (e) { if (isCurrent()) setError(e instanceof Error ? `${e.message} Reload this workspace before trying again.` : "Profile could not be saved."); }
    finally { if (isCurrent()) setSavingProfile(false); }
  }
  async function saveBrief() {
    if (!projectId || !brief) return;
    const pid = projectId; const epoch = requestGeneration.current;
    const isCurrent = () => isCurrentWorkspaceSelection(pid, epoch, selectedProjectId.current, requestGeneration.current);
    setSavingBrief(true); setError(""); setNotice("");
    try {
      const saved = await api.saveBusinessBrief(pid, { brief_md: briefDraft, expected_revision: brief.brief_revision, sources: parseJson<unknown[]>(brief.sources_json, []) });
      if (!isCurrent()) return;
      setBrief(saved); setBriefDraft(saved.brief_md); setBriefDirty(false); setNotice("Brief saved.");
    } catch (e) { if (isCurrent()) setError(e instanceof Error ? `${e.message} Reload this workspace before trying again.` : "Brief could not be saved."); }
    finally { if (isCurrent()) setSavingBrief(false); }
  }
  async function generate() {
    if (!projectId || !profile || !brief) return;
    if ((briefDraft.trim() || brief.brief_md.trim()) && !window.confirm("Replace this brief with generated content? On success, the generated brief is saved over the current saved content, and any unsaved edits in the editor are discarded.")) return;
    const pid = projectId; const epoch = requestGeneration.current;
    const isCurrent = () => isCurrentWorkspaceSelection(pid, epoch, selectedProjectId.current, requestGeneration.current);
    setGenerating(true); setGenerationAttempt({ status: "running" }); setError(""); setNotice("Generating brief…");
    try {
      const generated = await api.generateBusinessBrief(pid, { overwrite_confirmed: Boolean(brief.brief_md.trim()) });
      if (!isCurrent()) return;
      const meta = parseJson<{ provider?: string; model?: string }>(generated.generation_json, {});
      setBrief(generated); setBriefDraft(generated.brief_md); setBriefDirty(false); setGenerationAttempt({ status: "generated", provider: meta.provider, model: meta.model }); setNotice(`Generation complete${meta.provider ? ` · ${meta.provider}` : ""}${meta.model ? ` · ${meta.model}` : ""}. The brief is saved.`);
    } catch (e) { if (isCurrent()) { const message = e instanceof Error ? e.message : "Generation failed."; const provider = e instanceof ApiRequestError ? e.provider : undefined; const model = e instanceof ApiRequestError ? e.model : undefined; setGenerationAttempt({ status: "failed", provider, model, error: message }); setError(`${message} You can continue editing and save the brief manually.`); setNotice("Generation failed; manual editing remains available."); } }
    finally { if (isCurrent()) setGenerating(false); }
  }

  const field = (label: string, key: keyof ProfileDraft, optional = true) => (
    <label key={key} className="flex flex-col gap-1 text-bodysm font-medium">{label}{optional && <span className="font-normal text-inksecondary">Optional</span>}
      {key === "description" || key === "differentiators" ? <textarea rows={3} value={draft[key]} onChange={(e) => updateDraft(key, e.target.value)} className="rounded-sm border border-linedefault bg-base px-3 py-2 font-normal" /> : <input required={!optional} value={draft[key]} onChange={(e) => updateDraft(key, e.target.value)} className="rounded-sm border border-linedefault bg-base px-3 py-2 font-normal" />}
    </label>
  );

  return <div className="mx-auto max-w-6xl px-5 pb-10">
    <PageHeader title="Client workspace" description="Keep each client’s business profile, evidence, and editable brief together." />
    <div className="mt-4 flex flex-wrap items-center gap-3 rounded-md border border-linedefault bg-raised p-3">
      <label className="text-meta font-medium">Client <select aria-label="Select client workspace" value={projectId ?? ""} onChange={(e) => setProjectId(e.target.value || null)} className="ml-2 rounded border border-linedefault bg-base px-2 py-1">
        {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select></label>
      <Link className="text-meta text-accent underline" to="/app/business/new?mode=client">Create client</Link>
      <div className="ml-auto flex flex-wrap gap-3 text-meta"><Link to="/app">Account Manager</Link><Link to="/app/campaigns">Campaigns</Link><Link to="/app/knowledge">Knowledge</Link><Link to="/app/settings?section=files">Files</Link></div>
    </div>
    {projectsLoading || loading ? <p role="status" className="py-10 text-bodysm text-inksecondary">Loading this client workspace…</p> : !projectId ? <div className="mt-6 rounded border border-linedefault p-6"><p>No client workspace yet.</p><Link className="text-accent underline" to="/app/business/new?mode=client">Create a client workspace</Link></div> : !visible ? <p role="status" className="py-10 text-bodysm text-inksecondary">{error || "Loading client workspace…"}</p> : <>
      {error && <p role="alert" className="mt-4 rounded bg-red-50 px-3 py-2 text-bodysm text-error">{error}</p>}{notice && <p role="status" className="mt-3 text-meta text-inksecondary">{notice}</p>}
      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <section className="rounded-lg border border-linedefault bg-raised p-5" aria-labelledby="profile-heading"><div className="mb-4 flex items-center justify-between"><h2 id="profile-heading" className="text-title font-semibold">Business profile</h2><span className="text-meta text-inksecondary">User facts</span></div>
          <div className="grid gap-3">{field("Business name", "business_name", false)}{field("Website", "website")}{field("Industry", "industry")}{field("About the business", "description")}{field("Audience", "audience")}{field("Location / market", "location")}{field("Offer", "offer")}{field("Differentiators", "differentiators")}</div>
          <button type="button" onClick={() => void saveProfile()} disabled={!profileDirty || savingProfile || !draft.business_name.trim()} className="mt-4 rounded-sm bg-accent px-4 py-2 text-bodysm font-semibold text-white disabled:opacity-50">{savingProfile ? "Saving…" : "Save profile"}</button>{profileDirty && <span className="ml-3 text-meta text-inksecondary">Unsaved changes</span>}
        </section>
        <section className="rounded-lg border border-linedefault bg-raised p-5" aria-labelledby="brief-heading"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 id="brief-heading" className="text-title font-semibold">Business brief</h2><p className="mt-1 text-meta text-inksecondary">Editable Markdown · {briefDirty ? "Unsaved changes" : "Saved"}</p></div><div className="flex gap-2"><button type="button" onClick={() => void generate()} disabled={generating || savingBrief || savingProfile || profileDirty} title={profileDirty ? "Save profile changes before generating" : "Generate a brief from this client’s profile and Knowledge"} className="rounded-sm border border-linedefault px-3 py-2 text-bodysm disabled:opacity-50">{generating ? "Generating…" : briefDraft.trim() ? "Regenerate" : "Generate"}</button><button type="button" onClick={() => void saveBrief()} disabled={!briefDirty || savingBrief || generating} className="rounded-sm bg-accent px-3 py-2 text-bodysm font-semibold text-white disabled:opacity-50">{savingBrief ? "Saving…" : "Save brief"}</button></div></div>
          {(() => { const meta = parseJson<{ status?: string; provider?: string; model?: string; error?: string }>(brief?.generation_json, {}); const attempt = generationAttempt; return <p className="mt-3 text-meta text-inksecondary">Generation status: {generating ? "Running" : attempt?.status || meta.status || "Not generated"}{(attempt?.provider || meta.provider) && ` · Provider: ${attempt?.provider || meta.provider}`}{(attempt?.model || meta.model) && ` · Model: ${attempt?.model || meta.model}`}{(attempt?.error || meta.error) && ` · ${attempt?.error || meta.error}`}</p>; })()}
          <textarea aria-label="Editable business brief" value={briefDraft} onChange={(e) => { setBriefDraft(e.target.value); setBriefDirty(true); }} rows={20} disabled={!brief || generating} placeholder="Write a brief here, or generate one when ready." className="mt-3 w-full resize-y rounded-sm border border-linedefault bg-base p-3 font-mono text-bodysm disabled:opacity-60" />
          <div className="mt-4 grid gap-2 text-meta"><p><strong>User facts:</strong> Profile fields above are saved facts supplied for this client.</p><p><strong>Retrieved evidence:</strong> Evidence and source references from this project’s Knowledge appear here.</p><p><strong>AI suggestions:</strong> Treat generated recommendations as suggestions for review.</p><p><strong>Missing information:</strong> Generation should call out gaps and insufficient Knowledge. Add project files in <Link className="text-accent underline" to="/app/settings?section=files">Files</Link>.</p></div>
          <div className="mt-3"><h3 className="text-bodysm font-semibold">Source references</h3>{parseJson<Array<{source_id?: string; path?: string; text?: string}>>(brief?.sources_json, []).length ? <ul className="mt-1 list-disc pl-5 text-meta">{parseJson<Array<{source_id?: string; path?: string; text?: string}>>(brief?.sources_json, []).map((source, i) => <li key={source.source_id ?? i}>{source.path || source.source_id || "Project source"}</li>)}</ul> : <p className="mt-1 text-meta text-inksecondary">No retrieved source references yet. Never add a citation unless it appears in this list.</p>}</div>
        </section>
      </div>
    </>}
  </div>;
}
