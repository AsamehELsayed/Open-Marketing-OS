import { Link } from "react-router-dom";
import StatusBadge from "../chrome/StatusBadge";
import { filterEvents, friendlyLabel, type PanelEventLike } from "./honesty";
import type { SpaJob } from "../../api/client";

/**
 * DEV-004 W4: background-job card — kind, status badge, timestamps, a
 * stage timeline derived from the job's own events, and a "View process"
 * deep-link to the legacy full-page job view (`/jobs/{id}/process`).
 * No JSON job-detail endpoint exists in the W1 contract, so the legacy
 * link is the process view (documented, not hidden).
 */

function formatWhen(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function JobCard({
  job,
  events,
  devMode,
}: {
  job: SpaJob;
  /** Conversation/panel events — the card picks its own job stages. */
  events?: PanelEventLike[];
  devMode?: boolean;
}) {
  const stages = (events ?? []).filter((ev) => {
    const t = ev.event_type.toLowerCase();
    if (t === "job_created" || t === "job_status_changed") return true;
    return job.id !== "" && `${ev.label} ${ev.detail}`.includes(job.id);
  });
  const visible = filterEvents(stages, devMode ?? false);

  return (
    <div className="rounded-sm border border-linedefault bg-base px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-bodysm font-medium text-ink">
          {job.kind || "Job"}
        </span>
        <StatusBadge status={job.status} />
      </div>
      <p className="mt-1 text-meta text-inksecondary">
        Created {formatWhen(job.created_at)} · Updated{" "}
        {formatWhen(job.updated_at)}
      </p>
      {visible.length > 0 && (
        <ol className="mt-2 space-y-1 border-t border-linesubtle pt-2">
          {visible.map((ev, i) => (
            <li key={i} className="flex items-start gap-2 text-meta">
              <span
                aria-hidden
                className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-inkmuted"
              />
              <span className="text-inksecondary">{friendlyLabel(ev)}</span>
            </li>
          ))}
        </ol>
      )}
      <Link
        to="/app/jobs"
        className="mt-2 inline-block text-meta text-accenthover hover:underline"
      >
        View in Jobs queue
      </Link>
    </div>
  );
}
