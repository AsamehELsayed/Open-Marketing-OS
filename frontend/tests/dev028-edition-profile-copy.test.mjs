import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const helperSource = fs.readFileSync(path.join(root, "src/editionProfileCopy.ts"), "utf8");
const compiled = ts.transpileModule(helperSource, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const helperUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { getAiSettingsProfileCopy, getEditionKind, getStartHereProfileCopy } = await import(helperUrl);

const localId = "omos-local-v0.1.0-beta.1";
const openrouterId = "omos-openrouter-v0.1.0-beta.1";

assert.equal(getEditionKind(localId), "local");
assert.equal(getEditionKind(openrouterId), "openrouter");
assert.equal(getEditionKind("unknown-profile"), "generic");

const localFirstRun = getStartHereProfileCopy(localId);
assert.equal(localFirstRun.settingsSection, "local");
assert.equal(localFirstRun.showCloudSetupImage, false);
assert.match(localFirstRun.text, /No provider API key is required/);
assert.match(localFirstRun.text, /explicitly download/);
assert.match(localFirstRun.text, /llama\.cpp runtime/);
assert.match(localFirstRun.stepText, /verified managed runtime/);

const openrouterFirstRun = getStartHereProfileCopy(openrouterId);
assert.equal(openrouterFirstRun.settingsSection, "ai");
assert.equal(openrouterFirstRun.showCloudSetupImage, true);
assert.match(openrouterFirstRun.text, /AUTO routing/);
assert.match(openrouterFirstRun.text, /DPAPI/);
assert.match(openrouterFirstRun.text, /No Local model download or runtime setup is required/);
assert.match(openrouterFirstRun.text, /sent to OpenRouter/);

const localSettings = getAiSettingsProfileCopy(localId);
assert.equal(localSettings.localModelAvailable, true);
assert.match(localSettings.summaryText, /No provider API key is required/);
assert.match(localSettings.summaryText, /explicitly download/);
assert.match(localSettings.autoText, /ready Local model/);

const openrouterSettings = getAiSettingsProfileCopy(openrouterId);
assert.equal(openrouterSettings.localModelAvailable, false);
assert.match(openrouterSettings.summaryText, /AUTO routing/);
assert.match(openrouterSettings.summaryText, /DPAPI/);
assert.match(openrouterSettings.summaryText, /not required/);
assert.match(openrouterSettings.autoText, /Local inference is disabled/);
assert.match(openrouterSettings.openrouterText, /sent to OpenRouter/);

const startHere = fs.readFileSync(path.join(root, "src/routes/StartHere.tsx"), "utf8");
const settings = fs.readFileSync(path.join(root, "src/routes/Settings.tsx"), "utf8");
assert.doesNotMatch(startHere, /Local AI is planned for a later beta/);
assert.match(startHere, /getStartHereProfileCopy\(editionProfile\)/);
assert.match(startHere, /editionCopy\.settingsSection/);
assert.match(settings, /getAiSettingsProfileCopy\(config\?\.ai\.edition_profile\)/);
assert.match(settings, /TABS\.filter\(\(item\) => item\.id !== "local"\)/);

console.log("DEV-028 edition profile first-run and settings copy passed for Local and OpenRouter");
