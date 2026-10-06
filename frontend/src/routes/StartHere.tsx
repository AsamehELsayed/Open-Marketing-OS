import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import PageHeader from "../components/chrome/PageHeader";

const topics = [
  { title: "Welcome to OMOS", text: "OMOS is an AI marketing workspace. Its Account Manager can coordinate specialist work, use marketing playbooks and connected tools, and bring the evidence into one clear result." },
  { title: "How OMOS works", text: "You describe the work. The Account Manager chooses a practical approach, brings in suitable specialists, gathers evidence, and combines the findings into a result you can review." },
  { title: "Your Account Manager", text: "The Account Manager is your main point of contact. It understands your request, coordinates work, selects relevant skills and tools, tracks progress, and summarizes the outcome." },
  { title: "Your marketing team", text: "Depending on the task, OMOS may bring in specialists for research, SEO, conversion improvement, strategy, social media, or content. The team used can vary with the request." },
  { title: "Skills and tools", text: "A Skill is a marketing method or playbook, such as an SEO audit. A Tool retrieves information or performs an action, such as reading a website or researching public web pages." },
  { title: "Your business workspace", text: "Create a business profile with its name and, if useful, a website, short context, and primary market. Your Account Manager uses this information to make its first recommendations more relevant." },
  { title: "Connect AI", text: "This beta supports OpenRouter and OpenAI. Add your own provider credential in Settings and test the connection there. OMOS stores the credential securely and does not show it again after saving. Local AI is planned for a later beta." },
  { title: "Connect integrations", text: "Settings lists the integrations available in this build. Public Instagram research can use its configured research providers; Meta Insights is for a connected owned account. Website analysis can use public website content. Availability depends on configuration and provider access." },
  { title: "Start your first task", text: "Open Chat and ask for one clear outcome. Try: “Review my website and suggest three improvements,” “Compare my competitors,” “Improve my positioning,” “Audit my Instagram,” or “Suggest growth experiments.”" },
  { title: "Understand the execution tree", text: "The activity view shows work that has been queued, started, completed, or failed, plus items waiting for approval. It is a progress view, not a display of private model reasoning. Completed branches feed into the final summary." },
  { title: "Approvals", text: "Some actions pause and ask you to decide before continuing. Review pending items in Approvals, read what is proposed, then approve or reject it. OMOS resumes the work with the decision you make." },
  { title: "When something fails", text: "A branch may report that a provider or specialist could not complete its work. Other independent work may continue. OMOS should identify unavailable evidence so you can decide whether to retry or adjust the request." },
  { title: "Quick troubleshooting", text: "AI provider not connected: open Settings → AI & Models. Integration not connected: open Settings → Integrations. Website or Instagram research unavailable: check the URL, public access, and provider status. A failed branch: open its activity details and retry with a narrower request. Chat not responding: confirm a provider is connected, then reload and try again." },
  { title: "Recommended first prompts", text: "English: “Analyze my website, SEO, positioning and conversion funnel, then propose three prioritized growth experiments.” Arabic: “حلل موقعي والـSEO والتموضع وقمع التحويل، واقترح 3 تجارب نمو ذات أولوية.” You can also start with a website review, competitor comparison, positioning critique, Instagram audit, or experiment ideas." },
];

