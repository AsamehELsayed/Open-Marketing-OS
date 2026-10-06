import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { api, type SpaActivityEvent, type SpaJob } from "../../api/client";
import { useProject } from "../shell/project-context";
import ExecutionEvent from "./ExecutionEvent";
import { filterEvents, type PanelEventLike } from "./honesty";
import { subscribeLiveBus, type LiveBusEvent } from "./liveBus";
import JobCard from "./JobCard";
import WorkerStatus from "./WorkerStatus";

/**
 * DEV-004 W4: right-rail live activity panel (AppShell `activity` slot).
 *
 * - Resolves the conversation from props or the `/app/chat/:id` route
 *   (location-aware so the AppShell-level slot needs no W3 wiring).
 * - Loads persisted history via GET /api/activity?conversation_id= on
 *   conversation change + appends live turn events from `liveBus`
 *   (W3 ChatThread publishes; panel degrades to history-only if it doesn't).
 */

const ACTIVE_JOB_STATUS = new Set([
  "queued",
  "running",
  "executing",
  "pending",
  "measuring",
  "in_progress",
  "started",
]);

function conversationIdFromPath(pathname: string): string | null {
  const m = /^\/app\/chat\/([^/]+)/.exec(pathname);
  if (!m) return null;
  // "new" is the NewChat route sentinel, not a conversation id.
  const id = decodeURIComponent(m[1]);
  return id && id !== "new" ? id : null;
}

function toPanelEvent(ev: SpaActivityEvent | LiveBusEvent): PanelEventLike & { id: number | string } {
  return {
    id: ev.id,
    event_type: ev.event_type,
    label: ev.label,
    detail: ev.detail,
  };
}

export default function ActivityPanel({
  conversationId: propId,
}: {
  conversationId?: string;
}) {
  const location = useLocation();
  const { projectId } = useProject();
  const routeId = conversationIdFromPath(location.pathname);
  const conversationId = propId ?? routeId;

  const [events, setEvents] = useState<SpaActivityEvent[]>([]);
  const [live, setLive] = useState<LiveBusEvent[]>([]);
  const [turnLine, setTurnLine] = useState<string>("Idle");
  const [jobs, setJobs] = useState<SpaJob[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Persisted history reloads whenever the conversation changes.
  useEffect(() => {
    setLive([]);
    setTurnLine("Idle");
    if (!conversationId) {
      setEvents([]);
      return;
    }
    let cancelled = false;
    api
      .getActivity(conversationId)
      .then((rows) => {
        if (!cancelled) {
          setEvents(rows);
          setError(null);
        }
      })
      .catch(() => {
        if (!cancelled) setError("Could not load activity.");
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  // Live turn events from the bus (W3 publishes; ignore other conversations).
  useEffect(
    () =>
      subscribeLiveBus((e) => {
        if (
          e.conversationId &&
          conversationId &&
          e.conversationId !== conversationId
        ) {
          return;
        }
        setLive((prev) => [...prev.slice(-99), e]);
        const t = e.event_type.toLowerCase();
        if (t === "turn_started") setTurnLine("Working…");
        else if (t === "turn_completed") setTurnLine("Idle — answer ready");
        else if (t === "turn_failed") setTurnLine("Idle — turn failed");
      }),
    [conversationId],
  );

  // Active project jobs for the JobCards section.
  useEffect(() => {
    if (!projectId) {
      setJobs([]);
      return;
    }
    let cancelled = false;
    api
      .getJobs(projectId)
      .then((rows) => {
        if (!cancelled) setJobs(rows);
      })
      .catch(() => {
        if (!cancelled) setJobs([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const merged = useMemo(
    () => [...events.map(toPanelEvent), ...live.map(toPanelEvent)],
    [events, live],
  );
  const visible = useMemo(() => filterEvents(merged, false), [merged]);
  const activeJobs = useMemo(
    () =>
      jobs
        .filter((j) => ACTIVE_JOB_STATUS.has((j.status || "").toLowerCase()))
        .slice(0, 3),
    [jobs],
  );

  if (collapsed) {
    return (
      <div className="p-3">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="w-full rounded-sm border border-linedefault px-2 py-1 text-bodysm text-inksecondary"
        >
          Show live activity
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-4 py-2.5">
        <span className="text-meta font-semibold uppercase tracking-wide text-inksecondary">
          Live activity
        </span>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          className="rounded-sm px-2 py-0.5 text-meta text-inksecondary"
          aria-label="Collapse activity panel"
        >
          Hide
        </button>
      </div>

      <p className="border-y border-linesubtle px-4 py-2 text-bodysm text-inksecondary">
        {conversationId ? turnLine : "Open a chat to see live activity"}
      </p>

      {error && (
        <p className="px-4 py-2 text-bodysm text-err">{error}</p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        <WorkerStatus events={visible} />

        {visible.length === 0 ? (
          <p className="px-4 py-3 text-bodysm text-inkmuted">
            {conversationId
              ? "No activity yet — send a message to start."
              : "No conversation selected."}
          </p>
        ) : (
          <ul className="divide-y divide-linesubtle">
            {visible.map((ev, i) => (
              <ExecutionEvent
                key={`${String(ev.id)}-${i}`}
                event={ev}
                showDetail={false}
              />
            ))}
          </ul>
        )}

        {activeJobs.length > 0 && (
          <div className="space-y-2 border-t border-linesubtle px-3 py-3">
            <p className="text-meta font-semibold uppercase tracking-wide text-inksecondary">
              Active jobs
            </p>
            {activeJobs.map((j) => (
              <JobCard key={j.id} job={j} events={visible} devMode={false} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
