import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, parseTurnIdFromAck, sendChatTurn } from "../api/client";
import { useTurnStream } from "../hooks/useTurnStream";
import type { TurnStatus } from "../hooks/useTurnStream";
import { usePersistedTurnTree } from "../hooks/usePersistedTurnTree";
import { publish } from "../components/activity/liveBus";
import ChatMessage from "../components/chat/ChatMessage";
import Composer from "../components/chat/Composer";
import EmptyChat from "../components/chat/EmptyChat";
import TurnRuntimePanel from "../components/runtime/TurnRuntimePanel";
import ExecutionTree from "../components/skills/ExecutionTree";
import { useProject } from "../components/shell/project-context";

/**
 * DEV-004 W3: chat thread route (`/app/chat/:id`, :id = conversation id).
 *
 * - Loads transcript via GET /api/chats/{id}/messages.
 * - Send flow: optimistic user bubble → sendChatTurn (existing POST
 *   /chat/turn) → parseTurnIdFromAck → useTurnStream attaches the SSE.
 * - Lifecycle events from the stream are published into the shared live
 *   bus (`components/activity/liveBus.ts`) for W4's activity panel.
 * - Retry (failed only): POST /chat/turn/{id}/retry → new turn id from the
 *   ack HTML → stream the new turn. Regenerate is intentionally omitted
 *   (see ChatMessage: unsafe to re-run tool side effects).
 * - DEV-008-SKILLS-OPS: `<ExecutionTree>` renders this turn's real execution
 *   tree from the same lifecycle rows the activity panel consumes. It derives
 *   from `meta.parent_id` / `meta.status` and invents nothing, and it returns
 *   null when a turn emitted no tree events, so legacy turns are unaffected.
 *   It is a separate component from the frozen `GRAPH_NODES` route list
 *   (`api/runtime.ts`), which this route does not touch.
 * - DEV-008-SKILLS-OPS W12: the tree also stays inspectable after a reload.
 *   When there is no live activeTurnId, `usePersistedTurnTree` reads the
 *   conversation's latest finished turn (GET /api/chats/{id}/last_turn) and
 *   replays its persisted execution rows into the SAME `ExecutionTree` with
 *   `live: false` — the replay never animates and never publishes to the
 *   live bus. The live send/retry paths are untouched.
 *
 * - DEV-008-SKILLS-OPS-HOTFIX: the two `<ExecutionTree>` renders below are the
 *   ONLY two, and they are mutually exclusive by construction — the first is
 *   gated on `activeTurnId`, the second on `!activeTurnId`, so the live tree
 *   and the persisted replay can never both be on screen. They are also
 *   guaranteed to AGREE, by two invariants that live in the hooks and in the
 *   tree builder rather than here:
 *     * ONE shared lifecycle filter — `isLifecycleEventType` from
 *       `useTurnStream`, applied by `usePersistedTurnTree` too, so a reload
 *       yields the same ROWS as the live stream. (The previous two
 *       hand-written copies disagreed about `turn_completed` / `turn_failed`.)
 *     * `buildExecutionTree` sorts every level by persisted
 *       `execution_events.id`, so it is a function of the row SET, not of
 *       arrival order.
 *   Both `<ExecutionTree ... />` call sites keep their exact previous shape on
 *   purpose: `test_dev008so_frontend_wiring.py` pins the live one against a
 *   second, independently-attached stream.
 */

interface Msg {
  key: string;
  role: "user" | "assistant";
  bodyMd: string;
  turnId?: string | null;
  failed?: boolean;
  streaming?: boolean;
  latencyMs?: number | null;
  citations?: unknown[];
}

