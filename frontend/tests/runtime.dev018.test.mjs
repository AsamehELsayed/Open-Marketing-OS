import assert from "node:assert/strict";
import ts from "typescript";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const sourcePath = new URL("../src/api/runtime.ts", import.meta.url);
const source = await readFile(sourcePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const runtimeUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { parseModelMeta, escalationTrail, mergeModelCalls } = await import(runtimeUrl);

const meta = {
  model_visibility: {
    generation_attempt: {
      generation_source: "none",
      generation_status: "failed",
      call_id: "synthetic-failed-call",
      invocation_started: true,
      provider: "openrouter",
      requested_model: "stealth/space-bunny-alpha",
      route_mode: "OPENROUTER",
      route_reason: "explicit user selection",
      error_type: "ModelCallFailure",
      failure_code: "authentication",
      http_status: 401,
      failure_reason: "credential_rejected",
      network_attempted: true,
      http_attempt_count: 1,
      http_response_count: 1,
      network_phase: "http_response",
      attempt_latency_ms: 10827,
    },
    retrieval_telemetry: {
      mode: "HYBRID",
      lexical_hits: 2,
      vector_hits: 3,
      fused_hits: 4,
      selected_chunks: [{ chunk_id: "c000", file_id: "synthetic-file" }],
    },
  },
};

const attempt = parseModelMeta(meta, "synthesis_completed", 12, "synthetic-turn");
assert.ok(attempt);
assert.equal(attempt.provider, "openrouter");
assert.equal(attempt.requestedModel, "stealth/space-bunny-alpha");
assert.equal(attempt.model, "unknown", "requested model must not be presented as actual model");
assert.equal(attempt.routeMode, "OPENROUTER");
assert.equal(attempt.routeReason, "explicit user selection");
assert.equal(attempt.generationStatus, "failed");
assert.equal(attempt.errorType, "ModelCallFailure");
assert.equal(attempt.failureCode, "authentication");
assert.equal(attempt.httpStatus, 401);
assert.equal(attempt.failureReason, "credential_rejected");
assert.equal(attempt.networkAttempted, true);
assert.equal(attempt.httpAttemptCount, 1);
assert.equal(attempt.httpResponseCount, 1);
assert.equal(attempt.networkPhase, "http_response");
assert.equal(attempt.attemptLatencyMs, 10827);
assert.equal(attempt.lexicalHits, 2);
assert.equal(attempt.vectorHits, 3);
assert.equal(attempt.fusedHits, 4);
assert.equal(attempt.selectedChunkCount, 1);
assert.equal(attempt.sourceFileCount, 1);
assert.equal(escalationTrail([attempt])[0].model, "unknown");

const topLevel = parseModelMeta({
  generation_source: "none",
  generation_status: "failed",
  provider: "openrouter",
  requested_model: "requested-only",
  route_mode: "OPENROUTER",
  route_reason: "explicit user selection",
  failure_code: "rate_limited",
  http_status: 429,
  retrieval_telemetry: { mode: "HYBRID", lexical_hits: 1, vector_hits: 1, fused_hits: 1 },
}, "synthesis_completed", 13, "synthetic-turn-2");
assert.equal(topLevel?.failureCode, "rate_limited");
assert.equal(topLevel?.httpStatus, 429);
assert.equal(topLevel?.retrievalMode, "HYBRID");

const beforeTransport = parseModelMeta({
  generation_attempt: {
    call_id: "synthetic-before-transport",
    provider: "openrouter",
    requested_model: "safe-model",
    route_mode: "OPENROUTER",
    generation_source: "none",
    generation_status: "failed",
    network_attempted: false,
    http_attempt_count: 0,
    http_response_count: 0,
    network_phase: "credential_vault",
    failure_reason: "Authorization: secret-value",
    failure_code: "vault_unavailable",
  },
}, "synthesis_completed", 14, "synthetic-turn-3");
assert.equal(beforeTransport?.networkAttempted, false);
assert.equal(beforeTransport?.httpAttemptCount, 0);
assert.equal(beforeTransport?.networkPhase, "credential_vault");
assert.equal(beforeTransport?.failureReason, undefined, "unsafe free text must not enter UI projection");
assert.equal(beforeTransport?.failureCode, "vault_unavailable");

assert.equal(parseModelMeta({
  transport_lifecycle: true,
  state: "provider_request_started",
  call_id: "call-safe",
  turn_id: "turn-safe",
  provider: "openrouter",
}, "synthesis_completed", 15, "turn-safe"), null,
"individual safe transport checkpoints must not create noisy model-call UI rows");

// DEV-023 backend contract: compact snake_case fields survive model_completed
// and the UI retains exact IDs while preserving explicit zero vs unavailable.
const compact = parseModelMeta({
  telemetry: {
    call_id: "acceptance-call", provider: "openrouter",
    requested_model: "requested/model", model: "actual/model",
    route_mode: "OPENROUTER", generation_source: "provider",
    generation_status: "completed", network_attempted: true,
    http_attempt_count: 1, http_response_count: 1, http_status: 200,
    latency_ms: 52051,
  },
  retrieval_telemetry: {
    retrieval_mode: "HYBRID", lexical_hits: 0, vector_hits: 1,
    fused_hits: 1, selected_chunk_ids: ["c000"], source_file_ids: ["file-123"],
  },
}, "model_completed", 16, "turn-023");
assert.equal(compact?.provider, "openrouter");
assert.equal(compact?.requestedModel, "requested/model");
assert.equal(compact?.model, "actual/model");
assert.equal(compact?.routeMode, "OPENROUTER");
assert.equal(compact?.generationStatus, "completed");
assert.equal(compact?.networkAttempted, true);
assert.equal(compact?.httpAttemptCount, 1);
assert.equal(compact?.httpStatus, 200);
assert.equal(compact?.latencyMs, 52051);
assert.equal(compact?.retrievalMode, "HYBRID");
assert.equal(compact?.lexicalHits, 0, "known zero remains distinct from unknown");
assert.equal(compact?.vectorHits, 1);
assert.equal(compact?.fusedHits, 1);
assert.deepEqual(compact?.selectedChunkIds, ["c000"]);
assert.deepEqual(compact?.sourceFileIds, ["file-123"]);
assert.equal(compact?.inputTokens, null);
const merged = mergeModelCalls([attempt], { ...compact, callId: "synthetic-failed-call" });
assert.equal(merged[0].selectedChunkIds?.[0], "c000", "later completion preserves retrieval projection");

console.log("DEV-018 runtime projection: PASS");
