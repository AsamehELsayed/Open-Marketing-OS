/**
 * DEV-004 W1 typed API client.
 *
 * - Chat send/streaming REUSES the existing Jinja endpoints verbatim:
 *   POST /chat/turn (form: conversation_id, text, client_message_id) returns
 *   an HTML ack containing a `data-turn-stream="<turnId>"` marker;
 *   GET /chat/turns/{id}/events is SSE with events assistant_delta /
 *   assistant_completed / turn_completed / turn_failed plus lifecycle events
 *   whose data payload is {label, detail, meta}.
 * - All /api/* responses use the {"ok": true, "data": ...} envelope.
 * - DEV-008-SKILLS-OPS: `LEGACY_EVENT_TYPES` below mirrors the server's frozen
 *   event catalog name-for-name, so every event the server can emit has a live
 *   listener here. `/api/skills` reads the vendored marketing playbook
 *   registry; skills are knowledge, tools are action, and the two vocabularies
 *   never share a type.
 */

export interface ApiEnvelope<T> {
  ok: boolean;
  data: T;
}

export interface SpaProject {
  id: string;
  name: string;
  website: string;
  goal: string;
}

export interface SpaChat {
  id: string;
  title: string;
  project_id: string;
  updated_at: string;
}

export interface SpaMessage {
  role: string;
  body_md: string;
  citations: unknown[];
}

export interface SpaActivityEvent {
  id: number;
  event_type: string;
  label: string;
  detail: string;
}

/**
 * DEV-008-SKILLS-OPS §1.1: one vendored marketing playbook, as the server
 * serialises it (`MarketingSkillRecord.to_dict()`).
 *
 * Mirrored field-for-field. `path` is RELATIVE by construction
 * (`.agents/skills/<skill_id>`) because an absolute path would be redacted to
 * `[REDACTED]` by `sanitize_user_text` on the way to the wire.
 */
export interface SpaSkillRecord {
  skill_id: string;
  name: string;
  description: string;
  version: string;
  source: string;
  path: string;
  triggers?: string[];
  related_skills?: string[];
  unresolved_related?: string[];
  category: string;
  checksum: string;
  eval_count: number;
  enabled: boolean;
  validation_status: string;
  validation_reason?: string;
  description_truncated?: boolean;
}

/**
 * DEV-008-SKILLS-OPS: `GET /api/skills`.
 *
 * Every count here is MEASURED by the loader on the request. The client never
 * recomputes a total and never assumes a library size, so a re-pin needs no
 * frontend change.
 */
export interface SpaSkillsResponse {
  library_root: string;
  hash_scheme: string;
  summary: string;
  total: number;
  valid: number;
  missing: number;
  invalid: { skill_id: string; reason_code: string }[];
  warnings: string[];
  library_checksum: string;
  file_count: number;
  skills: SpaSkillRecord[];
}

/** `GET /api/skills/manifest` — the generated pin document. */
export interface SpaSkillsManifest {
  schema: string;
  upstream: {
    repository: string;
    version: string;
    commit: string;
    license: string;
    license_file?: string;
  };
  hash_scheme: string;
  skill_count: number;
  file_count: number;
  library_checksum: string;
  role_table_version?: string;
  skills: SpaSkillRecord[];
}

