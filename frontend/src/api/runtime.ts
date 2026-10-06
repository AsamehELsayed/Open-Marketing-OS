/**
 * DEV-005 W7: runtime observability types + pure parsing helpers.
 *
 * Sources of truth:
 * - W0 `app/contracts/model_call.py` (nullable token legs; unknown stays
 *   null, never 0-filled; local API cost $0.00 with a compute-not-metered
 *   note) and `app/contracts/events.py` (frozen SSE wire types; W6 maps
 *   LangGraph node events onto them via `meta`, no new wire types).
 * - W5 `workers/w5.md` (telemetry fields, per-million pricing versions,
 *   local-cost marker) and `docs/v1/architecture.md` §7 (modes
 *   AUTO/LOCAL/OPENAI, escalation reasons, SSE model facts).
 *
 * Honesty rule: every formatter renders `null`/`undefined` as "unknown".
 * Nothing here estimates or fabricates provider usage.
 */

export type RouteMode = "AUTO" | "LOCAL" | "OPENAI" | "OPENROUTER" | "UNKNOWN";

export interface ModelCallInfo {
  callId: string;
  turnId: string;
  provider: string;
  model: string;
  adapter?: string;
  requestedModel?: string;
  behaviorProfile?: string;
  /** Raw route label from the recorded call, when available. */
  route?: string;
  httpAttemptCount?: number | null;
  httpStatus?: number | null;
  quantization?: string;
  routeMode: RouteMode;
  routeReason: string;
  inputTokens?: number | null;
  cachedTokens?: number | null;
  outputTokens?: number | null;
  reasoningTokens?: number | null;
  totalTokens?: number | null;
  latencyMs?: number | null;
  estimatedCostUsd?: number | null;
  pricingVersion?: string;
  costNote?: string;
  generationSource?: string;
  generationStatus?: string;
  invocationStarted?: boolean | null;
  /** Safe provider failure facts; actual model/usage may remain unknown. */
  errorType?: string;
  failureCode?: string;
  failureReason?: string;
  attemptLatencyMs?: number | null;
  /** Physical HTTP transport facts; null means the backend did not report it. */
  networkAttempted?: boolean | null;
  httpResponseCount?: number | null;
  networkPhase?: string;
  retrievalMode?: string;
  lexicalHits?: number | null;
  vectorHits?: number | null;
  fusedHits?: number | null;
  selectedChunkCount?: number | null;
  sourceFileCount?: number | null;
  /** Compact acceptance-critical retrieval evidence (IDs only, no chunk bodies). */
  selectedChunkIds?: string[];
  sourceFileIds?: string[];
}

export interface EscalationStep {
  provider: string;
  model: string;
  routeMode: RouteMode;
  reason: string;
}

export type GraphNodeStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "awaiting_approval";

export interface GraphNodeState {
  node: string;
  status: GraphNodeStatus;
  label: string;
  detail: string;
}

/** Normative node names (`docs/v1/architecture.md` §2). */
export const GRAPH_NODES: readonly string[] = [
  "load_project",
  "load_conversation",
  "understand",
  "route",
  "state_only",
  "knowledge",
  "external_research",
  "social_research",
  "deep_research",
  "campaign_operation",
  "approval_operation",
  "job_followup",
  "aggregate",
  "synthesize",
  "approval-interrupt",
  "respond",
];

const ROUTE_MODES: ReadonlySet<string> = new Set(["AUTO", "LOCAL", "OPENAI", "OPENROUTER"]);

function asNonEmptyString(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const s = v.trim();
  return s ? s : null;
}

function asNonNegativeInt(v: unknown): number | null {
  if (typeof v !== "number" || !Number.isFinite(v) || v < 0) return null;
  return Math.floor(v);
}

function asNonNegativeNumber(v: unknown): number | null {
  if (typeof v !== "number" || !Number.isFinite(v) || v < 0) return null;
  return v;
}

// Failure details are telemetry, so accept only bounded machine-readable
// labels. This prevents an exception or provider message from reaching UI.
function asSafeDiagnostic(v: unknown): string | undefined {
  if (typeof v !== "string") return undefined;
  const s = v.trim();
  return s.length > 0 && s.length <= 64 && /^[a-zA-Z0-9_.-]+$/.test(s)
    ? s
    : undefined;
}

