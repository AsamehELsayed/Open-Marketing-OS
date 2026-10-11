/**
 * DEV-032 W3 — campaign deliverable workspace.
 *
 * Covers the three things the brief names, without a renderer or a test
 * framework (the repo's established shape: plain node, `node:assert/strict`,
 * execution of the script IS the test):
 *
 *   1. editor / list / status / history / export flow,
 *   2. project scope on every call,
 *   3. discarding stale results after a project switch.
 *
 * The modules under test are loaded with `ts.transpileModule` + a `data:` URL,
 * the same portable approach dev021/dev023/dev028 use, so the test runs on any
 * node the repo supports without an experimental type-stripping flag.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

/** Transpile a repo `.ts` module into an importable URL, rebinding its imports. */
async function moduleUrl(relativePath, replacements = {}) {
  const source = fs.readFileSync(new URL(relativePath, import.meta.url), "utf8");
  let code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  for (const [specifier, target] of Object.entries(replacements)) {
    code = code.split(specifier).join(target);
  }
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}

const CLIENT_URL = await moduleUrl("../src/api/client.ts");
const { api, ApiRequestError, campaignDeliverablePath, campaignDeliverablesPath, DELIVERABLE_STATUSES, DELIVERABLE_TYPES } =
  await import(CLIENT_URL);
const {
  canSubmitDraft,
  campaignBriefing,
  deliverableStatusLabel,
  deliverableTypeLabel,
  draftFromDeliverable,
  draftIsDirty,
  emptyDraft,
  isDeliverableStatus,
  isDeliverableType,
  nextDeliverableStatus,
  normalizeDeliverable,
  normalizeDeliverableList,
  normalizeRevision,
  normalizeRevisionList,
  summarizeRevision,
} = await import(
  await moduleUrl("../src/components/workspace/deliverables.ts", { "../../api/client.ts": CLIENT_URL })
);
const { isCurrentWorkspaceSelection } = await import(await moduleUrl("../src/routes/workspaceSelection.ts"));

const CLIENT_A = "client-a";
const CLIENT_B = "client-b";
const CAMPAIGN = "camp-001";
const SCOPE = { projectId: CLIENT_A, campaignId: CAMPAIGN };

/** A server row exactly as W1's `_public()` serialises it. */
function serverDeliverable(overrides = {}) {
  return {
    id: "del-001",
    project_id: CLIENT_A,
    campaign_id: CAMPAIGN,
    type: "social_post",
    title: "Launch post",
    platform: "LinkedIn",
    content_md: "# Launch\n\nFirst draft.",
    status: "DRAFT",
    current_version: 1,
    created_at: "2026-02-01T10:00:00Z",
    updated_at: "2026-02-01T10:00:00Z",
    ...overrides,
  };
}