export interface SpaJob {
  id: string;
  kind: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface SpaApproval {
  id: string;
  title: string;
  kind: string;
  status: string;
  body_md: string;
  project_id?: string;
  requested_by?: string;
  task?: { id: string; title: string };
  campaign?: { id: string; title: string };
}

export interface OnboardingStatus {
  business_described: boolean;
  intro_version_seen: string;
  project: SpaProject;
}

export interface CreateBusinessBody {
  name: string;
  website?: string;
  context?: string;
  primary_market?: string;
}

export interface CreateBusinessResult {
  project: Pick<SpaProject, "id" | "name" | "website">;
  warning: string;
  starting_points: Array<{ title: string; prompt: string }>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = "";
    try {
      const body = (await res.clone().json()) as { detail?: unknown; error?: unknown; message?: unknown };
      const nestedError = body.error && typeof body.error === "object"
        ? (body.error as { message?: unknown; code?: unknown })
        : null;
      detail = typeof body.detail === "string"
        ? body.detail
        : typeof body.message === "string"
          ? body.message
          : typeof nestedError?.message === "string"
            ? nestedError.message
            : typeof nestedError?.code === "string"
              ? nestedError.code.replace(/_/g, " ")
              : typeof body.error === "string"
                ? body.error
                : "";
    } catch {
      // Keep the status-based fallback when the server did not return JSON.
    }
    throw new Error(detail || `Request failed (${res.status}). Please try again.`);
  }
  const json = (await res.json()) as ApiEnvelope<T>;
  if (!json || json.ok !== true) {
    throw new Error(`bad envelope from ${path}`);
  }
  return json.data;
}

export type CredentialConfig = Record<string, string | number | boolean | null>;

export interface CredentialConnectBody {
  target: string;
  token?: string;
  scope?: string;
  project_id?: string;
  config?: CredentialConfig;
  dataset_id?: string;
  ig_account_id?: string;
  account_id?: string;
  server_id?: string;
  endpoint?: string;
}

export interface CredentialTestBody {
  target: string;
  scope?: string;
  project_id?: string;
  config?: CredentialConfig;
  dataset_id?: string;
  ig_account_id?: string;
  account_id?: string;
  server_id?: string;
  endpoint?: string;
}

export interface CredentialDisconnectBody {
  target: string;
  scope?: string;
  project_id?: string;
  server_id?: string;
}

export interface CredentialTestResult {
  target: string;
  configured?: boolean;
  status?: string;
  credential_status?: string;
  capability_health?: string;
  provider_health?: string;
  last_error_code?: string;
  actor_id?: string;
  cost_type?: string;
  detail?: string;
  last_checked_at?: string;
  last_checked?: string;
  model_count?: number | null;
  actual_model?: string | null;
  dataset_configured?: boolean;
  account_configured?: boolean;
  server_id?: string;
  health_status?: string;
  checked_at?: string;
}

export interface SettingsMcpServer {
  id?: string;
  name?: string;
  server?: string;
  server_id?: string;
  scope?: string;
  allowed?: boolean;
  connected?: boolean;
  status?: string;
  last_health?: string;
  health_status?: string;
  last_checked_at?: string;
  detail?: string;
  approval_label?: string;
  approval_tone?: string;
  capabilities?: Array<{ name: string; label: string; tone: string }>;
}

export interface SettingsVisionStatus {
  status?: string;
  provider_status?: string;
  capability_health?: string;
  last_checked_at?: string;
  available?: boolean;
  configured?: boolean;
}

