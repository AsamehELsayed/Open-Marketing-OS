import assert from "node:assert/strict";
import ts from "typescript";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const wirePath = process.argv[2];
assert.ok(wirePath, "expected a persisted SSE metadata JSON path");
const meta = JSON.parse(await readFile(wirePath, "utf8"));
const source = await readFile(new URL("../src/api/runtime.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const runtimeUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { parseModelMeta } = await import(runtimeUrl);
const projected = parseModelMeta(meta, "model_completed", 1, meta.turn_id);
assert.ok(projected, "persisted model event should project into runtime state");
assert.equal(projected.lexicalHits, meta.retrieval_telemetry.lexical_hits);
assert.equal(projected.vectorHits, meta.retrieval_telemetry.vector_hits);
assert.equal(projected.fusedHits, meta.retrieval_telemetry.fused_hits);
assert.deepEqual(projected.selectedChunkIds, meta.retrieval_telemetry.selected_chunk_ids);
assert.deepEqual(projected.sourceFileIds, meta.retrieval_telemetry.source_file_ids);
assert.equal(projected.provider, meta.telemetry.provider);
assert.equal(projected.model, meta.telemetry.model);
console.log("DEV-023 persisted SSE → parseModelMeta projection: PASS");
