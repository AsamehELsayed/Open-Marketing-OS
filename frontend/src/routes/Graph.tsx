import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTurnRuntime } from "../hooks/useTurnRuntime";
import PageHeader from "../components/chrome/PageHeader";
import EscalationTrail from "../components/runtime/EscalationTrail";
import GraphNodeList from "../components/runtime/GraphNodeList";
import ModelBadge from "../components/runtime/ModelBadge";
import TokenCostFooter from "../components/runtime/TokenCostFooter";

/**
 * DEV-005 W7: GraphView route (`/app/graph?turn=<turnId>`).
 *
 * Shows LangGraph node/worker/approval state derived from W6 SSE events
 * (frozen wire shape `GET /chat/turns/{id}/events`), plus the live model
 * badge, escalation trail, and token/cost footer for the same turn. All
 * state comes from real stream facts with cursor-based SSE reconnect;
 * nothing is simulated.
 */

export default function Graph() {
  const [params, setParams] = useSearchParams();
  const initial = params.get("turn") ?? "";
  const [draft, setDraft] = useState(initial);
  const turnId = initial || null;
  const runtime = useTurnRuntime(turnId);

  const approvals = useMemo(
    () => runtime.nodes.filter((n) => n.status === "awaiting_approval"),
    [runtime.nodes],
  );

  const connect = () => {
    const clean = draft.trim();
    if (clean) setParams({ turn: clean }, { replace: true });
  };

  return (
    <div>
      <PageHeader
        title="Graph run"
        description="Live LangGraph node, worker, and approval state for one turn."
      />
      <div className="flex flex-wrap items-center gap-2 px-6 py-3">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="turn id"
          aria-label="turn id"
          className="min-w-0 flex-1 rounded-sm border border-linedefault bg-raised px-2 py-1 text-bodysm text-ink"
        />
        <button
          type="button"
          onClick={connect}
          className="rounded-sm border border-linedefault px-2.5 py-1 text-bodysm text-inksecondary hover:text-ink"
        >
          Watch
        </button>
        <ModelBadge calls={runtime.calls} live={runtime.live} />
      </div>
      {!turnId ? (
        <p className="px-6 py-2 text-bodysm text-inkmuted">
          Paste a turn id to watch its graph run. Open a chat and send a
          message, then follow the turn link here.
        </p>
      ) : (
        <div className="grid max-w-4xl gap-4 px-6 pb-6">
          <div className="text-meta text-inksecondary" aria-live="polite">
            {runtime.live
              ? "Live — streaming turn events"
              : approvals.length > 0
                ? `Paused — ${approvals.length} approval${approvals.length === 1 ? "" : "s"} waiting in /app/approvals`
                : runtime.reconnects > 0
                  ? `Reconnecting… (attempt ${runtime.reconnects})`
                  : "Connecting…"}
          </div>
          <section aria-label="Escalation trail">
            <h2 className="mb-1 text-meta font-semibold uppercase tracking-wide text-inksecondary">
              Route
            </h2>
            <EscalationTrail steps={runtime.escalation} />
          </section>
          <section aria-label="Graph nodes">
            <h2 className="mb-1 text-meta font-semibold uppercase tracking-wide text-inksecondary">
              Nodes
            </h2>
            <GraphNodeList nodes={runtime.nodes} />
          </section>
          <section aria-label="Usage and cost">
            <h2 className="mb-1 text-meta font-semibold uppercase tracking-wide text-inksecondary">
              Usage
            </h2>
            <TokenCostFooter totals={runtime.totals} calls={runtime.calls} />
          </section>
        </div>
      )}
    </div>
  );
}