export interface SettingsConfig {
  project_id: string;
  general: {
    theme: string;
    auto_load_project: boolean;
    language: string;
  };
  ai: {
    ai_mode: string;
    local_ai_status: string;
    active_model: string;
    marketing_adapter: string;
    manager_provider: string;
    openai_connected: boolean;
    openrouter_connected?: boolean;
    openrouter_default_model?: string;
    openrouter_credential_status?: string;
    openrouter_provider_health?: string;
    openrouter_capability_health?: string;
    openrouter_last_checked_at?: string;
    cloud_escalation?: string;
    orchestrator: string;
  };
  integrations: {
    instagram_public: {
      connected: boolean;
      provider: string;
      apify_ready: boolean;
      apify_connected?: boolean;
      apify_credential_status?: string;
      apify_provider_health?: string;
      apify_capability_health?: string;
      apify_last_checked_at?: string;
      apify_actor_id?: string;
      last_checked_at?: string;
      last_error_code?: string;
      cost_type?: string;
      brightdata_ready: boolean;
      brightdata_connected?: boolean;
      brightdata_credential_status?: string;
      brightdata_provider_health?: string;
      brightdata_capability_health?: string;
      brightdata_last_checked_at?: string;
      brightdata_dataset_id?: string;
      brightdata_dataset?: string;
      brightdata_dataset_configured?: boolean;
      brightdata_config?: CredentialConfig;
      dataset_id?: string;
      browser_fallback: boolean;
    };
    instagram_project: {
      project_id: string;
      handle: string;
      verified: boolean;
    };
    openai?: {
      connected: boolean;
      credential_status?: string;
      provider_health?: string;
      capability_health?: string;
      last_checked_at?: string;
    };
    openrouter?: {
      connected: boolean;
      credential?: string;
      credential_status?: string;
      provider?: string;
      provider_health?: string;
      capability?: string;
      capability_health?: string;
      last_checked_at?: string;
      default_model?: string;
      cloud_escalation?: string;
      last_error_code?: string;
    };
    meta_insights: {
      connected: boolean;
      status: string;
      credential_status?: string;
      provider_health?: string;
      capability_health?: string;
      last_checked_at?: string;
      account_id?: string;
      ig_account_id?: string;
      account_configured?: boolean;
      config?: CredentialConfig;
    };
    web_research: {
      status: string;
      provider: string;
    };
    mcp: {
      servers: SettingsMcpServer[];
      count?: number;
    };
    vision?: SettingsVisionStatus;
  };
  files_knowledge: {
    project_id: string;
    indexed_documents: number;
    project_files: number;
    search_mode: string;
    vector_status?: string;
    vector_reason?: string;
    accepted_types?: FileCapability[];
    max_document_bytes?: number;
    max_image_bytes?: number;
    ocr_supported?: boolean;
    count_status?: string;
    count_error?: string;
    storage_location: string;
    max_upload_mb: number;
  };
  privacy_security: {
    vault_backend: string;
    encryption: string;
    active_credentials: Array<{
      scope: string;
      label: string;
      created_at: string;
      status: string;
    }>;
    total_stored: number;
    data_privacy: string;
  };
  system: {
    version: string;
    status?: string;
    backend: string;
    orchestrator: string;
    database: string;
    rag: string;
    model_runtime: string;
    active_model: string;
  };
  vision?: SettingsVisionStatus;
}

export interface FileCapability {
  label: string;
  extensions: string[];
  mime: string | string[];
  searchable_text: boolean;
  max_bytes: number;
}

export interface FileCapabilities {
  accepted_types: FileCapability[];
  max_document_bytes: number;
  max_image_bytes: number;
  ocr_supported: boolean;
}

export interface ProjectKnowledgeFile {
  file_id: string;
  project_id: string;
  original_name: string;
  mime_detected: string;
  size: number;
  kind?: string;
  extraction?: string;
  attach_scope: string;
  indexed: boolean;
  index_status?: "pending" | "indexed" | "failed" | "quarantined" | "not_searchable" | string;
  index_error?: string | null;
  created_at?: string;
  conversation_id?: string;
  search_mode?: string;
  vector_status?: string;
  vector_reason?: string;
}

export interface UploadProgress {
  status: "running" | "completed" | "failed";
  stage: string;
  current: number | null;
  total: number | null;
}

export type AttachmentScope = "turn" | "project";

