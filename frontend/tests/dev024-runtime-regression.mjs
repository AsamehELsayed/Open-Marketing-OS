import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as runtime from "../src/api/runtime.ts";
import * as runtimeClient from "../src/api/runtimeClient.ts";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const replay = JSON.parse(
  fs.readFileSync(path.join(root, "tests/fixtures/dev024-model-completed.json"), "utf8"),
);

const parsed = runtime.parseModelMeta(
  replay.meta,
  replay.event,
  42,
  replay.meta.turn_id,
);
assert.ok(parsed);
assert.equal(parsed.provider, "OpenRouter");
assert.equal(parsed.requestedModel, "stealth/space-bunny-alpha");
assert.equal(parsed.model, "stealth/space-bunny-alpha");
assert.equal(parsed.route, "openrouter");
assert.equal(parsed.routeMode, "OPENROUTER");
assert.equal(parsed.httpAttemptCount, 1);
assert.equal(parsed.httpStatus, 200);
assert.equal(parsed.latencyMs, 26708);

const compactAliases = runtime.parseModelMeta(
  {
    telemetry: {
      provider: "OpenRouter",
      model: "stealth/space-bunny-alpha",
      attempts: 0,
      status_code: 200,
    },
  },
  "model_completed",
  43,
  "offline-aliases",
);
assert.ok(compactAliases);
assert.equal(compactAliases.httpAttemptCount, 0, "known zero attempts must remain zero");
assert.equal(compactAliases.httpStatus, 200);
assert.equal(compactAliases.requestedModel, undefined);
assert.equal(compactAliases.latencyMs, null);

const restCall = runtimeClient.normalizeModelCallRow(
  {
    provider: "OpenRouter",
    model: "stealth/space-bunny-alpha",
    requested_model: "stealth/space-bunny-alpha",
    route: "openrouter",
    http_attempts: 1,
    http_status: 200,
    latency_ms: 26708,
  },
  "offline-rest",
);
assert.equal(restCall.httpAttemptCount, 1);
assert.equal(restCall.httpStatus, 200);
assert.equal(restCall.latencyMs, 26708);

const unknown = runtime.parseModelMeta(
  { telemetry: { provider: "OpenRouter", actual_model: "stealth/space-bunny-alpha" } },
  "model_completed",
  44,
  "offline-unknowns",
);
assert.ok(unknown);
assert.equal(unknown.httpAttemptCount, null);
assert.equal(unknown.httpStatus, null);
assert.equal(unknown.latencyMs, null);
assert.equal(unknown.routeMode, "OPENROUTER", "known provider can identify route mode");

const missingRoute = runtimeClient.normalizeModelCallRow({ model: "unknown" }, "offline-missing");
assert.equal(missingRoute.routeMode, "UNKNOWN", "missing route must not be inferred as local");

console.log("DEV-024 runtime parser regression: 5 cases passed");
