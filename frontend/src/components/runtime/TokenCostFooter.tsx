import {
  formatCost,
  formatLatency,
  formatTokens,
  type ModelCallInfo,
  type TurnTotals,
} from "../../api/runtime";

/**
 * DEV-005 W7: token/cost footer — input/output/total/cached/reasoning
 * tokens + latency + cost + pricing version per turn.
 *
 * Honesty: unknown legs render as "unknown" (never 0). Local calls cost
 * $0.00 API with the W5 compute-not-metered note; non-local calls without
 * reported usage render cost "unknown" instead of an estimate.
 */

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-meta text-inkmuted">{k}</span>
      <span className="text-meta text-ink">{v}</span>
    </div>
  );
}

export default function TokenCostFooter({
  totals,
  calls,
}: {
  totals: TurnTotals;
  calls: ModelCallInfo[];
}) {
  const realCalls = calls.filter((call) => !call.generationStatus);
  const allLocal = realCalls.length > 0 && realCalls.every((c) => c.provider === "local");
  const showNote =
    realCalls.length > 0 &&
    realCalls.some((c) => (c.costNote || "").trim().length > 0);
  const note = showNote
    ? (realCalls.find((c) => (c.costNote || "").trim())?.costNote || "").trim()
    : allLocal
      ? "local compute cost is not metered"
      : "";
  const versions =
    totals.pricingVersions.length > 0
      ? totals.pricingVersions.join(", ")
      : "unknown";

  return (
    <div
      className="rounded-sm border border-linesubtle bg-raised px-3 py-2"
      aria-label="Token usage and cost for this turn"
    >
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 sm:grid-cols-3">
        <Row k="input" v={formatTokens(totals.inputTokens)} />
        <Row k="cached" v={formatTokens(totals.cachedTokens)} />
        <Row k="output" v={formatTokens(totals.outputTokens)} />
        <Row k="reasoning" v={formatTokens(totals.reasoningTokens)} />
        <Row k="total" v={formatTokens(totals.totalTokens)} />
        <Row k="latency" v={formatLatency(totals.latencyMs)} />
        <Row
          k="cost"
          v={formatCost(totals.estimatedCostUsd, allLocal)}
        />
        <Row k="pricing" v={versions} />
      </div>
      {note ? (
        <p className="mt-1 text-meta text-inkmuted">{note}</p>
      ) : null}
      {totals.allUnknown && realCalls.length > 0 ? (
        <p className="mt-1 text-meta text-inkmuted">
          Provider did not report usage for this turn.
        </p>
      ) : null}
    </div>
  );
}
