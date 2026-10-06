/**
 * DEV-005 W7: W6 endpoint wiring with legacy fallback.
 *
 * W6 owns the FastAPI graph runtime (`docs/v1/worker-dag.md`): it maps
 * LangGraph events onto the frozen SSE wire shape
 * (`GET /chat/turns/{id}/events`, `?after=` cursor + `Last-Event-ID`
 * resume) and adds JSON endpoints for model-call telemetry and approval
 * resume. Until W6 lands, those JSON paths 404 — every fetcher below
 * treats 404/501 as "W6 not deployed yet" and falls back to what exists
 * today (SSE meta facts; legacy approvals queue link). No fake data is
 * ever synthesized: absence is returned as absence.
 */

import type { ApiEnvelope } from "./client";
import { idempotencyKeyFor, type ModelCallInfo } from "./runtime.ts";

export interface W6ModelCallRow {
  call_id?: string;
  callId?: string;
  turn_id?: string;
  turnId?: string;
  provider?: string;
  model?: string;
  actual_model?: string;
  actualModel?: string;
  adapter?: string;
  requested_model?: string;
  requestedModel?: string;
  behavior_profile?: string;
  behaviorProfile?: string;
  route?: string;
  http_attempt_count?: number | null;
  http_attempts?: number | null;
  attempts?: number | null;
  http_status?: number | null;
  status_code?: number | null;
  status?: number | null;
  quantization?: string;
  quant?: string;
  route_mode?: string;
  routeMode?: string;
  route_reason?: string;
  routeReason?: string;
  reason?: string;
  input_tokens?: number | null;
  cached_tokens?: number | null;
  output_tokens?: number | null;
  reasoning_tokens?: number | null;
  total_tokens?: number | null;
  latency_ms?: number | null;
  estimated_cost_usd?: number | null;
  pricing_version?: string;
  cost_note?: string;
  generation_source?: string;
  generation_status?: string;
  network_attempted?: boolean | null;
  http_response_count?: number | null;
  retrieval_mode?: string;
  lexical_hits?: number | null;
  vector_hits?: number | null;
  fused_hits?: number | null;
  selected_chunk_ids?: string[];
  source_file_ids?: string[];
}

function normMode(v: unknown, provider: string): "AUTO" | "LOCAL" | "OPENAI" | "OPENROUTER" | "UNKNOWN" {
  const s = typeof v === "string" ? v.trim().toUpperCase() : "";
  if (s === "OPENAI" || s === "OPENROUTER" || s === "AUTO") return s;
  if (s === "LOCAL") return s;
  const normalizedProvider = provider.trim().toLowerCase();
  if (normalizedProvider === "openai") return "OPENAI";
  if (normalizedProvider === "openrouter") return "OPENROUTER";
  if (normalizedProvider === "local") return "LOCAL";
  return "UNKNOWN";
}

function numOrUndef(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : undefined;
}

/** Normalize a W6 model-call row into `ModelCallInfo` (unknown stays unknown). */
export function normalizeModelCallRow(
  row: W6ModelCallRow,
  turnId: string,
): ModelCallInfo {
  const provider =
    (typeof row.provider === "string" && row.provider.trim()) || "unknown";
  return {
    callId:
      (typeof row.call_id === "string" && row.call_id) ||
      (typeof row.callId === "string" && row.callId) ||
      `rest-${turnId}`,
    turnId:
      (typeof row.turn_id === "string" && row.turn_id) ||
      (typeof row.turnId === "string" && row.turnId) ||
      turnId,
    provider,
    model:
      (typeof row.actual_model === "string" && row.actual_model) ||
      (typeof row.actualModel === "string" && row.actualModel) ||
      (typeof row.model === "string" && row.model) ||
      "unknown",
    adapter: typeof row.adapter === "string" ? row.adapter : undefined,
    requestedModel:
      (typeof row.requested_model === "string" && row.requested_model) ||
      (typeof row.requestedModel === "string" && row.requestedModel) ||
      undefined,
    behaviorProfile:
      (typeof row.behavior_profile === "string" && row.behavior_profile) ||
      (typeof row.behaviorProfile === "string" && row.behaviorProfile) ||
      undefined,
    route: typeof row.route === "string" ? row.route : undefined,
    httpAttemptCount: numOrUndef(
      row.http_attempt_count ?? row.http_attempts ?? row.attempts,
    ),
    httpStatus: numOrUndef(row.http_status ?? row.status_code ?? row.status),
    quantization:
      (typeof row.quantization === "string" && row.quantization) ||
      (typeof row.quant === "string" && row.quant) ||
      undefined,
    routeMode: normMode(row.route_mode ?? row.routeMode, provider),
    routeReason:
      (typeof row.route_reason === "string" && row.route_reason) ||
      (typeof row.routeReason === "string" && row.routeReason) ||
      (typeof row.reason === "string" && row.reason) ||
      "",
    inputTokens: numOrUndef(row.input_tokens),
    cachedTokens: numOrUndef(row.cached_tokens),
    outputTokens: numOrUndef(row.output_tokens),
    reasoningTokens: numOrUndef(row.reasoning_tokens),
    totalTokens: numOrUndef(row.total_tokens),
    latencyMs: numOrUndef(row.latency_ms),
    estimatedCostUsd:
      typeof row.estimated_cost_usd === "number" &&
      Number.isFinite(row.estimated_cost_usd)
        ? row.estimated_cost_usd
        : undefined,
    pricingVersion:
      typeof row.pricing_version === "string" ? row.pricing_version : undefined,
    costNote: typeof row.cost_note === "string" ? row.cost_note : undefined,
    generationSource: typeof row.generation_source === "string" ? row.generation_source : undefined,
    generationStatus: typeof row.generation_status === "string" ? row.generation_status : undefined,
    networkAttempted: typeof row.network_attempted === "boolean" ? row.network_attempted : null,
    httpResponseCount: numOrUndef(row.http_response_count),
    retrievalMode: typeof row.retrieval_mode === "string" ? row.retrieval_mode : undefined,
    lexicalHits: numOrUndef(row.lexical_hits),
    vectorHits: numOrUndef(row.vector_hits),
    fusedHits: numOrUndef(row.fused_hits),
    selectedChunkIds: Array.isArray(row.selected_chunk_ids) ? row.selected_chunk_ids.filter((x): x is string => typeof x === "string") : undefined,
    sourceFileIds: Array.isArray(row.source_file_ids) ? row.source_file_ids.filter((x): x is string => typeof x === "string") : undefined,
  };
}

