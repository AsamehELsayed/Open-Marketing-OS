import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type SpaChat, type SpaProject } from "../../api/client";
import { useToast } from "../chrome/Toast";
import { useProject } from "../shell/project-context";

type Mode = "root" | "projects" | "campaigns" | "chats";

interface Entry {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
}

const ROOT_ACTIONS = [
  { id: "new-chat", label: "New chat", hint: "create a conversation" },
  { id: "switch-project", label: "Switch project…", hint: "jump to another project" },
  { id: "open-campaign", label: "Open campaign…", hint: "list campaigns" },
  { id: "open-approvals", label: "Open approvals", hint: "review queue" },
  { id: "open-settings", label: "Open settings", hint: "project settings" },
  { id: "search-chats", label: "Search chats…", hint: "find and jump" },
] as const;

/**
 * DEV-004 W2: global Ctrl/Cmd+K palette. Mounted once in App.
 * Arrow keys + Enter + Esc navigable; submenu modes for projects,
 * campaigns, and chat search.
 */
export default function CommandMenu() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<Mode>("root");
  const [index, setIndex] = useState(0);
  const [projects, setProjects] = useState<SpaProject[]>([]);
  const [campaigns, setCampaigns] = useState<{ id: string; title: string }[]>([]);
  const [chats, setChats] = useState<SpaChat[]>([]);
  const [loading, setLoading] = useState(false);
  const { projectId, setProjectId } = useProject();
  const navigate = useNavigate();
  const { push } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);

  const reset = useCallback(() => {
    setQuery("");
    setMode("root");
    setIndex(0);
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    reset();
  }, [reset]);

  // Global Ctrl/Cmd+K toggle.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => {
          if (v) reset();
          return !v;
        });
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [reset]);

  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
      setLoading(true);
      const pid = projectId ?? undefined;
      Promise.all([
        api.getProjects().catch(() => [] as SpaProject[]),
        api
          .getCampaigns(pid)
          .catch(() => [] as Record<string, unknown>[])
          .then((rows) =>
            rows.map((r) => ({
              id: String(r["id"] ?? ""),
              title: String(r["name"] ?? r["title"] ?? r["id"] ?? "Untitled campaign"),
            })),
          ),
        api.getChats(pid ? { project_id: pid } : {}).catch(() => [] as SpaChat[]),
      ])
        .then(([p, c, ch]) => {
          setProjects(p);
          setCampaigns(c.filter((x) => x.id));
          setChats(ch);
        })
        .finally(() => setLoading(false));
    }
  }, [open, projectId]);

  const go = useCallback(
    (to: string) => {
      close();
      navigate(to);
    },
    [close, navigate],
  );

  const entries: Entry[] = useMemo(() => {
    const q = query.trim().toLowerCase();
    const match = (s: string) => !q || s.toLowerCase().includes(q);

    if (mode === "projects") {
      return projects
        .filter((p) => match(p.name))
        .map((p) => ({
          id: `project:${p.id}`,
          label: p.name,
          hint: "switch project",
          run: () => {
            setProjectId(p.id);
            go("/app");
          },
        }));
    }
    if (mode === "campaigns") {
      return campaigns
        .filter((c) => match(c.title))
        .map((c) => ({
          id: `campaign:${c.id}`,
          label: c.title,
          hint: "open campaign",
          run: () => go(`/app/campaigns/${c.id}`),
        }));
    }
    if (mode === "chats") {
      return chats
        .filter((c) => match(c.title))
        .map((c) => ({
          id: `chat:${c.id}`,
          label: c.title,
          hint: "open chat",
          run: () => go(`/app/chat/${c.id}`),
        }));
    }
    const actions: Entry[] = [];
    for (const a of ROOT_ACTIONS) {
      if (!match(a.label)) continue;
      switch (a.id) {
        case "new-chat":
          actions.push({ id: a.id, label: a.label, hint: a.hint, run: () => go("/app/chat/new") });
          break;
        case "switch-project":
          actions.push({
            id: a.id,
            label: a.label,
            hint: a.hint,
            run: () => {
              setMode("projects");
              setQuery("");
              setIndex(0);
            },
          });
          break;
        case "open-campaign":
          actions.push({
            id: a.id,
            label: a.label,
            hint: a.hint,
            run: () => {
              setMode("campaigns");
              setQuery("");
              setIndex(0);
            },
          });
          break;
        case "open-approvals":
          actions.push({ id: a.id, label: a.label, hint: a.hint, run: () => go("/app/approvals") });
          break;
        case "open-settings":
          actions.push({ id: a.id, label: a.label, hint: a.hint, run: () => go("/app/settings") });
          break;
        case "search-chats":
          actions.push({
            id: a.id,
            label: a.label,
            hint: a.hint,
            run: () => {
              setMode("chats");
              setQuery("");
              setIndex(0);
            },
          });
          break;
      }
    }
    return actions;
  }, [mode, query, projects, campaigns, chats, go, setProjectId]);

  useEffect(() => {
    setIndex(0);
  }, [query, mode]);

  useEffect(() => {
    if (!open) return;
    if (index >= entries.length) setIndex(0);
  }, [open, index, entries.length]);

  const onPaletteKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      if (mode !== "root") {
        setMode("root");
        setQuery("");
        setIndex(0);
      } else {
        close();
      }
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setIndex((i) => (entries.length ? (i + 1) % entries.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setIndex((i) => (entries.length ? (i - 1 + entries.length) % entries.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const entry = entries[index];
      if (entry) {
        try {
          entry.run();
        } catch {
          push("That action failed.", "error");
        }
      }
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-24">
      <button type="button" aria-label="Close command menu" onClick={close} className="absolute inset-0 bg-black/60" />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command menu"
        className="relative w-full max-w-lg overflow-hidden rounded-lg border border-linedefault bg-raised shadow-lg"
      >
        <div className="flex items-center gap-2 border-b border-linesubtle px-3">
          {mode !== "root" && (
            <button
              type="button"
              aria-label="Back to actions"
              onClick={() => {
                setMode("root");
                setQuery("");
                setIndex(0);
              }}
              className="text-bodysm text-inksecondary"
            >
              ←
            </button>
          )}
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onPaletteKey}
            placeholder={
              mode === "root"
                ? "Type a command… (↑↓ navigate, ⏎ run, esc close)"
                : mode === "projects"
                  ? "Pick a project…"
                  : mode === "campaigns"
                    ? "Pick a campaign…"
                    : "Search chats…"
            }
            aria-label="Command menu input"
            className="w-full bg-transparent py-3 text-body text-ink outline-none placeholder:text-inksecondary"
          />
        </div>
        <ul role="listbox" aria-label="Results" className="max-h-72 overflow-y-auto py-1">
          {loading && (
            <li className="px-3 py-2 text-bodysm text-inksecondary">Loading…</li>
          )}
          {!loading &&
            entries.map((entry, i) => (
              <li key={entry.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={i === index}
                  onMouseEnter={() => setIndex(i)}
                  onClick={() => entry.run()}
                  className={[
                    "flex w-full items-center gap-2 px-3 py-2 text-left text-bodysm",
                    i === index ? "bg-elevated text-ink" : "text-inksecondary",
                  ].join(" ")}
                >
                  <span className="min-w-0 flex-1 truncate">{entry.label}</span>
                  {entry.hint && <span className="text-meta text-inksecondary">{entry.hint}</span>}
                </button>
              </li>
            ))}
          {!loading && entries.length === 0 && (
            <li className="px-3 py-2 text-bodysm text-inksecondary">No matches.</li>
          )}
        </ul>
      </div>
    </div>
  );
}
