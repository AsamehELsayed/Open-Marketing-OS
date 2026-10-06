import { useEffect, useRef, useState } from "react";
import { streamTurnEvents } from "../api/client";
import type { TurnStreamEvent } from "../api/client";

/**
 * DEV-004 W3: turn streaming state.
 *
 * Attaches `streamTurnEvents(turnId, {after})` and accumulates the reply.
 * Contract notes (backend `app/services/turns.py` + `app/routes/chat.py`):
 * - `assistant_delta` detail = CUMULATIVE full reply so far (replace).
 *   Providers that explicitly send meta.cumulative !== true fall back to
 *   append. (Legacy chat.html also replaces per delta.)
 * - `assistant_completed` detail = FULL cumulative reply (set, not append).
 * - `turn_completed` / `turn_failed` are terminal; latency lives in
 *   `meta.latency_ms` on the terminal events.
 * - Resume-safe: `after` is forwarded as the SSE `?after=` cursor so a
 *   re-attach replays missed persisted events instead of faking progress.
 * - No fake progress: status derives only from real server events.
 * - `meta` is carried on every lifecycle row. The execution tree
 *   (DEV-008-SKILLS-OPS §1.3.3) links nodes on `meta.parent_id` and reads
 *   `meta.status`, so dropping `meta` here would make the tree impossible to
 *   build; it is passed through verbatim and never interpreted.
 *
 * DEV-008-SKILLS-OPS-HOTFIX — the ONE lifecycle filter
 * ---------------------------------------------------
 * `NON_LIFECYCLE_EVENT_TYPES` is the single list of wire types that never
 * become a lifecycle row: the four text carriers (whose content is the
 * message, not an activity row) and the three transport-only markers.
 *
 * It lives HERE, next to the `LifecycleItem` shape, and both the live stream
 * (this hook) and the post-reload replay (`usePersistedTurnTree`) read it.
 * They previously carried two hand-written copies that DISAGREED: this hook
 * appended `turn_completed` / `turn_failed` while the replay hook skipped
 * them, so the live tree ended a turn two nodes longer than the identical
 * tree rebuilt after a reload. One exported set cannot drift from itself.
 *
 * `turn_completed` and `turn_failed` are deliberately NOT in the set: they are
 * real lifecycle rows (a completed and a failed turn are facts about the turn,
 * not transport), so live and replayed trees both show them.
 */

export type TurnStatus = "idle" | "working" | "streaming" | "done" | "failed";

export interface LifecycleItem {
  id: number;
  type: string;
  label: string;
  detail: string;
  /**
   * The sanitised `meta` dict exactly as the server sent it. Opaque here on
   * purpose: every key is interpreted by the component that needs it, so a new
   * server-side key can never be silently reshaped by this hook.
   */
  meta: Record<string, unknown>;
}

const DELTA = "assistant_delta";

/**
 * The wire types that are never lifecycle rows. Exported so the persisted
 * replay applies the identical filter — see the module docstring.
 */
export const NON_LIFECYCLE_EVENT_TYPES: ReadonlySet<string> = new Set([
  "assistant_delta",
  "assistant_completed",
  "model_delta",
  "model_completed",
  "turn_closed",
  "stream_end",
  "message",
]);

/** True for a wire type that belongs in the lifecycle list / the tree. */
export function isLifecycleEventType(type: string): boolean {
  return !NON_LIFECYCLE_EVENT_TYPES.has(type);
}

function appendLifecycle(prev: LifecycleItem[], e: TurnStreamEvent): LifecycleItem[] {
  if (prev.some((p) => p.id === e.id)) return prev;
  return [...prev, { id: e.id, type: e.type, label: e.label, detail: e.detail, meta: e.meta }];
}

function metaLatency(e: TurnStreamEvent): number | null {
  const ms = (e.meta as Record<string, unknown> | undefined)?.["latency_ms"];
  return typeof ms === "number" && ms >= 0 ? ms : null;
}