export default function StartHere() {
  const navigate = useNavigate();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const continueTo = async (path: string) => {
    if (saving) return;
    setSaving(true);
    setError("");
    try {
      await api.completeStartHere();
      navigate(path);
    } catch {
      setError("OMOS could not save this choice. Check the connection and try again.");
      setSaving(false);
    }
  };

  return <div className="h-full overflow-y-auto">
    <PageHeader title="Start Here" description="A short guide to setting up OMOS and getting useful work done." />
    <div className="mx-auto max-w-5xl px-5 py-7 sm:px-8">
      <section className="rounded-lg border border-linedefault bg-raised p-5 sm:p-7">
        <p className="text-meta font-semibold uppercase tracking-wide text-accent">Your marketing workspace</p>
        <h2 className="mt-2 text-h1 font-semibold text-ink">Tell OMOS what you want to accomplish.</h2>
        <p className="mt-3 max-w-3xl text-body text-inksecondary">The Account Manager coordinates the work, uses the right marketing methods and available tools, then gives you a result with progress and evidence you can review.</p>
        <div className="mt-5 flex flex-wrap gap-2">
          <button type="button" onClick={() => navigate("/app/business/new")} className="rounded bg-accent px-4 py-2 text-bodysm font-semibold text-white hover:bg-accenthover">Create your business</button>
          <button type="button" disabled={saving} onClick={() => void continueTo("/app/settings?section=ai")} className="rounded bg-accent px-4 py-2 text-bodysm font-semibold text-white hover:bg-accenthover disabled:opacity-60">{saving ? "Saving…" : "Connect AI"}</button>
          <button type="button" disabled={saving} onClick={() => void continueTo("/app")} className="rounded border border-linedefault px-4 py-2 text-bodysm font-medium text-ink hover:bg-elevated">Open Chat</button>
          <button type="button" disabled={saving} onClick={() => void continueTo("/app/settings?section=general")} className="rounded border border-linedefault px-4 py-2 text-bodysm font-medium text-ink hover:bg-elevated">Explore Settings</button>
        </div>
        {error && <p role="alert" className="mt-3 text-bodysm text-error">{error}</p>}
      </section>

      <section className="mt-5 rounded-lg border border-linedefault bg-raised p-5 sm:p-7" aria-labelledby="first-use-title">
        <h2 id="first-use-title" className="text-h2 font-semibold text-ink">Your first five steps</h2>
        <ol className="mt-4 grid gap-3 sm:grid-cols-2">
          {[
            { title: "Create your business", text: "Add your business name and any optional context.", to: "/app/business/new", action: "Create business" },
            { title: "Connect an AI provider", text: "Add and test a provider credential.", to: "/app/settings?section=ai", action: "Open AI settings" },
            { title: "Add optional integrations", text: "Configure the integrations available in this workspace.", to: "/app/settings?section=integrations", action: "Open integrations" },
            { title: "Open Account Manager", text: "Meet your main point of contact for marketing work.", to: "/app", action: "Open Account Manager" },
            { title: "Ask your first marketing task", text: "Describe one clear outcome you want help with.", to: "/app/chat/new", action: "Start a new chat" },
          ].map(({ title, text, to, action }, index) => <li key={title} className="rounded-md border border-linesubtle bg-base p-3">
            <p className="text-meta font-semibold text-accent">STEP {index + 1}</p>
            <h3 className="mt-1 text-bodysm font-semibold text-ink">{title}</h3>
            <p className="mt-1 text-meta leading-relaxed text-inksecondary">{text}</p>
            <Link className="mt-2 inline-block text-meta font-medium text-accent hover:underline" to={to}>{action} →</Link>
          </li>)}
        </ol>
      </section>

      <div className="mt-6 grid gap-3 md:grid-cols-2">
        {topics.map((topic, index) => <section key={topic.title} className="rounded-lg border border-linedefault bg-raised p-4 sm:p-5">
          <p className="text-meta font-medium text-inkmuted">{String(index + 1).padStart(2, "0")}</p>
          <h3 className="mt-1 text-h3 font-semibold text-ink">{topic.title}</h3>
          <p className="mt-2 text-bodysm leading-relaxed text-inksecondary">{topic.text}</p>
          {index === 5 && <Link className="mt-3 inline-block text-bodysm font-medium" to="/app/settings?section=general">Open general settings →</Link>}
          {index === 6 && <>
            <figure className="mt-4 overflow-hidden rounded-md border border-linedefault bg-base">
              <img src="/app/start-here/settings-ai.png" alt="Settings AI and Models page showing provider choices and an unavailable Local AI state." loading="lazy" className="block max-h-56 w-full object-cover object-top sm:max-h-64" />
              <figcaption className="border-t border-linesubtle px-3 py-2 text-meta text-inksecondary">Settings → AI &amp; Models. This synthetic capture shows provider options; no credential values are present.</figcaption>
            </figure>
            <Link className="mt-3 inline-block text-bodysm font-medium" to="/app/settings?section=ai">Open AI settings →</Link>
          </>}
          {index === 7 && <>
            <figure className="mt-4 overflow-hidden rounded-md border border-linedefault bg-base">
              <img src="/app/start-here/settings-integrations.png" alt="Settings Integrations page showing public Instagram research and Meta Insights configuration." loading="lazy" className="block max-h-56 w-full object-cover object-top sm:max-h-64" />
              <figcaption className="border-t border-linesubtle px-3 py-2 text-meta text-inksecondary">Settings → Integrations. Only currently available integration options are shown.</figcaption>
            </figure>
            <Link className="mt-3 inline-block text-bodysm font-medium" to="/app/settings?section=integrations">Open integrations →</Link>
          </>}
          {index === 8 && <>
            <figure className="mt-4 overflow-hidden rounded-md border border-linedefault bg-base">
              <img src="/app/start-here/completed-analysis-synthetic.png" alt="Current OMOS chat page with a clearly labeled synthetic example response." loading="lazy" className="block max-h-56 w-full object-cover object-top sm:max-h-64" />
              <figcaption className="border-t border-linesubtle px-3 py-2 text-meta text-inksecondary">Synthetic example shown in the current Chat interface; live results depend on the work OMOS completes.</figcaption>
            </figure>
            <figure className="mt-4 overflow-hidden rounded-md border border-linedefault bg-base">
              <img src="/app/start-here/main-chat-after-intro.png" alt="Account Manager chat home in a synthetic workspace, ready for a new request." loading="lazy" className="block max-h-56 w-full object-cover object-top sm:max-h-64" />
              <figcaption className="border-t border-linesubtle px-3 py-2 text-meta text-inksecondary">Account Manager home after the guide. The workspace and page state use synthetic data.</figcaption>
            </figure>
            <Link className="mt-3 inline-block text-bodysm font-medium" to="/app">Open Chat →</Link>
          </>}
          {index === 9 && <figure className="mt-4 overflow-hidden rounded-md border border-linedefault bg-base">
            <img src="/app/start-here/execution-tree-synthetic.png" alt="Current OMOS execution tree showing a synthetic Account Manager plan, specialist, tool and completed summary." loading="lazy" className="block max-h-56 w-full object-cover object-top sm:max-h-64" />
            <figcaption className="border-t border-linesubtle px-3 py-2 text-meta text-inksecondary">Synthetic activity rendered by the current execution-tree UI; it shows progress events, not private model reasoning.</figcaption>
          </figure>}
          {index === 10 && <Link className="mt-3 inline-block text-bodysm font-medium" to="/app/approvals">Review approvals →</Link>}
        </section>)}
      </div>
      <footer className="my-7 flex flex-wrap items-center justify-between gap-3 border-t border-linesubtle pt-4">
        <p className="text-meta text-inksecondary">You can reopen this guide any time from the sidebar.</p>
        <button type="button" disabled={saving} onClick={() => void continueTo("/app")} className="rounded border border-linedefault px-3 py-1.5 text-bodysm text-ink hover:bg-elevated">Done — don’t show automatically again</button>
      </footer>
    </div>
  </div>;
}
