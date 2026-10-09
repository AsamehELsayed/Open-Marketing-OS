import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const workspace = fs.readFileSync(path.join(root, "src/routes/Workspace.tsx"), "utf8");

const confirmation = workspace.match(/window\.confirm\("([^"]+)"\)/)?.[1] ?? "";
assert.match(confirmation, /Replace this brief with generated content\?/);
assert.match(confirmation, /saved over the current saved content/);
assert.match(confirmation, /unsaved edits .* discarded/);
assert.match(workspace, /if \(\(briefDraft\.trim\(\) \|\| brief\.brief_md\.trim\(\)\) && !window\.confirm\(/,
  "any non-empty saved or draft brief requires an explicit confirmation");
assert.match(workspace, /api\.generateBusinessBrief\(pid, \{ overwrite_confirmed: Boolean\(brief\.brief_md\.trim\(\)\) \}\)/,
  "the server overwrite guard follows the user's explicit confirmation");
assert.match(workspace, /setBrief\(generated\); setBriefDraft\(generated\.brief_md\); setBriefDirty\(false\)/,
  "successful generation presents the immediately saved brief");

console.log("DEV-031 Regenerate confirmation: saved replacement and discarded edits are stated explicitly");
