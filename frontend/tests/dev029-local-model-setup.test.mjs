import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  downloadLocalModel,
  formatLocalBytes,
  getLocalRuntime,
  localLifecycleLabel,
  startLocalRuntime,
  stopLocalRuntime,
} from "../src/api/localRuntime.ts";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const calls = [];
const syntheticStatus = {
  model_id: "synthetic/model-gguf-v1",
  display_name: "Synthetic Local Model",
  source: "https://models.example.test/synthetic/model",
  publisher: "Synthetic Publisher",
  license: "Synthetic License 1.0",
  license_url: "https://licenses.example.test/synthetic",
  model_bytes: 1_073_741_824,
  temporary_disk_bytes: 1_610_612_736,
  sha256: "a".repeat(64),
  download_state: "not_started",
  downloaded_bytes: 0,
  download_total_bytes: 1_073_741_824,
  verification_state: "not_verified",
  activation_state: "inactive",
  runtime_state: "stopped",
};

globalThis.fetch = async (url, init = {}) => {
  calls.push({ url: String(url), method: init.method ?? "GET" });
  return new Response(JSON.stringify({ ok: true, data: syntheticStatus }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};

assert.equal((await getLocalRuntime()).model_id, syntheticStatus.model_id);
assert.equal((await downloadLocalModel()).model_bytes, syntheticStatus.model_bytes);
assert.equal((await startLocalRuntime()).runtime_state, "stopped");
assert.equal((await stopLocalRuntime()).runtime_state, "stopped");
assert.deepEqual(calls, [
  { url: "/api/local/runtime", method: "GET" },
  { url: "/api/local/model/download", method: "POST" },
  { url: "/api/local/runtime/start", method: "POST" },
  { url: "/api/local/runtime/stop", method: "POST" },
]);
assert.equal(formatLocalBytes(1_073_741_824), "1 GB");
assert.equal(localLifecycleLabel("verified"), "Checksum verified");

const component = fs.readFileSync(path.join(root, "src/components/settings/LocalModelSetup.tsx"), "utf8");
assert.match(component, /useEffect\(\(\) => \{ void refresh\(\); \}, \[refresh\]\)/, "visiting the panel only reads status");
assert.match(component, /onClick=\{\(\) => \{ void runAction\(downloadLocalModel\); \}\}/, "model download is tied to its explicit button");
assert.match(component, /onClick=\{\(\) => \{ void runAction\(startLocalRuntime\); \}\}/, "runtime start is a separate action");
assert.doesNotMatch(component, /runAction\(downloadLocalModel\)[^\n]*startLocalRuntime/, "starting the runtime cannot invoke the download action");
assert.match(component, /aria-live="polite"/, "lifecycle state changes are announced accessibly");
assert.match(component, /Expected SHA-256/, "pinned checksum is presented");
assert.match(component, /Temporary disk space required/, "temporary disk requirement is presented");

console.log("DEV-029 Local model setup: API routes, explicit download action, states, and metadata passed");
