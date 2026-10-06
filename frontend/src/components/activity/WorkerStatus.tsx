import type { PanelEventLike } from "./honesty";

/**
 * DEV-004 W4: parallel-worker status groups.
 *
 * Derived ONLY from real `worker_started` / `worker_completed` /
 * `worker_failed` events, grouped by worker label — concurrent workers
 * appear as simultaneous "Running" rows that flip to checkmarks as their
 * completion events arrive. No timers, no fake animation.
 */

interface WorkerGroup {
  label: string;
  started: boolean;
  done: boolean;
  failed: boolean;
}

export function groupWorkerEvents(events: PanelEventLike[]): WorkerGroup[] {
  const groups = new Map<string, WorkerGroup>();
  for (const ev of events) {
    const t = ev.event_type.toLowerCase();
    if (
      t !== "worker_started" &&
      t !== "worker_completed" &&
      t !== "worker_failed"
    ) {
      continue;
    }
    const label = (ev.label || "").trim() || "Worker";
    let g = groups.get(label);
    if (!g) {
      g = { label, started: false, done: false, failed: false };
      groups.set(label, g);
    }
    if (t === "worker_started") g.started = true;
    if (t === "worker_completed") g.done = true;
    if (t === "worker_failed") g.failed = true;
  }
  return [...groups.values()];
}

export default function WorkerStatus({
  events,
}: {
  events: PanelEventLike[];
}) {
  const groups = groupWorkerEvents(events);
  if (groups.length === 0) return null;
  return (
    <div className="border-b border-linesubtle px-4 py-2">
      <ul className="space-y-1.5">
        {groups.map((g) => {
          const state = g.failed ? "failed" : g.done ? "done" : "running";
          return (
            <li
              key={g.label}
              className="flex items-center gap-2 text-bodysm"
              aria-label={`${g.label}: ${state}`}
            >
              {state === "done" ? (
                <span aria-hidden className="text-ok">
                  ✓
                </span>
              ) : state === "failed" ? (
                <span aria-hidden className="text-err">
                  ✕
                </span>
              ) : (
                <span
                  aria-hidden
                  className="h-2 w-2 rounded-full bg-accent animate-pulse"
                />
              )}
              <span className="min-w-0 flex-1 truncate text-ink">{g.label}</span>
              <span
                className={
                  state === "done"
                    ? "text-meta text-ok"
                    : state === "failed"
                      ? "text-meta text-err"
                      : "text-meta text-accenthover"
                }
              >
                {state === "done"
                  ? "Complete"
                  : state === "failed"
                    ? "Failed"
                    : "Running"}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
