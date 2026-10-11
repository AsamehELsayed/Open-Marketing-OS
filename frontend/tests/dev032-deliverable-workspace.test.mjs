/**
 * DEV-032 W3 — deliverable workspace wiring.
 *
 * `DeliverableEditor.tsx` and `CampaignDetail.tsx` are React components and
 * a `.tsx` cannot be imported by this node test. This file checks static
 * scope/version contracts; the history race also has a real-browser,
 * deferred-response test.
 *
 *   - every async action re-checks the selection epoch before touching state,
 *   - writes carry the version they read,
 *   - the workspace refuses to render under the wrong client,
 *   - the copy never implies approval publishes or spends.
 *
 * The decisions themselves are covered behaviourally in
 * dev032-campaign-deliverables.test.mjs.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const editor = fs.readFileSync(path.join(root, "src/components/workspace/DeliverableEditor.tsx"), "utf8");
const detail = fs.readFileSync(path.join(root, "src/routes/CampaignDetail.tsx"), "utf8");
const client = fs.readFileSync(path.join(root, "src/api/client.ts"), "utf8");

// ---------------------------------------------------------------------------
// Stale responses and writes: scope guards remain in place for each action.
// ---------------------------------------------------------------------------

const actions = [
  ["save", "function transition"],
  ["transition", "async function openHistory"],
  ["openHistory", "async function exportMarkdown"],
  ["exportMarkdown", "async function exportPackage"],
  ["exportPackage", "if (!projectId)"],
].map(([start, end]) => editor.split(`async function ${start}(`)[1].split(end)[0]);

for (const [index, action] of actions.entries()) {
  assert.match(action, /const epoch = requestGeneration\.current/,
    `action ${index} captures the selection epoch before it awaits`);
  assert.match(action, /const isCurrent = \(\) =>\s*isCurrentWorkspaceSelection\(/,
    `action ${index} guards on project id AND epoch, so A→B→A cannot revive it`);
  if (index === 0) {
    assert.match(action, /selectedIdRef\.current !== selectionAtStart/,
      "save does not reselect an older row after the user changes selection");
  } else if (index === 1) {
    assert.match(action, /selectedIdRef\.current !== deliverableId/,
      "a status write does not reselect an older row after the user changes selection");
  } else if (index === 2) {
    assert.match(action, /selectedIdRef\.current !== deliverableId/,
      "history discards its result when the selected deliverable moved");
    assert.match(action, /!scopedNow\(\)/,
      "history discards continuations after a same-project campaign switch");
    assert.match(action, /const rows = await api\.getCampaignDeliverableHistory\(pid, cid, deliverableId\);\s*if \(!isCurrent\(\) \|\| !scopedNow\(\) \|\| selectedIdRef\.current !== deliverableId\) return;/,
      "history success checks the captured campaign before storing rows");
    assert.match(action, /catch \(e\) \{\s*if \(!isCurrent\(\) \|\| !scopedNow\(\) \|\| selectedIdRef\.current !== deliverableId\) return;/,
      "history errors are ignored after a same-project campaign switch");
    assert.match(action, /finally \{\s*if \(isCurrent\(\) && scopedNow\(\) && selectedIdRef\.current === deliverableId\)/,
      "stale campaign history cannot clear the current campaign's loading state");
  } else {
    assert.match(action, /if \(!isCurrent\(\) \|\| (!scopedNow\(\)|selectedId !== deliverableId)\) return/,
      `action ${index} discards its result when the project, campaign, or selection moved`);
  }
  assert.match(action, /catch \(e\) \{\s*if \(!isCurrent\(\)(?: \|\| !scopedNow\(\))?(?: \|\| selectedIdRef\.current !== (?:selectionAtStart|deliverableId))?\) return;/,
    `action ${index} ignores a failure that belongs to a superseded selection`);
  assert.match(action, /finally \{\s*if \(isCurrent\(\)(?: && scopedNow\(\))?(?: && selectedIdRef\.current === deliverableId)?\)/,
    `action ${index} clears its busy flag only for the selection it belongs to`);
}

// ---------------------------------------------------------------------------
// The list load itself is guarded the same way, and resets state up front so a
// previous client's copy can never be shown under the new client's campaign.
// ---------------------------------------------------------------------------

const loader = editor.split("useEffect(() => {")[1].split("}, [scope]);")[0];
assert.match(loader, /const token = \+\+requestGeneration\.current/);
assert.match(loader, /setItems\(\[\]\)/, "the previous client's list is cleared before the new one arrives");
assert.match(loader, /chooseSelectedId\(null\)/);
assert.match(loader, /setHistory\(\[\]\)/, "revision history is per deliverable and is not carried across a switch");
assert.match(loader, /setDraft\(emptyDraft\(\)\)/, "an unsaved draft from another client is discarded, not carried over");
assert.match(loader, /if \(!scope\) return;/, "with no client selected nothing is requested at all");
assert.match(loader, /normalizeDeliverableList\(rows, scope\)/,
  "rows are re-checked against the requested scope before they are stored");
assert.match(loader, /requestGeneration\.current\+\+;/, "the effect bumps the epoch on cleanup so the next scope invalidates this one");

const startCreateAction = editor.split("function startCreate() {")[1].split("function cancelEdit()")[0];
const selectAction = editor.split("function select(id: string) {")[1].split("function updateDraft")[0];
assert.match(startCreateAction, /if \(!confirmDiscardDraft\(\)\) return;/,
  "starting a new draft asks before replacing actual unsaved content");
assert.match(selectAction, /if \(!confirmDiscardDraft\(\)\) return;/,
  "selecting a saved row asks before replacing actual unsaved content");
assert.match(editor, /function confirmDiscardDraft\(\): boolean \{\s*if \(mode === null \|\| !dirty\) return true;\s*const baseline = mode === "create" \? null : selected;\s*if \(!draftIsDirty\(draft, baseline\)\) return true;\s*return window\.confirm\(/,
  "the shared transition guard checks create drafts against emptyDraft and edits against the selected row");

// ---------------------------------------------------------------------------
// Writes are optimistic: the version that was read travels with the write.
// ---------------------------------------------------------------------------

const saveAction = actions[0];
assert.match(saveAction, /expected_version: selected\.current_version/,
  "a revise sends the version it read so a stale tab gets a conflict instead of overwriting");
assert.match(saveAction, /api\.createCampaignDeliverable\(pid, cid, \{ type: draft\.type, \.\.\.body \}\)/);
assert.match(saveAction, /const pid = scope\.projectId;\s*const cid = scope\.campaignId;/,
  "the identifiers sent are the captured scope, never the props at resolve time");

const transitionAction = actions[1];
assert.match(transitionAction, /expected_version: selected\.current_version/,
  "a status change is also a compare-and-swap");
assert.match(transitionAction, /const deliverableId = selected\.id;/,
  "the transition targets the deliverable that was selected when it started");

// ---------------------------------------------------------------------------
// Conflicts are explained, not silently resolved by overwriting.
// ---------------------------------------------------------------------------

assert.match(editor, /error\.status === 409[\s\S]*?your save was rejected and nothing was overwritten/,
  "a 409 tells the user nothing was lost and nothing was clobbered");
assert.match(editor, /error\.status === 404[\s\S]*?does not belong to the selected client/,
  "a 404 is reported as a scope problem rather than a generic failure");
assert.doesNotMatch(editor, /window\.location\.reload/,
  "a rejected write must not reload the page and throw away the user's text");

// ---------------------------------------------------------------------------
// Approval is an internal editorial sign-off, never a publish or a spend.
// ---------------------------------------------------------------------------

assert.match(editor, /it does not publish, schedule, or spend anything/,
  "the panel states what sign-off does not do");
assert.match(editor, /Status is now \$\{deliverableStatusLabel\(saved\.status\)\}/,
  "the confirmation names the new status instead of implying a side effect");
assert.match(editor, /"Approve internally"/, "the approve action is labelled internal sign-off");
assert.match(editor, /"Send to review"/);

// "publish", "schedule" and "spend" may appear only inside a denial. A single
// unnegated occurrence would be a promise the backend deliberately does not
// keep: DEV-032 approval is editorial and must not imply distribution.
const externalWords = [...editor.matchAll(/\b(publish\w*|schedul\w*|spend\w*|post to|go live|run ads)\b/gi)];
assert.ok(externalWords.length > 0, "the panel still has to say what sign-off does not do");
for (const match of externalWords) {
  const context = editor.slice(Math.max(0, match.index - 90), match.index + 40);
  assert.match(
    context,
    /(does not|nothing|never|no publishing|not\b)/i,
    `"${match[0]}" must only ever appear inside a denial of external action`,
  );
}
assert.doesNotMatch(editor, /api\.(startCampaign|publish|schedule|launch|createAd|sendEmail)/,
  "no external action is reachable from the deliverable workspace");
assert.doesNotMatch(editor, /<StatusBadge status="(Published|Live|Sent|Scheduled|Running)"/,
  "no deliverable status is presented as a distribution outcome");

// ---------------------------------------------------------------------------
// Scope gate: no raw fetch escapes the client, and the route refuses the wrong
// client rather than silently loading it.
// ---------------------------------------------------------------------------

assert.doesNotMatch(detail, /\bfetch\(/,
  "the route uses the API client; it does not hand-write requests");
assert.match(detail, /campaignProjectId === projectId \? \(\s*<DeliverableEditor projectId=\{projectId\} campaignId=\{id\} \/>/,
  "the workspace mounts only when the selected client is the campaign's own client");
assert.match(detail, /title="Switch to this campaign's client"/,
  "a mismatch is explained, not rendered as an empty workspace");
assert.match(detail, /\{projectId && id &&/, "the workspace is not mounted without an explicit project and campaign");

// ---------------------------------------------------------------------------
// Overview shows the campaign's own planning fields, and only what exists.
// ---------------------------------------------------------------------------

assert.match(detail, /<Field label="Objective" value=\{briefing\.objective\} \/>/);
assert.match(detail, /<Field label="Target audience" value=\{briefing\.audience\} \/>/);
assert.match(detail, /<Field label="Channels" value=\{briefing\.channels\} \/>/);
assert.doesNotMatch(detail, /No content yet/,
  "the Content tab is no longer the placeholder it used to be");

// ---------------------------------------------------------------------------
// The client owns the requests: every deliverable method names both ids and
// there is no unscoped variant to reach for by mistake.
// ---------------------------------------------------------------------------

for (const method of [
  "getCampaignDeliverables",
  "getCampaignDeliverable",
  "createCampaignDeliverable",
  "saveCampaignDeliverable",
  "setCampaignDeliverableStatus",
  "getCampaignDeliverableHistory",
  "downloadDeliverableMarkdown",
  "downloadCampaignDeliverablesPackage",
]) {
  const signature = client.match(new RegExp(`\\n  ${method}: \\(([^)]*)\\)`))?.[1] ?? "";
  assert.match(
    signature,
    /^project_id: string, campaign_id: string/,
    `${method} cannot be called without naming the project and the campaign`,
  );
}

console.log(
  "DEV-032 deliverable workspace: stale guards, CAS writes, client scoping, and editorial-only approval passed",
);
