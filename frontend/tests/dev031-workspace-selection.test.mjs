import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { isCurrentWorkspaceSelection } from "../src/routes/workspaceSelection.ts";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const workspace = fs.readFileSync(path.join(root, "src/routes/Workspace.tsx"), "utf8");

const beforeSwitchBack = { projectId: "client-a", epoch: 4 };
const afterSwitchBack = { projectId: "client-a", epoch: 6 };
for (const action of ["profile save", "brief save", "generation"]) {
  assert.equal(
    isCurrentWorkspaceSelection(
      beforeSwitchBack.projectId,
      beforeSwitchBack.epoch,
      afterSwitchBack.projectId,
      afterSwitchBack.epoch,
    ),
    false,
    `${action} from the first client-a selection must be stale after A→B→A`,
  );
}
assert.equal(isCurrentWorkspaceSelection("client-a", 6, "client-a", 6), true);

const actions = [
  ["saveProfile", "saveBrief"],
  ["saveBrief", "generate"],
  ["generate", "const field ="],
].map(([start, end]) => workspace.split(`async function ${start}()`)[1].split(end)[0]);
for (const action of actions) {
  assert.match(action, /const epoch = requestGeneration\.current/);
  assert.match(action, /const isCurrent = \(\) => isCurrentWorkspaceSelection\(/);
  assert.match(action, /if \(!isCurrent\(\)\) return/);
  assert.match(action, /catch \(e\) \{ if \(isCurrent\(\)\)/);
  assert.match(action, /finally \{ if \(isCurrent\(\)\)/);
}

console.log("DEV-031 workspace selection: delayed saves and generation are ignored after A→B→A");