async function getJson<T>(path: string): Promise<{ status: number; data: T }> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) return { status: res.status, data: [] as unknown as T };
  const json = (await res.json()) as ApiEnvelope<T>;
  if (!json || json.ok !== true) return { status: 502, data: [] as unknown as T };
  return { status: 200, data: json.data };
}

export interface ModelCallsResult {
  /** True when the W6 endpoint answered (even with zero rows). */
  fromServer: boolean;
  calls: ModelCallInfo[];
}

/**
 * W6: `GET /api/turns/{id}/model-calls` (W5 `ModelCalls.for_turn` behind
 * the `/api` envelope). Falls back to `{fromServer:false}` on 404 so the
 * caller keeps SSE-meta-derived facts instead of showing nothing.
 */
export async function fetchTurnModelCalls(
  turnId: string,
): Promise<ModelCallsResult> {
  const { status, data } = await getJson<W6ModelCallRow[]>(
    `/api/turns/${encodeURIComponent(turnId)}/model-calls`,
  );
  if (status === 200 && Array.isArray(data)) {
    return {
      fromServer: true,
      calls: data.map((r) => normalizeModelCallRow(r, turnId)),
    };
  }
  return { fromServer: false, calls: [] };
}

export type ApprovalDecision = "approved" | "rejected";

export interface ApprovalResumeResult {
  ok: boolean;
  /** False when the W6 JSON endpoint is absent (use legacy queue link). */
  resumed: boolean;
  status?: string;
  error?: string;
}

/**
 * W6: `POST /api/approvals/{id}/resume` with the W0 `ApprovalResume`
 * shape (`decision`, `decided_by`, stable `idempotency_key`). Retries
 * reuse the same key (see `idempotencyKeyFor`) so resume/replay cannot
 * duplicate side effects. A 404/501 means W6 is not deployed yet — the
 * caller must offer the legacy queue instead of failing the decision.
 */
export async function resumeApproval(
  approvalId: string,
  decision: ApprovalDecision,
  decidedBy: string,
  idempotencyKey?: string,
): Promise<ApprovalResumeResult> {
  const key = idempotencyKey ?? idempotencyKeyFor(approvalId);
  let res: Response;
  try {
    res = await fetch(`/api/approvals/${encodeURIComponent(approvalId)}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        decided_by: decidedBy || "web-ui",
        idempotency_key: key,
      }),
    });
  } catch {
    return {
      ok: false,
      resumed: false,
      error: "Could not reach the approval service.",
    };
  }
  if (res.status === 404 || res.status === 501) {
    return { ok: false, resumed: false };
  }
  if (!res.ok) {
    return { ok: false, resumed: true, error: "The approval could not be resumed." };
  }
  let status: string | undefined;
  try {
    const json = (await res.json()) as ApiEnvelope<{ status?: string }>;
    status =
      json && json.ok === true && typeof json.data?.status === "string"
        ? json.data.status
        : undefined;
  } catch {
    status = undefined;
  }
  return { ok: true, resumed: true, status };
}
