import { Link } from "react-router-dom";
import { useTurnRuntime } from "../../hooks/useTurnRuntime";
import EscalationTrail from "./EscalationTrail";
import ModelBadge from "./ModelBadge";
import TokenCostFooter from "./TokenCostFooter";

/**
 * DEV-005 W7: per-turn runtime panel for the chat thread.
 *
 * Wires `useTurnRuntime(turnId)` (frozen SSE + W6 REST supplement with
 * cursor-based reconnect) to the visible badge, escalation trail, and
 * token/cost footer. Additive: the message stream itself stays owned by
 * `useTurnStream`.
 */

export default function TurnRuntimePanel({
  turnId,
}: {
  turnId: string | null;
}) {
  const runtime = useTurnRuntime(turnId);
  if (!turnId) return null;
  return (
    <div data-testid="turn-runtime-panel" className="rounded-md border border-linesubtle bg-raised px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <ModelBadge calls={runtime.calls} live={runtime.live} />
        <span className="flex-1" />
        <Link
          to={`/app/graph?turn=${encodeURIComponent(turnId)}`}
          className="text-meta text-inksecondary hover:text-ink"
        >
          Graph view
        </Link>
      </div>
      <div className="mt-2 grid gap-2">
        <EscalationTrail steps={runtime.escalation} />
        <TokenCostFooter totals={runtime.totals} calls={runtime.calls} />
      </div>
      {!runtime.live && runtime.reconnects > 0 ? (
        <p className="mt-1 text-meta text-inkmuted" aria-live="polite">
          Stream reconnecting… (attempt {runtime.reconnects})
        </p>
      ) : null}
    </div>
  );
}
