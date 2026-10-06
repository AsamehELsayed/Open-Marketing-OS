import { friendlyLabel, type PanelEventLike } from "./honesty";

/**
 * DEV-004 W4: single activity event row — status dot + friendly label +
 * (dev-mode only) detail. Never renders ids.
 */

function dotTone(eventType: string, label: string): string {
  const hay = `${eventType} ${label}`.toLowerCase();
  if (/(fail|error|reject|block)/.test(hay)) return "bg-err";
  if (/(complete|finish|ready|done|approve|update)/.test(hay)) return "bg-ok";
  if (/(approv|pending|needs|waiting)/.test(hay)) return "bg-warn";
  if (/(start|run|work|writ|synthesis|check)/.test(hay))
    return "bg-accent animate-pulse";
  return "bg-inkmuted";
}

export default function ExecutionEvent({
  event,
  showDetail,
}: {
  event: PanelEventLike;
  showDetail: boolean;
}) {
  return (
    <li className="flex items-start gap-2.5 px-4 py-2">
      <span
        aria-hidden
        className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${dotTone(event.event_type, event.label)}`}
      />
      <div className="min-w-0">
        <p className="text-bodysm text-ink">{friendlyLabel(event)}</p>
        {showDetail && event.detail.trim() && (
          <p className="mt-0.5 break-words text-meta text-inksecondary">
            {event.detail}
          </p>
        )}
      </div>
    </li>
  );
}