function serverRevision(overrides = {}) {
  return {
    revision_id: "rev-001",
    project_id: CLIENT_A,
    campaign_id: CAMPAIGN,
    deliverable_id: "del-001",
    version: 1,
    operation: "CREATE",
    type: "social_post",
    title: "Launch post",
    platform: null,
    content_md: "# Launch\n\nFirst draft.",
    status: "DRAFT",
    created_at: "2026-02-01T10:00:00Z",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Project scope: every deliverable path carries BOTH ids, encoded.
// ---------------------------------------------------------------------------

assert.equal(
  campaignDeliverablesPath(CLIENT_A, CAMPAIGN),
  "/api/projects/client-a/campaigns/camp-001/deliverables",
  "the collection path is nested under both the project and the campaign",
);
assert.equal(
  campaignDeliverablePath(CLIENT_A, CAMPAIGN, "del-001"),
  "/api/projects/client-a/campaigns/camp-001/deliverables/del-001",
  "one deliverable is addressed under the same two-level scope",
);
assert.equal(
  campaignDeliverablePath("client a/../b", "camp 1", "del/001"),
  "/api/projects/client%20a%2F..%2Fb/campaigns/camp%201/deliverables/del%2F001",
  "path traversal and separators in ids are percent-encoded, never concatenated",
);

// ---------------------------------------------------------------------------
// Project scope: rows from another project are dropped before they are shown.
// ---------------------------------------------------------------------------

const kept = normalizeDeliverable(serverDeliverable(), SCOPE);
assert.ok(kept, "a row that echoes the requested scope is kept");
assert.equal(kept.title, "Launch post");
assert.equal(kept.current_version, 1);
assert.equal(kept.platform, "LinkedIn");

assert.equal(
  normalizeDeliverable(serverDeliverable({ project_id: CLIENT_B }), SCOPE),
  null,
  "another client's row is dropped, never rendered under this campaign",
);
assert.equal(
  normalizeDeliverable(serverDeliverable({ campaign_id: "camp-other" }), SCOPE),
  null,
  "another campaign's row is dropped even inside the right project",
);
assert.equal(
  normalizeDeliverable({ ...serverDeliverable(), project_id: undefined }, SCOPE),
  null,
  "a row that does not state its project is not trusted",
);
assert.equal(normalizeDeliverable(null, SCOPE), null);
assert.equal(normalizeDeliverable([serverDeliverable()], SCOPE), null, "an array is not a deliverable");

// ---------------------------------------------------------------------------
// Project scope: one stray row does not discard the list.
// ---------------------------------------------------------------------------

const mixed = normalizeDeliverableList(
  [
    serverDeliverable({ id: "del-a1" }),
    serverDeliverable({ id: "del-x", project_id: CLIENT_B }),
    serverDeliverable({ id: "del-a2", campaign_id: "camp-other" }),
    serverDeliverable({ id: "del-a3", status: "PUBLISHED" }),
    serverDeliverable({ id: "del-a4", type: "whitepaper" }),
    serverDeliverable({ id: "del-a5" }),
  ],
  SCOPE,
);
assert.deepEqual(
  mixed.map((row) => row.id),
  ["del-a1", "del-a5"],
  "only in-scope rows with a known type and status reach the list",
);
assert.equal(normalizeDeliverableList(null, SCOPE).length, 0, "a non-array list is empty, not a crash");

// ---------------------------------------------------------------------------
// Status flow: only DRAFT -> IN_REVIEW -> APPROVED, and APPROVED is terminal.
// ---------------------------------------------------------------------------

assert.deepEqual([...DELIVERABLE_STATUSES], ["DRAFT", "IN_REVIEW", "APPROVED"]);
assert.equal(nextDeliverableStatus("DRAFT"), "IN_REVIEW");
assert.equal(nextDeliverableStatus("IN_REVIEW"), "APPROVED");
assert.equal(nextDeliverableStatus("APPROVED"), null, "an approved deliverable has no next status");
assert.equal(nextDeliverableStatus("PUBLISHED"), null, "an unknown status offers nothing rather than guessing");
assert.equal(isDeliverableStatus("APPROVED"), true);
assert.equal(isDeliverableStatus("published"), false);
assert.equal(deliverableStatusLabel("IN_REVIEW"), "In review");
assert.equal(deliverableStatusLabel("WAT"), "Unknown");

// ---------------------------------------------------------------------------
// Types: exactly the five W1 persists, and nothing else is offered.
// ---------------------------------------------------------------------------

assert.deepEqual(
  [...DELIVERABLE_TYPES],
  ["strategy_brief", "social_post", "ad_copy", "creative_brief", "content_calendar"],
);
assert.equal(isDeliverableType("ad_copy"), true);
assert.equal(isDeliverableType("email"), false);
assert.equal(deliverableTypeLabel("content_calendar"), "Content calendar");
assert.equal(deliverableTypeLabel("mystery"), "Deliverable");

// ---------------------------------------------------------------------------
// Editor: draft state, dirty detection, and the no-op save guard.
// ---------------------------------------------------------------------------

const empty = emptyDraft();
assert.equal(empty.title, "");
assert.equal(empty.platform, "");
assert.equal(canSubmitDraft(empty), false, "an empty draft is not submittable");
assert.equal(
  canSubmitDraft({ ...empty, title: "T", content_md: "   " }),
  false,
  "whitespace-only content is not content",
);

const original = normalizeDeliverable(serverDeliverable(), SCOPE);
assert.ok(original);
const edited = draftFromDeliverable(original);
assert.equal(edited.type, "social_post");
assert.equal(edited.platform, "LinkedIn");
assert.equal(draftIsDirty(edited, original), false, "an untouched form is not dirty");
assert.equal(
  draftIsDirty({ ...edited, content_md: "# Launch\n\nSecond draft." }, original),
  true,
  "changed content is dirty",
);
assert.equal(
  draftIsDirty({ ...edited, title: "  Launch post  " }, original),
  false,
  "surrounding whitespace alone is not a change worth a new version",
);
assert.equal(
  draftIsDirty({ ...edited, platform: "" }, original),
  true,
  "clearing the platform is a real change",
);
assert.equal(draftIsDirty({ ...empty, title: "New" }, null), true, "any create draft with content is dirty");
assert.equal(draftIsDirty(empty, null), false);
assert.equal(draftIsDirty({ ...empty, type: "ad_copy" }, null), true, "changing the create type is dirty");
assert.equal(draftIsDirty({ ...empty, platform: "LinkedIn" }, null), true, "changing the create platform is dirty");
assert.equal(draftIsDirty({ ...empty, title: "A title" }, null), true, "changing the create title is dirty");
assert.equal(draftIsDirty({ ...empty, content_md: "# Draft" }, null), true, "changing create Markdown is dirty");

// ---------------------------------------------------------------------------
// History: in-scope only, newest first, and summarised for display.
// ---------------------------------------------------------------------------

const history = normalizeRevisionList(
  [
    serverRevision({ revision_id: "rev-1", version: 1 }),
    serverRevision({ revision_id: "rev-3", version: 3, operation: "STATUS", status: "APPROVED" }),
    serverRevision({ revision_id: "rev-2", version: 2 }),
    serverRevision({ revision_id: "rev-x", project_id: CLIENT_B }),
    serverRevision({ revision_id: "rev-y", deliverable_id: "del-other" }),
  ],
  SCOPE,
  "del-001",
);
assert.deepEqual(
  history.map((row) => row.version),
  [3, 2, 1],
  "history is newest first and excludes rows from another project or another deliverable",
);
assert.deepEqual(
  normalizeRevisionList([serverRevision()], SCOPE).map((row) => row.version),
  [1],
  "when the caller names no deliverable, an in-scope revision is still kept",
);
const newest = summarizeRevision(history[0]);
assert.equal(newest.operation, "STATUS");
assert.equal(newest.statusLabel, "Approved");
assert.equal(newest.excerpt, "# Launch First draft.", "the excerpt collapses Markdown whitespace for the summary line");
assert.equal(
  summarizeRevision({ ...history[0], content_md: "x".repeat(400) }).excerpt.length,
  140,
  "a long revision is truncated rather than pasted whole",
);
assert.equal(normalizeRevision(serverRevision(), SCOPE).deliverable_id, "del-001");
assert.equal(normalizeRevision(serverRevision({ revision_id: "" }), SCOPE), null, "a revision needs an id");
assert.equal(normalizeRevisionList("nope", SCOPE).length, 0);

// ---------------------------------------------------------------------------
// Overview: campaign planning fields are shown only when the API returns them.
// ---------------------------------------------------------------------------

const briefed = campaignBriefing({
  objective: "  Grow qualified demos  ",
  target_audience: ["Ops leads", "Founders"],
  channels: ["linkedin", "email"],
  campaign_project_leak: undefined,
});
assert.equal(briefed.objective, "Grow qualified demos");
assert.equal(briefed.audience, "Ops leads, Founders", "an array audience is joined, not dropped");
assert.equal(briefed.channels, "linkedin, email");
assert.equal(briefed.duration, "", "an absent field stays absent instead of showing a placeholder");

const unbriefed = campaignBriefing({ objective: "   ", channels: [] });
assert.equal(unbriefed.objective, "", "whitespace is not an objective");
assert.equal(unbriefed.audience, "");
assert.equal(campaignBriefing(null).objective, "");
assert.equal(campaignBriefing(undefined).channels, "");

// ---------------------------------------------------------------------------
// Stale results after a project switch: a response that belongs to the
// previous selection is discarded, including the A -> B -> A race that a
// project-id-only check would wrongly accept.
// ---------------------------------------------------------------------------

const generationOnA = 4;
assert.equal(
  isCurrentWorkspaceSelection(CLIENT_A, generationOnA, CLIENT_B, generationOnA + 1),
  false,
  "an in-flight client-a read is stale the moment client-b is selected",
);
assert.equal(
  isCurrentWorkspaceSelection(CLIENT_A, generationOnA, CLIENT_A, generationOnA + 2),
  false,
  "returning to client-a does not revive the first request; the epoch moved",
);
assert.equal(
  isCurrentWorkspaceSelection(CLIENT_A, generationOnA + 2, CLIENT_A, generationOnA + 2),
  true,
  "the current selection's own request is accepted",
);

// The list a slow client-a request resolves with, arriving after the user has
// already moved to client-b, must contribute nothing to client-b's page.
const lateClientAResponse = [serverDeliverable({ id: "del-a1" })];
const clientBScope = { projectId: CLIENT_B, campaignId: CAMPAIGN };
assert.equal(
  normalizeDeliverableList(lateClientAResponse, clientBScope).length,
  0,
  "a client-a response rendered under client-b is dropped",
);
assert.equal(
  normalizeDeliverableList(lateClientAResponse, SCOPE).length,
  1,
  "the same rows are still correct when the selection is back on client-a",
);

// ---------------------------------------------------------------------------
// Client: every request names its project AND campaign, and writes are CAS.
// ---------------------------------------------------------------------------

const calls = [];
const responders = [];
globalThis.fetch = async (url, init = {}) => {
  const body = init.body ? JSON.parse(init.body) : null;
  calls.push({ url: String(url), method: init.method ?? "GET", body });
  const next = responders.shift();
  return next ? next() : new Response(JSON.stringify({ ok: true, data: [] }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};

const scopedPrefix = `/api/projects/${CLIENT_A}/campaigns/${CAMPAIGN}/deliverables`;

await api.getCampaignDeliverables(CLIENT_A, CAMPAIGN);
await api.getCampaignDeliverable(CLIENT_A, CAMPAIGN, "del-001");
await api.createCampaignDeliverable(CLIENT_A, CAMPAIGN, {
  type: "social_post",
  title: "Launch post",
  content_md: "# Launch",
  platform: "LinkedIn",
});
await api.saveCampaignDeliverable(CLIENT_A, CAMPAIGN, "del-001", {
  expected_version: 3,
  type: "social_post",
  title: "Launch post",
  content_md: "# Launch v2",
});
await api.setCampaignDeliverableStatus(CLIENT_A, CAMPAIGN, "del-001", {
  expected_version: 4,
  status: "IN_REVIEW",
});
await api.getCampaignDeliverableHistory(CLIENT_A, CAMPAIGN, "del-001");

assert.deepEqual(
  calls.map((call) => `${call.method} ${call.url}`),
  [
    `GET ${scopedPrefix}`,
    `GET ${scopedPrefix}/del-001`,
    `POST ${scopedPrefix}`,
    `PUT ${scopedPrefix}/del-001`,
    `POST ${scopedPrefix}/del-001/status`,
    `GET ${scopedPrefix}/del-001/history`,
  ],
  "every deliverable request is nested under the requested project and campaign",
);
assert.equal(calls[2].body.platform, "LinkedIn");
assert.equal(
  calls[3].body.expected_version,
  3,
  "a revise carries the version it read, so the server can reject a stale write",
);
assert.equal(
  calls[4].body.expected_version,
  4,
  "a status change carries the version it read for the same reason",
);
assert.equal(calls[4].body.status, "IN_REVIEW", "the requested status travels in the body, not the URL");
assert.equal(calls[3].url.includes(CLIENT_B), false, "no other client is ever named in these calls");

// ---------------------------------------------------------------------------
// Client: a 409 surfaces as a conflict the editor can explain, not a silent
// overwrite.
// ---------------------------------------------------------------------------

responders.push(
  () =>
    new Response(JSON.stringify({ detail: { message: "version_conflict", code: "version_conflict" } }), {
      status: 409,
      headers: { "Content-Type": "application/json" },
    }),
);
await assert.rejects(
  () => api.saveCampaignDeliverable(CLIENT_A, CAMPAIGN, "del-001", { expected_version: 3 }),
  (error) => error instanceof ApiRequestError && error.status === 409 && error.code === "version_conflict",
  "a stale write is rejected with the status the editor needs to explain it",
);

// ---------------------------------------------------------------------------
// Client: exports use the same scoped paths and refuse a foreign extension.
// ---------------------------------------------------------------------------

const downloads = [];
globalThis.fetch = async (url, init = {}) => {
  downloads.push({ url: String(url), method: init.method ?? "GET" });
  const next = responders.shift();
  return next
    ? next()
    : new Response("bytes", {
        status: 200,
        headers: { "Content-Disposition": 'attachment; filename="launch-post.md"' },
      });
};
const mdFile = await api.downloadDeliverableMarkdown(CLIENT_A, CAMPAIGN, "del-001");
assert.equal(mdFile.filename, "launch-post.md");
const zipFile = await api.downloadCampaignDeliverablesPackage(CLIENT_A, CAMPAIGN);
assert.deepEqual(downloads, [
  { url: `${scopedPrefix}/del-001/export.md`, method: "GET" },
  { url: `${scopedPrefix}/exports/package.zip`, method: "GET" },
]);

globalThis.fetch = async () =>
  new Response("bytes", {
    status: 200,
    headers: { "Content-Disposition": 'attachment; filename="../../etc/passwd.md"' },
  });
const traversalName = await api.downloadDeliverableMarkdown(CLIENT_A, CAMPAIGN, "del-001");
assert.equal(
  traversalName.filename,
  "passwd.md",
  "directory segments are stripped from a server-supplied filename",
);

globalThis.fetch = async () =>
  new Response("bytes", {
    status: 200,
    headers: { "Content-Disposition": 'attachment; filename="deliverable.html"' },
  });
const wrongType = await api.downloadDeliverableMarkdown(CLIENT_A, CAMPAIGN, "del-001");
assert.equal(wrongType.filename, "deliverable.md", "a Markdown export that serves HTML falls back to the .md default");

// ---------------------------------------------------------------------------
// Client: the deliverable downloads were factored out of downloadChatMarkdown,
// so the existing chat export must behave exactly as it did before.
// ---------------------------------------------------------------------------

const chatDownloads = [];
globalThis.fetch = async (url, init = {}) => {
  chatDownloads.push(String(url));
  return new Response("# notes", {
    status: 200,
    headers: { "Content-Disposition": 'attachment; filename="chat-42.md"' },
  });
};
assert.equal((await api.downloadChatMarkdown("chat-42")).filename, "chat-42.md");
assert.deepEqual(chatDownloads, ["/api/chats/chat-42/export.md"]);

globalThis.fetch = async () =>
  new Response("nope", {
    status: 200,
    headers: { "Content-Disposition": 'attachment; filename="chat.exe"' },
  });
assert.equal(
  (await api.downloadChatMarkdown("chat-42")).filename,
  "conversation.md",
  "the chat export still refuses an extension it did not ask for",
);

console.log(
  "DEV-032 campaign deliverables: scoped calls, editor/status/history rules, exports, and stale-scope discard passed",
);
