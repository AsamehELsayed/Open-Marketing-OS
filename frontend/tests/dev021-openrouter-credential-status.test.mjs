import assert from "node:assert/strict";
import ts from "typescript";
import { readFile } from "node:fs/promises";

const source = await readFile(
  new URL("../src/api/openrouterCredentialStatus.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { projectOpenRouterCredentialStatus } = await import(moduleUrl);

assert.deepEqual(projectOpenRouterCredentialStatus("configured"), {
  status: "configured",
  connected: true,
  needsReconfiguration: false,
});
for (const status of ["missing", "unreadable", "empty"]) {
  assert.deepEqual(projectOpenRouterCredentialStatus(status), {
    status,
    connected: false,
    needsReconfiguration: true,
  });
}
assert.equal(projectOpenRouterCredentialStatus("not_configured").connected, false);
assert.equal(projectOpenRouterCredentialStatus(undefined).status, "unreadable");
assert.equal(projectOpenRouterCredentialStatus("configured-but-untrusted").connected, false);