function pickMeta(
  meta: Record<string, unknown>,
  ...keys: string[]
): unknown {
  for (const k of keys) {
    if (meta[k] !== undefined && meta[k] !== null) return meta[k];
  }
  return undefined;
}

/**
 * Extract per-call model facts from one SSE event `meta` object.
 * Returns `null` when the meta carries no model identity at all (legacy
 * lifecycle events) so callers can skip it. Unknown legs stay `undefined`.
 */
export function parseModelMeta(
  meta: Record<string, unknown>,
  eventType: string,
  eventId: number,
  turnId: string,
): ModelCallInfo | null {
  // Safe transport lifecycle records share the frozen SSE event type but do
  // not represent model-call rows; the final projection carries their summary.
  if (meta?.transport_lifecycle === true) return null;
  // model_completed carries authoritative telemetry nested under `telemetry`
  // (provider, actual model, tokens, cost). Prefer that; fall back to
  // top-level meta for legacy lifecycle events.
  let src = meta;
  const telemetry = meta && (meta.telemetry as Record<string, unknown> | undefined);
  const hasAuthoritativeTelemetry = Boolean(
    telemetry &&
      typeof telemetry === "object" &&
      (telemetry.provider || telemetry.model),
  );
  if (hasAuthoritativeTelemetry && telemetry) {
    src = { ...meta, ...telemetry } as Record<string, unknown>;
  }
  const generationSource = asNonEmptyString(pickMeta(src, "generation_source"));
  const generationStatus = asNonEmptyString(pickMeta(src, "generation_status"));
  const visibility = src.model_visibility as Record<string, unknown> | undefined;
  const attempt = (visibility?.generation_attempt ?? src.generation_attempt) as Record<string, unknown> | undefined;
  const retrieval = (visibility?.retrieval_telemetry ?? src.retrieval_telemetry ?? src.retrieval) as Record<string, unknown> | undefined;
  if ((eventType === "synthesis_completed" && (generationSource || generationStatus || attempt)) ||
      (eventType === "model_completed" && (hasAuthoritativeTelemetry || generationStatus))) {
    const attemptMeta = attempt && typeof attempt === "object" ? attempt : src;
    const retrievalMeta = retrieval && typeof retrieval === "object" ? retrieval : {};
    const selectedChunks = Array.isArray(retrievalMeta.selected_chunks)
      ? retrievalMeta.selected_chunks
      : Array.isArray(retrievalMeta.selectedChunks) ? retrievalMeta.selectedChunks : [];
    const compactChunkIds = Array.isArray(retrievalMeta.selected_chunk_ids)
      ? retrievalMeta.selected_chunk_ids : Array.isArray(retrievalMeta.selectedChunkIds) ? retrievalMeta.selectedChunkIds : [];
    const compactFileIds = Array.isArray(retrievalMeta.source_file_ids)
      ? retrievalMeta.source_file_ids : Array.isArray(retrievalMeta.sourceFileIds) ? retrievalMeta.sourceFileIds : [];
    const hasChunkEvidence = Array.isArray(retrievalMeta.selected_chunks) || Array.isArray(retrievalMeta.selectedChunks) || Array.isArray(retrievalMeta.selected_chunk_ids) || Array.isArray(retrievalMeta.selectedChunkIds);
    const hasFileEvidence = Array.isArray(retrievalMeta.source_file_ids) || Array.isArray(retrievalMeta.sourceFileIds) || Array.isArray(retrievalMeta.selected_chunks) || Array.isArray(retrievalMeta.selectedChunks);
    const chunkIds = [...new Set([...compactChunkIds, ...selectedChunks.map((item) =>
      item && typeof item === "object" ? (item as Record<string, unknown>).chunk_id ?? (item as Record<string, unknown>).chunkId : undefined,
    )].map(asNonEmptyString).filter((v): v is string => Boolean(v)))];
    const fileIds = new Set([...compactFileIds, ...selectedChunks.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const id = asNonEmptyString((item as Record<string, unknown>).file_id ?? (item as Record<string, unknown>).fileId);
      return id ? [id] : [];
    })].map(asNonEmptyString).filter((v): v is string => Boolean(v)));
    const rawMode = asNonEmptyString(pickMeta(src, "route_mode", "routeMode"));
    return {
      callId: asNonEmptyString(pickMeta(src, "call_id", "callId")) ?? asNonEmptyString(pickMeta(attemptMeta, "call_id", "callId")) ?? `sse-${eventId}`,
      turnId,
      provider: asNonEmptyString(pickMeta(attemptMeta, "provider")) ?? "unknown",
      adapter: asNonEmptyString(pickMeta(src, "adapter")) ?? undefined,
      // Requested model is deliberately not presented as the actual model.
      model: asNonEmptyString(pickMeta(src, "actual_model", "model")) ?? "unknown",
      requestedModel: asNonEmptyString(pickMeta(attemptMeta, "requested_model", "requestedModel")) ?? undefined,
      route: asNonEmptyString(pickMeta(src, "route")) ?? undefined,
      routeMode: (() => {
        const mode = asNonEmptyString(pickMeta(attemptMeta, "route_mode", "routeMode")) ?? rawMode;
        if (mode && ROUTE_MODES.has(mode.toUpperCase())) return mode.toUpperCase() as RouteMode;
        const normalizedProvider = (asNonEmptyString(pickMeta(attemptMeta, "provider")) ?? "").toLowerCase();
        if (normalizedProvider === "openai") return "OPENAI";
        if (normalizedProvider === "openrouter") return "OPENROUTER";
        if (normalizedProvider === "local") return "LOCAL";
        return "UNKNOWN";
      })(),
      routeReason: asNonEmptyString(pickMeta(attemptMeta, "route_reason", "routeReason")) ?? eventType,
      generationSource: asNonEmptyString(pickMeta(attemptMeta, "generation_source", "generationSource")) ?? generationSource ?? "unknown",
      generationStatus: asNonEmptyString(pickMeta(attemptMeta, "generation_status", "generationStatus")) ?? generationStatus ?? "unknown",
      invocationStarted: typeof attemptMeta.invocation_started === "boolean"
        ? attemptMeta.invocation_started : null,
      errorType: asSafeDiagnostic(pickMeta(attemptMeta, "error_type", "errorType")),
      failureCode: asSafeDiagnostic(pickMeta(attemptMeta, "failure_code", "failureCode")),
      httpStatus: asNonNegativeInt(pickMeta(src, "http_status", "status_code", "status", "httpStatus")) ?? asNonNegativeInt(pickMeta(attemptMeta, "http_status", "status_code", "status", "httpStatus")),
      failureReason: asSafeDiagnostic(pickMeta(attemptMeta, "failure_reason", "failureReason")),
      networkAttempted: typeof pickMeta(src, "network_attempted", "networkAttempted") === "boolean" || typeof pickMeta(attemptMeta, "network_attempted", "networkAttempted") === "boolean"
        ? (pickMeta(src, "network_attempted", "networkAttempted") ?? pickMeta(attemptMeta, "network_attempted", "networkAttempted")) as boolean
        : null,
      httpAttemptCount: asNonNegativeInt(pickMeta(src, "http_attempt_count", "http_attempts", "attempts", "httpAttemptCount")) ?? asNonNegativeInt(pickMeta(attemptMeta, "http_attempt_count", "http_attempts", "attempts", "httpAttemptCount")),
      httpResponseCount: asNonNegativeInt(pickMeta(src, "http_response_count", "httpResponseCount")) ?? asNonNegativeInt(pickMeta(attemptMeta, "http_response_count", "httpResponseCount")),
      networkPhase: asSafeDiagnostic(pickMeta(attemptMeta, "network_phase", "networkPhase")),
      attemptLatencyMs: asNonNegativeInt(pickMeta(src, "latency_ms", "latencyMs")) ?? asNonNegativeInt(pickMeta(attemptMeta, "attempt_latency_ms", "attemptLatencyMs", "latency_ms")),
      latencyMs: asNonNegativeInt(pickMeta(src, "latency_ms", "latencyMs")),
      inputTokens: asNonNegativeInt(pickMeta(src, "input_tokens", "inputTokens")),
      cachedTokens: asNonNegativeInt(pickMeta(src, "cached_tokens", "cachedTokens")),
      outputTokens: asNonNegativeInt(pickMeta(src, "output_tokens", "outputTokens")),
      totalTokens: asNonNegativeInt(pickMeta(src, "total_tokens", "totalTokens")),
      retrievalMode: asNonEmptyString(pickMeta(retrievalMeta, "retrieval_mode", "retrievalMode", "mode")) ?? undefined,
      lexicalHits: asNonNegativeInt(pickMeta(retrievalMeta, "lexical_hits", "lexicalHits")),
      vectorHits: asNonNegativeInt(pickMeta(retrievalMeta, "vector_hits", "vectorHits")),
      fusedHits: asNonNegativeInt(pickMeta(retrievalMeta, "fused_hits", "fusedHits")),
      selectedChunkCount: hasChunkEvidence ? chunkIds.length : null,
      sourceFileCount: hasFileEvidence ? fileIds.size : null,
      selectedChunkIds: hasChunkEvidence ? chunkIds : undefined,
      sourceFileIds: hasFileEvidence ? [...fileIds] : undefined,
    };
  }
  const provider =
    asNonEmptyString(pickMeta(src, "provider", "model_provider")) ??
    asNonEmptyString(pickMeta(src, "modelProvider"));
  const model = asNonEmptyString(pickMeta(src, "actual_model", "model"));
  if (
    (eventType === "assistant_completed" ||
      eventType === "turn_completed" ||
      eventType === "turn_failed") &&
    !hasAuthoritativeTelemetry
  ) {
    return null;
  }
  if (!model || model.toLowerCase() === "unknown") return null;

  const rawMode = asNonEmptyString(pickMeta(src, "route_mode", "routeMode"));
  const routeMode: RouteMode =
    rawMode && ROUTE_MODES.has(rawMode.toUpperCase())
      ? (rawMode.toUpperCase() as RouteMode)
      : provider?.toLowerCase() === "openai"
        ? "OPENAI"
        : provider?.toLowerCase() === "openrouter"
          ? "OPENROUTER"
          : "UNKNOWN";
  const routeReason =
    asNonEmptyString(
      pickMeta(src, "route_reason", "routeReason", "reason"),
    ) ?? eventType;

  return {
    callId: asNonEmptyString(pickMeta(src, "call_id", "callId")) ?? `sse-${eventId}`,
    turnId,
    provider: provider ?? "unknown",
    model: model ?? "unknown",
    adapter: asNonEmptyString(pickMeta(src, "adapter")) ?? undefined,
    requestedModel:
      asNonEmptyString(pickMeta(src, "requested_model", "requestedModel")) ??
      undefined,
    behaviorProfile:
      asNonEmptyString(pickMeta(src, "behavior_profile", "behaviorProfile")) ??
      undefined,
    route: asNonEmptyString(pickMeta(src, "route")) ?? undefined,
    httpAttemptCount: asNonNegativeInt(
      pickMeta(src, "http_attempt_count", "http_attempts", "attempts"),
    ),
    httpStatus: asNonNegativeInt(
      pickMeta(src, "http_status", "status_code", "status"),
    ),
    quantization:
      asNonEmptyString(pickMeta(src, "quantization", "quant")) ?? undefined,
    routeMode,
    routeReason,
    inputTokens: asNonNegativeInt(pickMeta(src, "input_tokens", "inputTokens")),
    cachedTokens: asNonNegativeInt(
      pickMeta(src, "cached_tokens", "cachedTokens"),
    ) ?? undefined,
    outputTokens: asNonNegativeInt(
      pickMeta(src, "output_tokens", "outputTokens"),
    ),
    reasoningTokens: asNonNegativeInt(
      pickMeta(src, "reasoning_tokens", "reasoningTokens"),
    ) ?? undefined,
    totalTokens: asNonNegativeInt(
      pickMeta(src, "total_tokens", "totalTokens"),
    ),
    latencyMs: asNonNegativeInt(pickMeta(src, "latency_ms", "latencyMs")),
    estimatedCostUsd: asNonNegativeNumber(
      pickMeta(src, "estimated_cost_usd", "estimatedCostUsd", "cost_usd"),
    ),
    pricingVersion:
      asNonEmptyString(pickMeta(src, "pricing_version", "pricingVersion")) ??
      undefined,
    costNote:
      asNonEmptyString(pickMeta(src, "cost_note", "costNote")) ?? undefined,
  };
}

