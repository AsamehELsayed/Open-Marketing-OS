import { useEffect, useState } from "react";
import { streamTurnEvents } from "../api/client";
import { isLifecycleEventType } from "./useTurnStream";
import type { LifecycleItem } from "./useTurnStream";

/**
 * DEV-008-SKILLS-OPS W12: persisted-turn replay for the Chat surface.
 *
 * After a page reload, `Chat`'s transcript is restored from
 * GET /api/chats/{id}/messages, whose rows carry no turn id — so the live
 * hook (`useTurnStream`) is never attached and the execution tree renders
 * nothing. This hook closes that gap:
 *
 * 1. reads which turn to replay from the small additive read
 *    GET /api/chats/{id}/last_turn -> {turn_id, status}, scoped server-side
 *    to the conversation's own project;
 * 2. ONLY for a FINISHED turn (`completed` / `failed`): a still-running turn
 *    belongs to a live session elsewhere, and replaying it would animate as
 *    though it were live;
 * 3. attaches the EXISTING persisted-replay SSE
 *    GET /chat/turns/{turn_id}/events?after=0 (already wrapped by
 *    `streamTurnEvents` in `api/client.ts`) and normalises the rows to the
 *    SAME `LifecycleItem[]` shape `useTurnStream` yields, so `ExecutionTree`
 *    needs no branch logic.
 *
 * Normalisation uses `isLifecycleEventType` from `useTurnStream` — the SINGLE
 * exported filter both paths share (hotfix contract §6/§10: live and reloaded
 * must not disagree). It used to be a local copy of the same seven names, and
 * the copy silently disagreed: it also skipped `turn_completed` /
 * `turn_failed`, which the live hook appends, so a reloaded tree was two nodes
 * shorter than the live tree for the very same turn. Reading the shared
 * predicate makes that class of drift impossible. No field is invented.
 *
 * `buildExecutionTree` sorts by persisted id, so even a different ARRIVAL order
 * between the live stream and this replay cannot change the rendered tree. The
 * equality is asserted from a shuffled input in
 * `app/tests/test_dev008hf_frontend_tree.py`.
 *
 * The events stay LOCAL to this hook (they are never published to the live
 * activity bus): a persisted replay is not live activity.
 */

const TERMINAL_TURN_STATUSES = new Set(["completed", "failed"]);

export interface PersistedTurnState {
  turnId: string | null;
  events: LifecycleItem[];
}

export function usePersistedTurnTree(
  conversationId: string | null,
  enabled: boolean,
): PersistedTurnState {
  const [state, setState] = useState<PersistedTurnState>({
    turnId: null,
    events: [],
  });

  useEffect(() => {
    if (!conversationId || !enabled) {
      setState({ turnId: null, events: [] });
      return;
    }
    let cancelled = false;
    let close: (() => void) | null = null;
    setState({ turnId: null, events: [] });
    void (async () => {
      try {
        const res = await fetch(
          `/api/chats/${encodeURIComponent(conversationId)}/last_turn`,
        );
        if (!res.ok || cancelled) return;
        const body = (await res.json()) as {
          ok?: boolean;
          data?: { turn_id?: string; status?: string };
        };
        const turnId = (body.data?.turn_id || "").trim();
        const status = (body.data?.status || "").trim();
        // No turn with events, or the turn has not finished: render nothing.
        if (!turnId || !TERMINAL_TURN_STATUSES.has(status) || cancelled) return;
        setState({ turnId, events: [] });
        close = streamTurnEvents(turnId, {
          after: 0,
          onEvent: (e) => {
            if (!isLifecycleEventType(e.type) || cancelled) return;
            setState((prev) => {
              if (prev.turnId !== turnId) return prev;
              if (prev.events.some((p) => p.id === e.id)) return prev;
              return {
                turnId,
                events: [
                  ...prev.events,
                  { id: e.id, type: e.type, label: e.label, detail: e.detail, meta: e.meta },
                ],
              };
            });
          },
        });
      } catch {
        return; // best-effort read: a failed lookup renders nothing.
      }
    })();
    return () => {
      cancelled = true;
      close?.();
      close = null;
    };
  }, [conversationId, enabled]);

  return state;
}
