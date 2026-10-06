import type { GraphNodeState } from "../../api/runtime";
import { GRAPH_NODES } from "../../api/runtime";

/**
 * DEV-005 W7: graph node list — LangGraph node/worker/approval state.
 * Nodes with no observed event render as "pending" (never invented as
 * running); only real SSE facts move them.
 */

const STATUS_STYLE: Record<string, string> = {
  pending: "text-inkmuted",
  running: "text-accenthover",
  done: "text-ok",
  failed: "text-err",
  awaiting_approval: "text-err",
};

const STATUS_DOT: Record<string, string> = {
  pending: "bg-linesubtle",
  running: "bg-accent animate-pulse",
  done: "bg-ok",
  failed: "bg-err",
  awaiting_approval: "bg-err animate-pulse",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "pending",
  running: "running",
  done: "done",
  failed: "failed",
  awaiting_approval: "awaiting approval",
};

export default function GraphNodeList({
  nodes,
}: {
  nodes: GraphNodeState[];
}) {
  const byNode = new Map(nodes.map((n) => [n.node, n]));
  return (
    <ul className="divide-y divide-linesubtle rounded-sm border border-linesubtle bg-raised">
      {GRAPH_NODES.map((name) => {
        const s = byNode.get(name);
        const status = s?.status ?? "pending";
        return (
          <li
            key={name}
            className="flex items-start gap-2 px-3 py-1.5"
            aria-label={`${name}: ${STATUS_LABEL[status]}`}
          >
            <span
              aria-hidden
              className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${STATUS_DOT[status]}`}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-bodysm font-mono text-ink">{name}</span>
                <span className={`text-meta ${STATUS_STYLE[status]}`}>
                  {STATUS_LABEL[status]}
                </span>
              </div>
              {s?.label ? (
                <p className="truncate text-meta text-inksecondary">{s.label}</p>
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