/**
 * Merge one parsed call into the per-turn list. Same `callId` replaces
 * (later SSE facts win, e.g. streaming → completion); new ids append.
 * This is what lets the badge update once per turn event.
 */
export function mergeModelCalls(
  prev: ModelCallInfo[],
  next: ModelCallInfo,
): ModelCallInfo[] {
  const idx = prev.findIndex((c) => c.callId === next.callId);
  if (idx === -1) return [...prev, next];
  // Later model_completed events often omit compact retrieval/attempt details
  // carried by synthesis_completed. Keep the latest known value per field.
  return prev.map((c, i) => (i === idx ? { ...c, ...Object.fromEntries(
    Object.entries(next).filter(([, value]) => value !== undefined && value !== null),
  ), ...{
    selectedChunkIds: next.selectedChunkIds?.length ? next.selectedChunkIds : c.selectedChunkIds,
    sourceFileIds: next.sourceFileIds?.length ? next.sourceFileIds : c.sourceFileIds,
  } } : c));
}

export interface TurnTotals {
  inputTokens: number | null;
  cachedTokens: number | null;
  outputTokens: number | null;
  reasoningTokens: number | null;
  totalTokens: number | null;
  latencyMs: number | null;
  estimatedCostUsd: number | null;
  pricingVersions: string[];
  /** True when every leg of every call is unknown (show "unknown", not 0). */
  allUnknown: boolean;
}