/** Upload progress describes actual transferred bytes, then server processing. */
export function uploadChatAttachment(
  file: File,
  projectId: string,
  conversationId: string,
  scope: AttachmentScope,
  progressId: string,
  onProgress: (percent: number | null) => void,
  signal: AbortSignal,
): Promise<ProjectKnowledgeFile> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException("Upload cancelled", "AbortError")); return; }
    const form = new FormData();
    form.set("file", file);
    form.set("project_id", projectId);
    form.set("attach_scope", scope);
    form.set("progress_id", progressId);
    if (scope === "turn") form.set("conversation_id", conversationId);
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const cleanup = () => signal.removeEventListener("abort", abort);
    signal.addEventListener("abort", abort, { once: true });
    xhr.open("POST", "/files/upload");
    xhr.upload.onprogress = (event) => onProgress(event.lengthComputable
      ? Math.min(100, Math.round(event.loaded * 100 / event.total)) : null);
    xhr.upload.onload = () => onProgress(null);
    xhr.onerror = () => { cleanup(); reject(new Error("File upload failed. Check your connection.")); };
    xhr.onabort = () => { cleanup(); reject(new DOMException("Upload cancelled", "AbortError")); };
    xhr.onload = () => {
      cleanup();
      try {
        const body = JSON.parse(xhr.responseText) as {
          ok?: boolean; data?: ProjectKnowledgeFile; error?: { message?: string; code?: string };
        };
        if (xhr.status < 200 || xhr.status >= 300 || body.ok !== true || !body.data?.file_id) {
          throw new Error(body.error?.message || body.error?.code?.replace(/_/g, " ") || "File could not be uploaded.");
        }
        const uploaded = body.data;
        if (uploaded.project_id !== projectId || uploaded.attach_scope !== scope ||
            (scope === "turn" && uploaded.conversation_id !== conversationId)) {
          throw new Error("File upload returned a different chat or project scope.");
        }
        if (scope === "project") {
          window.dispatchEvent(new CustomEvent("omos:knowledge-changed", { detail: { projectId } }));
        }
        resolve(uploaded);
      } catch (error) { reject(error instanceof Error ? error : new Error("Invalid upload response.")); }
    };
    xhr.send(form);
  });
}

export interface KnowledgeRebuildReport {
  discovered: number;
  indexed: number;
  skipped: number;
  deleted: number;
  quarantined: number;
  not_searchable: number;
  failed: number;
  chunks: number;
  project_id: string;
  search_mode: string;
  vector_status: string;
  vector_reason?: string;
}

export interface KnowledgeRebuildResult {
  status: "indexed" | "partial" | "failed" | "empty" | string;
  project_id: string;
  message: string;
  report: KnowledgeRebuildReport;
}

export interface WorkspaceSyncResult {
  status: string;
  message: string;
  project_id?: string;
  stats?: Record<string, number | string | boolean | null>;
  [key: string]: unknown;
}

export interface SpaAiModel {
  id: string;
  name?: string;
  context_length?: number | null;
  modalities?: unknown;
  supports_tools?: boolean;
  pricing?: Record<string, unknown>;
}

/** Sanitized Local model catalog and lifecycle state from the managed runtime API. */
export interface LocalRuntimeStatus {
  model_id: string;
  display_name: string;
  source: string;
  publisher: string;
  license: string;
  license_url: string;
  model_bytes: number;
  temporary_disk_bytes: number;
  sha256: string;
  download_state: "not_started" | "downloading" | "downloaded" | "failed" | string;
  downloaded_bytes: number;
  download_total_bytes: number;
  verification_state: "not_verified" | "verifying" | "verified" | "failed" | string;
  activation_state: "inactive" | "active" | "failed" | string;
  runtime_state: "stopped" | "starting" | "ready" | "failed" | string;
  observed_model_id?: string | null;
  error_code?: string | null;
}

