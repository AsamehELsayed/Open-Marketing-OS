import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import PageHeader from "../components/chrome/PageHeader";
import { useToast } from "../components/chrome/Toast";
import { useProject } from "../components/shell/project-context";

function validWebsite(value: string): boolean {
  if (!value) return true;
  try {
    const url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : `https://${value}`);
    return (url.protocol === "http:" || url.protocol === "https:") && url.hostname.includes(".");
  } catch {
    return false;
  }
}

export default function CreateBusiness() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const createClient = searchParams.get("mode") === "client";
  const { setProjectId, refresh } = useProject();
  const { push } = useToast();
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [context, setContext] = useState("");
  const [primaryMarket, setPrimaryMarket] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const cleanName = name.trim();
  const cleanWebsite = website.trim();
  const cleanContext = context.trim();
  const cleanMarket = primaryMarket.trim();
  const websiteValid = validWebsite(cleanWebsite);
  const canSave = Boolean(cleanName) && cleanName.length <= 120 && cleanWebsite.length <= 300 && websiteValid && cleanContext.length <= 200 && cleanMarket.length <= 120 && !saving;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave) return;
    setSaving(true);
    setError("");
    try {
      const result = createClient
        ? await api.createProject({ business_name: cleanName, website: cleanWebsite, description: cleanContext, location: cleanMarket })
        : await api.createBusiness({ name: cleanName, website: cleanWebsite, context: cleanContext, primary_market: cleanMarket });
      // Select the returned id before refreshing the project list. The next
      // conversation is created with this id by NewChat's /chat/new request.
      setProjectId("project_id" in result ? result.project_id : result.project.id);
      refresh();
      if ("warning" in result && result.warning) push(result.warning, "info");
      navigate(createClient ? "/app/workspace" : "/app", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn’t save your business. Please try again.");
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-5 pb-10">
      <PageHeader title={createClient ? "Create client workspace" : "Set up your business"} description={createClient ? "Start with a business name. You can add profile details and a brief at any time." : "Give your Account Manager the context it needs to make useful recommendations."} />
      <form onSubmit={(event) => void submit(event)} className="mt-5 rounded-lg border border-linedefault bg-raised p-5 sm:p-7">
        <div className="flex flex-col gap-5">
          <label className="flex flex-col gap-1.5 text-bodysm font-medium text-ink">
            Business name <span className="text-error" aria-hidden="true">*</span>
            <input autoFocus required maxLength={120} value={name} onChange={(event) => setName(event.target.value)} className="rounded-sm border border-linedefault bg-base px-3 py-2 font-normal" placeholder="e.g. Northstar Studio" />
          </label>
          <label className="flex flex-col gap-1.5 text-bodysm font-medium text-ink">
            Website <span className="font-normal text-inksecondary">(optional)</span>
            <input type="text" inputMode="url" maxLength={300} value={website} onChange={(event) => setWebsite(event.target.value)} aria-invalid={!websiteValid} aria-describedby={!websiteValid ? "website-error" : undefined} className="rounded-sm border border-linedefault bg-base px-3 py-2 font-normal" placeholder="example.com" />
            {!websiteValid && <span id="website-error" className="text-meta font-normal text-error">Enter a website such as example.com or https://example.com.</span>}
          </label>
          {!createClient && <label className="flex flex-col gap-1.5 text-bodysm font-medium text-ink">
            What should your Account Manager know? <span className="font-normal text-inksecondary">(optional)</span>
            <textarea maxLength={200} rows={3} value={context} onChange={(event) => setContext(event.target.value)} className="resize-y rounded-sm border border-linedefault bg-base px-3 py-2 font-normal" placeholder="A short description of what you offer or what you’re working toward." />
            <span className="text-right text-meta font-normal text-inkmuted">{context.length}/200</span>
          </label>}
          {!createClient && <label className="flex flex-col gap-1.5 text-bodysm font-medium text-ink">
            Primary market <span className="font-normal text-inksecondary">(optional)</span>
            <input maxLength={120} value={primaryMarket} onChange={(event) => setPrimaryMarket(event.target.value)} className="rounded-sm border border-linedefault bg-base px-3 py-2 font-normal" placeholder="e.g. Independent restaurants in Cairo" />
          </label>}
        </div>
        {error && <p role="alert" className="mt-4 rounded-sm bg-red-50 px-3 py-2 text-bodysm text-error">{error}</p>}
        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <button type="button" disabled={saving} onClick={() => navigate(createClient ? "/app/workspace" : "/app/start")} className="rounded-sm border border-linedefault px-4 py-2 text-bodysm text-ink hover:bg-elevated disabled:opacity-50">{createClient ? "Cancel" : "Back to Start Here"}</button>
          <button type="submit" disabled={!canSave} className="rounded-sm bg-accent px-4 py-2 text-bodysm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">{saving ? "Creating workspace…" : createClient ? "Create client workspace" : "Save and open Account Manager"}</button>
        </div>
      </form>
    </div>
  );
}
