import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, type SpaChat } from "../../api/client";
import EmptyState from "../chrome/EmptyState";
import Modal from "../chrome/Modal";
import { useToast } from "../chrome/Toast";
import { useProject } from "./project-context";

export type ChatGroup = "TODAY" | "YESTERDAY" | "OLDER";

const DAY_MS = 86_400_000;

/** Group a chat by local calendar day of updated_at. Invalid dates → OLDER. */
export function groupChatByDay(updatedAt: string, now: Date = new Date()): ChatGroup {
  const d = new Date(updatedAt);
  if (Number.isNaN(d.getTime())) return "OLDER";
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((startOfDay(now) - startOfDay(d)) / DAY_MS);
  if (diff <= 0) return "TODAY";
  if (diff === 1) return "YESTERDAY";
  return "OLDER";
}

function ChatSkeleton() {
  return (
    <div className="flex flex-col gap-2" aria-label="Loading chats">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="h-9 animate-pulse rounded-sm bg-elevated" />
      ))}
    </div>
  );
}

/**
 * DEV-004 W2: conversation history for the active project.
 * New Chat / search (q param) / TODAY-YESTERDAY-OLDER grouping /
 * inline rename / archive with confirm. Titles only — no IDs shown.
 */
export default function ChatList() {
  const { projectId, current } = useProject();
  const navigate = useNavigate();
  const location = useLocation();
  const { push } = useToast();
  const [chats, setChats] = useState<SpaChat[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [archiving, setArchiving] = useState<SpaChat | null>(null);
  const archiveOpenerRef = useRef<HTMLElement | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQ(query.trim()), 250);
    return () => window.clearTimeout(t);
  }, [query]);

  const load = useCallback(() => {
    if (!projectId) {
      setChats([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    api
      .getChats({ project_id: projectId, q: debouncedQ || undefined })
      .then((list) => {
        setChats(list);
        setLoading(false);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "failed to load chats");
        setLoading(false);
      });
  }, [projectId, debouncedQ]);

  useEffect(() => {
    load();
    // Revalidate when the route changes: a chat created via /app/chat/new
    // navigates straight to /app/chat/:id without remounting the sidebar,
    // so without this the new conversation is missing from history.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, location.pathname]);

  const groups = useMemo(() => {
    const out: Record<ChatGroup, SpaChat[]> = { TODAY: [], YESTERDAY: [], OLDER: [] };
    for (const c of chats) out[groupChatByDay(c.updated_at)].push(c);
    return out;
  }, [chats]);

  const startRename = (chat: SpaChat) => {
    setRenamingId(chat.id);
    setRenameValue(chat.title);
  };

  const commitRename = async (chat: SpaChat) => {
    const title = renameValue.trim();
    if (!title || title === chat.title) {
      setRenamingId(null);
      return;
    }
    if (title.length > 120) {
      push("Title must be 120 characters or fewer.", "error");
      return;
    }
    setBusyId(chat.id);
    try {
      const updated = await api.renameChat(chat.id, title);
      setChats((prev) => prev.map((c) => (c.id === chat.id ? updated : c)));
      push("Chat renamed.", "success");
    } catch {
      push("Rename failed.", "error");
    } finally {
      setBusyId(null);
      setRenamingId(null);
    }
  };

  const commitArchive = async (chat: SpaChat) => {
    setBusyId(chat.id);
    try {
      await api.archiveChat(chat.id);
      setChats((prev) => prev.filter((c) => c.id !== chat.id));
      push("Chat archived.", "success");
      // If the archived chat is open, leave it: land on the home prompt.
      if (location.pathname === `/app/chat/${chat.id}`) navigate("/app");
    } catch {
      push("Archive failed.", "error");
    } finally {
      setBusyId(null);
      setArchiving(null);
    }
  };

  if (!projectId) {
    return (
      <EmptyState
        title="No project selected"
        hint="Select or create a project to start chatting."
        action={
          <button
            type="button"
            onClick={() => navigate("/app/settings")}
            className="rounded-sm border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink hover:bg-surface"
          >
            Open Settings
          </button>
        }
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => navigate("/app/chat/new")}
          className="flex-1 rounded-sm bg-accent px-3 py-1.5 text-bodysm font-semibold text-white"
        >
          + New Chat
        </button>
      </div>
      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={`Search chats in ${current?.name ?? "project"}…`}
        aria-label="Search chats"
        className="w-full rounded-sm border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink placeholder:text-inksecondary"
      />
      {loading ? (
        <ChatSkeleton />
      ) : error ? (
        <div className="text-bodysm text-inksecondary">
          Couldn&apos;t load chats ({error}).{" "}
          <button type="button" onClick={load} className="underline">
            Retry
          </button>
        </div>
      ) : chats.length === 0 ? (
        <div className="text-bodysm text-inksecondary">
          {debouncedQ ? "No chats match your search." : "No conversations yet — start a new chat."}
        </div>
      ) : (
        (["TODAY", "YESTERDAY", "OLDER"] as ChatGroup[]).map(
          (g) =>
            groups[g].length > 0 && (
              <div key={g}>
                <div className="px-1 py-1 text-meta font-semibold uppercase tracking-wide text-inksecondary">
                  {g === "TODAY" ? "Today" : g === "YESTERDAY" ? "Yesterday" : "Older"}
                </div>
                <ul className="flex flex-col gap-0.5">
                  {groups[g].map((chat) => (
                    <li key={chat.id} className="group rounded-sm hover:bg-elevated">
                      {renamingId === chat.id ? (
                        <input
                          autoFocus
                          value={renameValue}
                          maxLength={120}
                          disabled={busyId === chat.id}
                          onChange={(e) => setRenameValue(e.target.value)}
                          onBlur={() => void commitRename(chat)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void commitRename(chat);
                            if (e.key === "Escape") setRenamingId(null);
                          }}
                          aria-label="Rename chat"
                          className="w-full rounded-sm border border-accent bg-base px-2 py-1.5 text-bodysm text-ink"
                        />
                      ) : (
                        <div className="flex items-center gap-1 px-2 py-1.5">
                          <button
                            type="button"
                            onClick={() => navigate(`/app/chat/${chat.id}`)}
                            title={chat.title}
                            className="min-w-0 flex-1 truncate text-left text-bodysm text-ink"
                          >
                            {chat.title}
                          </button>
                          <button
                            type="button"
                            onClick={() => startRename(chat)}
                            aria-label={`Rename ${chat.title}`}
                            className="hidden rounded-sm px-1 text-meta text-inksecondary group-hover:block group-focus-within:block [@media(hover:none)]:block"
                          >
                            ✎
                          </button>
                          <button
                            type="button"
                            onClick={(event) => { archiveOpenerRef.current = event.currentTarget; setArchiving(chat); }}
                            aria-label={`Archive ${chat.title}`}
                            className="hidden rounded-sm px-1 text-meta text-inksecondary group-hover:block group-focus-within:block [@media(hover:none)]:block"
                          >
                            🗄
                          </button>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ),
        )
      )}
      {archiving && (
        <Modal title="Archive chat" returnFocusRef={archiveOpenerRef} onClose={() => setArchiving(null)}>
          <p className="text-bodysm text-inksecondary">
            Archive “{archiving.title}”? It will disappear from this list but is never deleted.
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setArchiving(null)}
              className="rounded-sm border border-linedefault px-3 py-1.5 text-bodysm text-ink"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={busyId === archiving.id}
              onClick={() => void commitArchive(archiving)}
              className="rounded-sm bg-accent px-3 py-1.5 text-bodysm font-semibold text-white disabled:opacity-50"
            >
              Archive
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