function newClientId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export default function Chat() {
  const { id } = useParams<{ id: string }>();
  const convoId = id ?? "";
  const {
    projectId,
    loading: projectLoading,
    hasExplicitProjectSelection,
    projects,
    setProjectIdFromConversation,
  } = useProject();
  const [conversationBinding, setConversationBinding] = useState<{
    conversationId: string;
    projectId: string;
  } | null>(null);
  const sendLockRef = useRef(false);
  const activeScopeRef = useRef("");
  const scopeVersionRef = useRef(0);
  const hydratedProvenanceRef = useRef(new Set<string>());
  const pendingSendRef = useRef<{ scope: string; signature: string; clientId: string } | null>(null);
  const draftProjectId = projectLoading
    ? null
    : !hasExplicitProjectSelection && convoId
    ? conversationBinding?.conversationId === convoId
      ? conversationBinding.projectId
      : null
    : projectId;
  const scopeKey = `${draftProjectId ?? "no-project"}:${convoId || "new-chat"}`;
  if (activeScopeRef.current !== scopeKey) {
    activeScopeRef.current = scopeKey;
    scopeVersionRef.current++;
  }

  const [messages, setMessages] = useState<Msg[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [approvalsCount, setApprovalsCount] = useState(0);

  const stream = useTurnStream(activeTurnId);
  // DEV-008-SKILLS-OPS W12: when there is NO live active turn (reload,
  // conversation re-entry), replay the latest finished turn's persisted
  // execution tree so the tree stays inspectable after a reload. The live
  // path below (handleSend / handleRetry) is untouched: while a turn is
  // active here the persisted hook is disabled and renders nothing.
  const persisted = usePersistedTurnTree(
    activeTurnId ? null : convoId,
    !activeTurnId,
  );
  const publishedRef = useRef<Set<number>>(new Set());
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    sendLockRef.current = false;
    setSending(false);
    setSendError(null);
  }, [scopeKey]);

  // Conversations have an immutable project binding. Resolve it independently
  // of the active project and fail closed until that binding is known.
  useEffect(() => {
    let cancelled = false;
    setConversationBinding(null);
    if (!convoId) return;
    api.getChats({ status: "all" }).then((chats) => {
      if (!cancelled) {
        const conversation = chats.find((chat) => chat.id === convoId);
        setConversationBinding(conversation
          ? { conversationId: convoId, projectId: conversation.project_id }
          : null);
      }
    }).catch(() => {
      if (!cancelled) setConversationBinding(null);
    });
    return () => { cancelled = true; };
  }, [convoId]);

  // A deep-linked conversation is authoritative when there is no valid stored
  // or user-selected project. Keep a provisional draft scope until its binding
  // resolves, then align the provider to that project. Explicit choices win.
  useEffect(() => {
    if (
      projectLoading ||
      hasExplicitProjectSelection ||
      conversationBinding?.conversationId !== convoId ||
      !projects.some((project) => project.id === conversationBinding.projectId)
    ) return;
    if (projectId !== conversationBinding.projectId) {
      setProjectIdFromConversation(conversationBinding.projectId);
    }
  }, [
    projectLoading,
    hasExplicitProjectSelection,
    conversationBinding,
    convoId,
    projects,
    projectId,
    setProjectIdFromConversation,
  ]);

  // Load transcript when the conversation changes.
  useEffect(() => {
    if (!convoId) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setMessages([]);
    setActiveTurnId(null);
    setActiveKey(null);
    publishedRef.current = new Set();
    api
      .getMessages(convoId)
      .then((rows) => {
        if (cancelled) return;
        setMessages(
          rows.map((m, i) => ({
            key: `hist-${i}`,
            role: m.role === "user" ? "user" : "assistant",
            bodyMd: m.body_md ?? "",
            citations: Array.isArray(m.citations) ? m.citations : [],
          })),
        );
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "Failed to load messages");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [convoId]);

  // Pending-approval count for EmptyChat ordering (best-effort, never fatal).
  useEffect(() => {
    let cancelled = false;
    api
      .getApprovals(projectId ? { project_id: projectId } : {})
      .then((rows) => {
        if (!cancelled) setApprovalsCount(rows.filter((a) => a.status === "pending").length);
      })
      .catch(() => {
        if (!cancelled) setApprovalsCount(0);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Fold stream state into the active assistant message + publish lifecycle.
  useEffect(() => {
    if (!activeTurnId || !activeKey) return;
    const turnId = activeTurnId;
    const key = activeKey;
    for (const ev of stream.events) {
      if (publishedRef.current.has(ev.id)) continue;
      publishedRef.current.add(ev.id);
      publish({
        id: ev.id,
        conversationId: convoId,
        turnId,
        event_type: ev.type,
        label: ev.label,
        detail: ev.detail,
        at: Date.now(),
      });
    }
    setMessages((prev) =>
      prev.map((m) => {
        if (m.key !== key) return m;
        const next: Msg = {
          ...m,
          bodyMd: stream.text || m.bodyMd,
          streaming: stream.status === "working" || stream.status === "streaming",
          failed: stream.status === "failed",
        };
        if (stream.status === "done" || stream.status === "failed") {
          next.streaming = false;
          next.latencyMs = stream.latencyMs;
        }
        return next;
      }),
    );
    if (stream.status === "done" || stream.status === "failed") {
      setSending(false);
      sendLockRef.current = false;
    }
    if (stream.status === "done" && activeTurnId && !hydratedProvenanceRef.current.has(activeTurnId)) {
      const completedTurnId = activeTurnId;
      const completedScope = activeScopeRef.current;
      hydratedProvenanceRef.current.add(completedTurnId);
      api.getMessages(convoId).then((rows) => {
        if (activeScopeRef.current !== completedScope || activeTurnId !== completedTurnId) return;
        const latestAssistant = rows.filter((row) => row.role !== "user").pop();
        if (!latestAssistant) return;
        setMessages((prev) => prev.map((message) => message.key === key && message.turnId === completedTurnId
          ? { ...message, citations: Array.isArray(latestAssistant.citations) ? latestAssistant.citations : [] }
          : message));
      }).catch(() => {
        hydratedProvenanceRef.current.delete(completedTurnId);
      });
    }
  }, [activeTurnId, activeKey, convoId, stream.events, stream.text, stream.status, stream.latencyMs]);

  // Keep the transcript pinned to the latest content.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, stream.text]);

  const handleSend = useCallback(
    async (text: string, attachmentIds: string[] = []): Promise<boolean> => {
      const clean = text.trim();
      const submittedScope = scopeKey;
      const submittedVersion = scopeVersionRef.current;
      if (!clean || !convoId || !projectId || sending || sendLockRef.current) return false;
      if (
        conversationBinding?.conversationId !== convoId ||
        conversationBinding.projectId !== projectId ||
        activeScopeRef.current !== submittedScope
      ) return false;
      sendLockRef.current = true;
      setSendError(null);
      setSending(true);
      const userKey = `u-${newClientId()}`;
      const asstKey = `a-${newClientId()}`;
      const signature = JSON.stringify([clean, attachmentIds]);
      if (pendingSendRef.current?.scope !== submittedScope || pendingSendRef.current.signature !== signature) {
        pendingSendRef.current = { scope: submittedScope, signature, clientId: newClientId() };
      }
      const clientId = pendingSendRef.current.clientId;
      // Optimistic user bubble: visible immediately, before POST resolves.
      setMessages((prev) => [...prev, { key: userKey, role: "user", bodyMd: clean }]);
      return sendChatTurn(convoId, clean, clientId, attachmentIds)
        .then(({ turnId }) => {
          if (activeScopeRef.current !== submittedScope || scopeVersionRef.current !== submittedVersion) return false;
          pendingSendRef.current = null;
          setMessages((prev) => [
            ...prev,
            { key: asstKey, role: "assistant", bodyMd: "", turnId, streaming: true },
          ]);
          publishedRef.current = new Set();
          setActiveKey(asstKey);
          setActiveTurnId(turnId);
          return true;
        })
        .catch((err: unknown) => {
          if (activeScopeRef.current !== submittedScope || scopeVersionRef.current !== submittedVersion) return false;
          setSending(false);
          sendLockRef.current = false;
          setSendError(err instanceof Error ? err.message : "Send failed");
          // Optimistic bubble stays; the error bar above the composer
          // surfaces the failure and the user can re-send.
          return false;
        });
    },
    [convoId, conversationBinding, projectId, scopeKey, sending],
  );

  const handleRetry = useCallback(
    (oldTurnId: string) => {
      if (sending) return;
      setSendError(null);
      setSending(true);
      fetch(`/chat/turn/${encodeURIComponent(oldTurnId)}/retry`, { method: "POST" })
        .then((res) => {
          if (!res.ok) throw new Error(`retry failed: ${res.status}`);
          return res.text();
        })
        .then((html) => {
          const newTurnId = parseTurnIdFromAck(html);
          const asstKey = `a-${newClientId()}`;
          setMessages((prev) => [
            ...prev,
            { key: asstKey, role: "assistant", bodyMd: "", turnId: newTurnId, streaming: true },
          ]);
          publishedRef.current = new Set();
          setActiveKey(asstKey);
          setActiveTurnId(newTurnId);
        })
        .catch((err: unknown) => {
          setSending(false);
          setSendError(err instanceof Error ? err.message : "Retry failed");
        });
    },
    [sending],
  );

  const statusLine = (s: TurnStatus): string | null => {
    if (s === "working") return "Working…";
    if (s === "streaming") return "Streaming…";
    return null;
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        <div className="mx-auto flex w-full max-w-[min(46rem,100%)] flex-col gap-4">
          {loading ? (
            <div className="flex flex-col gap-3" aria-label="Loading messages">
              <div className="h-16 animate-pulse rounded-md bg-elevated" />
              <div className="h-24 animate-pulse rounded-md bg-elevated" />
              <div className="h-16 animate-pulse rounded-md bg-elevated" />
            </div>
          ) : loadError ? (
            <div className="rounded-md border border-err/40 px-4 py-3 text-bodysm text-err">
              Couldn&apos;t load this conversation: {loadError}
            </div>
          ) : messages.length === 0 && !sending ? (
            <EmptyChat onPick={handleSend} approvalsCount={approvalsCount} />
          ) : (
            messages.map((m) => (
              <ChatMessage
                key={m.key}
                role={m.role}
                bodyMd={m.bodyMd}
                citations={m.citations}
                projectId={projectId}
                conversationId={convoId}
                failed={m.failed}
                streaming={m.streaming}
                turnId={m.turnId}
                latencyMs={m.latencyMs}
                onRetry={handleRetry}
              />
            ))
          )}
          {activeTurnId && statusLine(stream.status) ? (
            <div className="text-meta text-inkmuted" aria-live="polite">
              {statusLine(stream.status)}
              {stream.latencyMs != null ? ` · ${stream.latencyMs} ms` : null}
            </div>
          ) : null}
          {activeTurnId ? <ExecutionTree events={stream.events} /> : null}
          {!activeTurnId && persisted.turnId ? (
            <ExecutionTree events={persisted.events} live={false} />
          ) : null}
          {activeTurnId ? (
            <TurnRuntimePanel turnId={activeTurnId} />
          ) : null}
        </div>
      </div>
      {sendError ? (
        <div className="border-t border-err/40 bg-raised px-4 py-2">
          <div className="mx-auto flex w-full max-w-[min(46rem,100%)] items-center gap-2 text-bodysm text-err">
            <span className="flex-1">Send failed: {sendError}</span>
            <button
              type="button"
              onClick={() => setSendError(null)}
              className="rounded-sm border border-linedefault px-2 py-0.5 text-meta text-inksecondary"
            >
              Dismiss
            </button>
          </div>
        </div>
      ) : null}
      <Composer
        onSend={handleSend}
        sending={sending}
        scopeKey={scopeKey}
        projectId={projectId}
        conversationId={convoId}
        projectLoading={projectLoading}
        migrateFromScopeKey={
          !hasExplicitProjectSelection && conversationBinding?.conversationId === convoId
            ? `no-project:${convoId}`
            : undefined
        }
        canSubmit={Boolean(
          projectId &&
          conversationBinding?.conversationId === convoId &&
          conversationBinding.projectId === projectId
        )}
      />
    </div>
  );
}
