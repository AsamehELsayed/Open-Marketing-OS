/**
 * DEV-004 W4 honesty filter for the activity panel.
 *
 * - `assistant_delta` streaming text is ALWAYS excluded (it belongs in the
 *   chat thread, W3 — the panel shows lifecycle, not prose).
 * - `HIDDEN_UNLESS_DEV` covers backend/RAG internals: retrieval traces,
 *   state digests, prompt/system dumps, raw JSON blobs, process ids, and
 *   chain-of-thought-adjacent noise. Shown only in Advanced Developer Mode.
 * - `friendlyLabel` maps lifecycle event types to plain-language labels
 *   (no ids, no backend jargon).
 */

export interface PanelEventLike {
  event_type: string;
  label: string;
  detail: string;
}

/**
 * Lowercase substring patterns treated as internals. Matched against
 * `event_type + label + detail`. Kept to >=4-char tokens to avoid false
 * positives on ordinary words.
 */
export const HIDDEN_UNLESS_DEV: ReadonlySet<string> = new Set([
  "rag",
  "retrieval",
  "embedding",
  "chunk",
  "state_digest",
  "state digest",
  "prompt_dump",
  "prompt dump",
  "system_dump",
  "system dump",
  "system prompt",
  "raw_json",
  "raw json",
  "process_id",
  "process id",
  "chain_of_thought",
  "chain of thought",
  "chain-of-thought",
  "internal reasoning",
  "thinking trace",
  "token_usage",
  "token usage",
  "tool_input",
  "tool input",
  "tool_output",
  "tool output",
]);

/** Event types hidden unless dev mode (exact, lowercase match). */
const HIDDEN_TYPES: ReadonlySet<string> = new Set([
  "rag_query",
  "rag_result",
  "rag_retrieval",
  "state_digest",
  "prompt_dump",
  "system_dump",
  "raw_json",
  "process_spawn",
  "process_exit",
  "chain_of_thought",
  "thinking",
  "token_usage",
  "debug",
  "trace",
]);

/** Streaming prose — never shown in the panel (lives in the thread). */
const ALWAYS_HIDDEN_TYPES: ReadonlySet<string> = new Set([
  "assistant_delta",
  "stream_chunk",
  "delta",
]);

function haystack(ev: PanelEventLike): string {
  return `${ev.event_type} ${ev.label} ${ev.detail}`.toLowerCase();
}

export function isHiddenUnlessDev(ev: PanelEventLike): boolean {
  if (HIDDEN_TYPES.has(ev.event_type.toLowerCase())) return true;
  const hay = haystack(ev);
  for (const pat of HIDDEN_UNLESS_DEV) {
    if (hay.includes(pat)) return true;
  }
  return false;
}

/** Drop streaming prose always; drop internals unless `devMode`. */
export function filterEvents<T extends PanelEventLike>(
  events: T[],
  devMode: boolean,
): T[] {
  return events.filter((ev) => {
    if (ALWAYS_HIDDEN_TYPES.has(ev.event_type.toLowerCase())) return false;
    if (!devMode && isHiddenUnlessDev(ev)) return false;
    return true;
  });
}

const LABELS: Record<string, string> = {
  turn_started: "Working on it",
  turn_completed: "Answer ready",
  turn_failed: "Something went wrong",
  worker_started: "Worker started",
  worker_completed: "Worker finished",
  worker_failed: "Worker failed",
  job_created: "Job started",
  job_status_changed: "Job update",
  approval_required: "Approval needed",
  web_source: "Checked a source",
  synthesis_started: "Writing the answer",
  assistant_completed: "Answer updated",
};

function prettifyEventType(t: string): string {
  const s = t.replace(/[_-]+/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "Activity";
}

/** Plain-language label for a lifecycle event (never an id). */
export function friendlyLabel(ev: PanelEventLike): string {
  const mapped = LABELS[ev.event_type.toLowerCase()];
  if (mapped) return mapped;
  const lbl = (ev.label || "").trim();
  if (lbl) return lbl;
  return prettifyEventType(ev.event_type);
}