function sumKnown(values: (number | null | undefined)[]): number | null {
  const known = values.filter(
    (v): v is number => typeof v === "number" && Number.isFinite(v),
  );
  if (known.length === 0) return null;
  return known.reduce((a, b) => a + b, 0);
}

/**
 * Turn totals across calls. Sums ONLY known legs (W5 `turn_totals`
 * semantics: unknown legs never contribute, never 0-filled).
 */
export function turnTotals(calls: ModelCallInfo[]): TurnTotals {
  const versions = [
    ...new Set(
      calls
        .map((c) => (c.pricingVersion || "").trim())
        .filter((v) => v.length > 0),
    ),
  ];
  const totals: TurnTotals = {
    inputTokens: sumKnown(calls.map((c) => c.inputTokens)),
    cachedTokens: sumKnown(calls.map((c) => c.cachedTokens)),
    outputTokens: sumKnown(calls.map((c) => c.outputTokens)),
    reasoningTokens: sumKnown(calls.map((c) => c.reasoningTokens)),
    totalTokens: sumKnown(calls.map((c) => c.totalTokens)),
    latencyMs: sumKnown(calls.map((c) => c.latencyMs)),
    estimatedCostUsd: sumKnown(calls.map((c) => c.estimatedCostUsd)),
    pricingVersions: versions,
    allUnknown:
      calls.length === 0 ||
      calls.every(
        (c) =>
          c.inputTokens == null &&
          c.outputTokens == null &&
          c.totalTokens == null &&
          c.estimatedCostUsd == null,
      ),
  };
  return totals;
}

