import { useState } from "react";
import { useNavigate } from "react-router-dom";
import EmptyState from "../chrome/EmptyState";
import PageHeader from "../chrome/PageHeader";
import { useToast } from "../chrome/Toast";
import { useProject } from "./project-context";

/**
 * DEV-004 W2: new-chat surface. Lists projects, creates the conversation via
 * the EXISTING legacy endpoint POST /chat/new (form: project_id), then routes
 * to /app/chat/{id}. Zero backend changes.
 *
 * Implementation note: the spec asked for `redirect: "manual"` + Location
 * parsing, but same-origin manual redirects surface as `opaqueredirect`
 * (status 0, headers unreadable) in browsers. Instead we let fetch follow the
 * 303 and parse the conversation id from `response.url` (/chat/{id}).
 */
export default function NewChat() {
  const { projects, projectId, loading, setProjectId } = useProject();
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();
  const { push } = useToast();

  const activeId = selected ?? projectId;

  if (!loading && projects.length === 0) {
    return (
      <div className="p-6">
        <EmptyState
          title="No projects yet"
          hint="Set up your business first, then your Account Manager can tailor its work to you."
          action={
            <button
              type="button"
              onClick={() => navigate("/app/business/new")}
              className="rounded-sm border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink hover:bg-surface"
            >
              Create business
            </button>
          }
        />
      </div>
    );
  }

  const create = async () => {
    if (!activeId || creating) return;
    setCreating(true);
    try {
      const form = new URLSearchParams();
      form.set("project_id", activeId);
      const res = await fetch("/chat/new", { method: "POST", body: form });
      if (!res.ok) throw new Error(`create failed: ${res.status}`);
      const m = /\/chat\/([A-Za-z0-9_-]+)(?:[/?#]|$)/.exec(res.url);
      if (!m) throw new Error("could not parse new chat id");
      setProjectId(activeId);
      navigate(`/app/chat/${m[1]}`);
    } catch {
      push("Could not create the chat. Please retry.", "error");
      setCreating(false);
    }
  };

  return (
    <div className="mx-auto max-w-xl p-6">
      <PageHeader title="New chat" description="Pick a project, then start talking." />
      {loading ? (
        <div className="mt-4 flex flex-col gap-2" aria-label="Loading projects">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-11 animate-pulse rounded-sm bg-elevated" />
          ))}
        </div>
      ) : (
        <ul className="mt-4 flex flex-col gap-2">
          {projects.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => setSelected(p.id)}
                aria-pressed={activeId === p.id}
                className={[
                  "w-full rounded-md border px-4 py-3 text-left",
                  activeId === p.id
                    ? "border-accent bg-elevated"
                    : "border-linedefault bg-raised hover:bg-elevated",
                ].join(" ")}
              >
                <div className="text-bodysm font-semibold text-ink">{p.name}</div>
                {p.goal && <div className="mt-0.5 truncate text-meta text-inksecondary">{p.goal}</div>}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 flex justify-end">
        <button
          type="button"
          onClick={() => void create()}
          disabled={!activeId || creating || loading}
          className="rounded-sm bg-accent px-4 py-2 text-bodysm font-semibold text-white disabled:opacity-50"
        >
          {creating ? "Creating…" : "Start chat"}
        </button>
      </div>
    </div>
  );
}