export const api = {
  getLocalRuntime: () => request<LocalRuntimeStatus>("/api/local/runtime"),
  downloadLocalModel: () => request<LocalRuntimeStatus>("/api/local/model/download", { method: "POST" }),
  startLocalRuntime: () => request<LocalRuntimeStatus>("/api/local/runtime/start", { method: "POST" }),
  stopLocalRuntime: () => request<LocalRuntimeStatus>("/api/local/runtime/stop", { method: "POST" }),
  getOnboardingStatus: () => request<OnboardingStatus>("/api/onboarding/status"),
  createBusiness: (body: CreateBusinessBody) =>
    request<CreateBusinessResult>("/api/onboarding/business", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  completeStartHere: (version = "start-here-v1") =>
    request<{ intro_version_seen: string }>("/api/onboarding/intro/complete", {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  getProjects: () => request<SpaProject[]>("/api/projects"),
  getChats: (params: { project_id?: string; q?: string; status?: string } = {}) => {
    const qs = new URLSearchParams();
    if (params.project_id) qs.set("project_id", params.project_id);
    if (params.q) qs.set("q", params.q);
    if (params.status) qs.set("status", params.status);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request<SpaChat[]>(`/api/chats${suffix}`);
  },
  renameChat: (id: string, title: string) =>
    request<SpaChat>(`/api/chats/${encodeURIComponent(id)}/rename`, {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  archiveChat: (id: string) =>
    request<SpaChat>(`/api/chats/${encodeURIComponent(id)}/archive`, {
      method: "POST",
    }),
  getMessages: (id: string) =>
    request<SpaMessage[]>(`/api/chats/${encodeURIComponent(id)}/messages`),
  getActivity: (conversation_id: string) =>
    request<SpaActivityEvent[]>(
      `/api/activity?conversation_id=${encodeURIComponent(conversation_id)}`,
    ),
  getJobs: (project_id?: string) =>
    request<SpaJob[]>(
      project_id ? `/api/jobs?project_id=${encodeURIComponent(project_id)}` : "/api/jobs",
    ),
  getApprovals: (params: { project_id?: string; status?: string } = {}) => {
    const qs = new URLSearchParams();
    if (params.project_id) qs.set("project_id", params.project_id);
    if (params.status) qs.set("status", params.status);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request<SpaApproval[]>(`/api/approvals${suffix}`);
  },
  getCampaigns: (project_id?: string) =>
    request<Record<string, unknown>[]>(
      project_id ? `/api/campaigns?project_id=${encodeURIComponent(project_id)}` : "/api/campaigns",
    ),
  getCampaign: (id: string) =>
    request<Record<string, unknown>>(`/api/campaigns/${encodeURIComponent(id)}`),
  getResultsSummary: (project_id?: string) =>
    request<Record<string, unknown>>(
      project_id
        ? `/api/results/summary?project_id=${encodeURIComponent(project_id)}`
        : "/api/results/summary",
    ),
  getKnowledge: (project_id?: string) =>
    request<Record<string, unknown>>(
      project_id
        ? `/api/knowledge?project_id=${encodeURIComponent(project_id)}`
        : "/api/knowledge",
    ),
  getSettingsConfig: (project_id?: string) =>
    request<SettingsConfig>(
      project_id
        ? `/api/settings/config?project_id=${encodeURIComponent(project_id)}`
        : "/api/settings/config",
    ),
  updateGeneralSettings: (body: { theme?: string; auto_load_project?: boolean; language?: string }) =>
    request<Record<string, unknown>>("/api/settings/general", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateAiSettings: (body: { ai_mode?: string; active_model?: string; marketing_adapter?: string; manager_provider?: string; openrouter_default_model?: string; cloud_escalation?: string }) =>
    request<Record<string, unknown>>("/api/settings/ai", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getAiModels: (provider: string = "openrouter", refresh = false) =>
    request<{
      provider: string;
      connected: boolean;
      models: SpaAiModel[];
      curated: string[];
      cached_at?: number;
      detail?: string;
    }>(`/api/ai/models?provider=${encodeURIComponent(provider)}${refresh ? "&refresh=1" : ""}`),
  connectCredential: (body: CredentialConnectBody) =>
    request<{ target: string; status: string; message: string; configured?: boolean }>(
      "/api/settings/credentials/connect",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),
  testCredential: (body: CredentialTestBody) =>
    request<CredentialTestResult>("/api/settings/credentials/test", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  disconnectCredential: (body: CredentialDisconnectBody) =>
    request<{ target: string; status: string; message: string }>(
      "/api/settings/credentials/disconnect",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),
  updateInstagramSettings: (body: { public_provider?: string; handle?: string; project_id?: string }) =>
    request<{ status: string; project_id: string }>("/api/settings/integrations/instagram", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  syncWorkspace: (project_id?: string) =>
    request<WorkspaceSyncResult>("/api/settings/workspace/reimport", {
      method: "POST",
      body: JSON.stringify({ project_id }),
    }),
  reindexWorkspace: (project_id?: string) =>
    request<KnowledgeRebuildResult>("/api/settings/workspace/reindex", {
      method: "POST",
      body: JSON.stringify({ project_id }),
    }),
  getProjectKnowledgeFiles: (project_id: string) => {
    const qs = new URLSearchParams({ project_id, attach_scope: "project" });
    return request<ProjectKnowledgeFile[]>(`/files?${qs.toString()}`);
  },
  uploadProjectKnowledgeFile: (file: File, project_id: string, progress_id?: string) => {
    const form = new FormData();
    form.set("file", file);
    form.set("project_id", project_id);
    form.set("attach_scope", "project");
    if (progress_id) form.set("progress_id", progress_id);
    return request<ProjectKnowledgeFile & { index_status?: string; index_error?: string | null }>(
      "/files/upload",
      { method: "POST", body: form },
    );
  },
  getUploadProgress: (progress_id: string, project_id: string) => {
    const qs = new URLSearchParams({ project_id });
    return request<UploadProgress>(`/files/progress/${encodeURIComponent(progress_id)}?${qs.toString()}`);
  },
  getSystemHealth: () => request<Record<string, unknown>>("/api/settings/system/health"),

  // --- DEV-008-SKILLS-OPS: the marketing playbook registry ---------------
  // Skills are KNOWLEDGE (a vendored SKILL.md), not tools. These read and
  // toggle the registry; the enable flag is stored server-side and re-read per
  // request, so a toggle here takes effect on the next turn.

  getSkills: () => request<SpaSkillsResponse>("/api/skills"),
  setSkillEnabled: (skill_id: string, enabled: boolean) =>
    request<{ skill_id: string; enabled: boolean; status?: string }>(
      `/api/skills/${encodeURIComponent(skill_id)}/enabled`,
      { method: "POST", body: JSON.stringify({ enabled }) },
    ),
  getSkillsManifest: () => request<SpaSkillsManifest>("/api/skills/manifest"),
};

/** Extract the turn id from the POST /chat/turn HTML ack. */
export function parseTurnIdFromAck(html: string): string {
  const m = /data-turn-stream="([^"]+)"/.exec(html);
  if (!m) throw new Error("turn ack missing data-turn-stream marker");
  return m[1];
}

/**
 * Send a chat turn via the EXISTING endpoint. Returns the turn id parsed
 * from the HTML ack so callers can attach the SSE stream.
 */
export async function sendChatTurn(
  conversationId: string,
  text: string,
  clientMessageId: string,
  attachmentIds: string[] = [],
): Promise<{ turnId: string; ackHtml: string }> {
  const form = new FormData();
  form.set("conversation_id", conversationId);
  form.set("text", text);
  form.set("client_message_id", clientMessageId);
  attachmentIds.forEach((fileId) => form.append("attachment_ids", fileId));
  const res = await fetch("/chat/turn", { method: "POST", body: form });
  if (!res.ok) throw new Error(`chat turn failed: ${res.status}`);
  const ackHtml = await res.text();
  return { turnId: parseTurnIdFromAck(ackHtml), ackHtml };
}

export interface TurnStreamEvent {
  id: number;
  type: string;
  label: string;
  detail: string;
  meta: Record<string, unknown>;
  raw: string;
}

/**
 * DEV-008-SKILLS-OPS §1.3.1 — the COMPLETE wire event catalog.
 *
 * `EventSource.onmessage` only fires for UNNAMED events, and `app/routes/
 * chat.py:185` always sends a named `event:` line. So every event_type the
 * server is allowed to emit must be registered here by name or the SPA never
 * sees it. That gap was 13 types before this run; adding the 11 new
 * execution-tree types on top makes it the full catalog.
 *
 * The set is a mirror of `app.contracts.events.LEGACY_EVENT_TYPES` and the two
 * are gated for equality by `app/tests/test_dev008so_frontend_wiring.py` (and
 * again, across the ownership boundary, by `test_dev008so_event_registration`).
 * Do not add a name here that the server does not emit, and do not drop one it
 * does: either way the client silently stops receiving that event.
 *
 * `turn_summary` is deliberately absent — the server never puts it on the wire
 * (it is emitted straight into the event table, bypassing the frozen filter).
 *
 * The array literal below contains string literals ONLY, so a plain text scan
 * of this block recovers exactly this set.
 */
export const LEGACY_EVENT_TYPES = [
  "turn_started",
  "provider_selected",
  "context_started",
  "context_completed",
  "rag_started",
  "rag_completed",
  "web_started",
  "web_completed",
  "web_source",
  "instagram_provider_started",
  "instagram_provider_completed",
  "delegation_started",
  "state_read",
  "tool_completed",
  "tool_failed",
  "job_created",
  "approval_required",
  "synthesis_started",
  "assistant_delta",
  "model_delta",
  "assistant_completed",
  "model_completed",
  "turn_completed",
  "turn_failed",
  "turn_closed",
  "stream_end",
  "account_manager_started",
  "task_planned",
  "employee_queued",
  "employee_started",
  "employee_completed",
  "employee_failed",
  "skill_selected",
  "skill_loaded",
  "tool_started",
  "evidence_added",
  "synthesis_completed",
] as const;

/** True when `type` is an event_type the frozen wire contract can emit. */
export function isLegacyEventType(type: string): boolean {
  return (LEGACY_EVENT_TYPES as readonly string[]).includes(type);
}

/**
 * The four types that terminate the client stream. Kept as data so the closing
 * rule is one list, not four literals scattered through the handler.
 */
export const STREAM_CLOSING_EVENT_TYPES = [
  "turn_completed",
  "turn_failed",
  "turn_closed",
  "stream_end",
] as const;

/**
 * Stream events for a turn from the EXISTING SSE endpoint
 * GET /chat/turns/{id}/events. Calls onEvent per event; resolves when the
 * server closes the stream (turn_completed / turn_failed / turn_closed).
 * Returns an unsubscribe function.
 */
export function streamTurnEvents(
  turnId: string,
  opts: { after?: number; onEvent: (e: TurnStreamEvent) => void; onDone?: () => void; onError?: (err: Error) => void },
): () => void {
  const { after = 0, onEvent, onDone, onError } = opts;
  const src = new EventSource(
    `/chat/turns/${encodeURIComponent(turnId)}/events?after=${after}`,
  );
  let closed = false;
  const close = () => {
    if (!closed) {
      closed = true;
      src.close();
    }
  };
  const handle = (type: string) => (ev: MessageEvent) => {
    let payload: { label?: string; detail?: string; meta?: Record<string, unknown>; status?: string } = {};
    try {
      payload = JSON.parse((ev as MessageEvent).data || "{}");
    } catch {
      payload = {};
    }
    const lastId = Number((ev as MessageEvent).lastEventId || 0) || 0;
    // `meta` is only ever an object. A non-object (or a missing) value becomes
    // `{}` rather than being cast into a shape it does not have, so a consumer
    // reading `meta.parent_id` can rely on a plain dict.
    const rawMeta = (payload.meta ?? {}) as unknown;
    const meta: Record<string, unknown> =
      rawMeta && typeof rawMeta === "object" && !Array.isArray(rawMeta)
        ? (rawMeta as Record<string, unknown>)
        : {};
    onEvent({
      id: lastId,
      type,
      label: payload.label ?? "",
      detail: payload.detail ?? "",
      meta,
      raw: String((ev as MessageEvent).data ?? ""),
    });
    if ((STREAM_CLOSING_EVENT_TYPES as readonly string[]).includes(type)) {
      close();
      onDone?.();
    }
  };
  // Named server events. The catalog is the complete `LEGACY_EVENT_TYPES` set,
  // so no event the server can emit is invisible to the SPA.
  for (const t of LEGACY_EVENT_TYPES) {
    src.addEventListener(t, handle(t) as EventListener);
  }
  // Fallback: unnamed messages.
  src.onmessage = (ev) => handle("message")(ev);
  src.onerror = () => {
    // EventSource auto-retries; only surface if already closed unexpectedly.
    if (closed) return;
    // Leave the stream open for server retries; callers may unsubscribe.
    onError?.(new Error("sse connection error"));
  };
  return close;
}
