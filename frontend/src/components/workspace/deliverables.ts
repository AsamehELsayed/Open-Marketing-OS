/**
 * DEV-032 W3 — pure helpers for the campaign deliverable workspace.
 *
 * Everything here is side-effect free and DOM free so it can be exercised by a
 * plain node test without a renderer. The React components stay presentational
 * and delegate every decision (what a label says, which status is legal next,
 * whether a server row belongs to the scope we asked about) to this module.
 *
 * Two rules are enforced rather than documented:
 *
 * 1. SCOPE. A deliverable or revision row is only usable when its own
 *    `project_id`/`campaign_id`/`deliverable_id` match the request that
 *    produced it. A row that does not match is dropped, never rendered. The
 *    backend already enforces this; the client enforces it again so a routing
 *    or caching bug can never paint one client's copy under another client's
 *    campaign.
 *
 * 2. EDITORIAL STATUS ONLY. DRAFT → IN_REVIEW → APPROVED is an internal
 *    sign-off state for the copy itself. These helpers deliberately expose no
 *    "publish", "schedule", or "spend" vocabulary, because approving a
 *    deliverable does none of those things anywhere.
 */

import {
  DELIVERABLE_STATUSES,
  DELIVERABLE_TYPES,
  type DeliverableStatus,
  type DeliverableType,
  type SpaCampaignDeliverable,
  type SpaDeliverableRevision,
} from "../../api/client.ts";

/** The exact scope a row must echo before it is allowed on screen. */
export interface DeliverableScope {
  projectId: string;
  campaignId: string;
}

const TYPE_LABELS: Record<string, string> = {
  strategy_brief: "Strategy brief",
  social_post: "Social post",
  ad_copy: "Ad copy",
  creative_brief: "Creative brief",
  content_calendar: "Content calendar",
};

const STATUS_LABELS: Record<string, string> = {
  DRAFT: "Draft",
  IN_REVIEW: "In review",
  APPROVED: "Approved",
};

export function deliverableTypeLabel(type: string): string {
  return TYPE_LABELS[type] ?? "Deliverable";
}

export function deliverableStatusLabel(status: string): string {
  return STATUS_LABELS[status] ?? "Unknown";
}

export function isDeliverableType(value: string): value is DeliverableType {
  return (DELIVERABLE_TYPES as readonly string[]).includes(value);
}

export function isDeliverableStatus(value: string): value is DeliverableStatus {
  return (DELIVERABLE_STATUSES as readonly string[]).includes(value);
}

/**
 * The single status the editor may offer next, or null at the end of the
 * flow. Only DRAFT → IN_REVIEW → APPROVED exists: APPROVED is terminal and
 * there is no way back, because reversing sign-off is an edit (which the
 * server reopens as a new DRAFT version), not a transition.
 */
export function nextDeliverableStatus(status: string): DeliverableStatus | null {
  if (status === "DRAFT") return "IN_REVIEW";
  if (status === "IN_REVIEW") return "APPROVED";
  return null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function versionOf(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

function belongsToScope(row: Record<string, unknown>, scope: DeliverableScope): boolean {
  return (
    text(row.project_id) === scope.projectId && text(row.campaign_id) === scope.campaignId
  );
}

/**
 * Accept a server row only when it is complete AND in scope. Anything else
 * becomes null so the caller can render an honest error instead of another
 * project's deliverable.
 */
export function normalizeDeliverable(
  raw: unknown,
  scope: DeliverableScope,
): SpaCampaignDeliverable | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const row = raw as Record<string, unknown>;
  if (!belongsToScope(row, scope)) return null;
  const id = text(row.id);
  const type = text(row.type);
  const status = text(row.status);
  if (!id || !isDeliverableType(type) || !isDeliverableStatus(status)) return null;
  return {
    id,
    project_id: scope.projectId,
    campaign_id: scope.campaignId,
    type,
    title: text(row.title),
    platform: text(row.platform) || null,
    content_md: typeof row.content_md === "string" ? row.content_md : "",
    status,
    current_version: versionOf(row.current_version),
    created_at: text(row.created_at),
    updated_at: text(row.updated_at),
  };
}

/** Same contract as normalizeDeliverable, for the collection endpoint. */
export function normalizeDeliverableList(
  raw: unknown,
  scope: DeliverableScope,
): SpaCampaignDeliverable[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((row) => normalizeDeliverable(row, scope))
    .filter((row): row is SpaCampaignDeliverable => row !== null);
}

export function normalizeRevision(
  raw: unknown,
  scope: DeliverableScope,
  deliverableId?: string,
): SpaDeliverableRevision | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const row = raw as Record<string, unknown>;
  if (!belongsToScope(row, scope)) return null;
  const revisionId = text(row.revision_id);
  const deliverable = text(row.deliverable_id);
  if (!revisionId || !deliverable) return null;
  // History is requested for exactly one deliverable. When the caller names
  // one, a revision for a sibling deliverable is a contract violation, not
  // history to render.
  if (deliverableId !== undefined && deliverable !== deliverableId) return null;
  return {
    revision_id: revisionId,
    project_id: scope.projectId,
    campaign_id: scope.campaignId,
    deliverable_id: deliverable,
    version: versionOf(row.version),
    operation: text(row.operation),
    type: text(row.type),
    title: text(row.title),
    platform: text(row.platform) || null,
    content_md: typeof row.content_md === "string" ? row.content_md : "",
    status: text(row.status),
    provenance_json: typeof row.provenance_json === "string" ? row.provenance_json : undefined,
    created_at: text(row.created_at),
  };
}

