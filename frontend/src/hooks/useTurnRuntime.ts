import { useEffect, useRef, useState } from "react";
import {
  escalationTrail,
  graphNodesFromEvents,
  mergeModelCalls,
  parseModelMeta,
  reconnectDelay,
  turnTotals,
  type EscalationStep,
  type GraphNodeState,
  type ModelCallInfo,
  type RuntimeStreamEvent,
  type TurnTotals,
} from "../api/runtime";
import { fetchTurnModelCalls } from "../api/runtimeClient";
import { streamTurnEvents } from "../api/client";

/**
 * DEV-005 W7: per-turn runtime observability.
 *
 * Attaches the FROZEN SSE endpoint (`GET /chat/turns/{id}/events`, same
 * wire shape W6 reuses for LangGraph events) and derives:
 * - model calls (provider/model/adapter/quant/route-reason per turn),
 * - the AUTO vs LOCAL vs OPENAI escalation trail,
 * - turn token/cost/latency totals (unknown stays unknown),
 * - LangGraph node/worker/approval state.
 *
 * Reconnect: on transport error the hook re-attaches with the `after`
 * cursor set to the last seen event id (server replays persisted events,
 * never fakes progress) using `reconnectDelay(attempt)` backoff. Stops
 * reconnecting on terminal events, on unmount, or after 8 attempts.
 * Server REST (`GET /api/turns/{id}/model-calls`, W6) supplements the
 * SSE facts when deployed; SSE remains authoritative live.
 */

const MAX_RECONNECTS = 8;

export interface TurnRuntime {
  calls: ModelCallInfo[];
  escalation: EscalationStep[];
  totals: TurnTotals;
  nodes: GraphNodeState[];
  live: boolean;
  reconnects: number;
  serverSupplemented: boolean;
}

export function useTurnRuntime(turnId: string | null): TurnRuntime {
  const [calls, setCalls] = useState<ModelCallInfo[]>([]);
  const [events, setEvents] = useState<RuntimeStreamEvent[]>([]);
  const [reconnects, setReconnects] = useState(0);
  const [live, setLive] = useState(false);
  const [serverSupplemented, setServerSupplemented] = useState(false);
  const lastIdRef = useRef(0);
  const terminalRef = useRef(false);

  useEffect(() => {
    if (!turnId) {
      setCalls([]);
      setEvents([]);
      setReconnects(0);
      setLive(false);
      setServerSupplemented(false);
      return;
    }
    setCalls([]);
    setEvents([]);
    setReconnects(0);
    setLive(false);
    setServerSupplemented(false);
    lastIdRef.current = 0;
    terminalRef.current = false;

    let cancelled = false;
    let close: (() => void) | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    const appendEvent = (e: RuntimeStreamEvent) => {
      lastIdRef.current = Math.max(lastIdRef.current, e.id);
      setEvents((prev) => {
        if (prev.some((p) => p.id === e.id)) return prev;
        return [...prev.slice(-299), e];
      });
      const parsed = parseModelMeta(e.meta, e.type, e.id, turnId);
      if (parsed) {
        setCalls((prev) => mergeModelCalls(prev, parsed));
      }
      if (e.type === "turn_completed" || e.type === "turn_failed") {
        terminalRef.current = true;
      }
    };

    const attach = () => {
      if (cancelled || terminalRef.current) return;
      setLive(true);
      close = streamTurnEvents(turnId, {
        after: lastIdRef.current,
        onEvent: (e) => {
          if (cancelled) return;
          attempt = 0;
          appendEvent({
            id: e.id,
            type: e.type,
            label: e.label,
            detail: e.detail,
            meta: e.meta,
          });
          if (terminalRef.current) {
            setLive(false);
            close?.();
          }
        },
        onDone: () => {
          if (!cancelled && !terminalRef.current) {
            // Server closed without a terminal event: reconnect with cursor.
            scheduleReconnect();
          } else {
            setLive(false);
          }
        },
        onError: () => {
          if (!cancelled && !terminalRef.current) scheduleReconnect();
        },
      });
    };

    const scheduleReconnect = () => {
      if (cancelled || terminalRef.current) return;
      close?.();
      close = null;
      if (attempt >= MAX_RECONNECTS) {
        setLive(false);
        return;
      }
      const wait = reconnectDelay(attempt);
      attempt += 1;
      setReconnects(attempt);
      setLive(false);
      timer = setTimeout(() => {
        if (!cancelled) attach();
      }, wait);
    };

    attach();

    // One-shot W6 REST supplement (never overrides live SSE facts for the
    // same call ids — REST only adds calls SSE has not seen).
    fetchTurnModelCalls(turnId)
      .then((res) => {
        if (cancelled || !res.fromServer) return;
        setServerSupplemented(true);
        setCalls((prev) => {
          const known = new Set(prev.map((c) => c.callId));
          const extra = res.calls.filter((c) => !known.has(c.callId));
          return extra.length > 0 ? [...prev, ...extra] : prev;
        });
      })
      .catch(() => {
        /* W6 absent — SSE facts stand alone */
      });

    return () => {
      cancelled = true;
      close?.();
      if (timer) clearTimeout(timer);
    };
  }, [turnId]);

  const escalation = escalationTrail(calls);
  const totals = turnTotals(calls);
  const nodes = graphNodesFromEvents(events);
  return { calls, escalation, totals, nodes, live, reconnects, serverSupplemented };
}