/** AUTO vs LOCAL vs OPENAI route + reason chain, in arrival order. */
export function escalationTrail(calls: ModelCallInfo[]): EscalationStep[] {
  return calls.map((c) => ({
    provider: c.provider,
    model: c.model,
    routeMode: c.routeMode,
    reason: c.routeReason,
  }));
}

/** "unknown" for null/undefined — never 0, never blank. */
export function formatTokens(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v)
    ? v.toLocaleString("en-US")
    : "unknown";
}

export function formatCost(
  v: number | null | undefined,
  isLocal: boolean,
): string {
  if (isLocal) return "$0.00";
  return typeof v === "number" && Number.isFinite(v)
    ? `$${v.toFixed(6)}`
    : "unknown";
}

export function formatLatency(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v)
    ? `${v.toLocaleString("en-US")} ms`
    : "unknown";
}

/**
 * Exponential-backoff SSE reconnect delay (500ms base, ×2, capped at
 * 10s). Pure so the verification loop can assert the schedule.
 */
export function reconnectDelay(attempt: number): number {
  const n = Math.max(0, Math.floor(attempt));
  return Math.min(500 * 2 ** n, 10_000);
}

/**
 * Stable idempotency key per approval intent (W0 `approvals.py`: one key
 * per approval; yellow/red require it). Retries MUST reuse the same key
 * so resume/replay cannot duplicate side effects.
 */
export function idempotencyKeyFor(approvalId: string): string {
  return `approval-${approvalId.trim()}`;
}

export interface RuntimeStreamEvent {
  id: number;
  type: string;
  label: string;
  detail: string;
  meta: Record<string, unknown>;
}