export function normalizeRevisionList(
  raw: unknown,
  scope: DeliverableScope,
  deliverableId?: string,
): SpaDeliverableRevision[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((row) => normalizeRevision(row, scope, deliverableId))
    .filter((row): row is SpaDeliverableRevision => row !== null)
    .sort((a, b) => b.version - a.version);
}

export interface RevisionSummary {
  version: number;
  operation: string;
  statusLabel: string;
  createdAt: string;
  excerpt: string;
}

export function summarizeRevision(revision: SpaDeliverableRevision): RevisionSummary {
  const body = revision.content_md.replace(/\s+/g, " ").trim();
  return {
    version: revision.version,
    operation: revision.operation || "EDIT",
    statusLabel: deliverableStatusLabel(revision.status),
    createdAt: revision.created_at,
    excerpt: body.length > 140 ? `${body.slice(0, 139)}…` : body,
  };
}

/** Editable fields of one deliverable, kept apart from the server row. */
export interface DeliverableDraft {
  type: DeliverableType;
  title: string;
  platform: string;
  content_md: string;
}

export function draftFromDeliverable(record: SpaCampaignDeliverable): DeliverableDraft {
  return {
    type: isDeliverableType(record.type) ? record.type : "social_post",
    title: record.title,
    platform: record.platform ?? "",
    content_md: record.content_md,
  };
}

export function emptyDraft(type: DeliverableType = "social_post"): DeliverableDraft {
  return { type, title: "", platform: "", content_md: "" };
}

export function draftIsDirty(draft: DeliverableDraft, record: SpaCampaignDeliverable | null): boolean {
  if (!record) {
    const baseline = emptyDraft();
    return (
      draft.type !== baseline.type ||
      draft.title.trim() !== baseline.title.trim() ||
      draft.platform.trim() !== baseline.platform.trim() ||
      draft.content_md !== baseline.content_md
    );
  }
  const current = draftFromDeliverable(record);
  return (
    draft.type !== current.type ||
    draft.title.trim() !== current.title.trim() ||
    draft.platform.trim() !== current.platform.trim() ||
    draft.content_md !== current.content_md
  );
}

/**
 * Guard before sending a write: a create needs a title and a non-empty body,
 * a revise needs something that actually changed. Submitting an unchanged
 * form would burn a version number and, on an APPROVED row, reopen it as a
 * fresh DRAFT for no reason.
 */
export function canSubmitDraft(draft: DeliverableDraft): boolean {
  return draft.title.trim().length > 0 && draft.content_md.trim().length > 0;
}

export interface CampaignBriefing {
  objective: string;
  audience: string;
  channels: string;
  duration: string;
  request: string;
}

/**
 * Read the campaign's own planning fields defensively.
 *
 * DEV-032's backend (W1/W2) is the owner of these field names and the set is
 * still in flight, so each one is read through a candidate list and rendered
 * only when it is a non-empty string. A field the API does not return yet is
 * simply absent — the panel never invents a placeholder objective.
 */
export function campaignBriefing(
  campaign: Record<string, unknown> | null | undefined,
): CampaignBriefing {
  const row = campaign ?? {};
  const pick = (...keys: string[]): string => {
    for (const key of keys) {
      const value = row[key];
      if (typeof value === "string" && value.trim()) return value.trim();
      if (Array.isArray(value)) {
        const joined = value.filter((item): item is string => typeof item === "string").map((item) => item.trim()).filter(Boolean);
        if (joined.length) return joined.join(", ");
      }
    }
    return "";
  };
  return {
    objective: pick("objective", "goal", "campaign_objective"),
    audience: pick("target_audience", "audience", "campaign_audience"),
    channels: pick("channels", "channel_mix"),
    duration: pick("duration", "campaign_duration", "flight"),
    request: pick("request_facts", "request", "brief_request"),
  };
}