export function useTurnStream(turnId: string | null, after = 0): {
  status: TurnStatus;
  text: string;
  events: LifecycleItem[];
  latencyMs: number | null;
} {
  const [status, setStatus] = useState<TurnStatus>(turnId ? "working" : "idle");
  const [text, setText] = useState("");
  const [events, setEvents] = useState<LifecycleItem[]>([]);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const t0Ref = useRef(0);

  useEffect(() => {
    if (!turnId) {
      setStatus("idle");
      return;
    }
    setStatus("working");
    setText("");
    setEvents([]);
    setLatencyMs(null);
    t0Ref.current = performance.now();
    let firstDelta = true;

        const lastSeqRef = { current: 0 };
    const close = streamTurnEvents(turnId, {
      after,
      onEvent: (e: TurnStreamEvent) => {
        if (e.type === "model_delta") {
          const delta =
            (e.meta as Record<string, unknown> | undefined)?.["delta"] ??
            (e as any).delta ??
            e.detail ??
            "";
          const seq = Number(
            (e.meta as Record<string, unknown> | undefined)?.["sequence"] ??
              (e as any).sequence ??
              0,
          );
          if (seq > 0) {
            if (seq > lastSeqRef.current) {
              lastSeqRef.current = seq;
              if (delta) setText((p) => p + String(delta));
            }
          } else if (delta) {
            setText((p) => p + String(delta));
          }
          if (firstDelta) {
            firstDelta = false;
            setLatencyMs(Math.round(performance.now() - t0Ref.current));
          }
          setStatus((p) => (p === "working" ? "streaming" : p));
          return;
        }
        if (e.type === "model_completed") {
          const full =
            (e.meta as Record<string, unknown> | undefined)?.["text"] ??
            e.detail;
          if (full) setText(String(full));
          const ms = metaLatency(e);
          if (ms != null) setLatencyMs(ms);
          setStatus((p) => (p === "failed" ? p : "streaming"));
          return;
        }
        if (e.type === DELTA) {
          // If model_delta is already actively streaming, ignore legacy duplicate
          if (lastSeqRef.current > 0) return;
          const cumulative = (e.meta as Record<string, unknown> | undefined)?.["cumulative"];
          if (e.detail) {
            if (cumulative === true) setText(e.detail);
            else setText((p) => p + e.detail);
          }
          if (firstDelta) {
            firstDelta = false;
            setLatencyMs(Math.round(performance.now() - t0Ref.current));
          }
          setStatus((p) => (p === "working" ? "streaming" : p));
          return;
        }
        if (e.type === "assistant_completed") {
          // Cumulative full reply — replaces the accumulated deltas.
          if (e.detail) setText(e.detail);
          const ms = metaLatency(e);
          if (ms != null) setLatencyMs(ms);
          setStatus((p) => (p === "failed" ? p : "streaming"));
          // Intentionally NOT added to the lifecycle list: single-panel
          // rule — the completed text is the message, not an activity row.
          return;
        }
        if (e.type === "turn_completed") {
          const ms = metaLatency(e);
          if (ms != null) setLatencyMs(ms);
          setStatus("done");
          setEvents((p) => appendLifecycle(p, e));
          return;
        }
        if (e.type === "turn_failed") {
          setStatus("failed");
          setEvents((p) => appendLifecycle(p, e));
          return;
        }
        // Transport-only markers and text carriers carry no lifecycle row. The
        // filter is the exported set, shared with the persisted replay, so the
        // live tree and the reloaded tree are built from the same rows.
        if (!isLifecycleEventType(e.type)) return;
        // Everything else (turn_started, context_*, tool/rag/web/instagram,
        // job_created, approval_required, web_source, …) is lifecycle trace.
        setEvents((p) => appendLifecycle(p, e));
      },
      onDone: () => {
        // Stream closed by server: settle non-terminal states as done.
        // Failure is signalled ONLY by turn_failed, never by close/error.
        setStatus((p) => (p === "working" || p === "streaming" ? "done" : p));
      },
      onError: () => {
        // EventSource auto-retries; keep waiting for real terminal events.
      },
    });
    return close;
  }, [turnId, after]);

  return { status, text, events, latencyMs };
}
