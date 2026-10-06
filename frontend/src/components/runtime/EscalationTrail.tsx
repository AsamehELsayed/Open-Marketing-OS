import type { EscalationStep } from "../../api/runtime";

/**
 * DEV-005 W7: escalation trail — AUTO vs LOCAL vs OPENAI route plus the
 * recorded reason chain, in arrival order. Renders only observed steps;
 * an empty trail states that plainly instead of implying a route.
 */

const MODE_STYLE: Record<string, string> = {
  AUTO: "text-accenthover",
  LOCAL: "text-ok",
  OPENAI: "text-inksecondary",
};

export default function EscalationTrail({
  steps,
}: {
  steps: EscalationStep[];
}) {
  if (steps.length === 0) {
    return (
      <p className="text-meta text-inkmuted">
        No route recorded for this turn yet.
      </p>
    );
  }
  return (
    <ol className="space-y-1">
      {steps.map((s, i) => (
        <li
          key={`${s.provider}-${s.model}-${i}`}
          className="flex flex-wrap items-baseline gap-x-2 text-bodysm"
        >
          <span className="text-meta text-inkmuted">{i + 1}.</span>
          <span
            className={`text-meta font-semibold ${MODE_STYLE[s.routeMode] ?? "text-inksecondary"}`}
          >
            {s.routeMode}
          </span>
          <span className="text-ink">
            {s.provider} · {s.model}
          </span>
          {s.reason ? (
            <span className="text-meta text-inksecondary">— {s.reason}</span>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