function nodeFromMeta(meta: Record<string, unknown>): string | null {
  const raw = asNonEmptyString(
    pickMeta(meta, "graph_node", "node", "branch", "route"),
  );
  if (!raw) return null;
  const norm = raw.trim().toLowerCase().replace(/-/g, "-");
  if ((GRAPH_NODES as readonly string[]).includes(norm)) return norm;
  if (norm === "approval_interrupt" || norm === "approval interrupt") {
    return "approval-interrupt";
  }
  return null;
}

/**
 * Derive LangGraph node/worker/approval state from W6 SSE events.
 * W6 maps graph nodes onto the frozen wire types via `meta.graph_node`
 * (W0 events contract: no new wire types), so nodes are read from meta
 * first and from well-known lifecycle types second.
 */
export function graphNodesFromEvents(
  events: RuntimeStreamEvent[],
): GraphNodeState[] {
  const states = new Map<string, GraphNodeState>();
  const ensure = (node: string): GraphNodeState => {
    let s = states.get(node);
    if (!s) {
      s = { node, status: "pending", label: "", detail: "" };
      states.set(node, s);
    }
    return s;
  };
  const mark = (
    node: string,
    status: GraphNodeStatus,
    ev: RuntimeStreamEvent,
  ): void => {
    const s = ensure(node);
    // Never regress a terminal state with an older replayed event, except
    // that an approval gate may legitimately move done → awaiting_approval.
    if (
      (s.status === "done" || s.status === "failed") &&
      !(status === "awaiting_approval")
    ) {
      return;
    }
    s.status = status;
    if (ev.label) s.label = ev.label;
    if (ev.detail) s.detail = ev.detail;
  };

  for (const ev of events) {
    const t = ev.type.toLowerCase();
    const node = nodeFromMeta(ev.meta);

    if (t === "approval_required") {
      mark("approval-interrupt", "awaiting_approval", ev);
      mark("approval_operation", "awaiting_approval", ev);
      continue;
    }
    if (t === "turn_failed") {
      for (const s of states.values()) {
        if (s.status === "running") s.status = "failed";
      }
      continue;
    }
    if (t === "turn_completed") {
      let anyRunning = false;
      for (const s of states.values()) {
        if (s.status === "running") {
          s.status = "done";
          anyRunning = true;
        }
      }
      // Only record `respond` when the turn actually had observed node
      // activity — a bare terminal event invents nothing.
      if (anyRunning || states.has("synthesize") || states.has("aggregate")) {
        const r = ensure("respond");
        if (r.status !== "failed") r.status = "done";
      }
      continue;
    }
    if (t === "synthesis_started") {
      mark("synthesize", "running", ev);
      continue;
    }
    if (t === "job_created") {
      mark("job_followup", "running", ev);
      continue;
    }
    if (
      t === "worker_started" ||
      t === "delegation_started" ||
      t === "tool_started"
    ) {
      mark(node ?? "deep_research", "running", ev);
      continue;
    }
    if (
      t === "worker_completed" ||
      t === "worker_failed" ||
      t === "tool_completed" ||
      t === "tool_failed"
    ) {
      mark(
        node ?? "deep_research",
        t.endsWith("failed") ? "failed" : "done",
        ev,
      );
      continue;
    }
    if (t === "rag_started" || t === "rag_completed") {
      mark("knowledge", t === "rag_started" ? "running" : "done", ev);
      continue;
    }
    if (t === "web_started" || t === "web_completed" || t === "web_source") {
      mark(
        "external_research",
        t === "web_completed" ? "done" : "running",
        ev,
      );
      continue;
    }
    if (
      t === "instagram_provider_started" ||
      t === "instagram_provider_completed"
    ) {
      mark(
        "social_research",
        t.endsWith("started") ? "running" : "done",
        ev,
      );
      continue;
    }
    if (t === "context_started" || t === "context_completed") {
      mark("load_conversation", t.endsWith("started") ? "running" : "done", ev);
      continue;
    }
    if (t === "state_read") {
      mark("state_only", "done", ev);
      continue;
    }
    if (t === "provider_selected") {
      mark("understand", "done", ev);
      continue;
    }
    if (t === "turn_started") {
      mark("load_project", "running", ev);
      continue;
    }
    if (node) {
      mark(node, "running", ev);
    }
  }
  return [...states.values()];
}
