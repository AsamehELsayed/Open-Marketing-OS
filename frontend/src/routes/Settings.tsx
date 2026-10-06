import { useEffect, useRef, useState } from "react";
import { api, FileCapabilities, ProjectKnowledgeFile, SettingsConfig, SpaAiModel } from "../api/client";
import PageHeader from "../components/chrome/PageHeader";
import SkillList from "../components/skills/SkillList";
import Modal from "../components/chrome/Modal";
import { useProject } from "../components/shell/project-context";
import { projectOpenRouterCredentialStatus } from "../api/openrouterCredentialStatus";
import LocalModelSetup from "../components/settings/LocalModelSetup";

/**
 * DEV-008-SKILLS-OPS added the `skills` tab through the six documented
 * extension points and changed nothing else about this route:
 *   1. the `SectionTab` union      (below)
 *   2. `TABS`                      (below) — which also makes the existing
 *      `?section=` deep-link effect work with no edit, because it matches
 *      `TABS.some((t) => t.id === sec)`
 *   3. the `useState<SectionTab>` tab state (unchanged, already generic)
 *   4. the deep-link effect        (unchanged, already generic)
 *   5. the tab bar                 (unchanged, it maps `TABS`)
 *   6. the section body           (below)
 * The skills registry is independent of the settings-config fetch, so its
 * section renders OUTSIDE the `loading / !config` gate: a settings-config
 * failure must not hide the one tab that reports measured library health.
 */
type SectionTab =
  | "general"
  | "ai"
  | "local"
  | "integrations"
  | "files"
  | "privacy"
  | "system"
  | "advanced"
  | "skills";

const TABS: { id: SectionTab; label: string; icon: string }[] = [
  { id: "general", label: "General", icon: "⚙️" },
  { id: "ai", label: "AI & Models", icon: "🧠" },
  { id: "local", label: "Local Model", icon: "💻" },
  { id: "integrations", label: "Integrations", icon: "🔌" },
  { id: "files", label: "Files & Knowledge", icon: "📁" },
  { id: "privacy", label: "Privacy & Security", icon: "🛡️" },
  { id: "system", label: "System", icon: "🖥️" },
  { id: "advanced", label: "Advanced", icon: "🔧" },
  { id: "skills", label: "Skills", icon: "📚" },
];

function badgeClass(tone: "ok" | "muted" | "err"): string {
  if (tone === "ok") return "border-ok/40 bg-ok/10 text-ok";
  if (tone === "err") return "border-err/40 bg-err/10 text-err";
  return "border-linedefault bg-elevated text-inksecondary";
}

function normalizedState(value: unknown): string {
  return typeof value === "string" ? value.trim().toLowerCase() : "";
}

function stateLabel(value: unknown, fallback = "Unknown"): string {
  switch (normalizedState(value)) {
    case "running":
      return "Running";
    case "healthy":
      return "Healthy";
    case "active":
      return "Active";
    case "available":
      return "Available";
    case "ok":
      return "OK";
    case "connected":
      return "Connected";
    case "configured":
      return "Configured";
    case "missing":
    case "unreadable":
    case "empty":
      return "Needs reconfiguration";
    case "credential_stored":
      return "Credential stored";
    case "verified":
      return "Verified";
    case "fake":
    case "not_real":
      return "Unavailable";
    case "not_configured":
    case "not configured":
      return "Not configured";
    case "not_connected":
    case "not connected":
      return "Not connected";
    case "not_tested":
    case "not tested":
      return "Not tested";
    case "not_verified":
    case "not verified":
      return "Not verified";
    case "unavailable":
    case "unhealthy":
      return "Unavailable";
    case "registered":
      return "Registered";
    case "not_registered":
      return "Not registered";
    case "error":
    case "auth_error":
    case "authentication_error":
    case "auth_failed":
      return "Error";
    case "config_error":
    case "configuration_error":
      return "Configuration error";
    case "unreachable":
    case "provider_unavailable":
    case "rate_limited":
    case "timeout":
    case "not_found":
      return "Unavailable";
    case "unknown":
      return "Unknown";
    case "pending":
      return "Pending";
    case "standby":
      return "Standby";
    case "disabled":
      return "Disabled";
    case "inactive":
      return "Inactive";
    default:
      return fallback;
  }
}

function stateTone(value: unknown): "ok" | "muted" | "err" {
  switch (normalizedState(value)) {
    case "running":
    case "healthy":
    case "active":
    case "available":
    case "ok":
    case "verified":
    case "connected":
    case "configured":
    case "credential_stored":
      return "ok";
    case "unhealthy":
    case "fake":
    case "not_real":
    case "missing":
    case "unreadable":
    case "empty":
      return "err";
    case "error":
    case "auth_error":
    case "authentication_error":
    case "auth_failed":
    case "config_error":
    case "configuration_error":
    case "unavailable":
    case "unreachable":
    case "provider_unavailable":
    case "rate_limited":
    case "timeout":
    case "not_found":
      return "err";
    default:
      return "muted";
  }
}

function isCredentialConnected(value: unknown, fallback?: boolean): boolean {
  const state = normalizedState(value);
  if (state) {
    return ["connected", "configured", "credential_stored", "active"].includes(state);
  }
  return fallback === true;
}

function isHealthPositive(value: unknown): boolean {
  return ["running", "healthy", "active", "available", "ok", "verified"].includes(
    normalizedState(value),
  );
}

function isHealthError(value: unknown): boolean {
  return [
    "error",
    "auth_error",
    "authentication_error",
    "auth_failed",
    "config_error",
    "configuration_error",
    "unavailable",
    "unhealthy",
    "unreachable",
    "provider_unavailable",
    "rate_limited",
    "timeout",
    "not_found",
  ].includes(normalizedState(value));
}

function targetName(target: string): string {
  switch (target.toLowerCase()) {
    case "openrouter":
      return "OpenRouter";
    case "openai":
      return "OpenAI";
    case "apify":
      return "Apify";
    case "brightdata":
      return "Bright Data";
    case "meta":
      return "Meta";
    case "browser":
      return "Browser automation";
    case "mcp":
      return "MCP server";
    case "vision":
      return "Vision";
    default:
      return "Connection";
  }
}

function testResultMessage(result: {
  target: string;
  credentialStatus: string;
  providerHealth: string;
  capabilityHealth: string;
  lastCheckedAt?: string;
}): string {
  const name = targetName(result.target);
  const capability = normalizedState(
    healthEvidenceState(result.capabilityHealth, result.lastCheckedAt),
  );
  const provider = normalizedState(
    healthEvidenceState(result.providerHealth, result.lastCheckedAt),
  );
  if (isHealthError(provider) || isHealthError(capability)) {
    return `${name} connection test failed. Check the stored credential and provider access.`;
  }
  if (capability === "verified") return `${name} capability verified.`;
  if (isHealthPositive(capability)) {
    return `${name} capability reported ${stateLabel(capability, "Unknown").toLowerCase()}.`;
  }
  if (isHealthPositive(provider)) {
    return `${name} provider responded; capability is ${stateLabel(capability, "Unknown").toLowerCase()}.`;
  }
  if (!isCredentialConnected(result.credentialStatus)) return `${name} is not connected.`;
  return `${name} connection test completed; capability status is ${stateLabel(capability, "Unknown").toLowerCase()}.`;
}

function testResultTone(result: {
  credentialStatus: string;
  providerHealth: string;
  capabilityHealth: string;
  lastCheckedAt?: string;
}): "ok" | "muted" | "err" {
  const provider = healthEvidenceState(result.providerHealth, result.lastCheckedAt);
  const capability = healthEvidenceState(result.capabilityHealth, result.lastCheckedAt);
  if (
    isHealthError(provider) ||
    isHealthError(capability) ||
    isHealthError(result.credentialStatus)
  ) {
    return "err";
  }
  if (isHealthPositive(provider) || isHealthPositive(capability)) {
    return "ok";
  }
  return "muted";
}

function credentialDisplayName(value: string): string {
  const names: Record<string, string> = {
    integration_openai: "OpenAI",
    integration_apify: "Instagram data provider",
    integration_brightdata: "Bright Data",
    integration_meta: "Meta",
    integration_openrouter: "OpenRouter",
  };
  return names[value.trim().toLowerCase()] ?? "Credential";
}

function credentialTarget(value: string): string | null {
  const targets: Record<string, string> = {
    integration_openai: "openai",
    integration_apify: "apify",
    integration_brightdata: "brightdata",
    integration_meta: "meta",
    integration_openrouter: "openrouter",
  };
  return targets[value.trim().toLowerCase()] ?? null;
}

function mcpDisplayName(value: string | undefined, index: number): string {
  const name = (value || "").trim();
  return name && /^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$/.test(name)
    ? name
    : `MCP server ${index + 1}`;
}

function checkedLabel(value: unknown): string | null {
  if (
    typeof value !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T/.test(value.trim()) ||
    Number.isNaN(Date.parse(value))
  ) {
    return null;
  }
  return `checked ${value.replace("T", " ").slice(0, 16)}`;
}

function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "Size unavailable";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function retrievalModeLabel(mode: string | undefined): string {
  const normalized = (mode || "").trim().toUpperCase();
  if (normalized === "FTS_ONLY") return "FTS";
  if (normalized === "HYBRID") return "HYBRID";
  return normalized || "UNAVAILABLE";
}

function knowledgeFileState(file: ProjectKnowledgeFile): string {
  if (file.index_status) {
    switch (file.index_status) {
      case "indexed": return "Indexed";
      case "pending": return "Pending";
      case "failed": return "Failed";
      case "quarantined": return "Quarantined";
      case "not_searchable": return "Accepted, no searchable text";
      default: return file.index_status.replace(/_/g, " ");
    }
  }
  return file.indexed ? "Indexed" : "Not indexed";
}

function knowledgeUploadProgress(stage: string, current: number | null, total: number | null): string {
  if (stage === "downloading_model") {
    const received = typeof current === "number" ? `${(current / (1024 * 1024)).toFixed(1)} MB received` : "Receiving model bytes";
    const expected = typeof total === "number" && total > 0 ? ` of ${(total / (1024 * 1024)).toFixed(1)} MB` : "";
    return `Downloading local search model · ${received}${expected}`;
  }
  const labels: Record<string, string> = {
    receiving_file: "Receiving file…", checking_model_cache: "Checking local search model…",
    validating_model_cache: "Checking downloaded model files…", loading_local_model: "Loading local search model…",
    local_model_ready: "Preparing file index…", extracting_file: "Extracting file text…",
    indexing_file: "Indexing file for search…",
  };
  return labels[stage] || "Processing knowledge file…";
}

function healthEvidenceState(value: unknown, lastChecked: unknown): unknown {
  const state = normalizedState(value);
  if (["healthy", "verified", "available", "running", "ok"].includes(state) && !checkedLabel(lastChecked)) {
    return "unknown";
  }
  return value;
}

function safeConfigId(value: unknown): string {
  if (typeof value !== "string") return "";
  const clean = value.trim();
  if (!clean || clean.length > 128 || /[\\/:]/.test(clean) || /^vault:/i.test(clean)) {
    return "";
  }
  return clean;
}

function firstConfigId(...values: unknown[]): string {
  for (const value of values) {
    const safe = safeConfigId(value);
    if (safe) return safe;
  }
  return "";
}

function safeServerId(value: unknown): string {
  if (typeof value !== "string") return "";
  const clean = value.trim();
  if (!clean || clean.length > 128 || /[\\/]/.test(clean) || /^[a-z][a-z0-9+.-]*:\/\//i.test(clean)) {
    return "";
  }
  return clean;
}

function mcpServerId(value: {
  id?: string;
  name?: string;
  server?: string;
  server_id?: string;
}): string {
  for (const candidate of [value.server_id, value.id, value.server, value.name]) {
    const safe = safeServerId(candidate);
    if (safe) return safe;
  }
  return "";
}

function mcpHealthState(value: {
  status?: string;
  last_health?: string;
  health_status?: string;
  connected?: boolean;
}): string {
  const explicit = normalizedState(value.last_health || value.health_status);
  if (explicit) return explicit;
  const status = normalizedState(value.status);
  if (["healthy", "unhealthy", "ok", "unavailable", "error", "unknown"].includes(status)) {
    return status;
  }
  return value.connected === true ? "registered" : "unknown";
}

export default function Settings() {
  const { projectId } = useProject();
  const selectedProjectRef = useRef(projectId);
  selectedProjectRef.current = projectId;
  const configRequestRef = useRef(0);
  const knowledgeListRequestRef = useRef(0);
  const [tab, setTab] = useState<SectionTab>("general");
  const [config, setConfig] = useState<SettingsConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Form states
  const [theme, setTheme] = useState("system");
  const [autoLoad, setAutoLoad] = useState(true);
  const [language, setLanguage] = useState("en");

  const [aiMode, setAiMode] = useState("AUTO");
  const [activeModel, setActiveModel] = useState("Qwen2.5-7B-Instruct");
  const [marketingAdapter, setMarketingAdapter] = useState("OMOS Marketing v1");
  const [managerProvider, setManagerProvider] = useState("auto");
  const [brightdataDataset, setBrightdataDataset] = useState("");
  const [savedBrightdataDataset, setSavedBrightdataDataset] = useState("");
  const [metaAccountId, setMetaAccountId] = useState("");
  const [savedMetaAccountId, setSavedMetaAccountId] = useState("");
  const [mcpServerName, setMcpServerName] = useState("");
  const [mcpEndpoint, setMcpEndpoint] = useState("");
  const [mcpCredential, setMcpCredential] = useState("");
  const [mcpScope, setMcpScope] = useState("project");
  const [mcpRegistering, setMcpRegistering] = useState(false);

  // Credential modal / inputs
  const [connectTarget, setConnectTarget] = useState<string | null>(null);
  const credentialOpenerRef = useRef<HTMLElement | null>(null);
  const [credentialInput, setCredentialInput] = useState("");
  const [credentialSaving, setCredentialSaving] = useState(false);
  const [testingTarget, setTestingTarget] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{
    target: string;
    message: string;
    credentialStatus: string;
    providerHealth: string;
    capabilityHealth: string;
    lastCheckedAt?: string;
  } | null>(null);

  // Project Instagram handle
  const [igHandleInput, setIgHandleInput] = useState("");
  const [savingHandle, setSavingHandle] = useState(false);

  // OpenRouter
  const [openrouterModel, setOpenrouterModel] = useState("");
  const [cloudEscalation, setCloudEscalation] = useState("off");
  const [orModels, setOrModels] = useState<SpaAiModel[]>([]);
  const [orModelsLoading, setOrModelsLoading] = useState(false);
  const [orModelError, setOrModelError] = useState<string | null>(null);
  const [orModelSearch, setOrModelSearch] = useState("");

  // Workspace actions
  const [syncing, setSyncing] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [knowledgeFiles, setKnowledgeFiles] = useState<ProjectKnowledgeFile[]>([]);
  const [knowledgeFilesError, setKnowledgeFilesError] = useState<string | null>(null);
  const [knowledgeNotice, setKnowledgeNotice] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<FileCapabilities>();

  // Load config on mount
  const loadConfig = (requestedProjectId: string | null = selectedProjectRef.current) => {
    const requestId = ++configRequestRef.current;
    setLoading(true);
    api
      .getSettingsConfig(requestedProjectId || undefined)
      .then((data) => {
        if (requestId !== configRequestRef.current || selectedProjectRef.current !== requestedProjectId) return;
        setConfig(data);
        setCapabilities(data.files_knowledge.accepted_types ? {
          accepted_types: data.files_knowledge.accepted_types,
          max_document_bytes: data.files_knowledge.max_document_bytes ?? 25 * 1024 * 1024,
          max_image_bytes: data.files_knowledge.max_image_bytes ?? 15 * 1024 * 1024,
          ocr_supported: data.files_knowledge.ocr_supported ?? false,
        } : undefined);
        setTheme(data.general.theme || "system");
        setAutoLoad(data.general.auto_load_project ?? true);
        setLanguage(data.general.language || "en");

        setAiMode(data.ai.ai_mode || "AUTO");
        setActiveModel(data.ai.active_model || "Qwen2.5-7B-Instruct");
         setMarketingAdapter(data.ai.marketing_adapter || "OMOS Marketing v1");
         setManagerProvider(data.ai.manager_provider || "auto");
         const dataset = firstConfigId(
           data.integrations.instagram_public.brightdata_dataset_id,
           data.integrations.instagram_public.brightdata_dataset,
           data.integrations.instagram_public.brightdata_config?.dataset_id,
           data.integrations.instagram_public.dataset_id,
         );
         const account = firstConfigId(
           data.integrations.meta_insights.account_id,
           data.integrations.meta_insights.ig_account_id,
           data.integrations.meta_insights.config?.account_id,
           data.integrations.meta_insights.config?.ig_account_id,
         );
         setBrightdataDataset(dataset);
         setSavedBrightdataDataset(dataset);
         setMetaAccountId(account);
         setSavedMetaAccountId(account);
         setOpenrouterModel(data.ai.openrouter_default_model || "");
        setCloudEscalation(data.ai.cloud_escalation === "on" ? "on" : "off");

        setIgHandleInput(data.integrations.instagram_project.handle || "");
        setError(null);
        setLoading(false);
      })
      .catch(() => {
        if (requestId !== configRequestRef.current || selectedProjectRef.current !== requestedProjectId) return;
        setError("Could not load settings. Please retry.");
        setLoading(false);
      });
  };

  useEffect(() => {
    setConfig(null);
    setNotice(null);
    setKnowledgeNotice(null);
    loadConfig(projectId);
    // Check url search params for direct tab navigation (e.g. ?section=system)
    const params = new URLSearchParams(window.location.search);
    const sec = params.get("section");
    if (sec && TABS.some((t) => t.id === sec)) {
      setTab(sec as SectionTab);
    }
  }, [projectId]);

  useEffect(() => {
    const requestId = ++knowledgeListRequestRef.current;
    setKnowledgeFiles([]);
    if (!projectId) {
      setKnowledgeFilesError(null);
      return;
    }
    setKnowledgeFilesError(null);
    api.getProjectKnowledgeFiles(projectId)
      .then((files) => {
        if (requestId !== knowledgeListRequestRef.current || selectedProjectRef.current !== projectId) return;
        setKnowledgeFiles(files.filter((file) => file.attach_scope === "project" && file.project_id === projectId));
      })
      .catch((e: unknown) => {
        if (requestId !== knowledgeListRequestRef.current || selectedProjectRef.current !== projectId) return;
        setKnowledgeFilesError(e instanceof Error ? e.message : "Knowledge files could not be loaded.");
        setKnowledgeFiles([]);
      });
    return () => {
      if (requestId === knowledgeListRequestRef.current) knowledgeListRequestRef.current++;
    };
  }, [projectId]);

  useEffect(() => {
    const refreshKnowledge = (event: Event) => {
      const changedProject = (event as CustomEvent<{ projectId: string }>).detail?.projectId;
      if (!projectId || changedProject !== projectId) return;
      loadConfig(projectId);
      const requestId = ++knowledgeListRequestRef.current;
      api.getProjectKnowledgeFiles(projectId).then((files) => {
        if (requestId !== knowledgeListRequestRef.current || selectedProjectRef.current !== projectId) return;
        setKnowledgeFiles(files.filter((file) => file.attach_scope === "project" && file.project_id === projectId));
        setKnowledgeFilesError(null);
      }).catch((error: unknown) => {
        if (requestId !== knowledgeListRequestRef.current || selectedProjectRef.current !== projectId) return;
        setKnowledgeFilesError(error instanceof Error ? error.message : "Knowledge files could not be refreshed.");
      });
    };
    window.addEventListener("omos:knowledge-changed", refreshKnowledge);
    return () => window.removeEventListener("omos:knowledge-changed", refreshKnowledge);
  }, [projectId]);

  const flashNotice = (msg: string) => {
    setNotice(msg);
    setTimeout(() => setNotice(null), 4000);
  };

  const handleSaveGeneral = async () => {
    try {
      await api.updateGeneralSettings({ theme, auto_load_project: autoLoad, language });
      flashNotice("General settings saved.");
      loadConfig();
    } catch {
      setError("Could not save general settings. Please retry.");
    }
  };

  const handleAiModeChange = (mode: string) => {
    setAiMode(mode);
    if (mode === "OPENROUTER") setManagerProvider("openrouter");
    if (mode === "OPENAI") setManagerProvider("openai");
    if (mode === "BASE" || mode === "LOCAL") setManagerProvider("auto");
  };

  const handleManagerProviderChange = (provider: string) => {
    setManagerProvider(provider);
    if (provider === "openrouter") setAiMode("OPENROUTER");
    if (provider === "openai") setAiMode("OPENAI");
    if (provider === "auto" && (aiMode === "OPENROUTER" || aiMode === "OPENAI")) {
      setAiMode("AUTO");
    }
  };

  const handleSaveAi = async () => {
    try {
      await api.updateAiSettings({
        ai_mode: aiMode,
        active_model: activeModel,
        marketing_adapter: marketingAdapter,
        manager_provider: managerProvider,
        openrouter_default_model: openrouterModel,
        cloud_escalation: cloudEscalation,
      });
      flashNotice("AI & Model configuration updated.");
      loadConfig();
    } catch {
      setError("Could not update AI settings. Please retry.");
    }
  };

  const loadOpenrouterModels = async (refresh = false) => {
    setOrModelsLoading(true);
    setOrModelError(null);
    try {
      const r = await api.getAiModels("openrouter", refresh);
      setOrModels(r.models);
      if (!r.connected) setOrModelError("OpenRouter is not connected.");
    } catch {
      setOrModelError("Model catalog could not be loaded. Please retry.");
    } finally {
      setOrModelsLoading(false);
    }
  };

  const handleConnectCredential = async () => {
    if (!connectTarget) return;
    const target = connectTarget;
    const token = credentialInput.trim();
    if (!token && !(target === "mcp" || (target === "brightdata" && brightdataConnected))) {
      return;
    }
    setCredentialSaving(true);
    const projectId = config?.project_id;
    const body: Parameters<typeof api.connectCredential>[0] = {
      target,
      ...(token ? { token } : {}),
    };
    if (target === "brightdata") {
      body.scope = "installation";
      if (brightdataDataset) {
        body.dataset_id = brightdataDataset;
        body.config = { dataset_id: brightdataDataset };
      }
    }
     if (target === "meta") {
       body.scope = "project";
       body.project_id = projectId;
       if (metaAccountId) {
         body.ig_account_id = metaAccountId;
         body.config = { ig_account_id: metaAccountId };
       }
     }

    if (target === "mcp") {
      body.scope = mcpScope;
      body.project_id = projectId;
      body.server_id = safeConfigId(mcpServerName);
      body.endpoint = mcpEndpoint.trim();
      body.config = {
        server_id: safeConfigId(mcpServerName),
        endpoint: mcpEndpoint.trim(),
        scope: mcpScope,
      };
    }
    try {
      await api.connectCredential(body);
      setConnectTarget(null);
      setCredentialInput("");
      flashNotice(`${targetName(target)} configuration saved.`);
      loadConfig();
    } catch {
      setError("Could not save the integration configuration. Please retry.");
    } finally {
      setCredentialSaving(false);
    }
  };

  const handleTestCredential = async (
    target: string,
    options: {
       resultTarget?: string;
       scope?: string;
       serverId?: string;
      datasetId?: string;
      accountId?: string;
      config?: Record<string, string>;
    } = {},
  ) => {
    const resultTarget = options.resultTarget ?? target;
    setTestingTarget(resultTarget);
    setTestResult(null);
     const body: Parameters<typeof api.testCredential>[0] = {
       target,
       ...(options.scope ? { scope: options.scope } : {}),
       ...(config?.project_id ? { project_id: config.project_id } : {}),
       ...(options.serverId ? { server_id: options.serverId } : {}),
       ...(options.datasetId ? { dataset_id: options.datasetId } : {}),
       ...(options.accountId ? { ig_account_id: options.accountId } : {}),
       ...(options.config ? { config: options.config } : {}),

    };
    try {
      const res = await api.testCredential(body);
      const result = {
        target: resultTarget,
        message: "",
        credentialStatus: res.credential_status ?? "unknown",
        providerHealth: res.provider_health ?? "unknown",
        capabilityHealth: res.capability_health ?? "unknown",
         lastCheckedAt: res.last_checked_at ?? res.last_checked ?? res.checked_at,
      };
      setTestResult({ ...result, message: testResultMessage(result) });
    } catch {
      const result = {
        target: resultTarget,
        message: "",
        credentialStatus: "unknown",
        providerHealth: "error",
        capabilityHealth: "unknown",
      };
      setTestResult({ ...result, message: testResultMessage(result) });
    } finally {
      setTestingTarget(null);
    }
  };

  const handleDisconnectCredential = async (target: string, projectId?: string) => {
    if (!window.confirm(`Are you sure you want to disconnect ${targetName(target)}?`)) return;
    try {
      await api.disconnectCredential({
        target,
        ...(projectId ? { project_id: projectId } : {}),
      });
      flashNotice(`${targetName(target)} connection removed.`);
      loadConfig();
    } catch {
      setError("Could not remove the connection. Please retry.");
    }
  };

  const handleRegisterMcp = async () => {
    const serverId = safeConfigId(mcpServerName);
    const endpoint = mcpEndpoint.trim();
    if (!serverId || !endpoint || endpoint.length > 512) {
      setError("Enter a server name and a valid server address.");
      return;
    }
    setMcpRegistering(true);
    try {
      await api.connectCredential({
         target: "mcp",
         scope: mcpScope,
         project_id: config?.project_id,
         ...(mcpCredential.trim() ? { token: mcpCredential.trim() } : {}),
        server_id: serverId,
        endpoint,
        config: { server_id: serverId, endpoint, scope: mcpScope },
      });
       setMcpServerName("");
       setMcpEndpoint("");
       setMcpCredential("");
       flashNotice("MCP server registered.");
      loadConfig();
    } catch {
      setError("Could not register the MCP server. Please retry.");
    } finally {
      setMcpRegistering(false);
    }
  };

  const handleSaveInstagramHandle = async () => {
    setSavingHandle(true);
    try {
      await api.updateInstagramSettings({ handle: igHandleInput });
      flashNotice("Project Instagram account handle updated.");
      setSavingHandle(false);
      loadConfig();
    } catch {
      setError("Could not update the Instagram handle. Please retry.");
      setSavingHandle(false);
    }
  };

  const handleSyncWorkspace = async () => {
    const requestedProjectId = projectId;
    if (!requestedProjectId || syncing || reindexing || uploading) return;
    setSyncing(true);
    setKnowledgeNotice(null);
    try {
      const result = await api.syncWorkspace(requestedProjectId);
      if (selectedProjectRef.current !== requestedProjectId) return;
      const stats = result.stats ? Object.entries(result.stats).map(([name, value]) => `${name.replace(/_/g, " ")}: ${value}`).join(", ") : "";
      setKnowledgeNotice(result.message || (stats ? `Workspace data synced. ${stats}` : "Workspace data sync completed."));
      loadConfig(requestedProjectId);
      await refreshKnowledgeFileList(requestedProjectId);
    } catch (e) {
      if (selectedProjectRef.current === requestedProjectId) {
        setKnowledgeNotice(`Workspace data sync failed: ${e instanceof Error ? e.message : "Please retry."}`);
      }
    } finally {
      setSyncing(false);
    }
  };

  const handleReindex = async () => {
    const requestedProjectId = projectId;
    if (!requestedProjectId || syncing || reindexing || uploading) return;
    setReindexing(true);
    setKnowledgeNotice(null);
    try {
      const result = await api.reindexWorkspace(requestedProjectId);
      if (selectedProjectRef.current !== requestedProjectId) return;
      const report = result.report;
      const details = report
        ? `${report.indexed} indexed, ${report.skipped} unchanged, ${report.deleted} deleted, ${report.quarantined} quarantined, ${report.not_searchable} accepted without searchable text, ${report.failed} failed; ${report.chunks} chunks.`
        : "";
      const backendMessage = result.message || "Knowledge index rebuild returned no message.";
      const exclusions = [
        report?.quarantined ? `${report.quarantined} quarantined` : null,
        report?.not_searchable ? `${report.not_searchable} accepted without searchable text` : null,
      ].filter(Boolean).join(", ");
      const outcome = result.status === "empty"
        ? backendMessage
        : report?.failed
          ? `Knowledge index rebuild completed with ${report.failed} indexing failure${report.failed === 1 ? "" : "s"}. ${backendMessage} ${details}`
          : exclusions
            ? `Knowledge index rebuild complete with exclusions: ${exclusions}. ${backendMessage} ${details}`
            : result.status === "failed"
              ? `Knowledge index rebuild failed. ${backendMessage} ${details}`
              : `${backendMessage}${details ? ` ${details}` : ""}`;
      setKnowledgeNotice(outcome);
      loadConfig(requestedProjectId);
      await refreshKnowledgeFileList(requestedProjectId);
    } catch (e) {
      if (selectedProjectRef.current === requestedProjectId) {
        setKnowledgeNotice(`Knowledge index rebuild failed: ${e instanceof Error ? e.message : "Please retry."}`);
      }
    } finally {
      setReindexing(false);
    }
  };

  const refreshKnowledgeFileList = async (requestedProjectId: string) => {
    const requestId = ++knowledgeListRequestRef.current;
    try {
      const files = await api.getProjectKnowledgeFiles(requestedProjectId);
      if (requestId === knowledgeListRequestRef.current && selectedProjectRef.current === requestedProjectId) {
        setKnowledgeFiles(files.filter((file) => file.attach_scope === "project" && file.project_id === requestedProjectId));
        setKnowledgeFilesError(null);
      }
    } catch (e) {
      if (requestId === knowledgeListRequestRef.current && selectedProjectRef.current === requestedProjectId) {
        setKnowledgeFilesError(e instanceof Error ? e.message : "Knowledge files could not be refreshed.");
      }
    }
  };

  const handleKnowledgeFileSelected = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    const requestedProjectId = projectId;
    if (!file || !requestedProjectId || syncing || reindexing || uploading) return;
    setUploading(true);
    setKnowledgeNotice(null);
    const progressId = crypto.randomUUID();
    let poll = true;
    const pollHandle = window.setInterval(async () => {
      if (!poll) return;
      try {
        const state = await api.getUploadProgress(progressId, requestedProjectId);
        if (selectedProjectRef.current !== requestedProjectId) return;
        if (state.status === "running") {
          setKnowledgeNotice(knowledgeUploadProgress(state.stage, state.current, state.total));
        }
      } catch { /* the initial upload and final response may race a poll */ }
    }, 400);
    try {
      const uploaded = await api.uploadProjectKnowledgeFile(file, requestedProjectId, progressId);
      if (selectedProjectRef.current !== requestedProjectId) return;
      if (uploaded.index_status === "indexed" || uploaded.indexed) {
        setKnowledgeNotice(`${uploaded.original_name} uploaded and indexed.`);
      } else if (uploaded.index_status === "not_searchable") {
        setKnowledgeNotice(`${uploaded.original_name} was accepted, but has no searchable text. Images and scanned PDFs are not OCR processed.`);
      } else if (uploaded.index_status === "quarantined") {
        setKnowledgeNotice(`${uploaded.original_name} was quarantined and is not searchable.`);
      } else if (uploaded.index_status === "failed") {
        setKnowledgeNotice(`${uploaded.original_name} was accepted, but indexing failed${uploaded.index_error ? ` (${uploaded.index_error})` : ""}.`);
      } else {
        setKnowledgeNotice(`${uploaded.original_name} was accepted${uploaded.index_status === "pending" ? " and is awaiting indexing" : ""}.`);
      }
      loadConfig(requestedProjectId);
      await refreshKnowledgeFileList(requestedProjectId);
    } catch (e) {
      if (selectedProjectRef.current === requestedProjectId) {
        setKnowledgeNotice(`Upload failed: ${e instanceof Error ? e.message : "The file could not be accepted."}`);
      }
    } finally {
      poll = false;
      window.clearInterval(pollHandle);
      setUploading(false);
    }
  };

  const apify = config?.integrations.instagram_public;
  const apifyCredentialState =
    apify?.apify_credential_status ??
    (typeof apify?.apify_connected === "boolean"
      ? apify.apify_connected
        ? "connected"
        : "not_configured"
      : apify?.apify_ready
        ? "credential_stored"
        : "not_configured");
  const apifyProviderState = healthEvidenceState(
    apify?.apify_provider_health,
    apify?.apify_last_checked_at ?? apify?.last_checked_at,
  );
  const apifyCapabilityState = healthEvidenceState(
    apify?.apify_capability_health,
    apify?.apify_last_checked_at ?? apify?.last_checked_at,
  );
  const apifyConnected = isCredentialConnected(apifyCredentialState);
  const brightdataCredentialState =
    apify?.brightdata_credential_status ??
    (typeof apify?.brightdata_connected === "boolean"
      ? apify.brightdata_connected
        ? "connected"
        : "not_configured"
      : apify?.brightdata_ready
        ? "credential_stored"
        : "not_configured");
  const brightdataProviderState = healthEvidenceState(
    apify?.brightdata_provider_health,
    apify?.brightdata_last_checked_at,
  );
  const brightdataCapabilityState = healthEvidenceState(
    apify?.brightdata_capability_health,
    apify?.brightdata_last_checked_at,
  );
  const brightdataConnected = isCredentialConnected(brightdataCredentialState);
  const brightdataDatasetConfigured = Boolean(
    apify?.brightdata_dataset_configured || savedBrightdataDataset,
  );
  const instagramCredentialState = apifyConnected
    ? apifyCredentialState
    : brightdataCredentialState;
  const openrouter = config?.integrations.openrouter;
  const openrouterReadiness = projectOpenRouterCredentialStatus(
    config?.ai.openrouter_credential_status ??
      openrouter?.credential_status ??
      openrouter?.credential,
  );
  const openrouterCredentialState = openrouterReadiness.status;
  const openrouterProviderState = healthEvidenceState(
    openrouter?.provider_health ??
      openrouter?.provider ??
      config?.ai.openrouter_provider_health,
    openrouter?.last_checked_at ?? config?.ai.openrouter_last_checked_at,
  );
  const openrouterCapabilityState = healthEvidenceState(
    openrouter?.capability_health ??
      openrouter?.capability ??
      config?.ai.openrouter_capability_health,
    openrouter?.last_checked_at ?? config?.ai.openrouter_last_checked_at,
  );
  const openrouterConnected = openrouterReadiness.connected;
  const openai = config?.integrations.openai;
  const openaiCredentialState =
    openai?.credential_status ?? (openai?.connected ? "connected" : "not_configured");
  const meta = config?.integrations.meta_insights;
  const metaCredentialState =
    meta?.credential_status ?? (meta?.connected ? "connected" : "not_configured");
  const metaConnected = isCredentialConnected(metaCredentialState, meta?.connected);
  const metaAccountConfigured = Boolean(meta?.account_configured || savedMetaAccountId);
  const metaProviderState = healthEvidenceState(meta?.provider_health, meta?.last_checked_at);
  const metaCapabilityState = healthEvidenceState(meta?.capability_health, meta?.last_checked_at);
  const mcpServers = config?.integrations.mcp?.servers ?? [];
  const mcpCount = config?.integrations.mcp?.count ?? mcpServers.length;
  const vision = config?.integrations.vision ?? config?.vision;
  const visionStatus = healthEvidenceState(
    vision?.status ?? vision?.provider_status ?? vision?.capability_health ?? "unavailable",
    vision?.last_checked_at,
  );
  const vaultConfigured = Boolean(config?.privacy_security.vault_backend);

  return (
    <div className="flex flex-col min-h-screen">
      <PageHeader
        title="Settings"
        description="Centralized configuration for Open Marketing OS. All settings are managed through this application surface."
      />

      <div className="px-6 py-4 flex-1">
        {notice && (
          <div className="mb-4 rounded border border-ok/40 bg-ok/10 px-4 py-2 text-bodysm text-ok flex items-center justify-between">
            <span>{notice}</span>
            <button type="button" onClick={() => setNotice(null)} className="text-meta text-ok hover:underline">
              Dismiss
            </button>
          </div>
        )}

        {error && (
          <div className="mb-4 rounded border border-err/40 bg-err/10 px-4 py-2 text-bodysm text-err flex items-center justify-between">
            <span>{error}</span>
            <button type="button" onClick={() => setError(null)} className="text-meta text-err hover:underline">
              Dismiss
            </button>
          </div>
        )}

        {/* Tab Bar */}
        <div className="border-b border-linedefault mb-6 flex flex-wrap gap-2">
          {TABS.map((t) => {
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => {
                  setTab(t.id);
                  setTestResult(null);
                }}
                className={`flex items-center gap-2 px-4 py-2.5 text-bodysm font-medium border-b-2 transition-colors ${
                  active
                    ? "border-accent text-accenthover bg-surface"
                    : "border-transparent text-inksecondary hover:text-ink hover:border-linedefault"
                }`}
              >
                <span>{t.icon}</span>
                <span>{t.label}</span>
              </button>
            );
          })}
        </div>

        {loading ? (
          <div className="py-12 text-center text-inksecondary text-bodysm">
            Loading settings configuration…
          </div>
        ) : !config ? (
          <div className="py-12 text-center text-err text-bodysm">
            Could not load settings. Please retry.
          </div>
        ) : (
          <div className="max-w-4xl space-y-6">
            {tab === "local" && <LocalModelSetup />}

            {/* TAB 1: GENERAL */}
            {tab === "general" && (
              <div className="space-y-4">
                <section className="rounded border border-linedefault bg-surface p-5">
                  <h3 className="text-h3 font-semibold text-ink">Appearance & Display</h3>
                  <p className="text-bodysm text-inksecondary mt-1">
                    Customize your workspace theme and visual density.
                  </p>
                  <div className="mt-4 grid max-w-md gap-3">
                    <label className="text-bodysm text-ink">
                      Theme
                      <select
                        value={theme}
                        onChange={(e) => setTheme(e.target.value)}
                        className="mt-1 w-full rounded border border-linedefault bg-elevated px-3 py-2 text-bodysm text-ink focus:outline-none focus:border-accent"
                      >
                        <option value="system">System Default</option>
                        <option value="dark">Dark</option>
                        <option value="light">Light</option>
                      </select>
                    </label>

                    <label className="text-bodysm text-ink">
                      Language
                      <select
                        value={language}
                        onChange={(e) => setLanguage(e.target.value)}
                        className="mt-1 w-full rounded border border-linedefault bg-elevated px-3 py-2 text-bodysm text-ink focus:outline-none focus:border-accent"
                      >
                        <option value="en">English (US)</option>
                      </select>
                    </label>
                  </div>
                </section>

                <section className="rounded border border-linedefault bg-surface p-5">
                  <h3 className="text-h3 font-semibold text-ink">Startup & Workspace Location</h3>
                  <p className="text-bodysm text-inksecondary mt-1">
                    Control how projects load and where your private files are stored.
                  </p>
                  <div className="mt-4 space-y-3">
                    <label className="flex items-center gap-2.5 text-bodysm text-ink cursor-pointer">
                      <input
                        type="checkbox"
                        checked={autoLoad}
                        onChange={(e) => setAutoLoad(e.target.checked)}
                        className="rounded border-linedefault"
                      />
                      <span>Auto-load active project on application launch</span>
                    </label>

                    <div className="mt-2 text-bodysm text-inksecondary">
                      Workspace storage is managed by the application.
                    </div>
                  </div>
                </section>

                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={handleSaveGeneral}
                    className="rounded bg-accent px-4 py-2 text-bodysm font-medium text-white hover:bg-accenthover"
                  >
                    Save General Settings
                  </button>
                </div>
              </div>
            )}

            {/* TAB 2: AI & MODELS */}
            {tab === "ai" && (
              <div className="space-y-4">
                <section className="rounded border border-linedefault bg-surface p-5">
                  <div className="flex items-center justify-between">
                    <h3 className="text-h3 font-semibold text-ink">Local Intelligence</h3>
                  </div>
                  <p className="text-bodysm text-inksecondary mt-1">
                    Set up and manage the pinned local model from the Local Model tab. Downloading requires an explicit action there.
                  </p>
                  <p className="text-meta text-inkmuted mt-2">These values show the configured base model and adapter.</p>

                  <div className="mt-4 grid gap-4 sm:grid-cols-2">
                    <div className="rounded border border-linedefault bg-elevated p-3">
                      <span className="text-meta text-inkmuted block">Active Base Model</span>
                      <span className="text-bodysm font-semibold text-ink mt-0.5 block">
                        {config.ai.active_model}
                      </span>
                      <span className="text-meta text-inksecondary mt-1 block">Selected model</span>
                    </div>

                    <div className="rounded border border-linedefault bg-elevated p-3">
                      <span className="text-meta text-inkmuted block">Marketing Intelligence Adapter</span>
                      <span className="text-bodysm font-semibold text-ink mt-0.5 block">
                        {config.ai.marketing_adapter}
                      </span>
                      <span className="text-meta text-inksecondary mt-1 block">Configured adapter</span>
                    </div>
                  </div>
                </section>

                <section className="rounded border border-linedefault bg-surface p-5">
                  <h3 className="text-h3 font-semibold text-ink">AI Routing Mode</h3>
                   <p className="text-bodysm text-inksecondary mt-1">
                     Choose how OMOS routes model requests. Local routing is available when the verified model runtime is ready.
                   </p>

                   <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    {[
                      {
                        mode: "AUTO",
                        title: "Auto (Recommended)",
                        desc: "Uses the ready local model when available, then follows the configured provider fallback.",
                      },
                      {
                        mode: "BASE",
                        title: "Provider Default",
                        desc: "Uses the provider's own default model for every request.",
                      },
                       {
                         mode: "CLOUD",
                         title: "Cloud First",
                         desc: "Uses the selected cloud provider for model inference.",
                       },
                       {
                         mode: "OPENROUTER",
                         title: "OpenRouter",
                         desc: "Routes explicitly through the saved OpenRouter model and credential.",
                       },
                     ].map((opt) => (
                       <label
                         key={opt.mode}
                         className={`block rounded border p-3.5 cursor-pointer transition-colors ${
                          aiMode === opt.mode
                            ? "border-accent bg-accent/5 ring-1 ring-accent"
                            : "border-linedefault bg-elevated hover:border-linehover"
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-bodysm font-semibold text-ink">{opt.title}</span>
                          <input
                             type="radio"
                             name="aiMode"
                             aria-label={opt.title}
                            checked={aiMode === opt.mode}
                             onChange={() => handleAiModeChange(opt.mode)}
                            className="text-accent"
                          />
                        </div>
                         <p className="mt-2 text-meta text-inksecondary">{opt.desc}</p>
                       </label>
                    ))}
                   </div>

                   <label className="mt-5 block max-w-md text-bodysm text-ink">
                     Manager provider
                     <select
                       value={managerProvider}
                       onChange={(e) => handleManagerProviderChange(e.target.value)}
                       className="mt-1 w-full rounded border border-linedefault bg-elevated px-3 py-2 text-bodysm text-ink focus:outline-none focus:border-accent"
                     >
                        <option value="auto">Automatic provider choice</option>
                        <option value="openai">OpenAI</option>
                        <option value="openrouter">OpenRouter</option>

                     </select>
                   </label>

                   <div className="mt-5">
                     <button
                       type="button"
                       onClick={handleSaveAi}
                       className="rounded bg-accent px-4 py-2 text-bodysm font-medium text-white hover:bg-accenthover"
                     >
                       Update AI Mode
                     </button>
                   </div>
                </section>

                {/* OpenRouter */}
                <section className="rounded border border-linedefault bg-surface p-5">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-h3 font-semibold text-ink">OpenRouter</h3>
                      <p className="text-bodysm text-inksecondary mt-0.5">
                        Optional cloud AIs through one provider. Requests routed through OpenRouter leave the local machine.
                      </p>
                    </div>
                    <span
                      className={`rounded-sm border px-2 py-0.5 text-meta font-medium ${badgeClass(
                        stateTone(openrouterCredentialState),
                      )}`}
                    >
                      {stateLabel(openrouterCredentialState, "Not connected")}
                    </span>
                  </div>

                  <div className="mt-4 flex flex-wrap gap-2.5">
                    {!openrouterConnected ? (
                      <button
                        type="button"
                        onClick={(event) => {
                          credentialOpenerRef.current = event.currentTarget;
                          setConnectTarget("openrouter");
                          setCredentialInput("");
                        }}
                        className="rounded bg-accent px-3 py-1.5 text-bodysm font-medium text-white hover:bg-accenthover"
                      >
                        {openrouterReadiness.needsReconfiguration
                          ? "Re-enter OpenRouter credential"
                          : "Connect OpenRouter"}
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => handleTestCredential("openrouter")}
                          disabled={testingTarget === "openrouter"}
                          className="rounded border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink hover:bg-surface"
                        >
                          {testingTarget === "openrouter" ? "Testing…" : "Test"}
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDisconnectCredential("openrouter")}
                          className="rounded border border-err/30 px-3 py-1.5 text-bodysm text-err hover:bg-err/10"
                        >
                          Disconnect
                        </button>
                      </>
                    )}
                  </div>

                  {testResult && testResult.target === "openrouter" && (
                    <div
                      className={`mt-3 rounded border p-2 text-meta ${
                        testResultTone(testResult) === "err"
                          ? "border-err/30 bg-err/10 text-err"
                          : testResultTone(testResult) === "ok"
                            ? "border-ok/30 bg-ok/10 text-ok"
                            : "border-linedefault bg-elevated text-inksecondary"
                      }`}
                    >
                      {testResult.message}
                    </div>
                  )}

                  <div className="mt-4 flex flex-wrap items-center gap-1.5">
                    <span
                      className={`rounded-sm border px-1.5 py-0.2 text-meta ${badgeClass(stateTone(openrouterCredentialState))}`}
                      title="Credential status"
                    >
                      Credential: {stateLabel(openrouterCredentialState, "Not configured")}
                    </span>
                    <span
                      className={`rounded-sm border px-1.5 py-0.2 text-meta ${badgeClass(stateTone(openrouterProviderState))}`}
                      title="Provider status"
                    >
                      Provider: {stateLabel(openrouterProviderState, "Unknown")}
                    </span>
                    <span
                      className={`rounded-sm border px-1.5 py-0.2 text-meta ${badgeClass(stateTone(openrouterCapabilityState))}`}
                      title="Capability status"
                    >
                      Capability: {stateLabel(openrouterCapabilityState, "Unknown")}
                    </span>
                  </div>

                  {openrouterConnected && (
                    <div className="mt-4 space-y-3">
                      <div>
                        <div className="flex items-center justify-between">
                          <span className="text-bodysm font-medium text-ink">Default cloud model</span>
                          <button
                            type="button"
                            onClick={() => loadOpenrouterModels(true)}
                            disabled={orModelsLoading}
                            className="rounded border border-linedefault px-2 py-0.5 text-meta text-ink hover:bg-surface"
                          >
                            {orModelsLoading ? "Loading…" : "Refresh catalog"}
                          </button>
                        </div>
                        <input
                          type="text"
                          value={orModelSearch}
                          onChange={(e) => setOrModelSearch(e.target.value)}
                          placeholder="Search models (e.g. llama, haiku, free)…"
                          className="mt-1.5 w-full rounded border border-linedefault bg-elevated px-2.5 py-1.5 text-bodysm text-ink focus:outline-none focus:border-accent"
                        />
                        <select
                          value={openrouterModel}
                          onChange={(e) => setOpenrouterModel(e.target.value)}
                          className="mt-1.5 w-full rounded border border-linedefault bg-elevated px-2.5 py-1.5 text-bodysm text-ink focus:outline-none focus:border-accent"
                        >
                          <option value="">No default model selected</option>
                          {openrouterModel && !orModels.some((model) => model.id === openrouterModel) && (
                            <option value={openrouterModel}>
                              {openrouterModel} · availability unverified
                            </option>
                          )}
                          {orModels
                            .filter((m) => {
                              const q = orModelSearch.trim().toLowerCase();
                              if (!q) return true;
                              const intent = q.startsWith("intent ");
                              return (
                                m.id.toLowerCase().includes(q) ||
                                (m.name || "").toLowerCase().includes(intent ? q.slice(7) : q)
                              );
                            })
                            .slice(0, 60)
                            .map((m) => (
                              <option key={m.id} value={m.id}>
                                {m.name || m.id}
                                {typeof m.context_length === "number" ? ` · ${Math.round(m.context_length / 1000)}k ctx` : ""}
                                {m.supports_tools ? ` · tools` : ""}
                              </option>
                            ))}
                        </select>
                        {orModelError && (
                          <p className="mt-1 text-meta text-err">{orModelError}</p>
                        )}
                        {openrouterModel && (
                          <p className="mt-1 text-meta text-inksecondary">
                            Selected: <span className="font-mono">{openrouterModel}</span> · Cloud · Paid/Free per OpenRouter pricing
                          </p>
                        )}
                      </div>

                      <div className="flex items-center justify-between rounded border border-linedefault bg-elevated p-3">
                        <div>
                          <span className="text-bodysm font-medium text-ink">Cloud escalation</span>
                          <p className="text-meta text-inksecondary">
                            When On, complex local turns may escalate to the cloud provider. Off keeps AI strictly local.
                          </p>
                        </div>
                        <label className="flex cursor-pointer items-center gap-2">
                          <input
                            type="checkbox"
                            checked={cloudEscalation === "on"}
                            onChange={(e) => setCloudEscalation(e.target.checked ? "on" : "off")}
                            className="text-accent"
                          />
                          <span className="text-meta text-ink">{cloudEscalation === "on" ? "On" : "Off"}</span>
                        </label>
                      </div>

                      <p className="rounded border border-linedefault bg-elevated p-2 text-meta text-inksecondary">
                        Privacy: OpenRouter requests are processed by a third-party cloud provider and are NOT private.
                        Local calls keep data on this machine.
                      </p>

                      <button
                        type="button"
                        onClick={handleSaveAi}
                        className="rounded bg-accent px-4 py-2 text-bodysm font-medium text-white hover:bg-accenthover"
                      >
                        Save OpenRouter settings
                      </button>
                    </div>
                  )}
                </section>

                <section className="rounded border border-linedefault bg-surface p-5">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-h3 font-semibold text-ink">Cloud AI Connection</h3>
                      <p className="text-bodysm text-inksecondary mt-0.5">
                        Optional integration for OpenAI models. Credentials are managed by the application.
                      </p>
                    </div>
                    <span
                      className={`rounded-sm border px-2 py-0.5 text-meta font-medium ${badgeClass(
                        stateTone(openaiCredentialState),
                      )}`}
                    >
                      {stateLabel(openaiCredentialState, "Not connected")}
                    </span>
                  </div>

                  <div className="mt-4 flex flex-wrap gap-2.5">
                    {!isCredentialConnected(openaiCredentialState) ? (
                      <button
                        type="button"
                        onClick={(event) => {
                          credentialOpenerRef.current = event.currentTarget;
                          setConnectTarget("openai");
                          setCredentialInput("");
                        }}
                        className="rounded bg-accent px-3 py-1.5 text-bodysm font-medium text-white hover:bg-accenthover"
                      >
                        Connect OpenAI
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => handleTestCredential("openai")}
                          disabled={testingTarget === "openai"}
                          className="rounded border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink hover:bg-surface"
                        >
                          {testingTarget === "openai" ? "Testing…" : "Test Connection"}
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDisconnectCredential("openai")}
                          className="rounded border border-err/30 px-3 py-1.5 text-bodysm text-err hover:bg-err/10"
                        >
                          Disconnect
                        </button>
                      </>
                    )}
                  </div>

                  {testResult && testResult.target === "openai" && (
                    <div
                      className={`mt-3 rounded border p-2 text-meta ${
                        testResultTone(testResult) === "err"
                          ? "border-err/30 bg-err/10 text-err"
                          : testResultTone(testResult) === "ok"
                            ? "border-ok/30 bg-ok/10 text-ok"
                            : "border-linedefault bg-elevated text-inksecondary"
                      }`}
                    >
                      {testResult.message}
                    </div>
                  )}
                </section>
              </div>
            )}

            {/* TAB 3: INTEGRATIONS */}
            {tab === "integrations" && (
              <div className="space-y-4">
                {/* Instagram Public Research */}
                <section className="rounded border border-linedefault bg-surface p-5">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-h3 font-semibold text-ink">Instagram Public Research</h3>
                      <p className="text-bodysm text-inksecondary mt-0.5">
                        Audit competitor profiles, reels performance, and hashtag trends without logging in.
                      </p>
                    </div>
                    <span
                      className={`rounded-sm border px-2 py-0.5 text-meta font-medium ${badgeClass(
                        stateTone(instagramCredentialState),
                      )}`}
                    >
                      {stateLabel(instagramCredentialState, "Not connected")}
                    </span>
                  </div>

                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <div className="rounded border border-linedefault bg-elevated p-3">
                      <div className="flex items-center justify-between">
                        <span className="text-bodysm font-medium text-ink">Apify Scraping Provider</span>
                        <div className="flex items-center gap-1.5">
                          {(() => {
                            const cred = testResult?.target === "apify"
                              ? testResult.credentialStatus
                              : apifyCredentialState;
                            const provider = testResult?.target === "apify"
                              ? testResult.providerHealth
                              : apifyProviderState;
                            const cap = testResult?.target === "apify"
                              ? testResult.capabilityHealth
                              : apifyCapabilityState;
                            const lastChecked = testResult?.target === "apify"
                              ? testResult.lastCheckedAt
                              : apify?.apify_last_checked_at ?? apify?.last_checked_at;
                            const checked = checkedLabel(lastChecked);
                            return (
                              <>
                                <span
                                  className={`rounded-sm border px-1.5 py-0.2 text-meta ${badgeClass(stateTone(cred))}`}
                                  title="Credential status"
                                >
                                  Cred: {stateLabel(cred, "Not configured")}
                                </span>
                                <span
                                  className={`rounded-sm border px-1.5 py-0.2 text-meta ${badgeClass(stateTone(provider))}`}
                                  title="Provider status"
                                >
                                  Provider: {stateLabel(provider, "Unknown")}
                                </span>
                                <span
                                  className={`rounded-sm border px-1.5 py-0.2 text-meta ${badgeClass(stateTone(cap))}`}
                                  title="Capability status"
                                >
                                  Scraper: {stateLabel(cap, "Unknown")}
                                </span>
                                {checked ? (
                                  <span className="text-meta text-inkmuted" title="Last check">
                                    {checked}
                                  </span>
                                ) : null}
                              </>
                            );
                          })()}
                        </div>
                      </div>
                      <p className="mt-1 text-meta text-inksecondary">
                        Fast public profile & post extraction via Apify actor (metered).
                      </p>
                      {testResult?.target === "apify" && (
                        <div className="mt-2 text-micro rounded bg-surface p-1.5 border border-linedefault text-inksecondary">
                          <span className="font-medium text-ink">Last tested: </span>
                          {testResult.message}
                        </div>
                      )}
                      <div className="mt-3 flex flex-wrap gap-2">
                        {!apifyConnected ? (
                          <button
                            type="button"
                            onClick={(event) => {
                              credentialOpenerRef.current = event.currentTarget;
                              setConnectTarget("apify");
                              setCredentialInput("");
                            }}
                            className="rounded bg-accent px-2.5 py-1 text-meta font-medium text-white hover:bg-accenthover"
                          >
                            Configure Token
                          </button>
                        ) : (
                          <>
                            <button
                              type="button"
                              onClick={() => handleTestCredential("apify")}
                              disabled={testingTarget === "apify"}
                              className="rounded border border-linedefault px-2.5 py-1 text-meta text-ink hover:bg-surface disabled:opacity-50"
                            >
                              {testingTarget === "apify"
                                ? "Testing..."
                                : testResult?.target === "apify" && testResultTone(testResult) === "err"
                                  ? "Test again"
                                  : "Test"}
                            </button>
                            <button
                              type="button"
                              onClick={(event) => {
                                credentialOpenerRef.current = event.currentTarget;
                                setConnectTarget("apify");
                                setCredentialInput("");
                              }}
                              className="rounded border border-linedefault px-2.5 py-1 text-meta text-ink hover:bg-surface"
                            >
                              {testResult?.target === "apify" && testResultTone(testResult) === "err"
                                ? "Repair"
                                : "Configure"}
                            </button>
                            <button
                              type="button"
                              onClick={() => handleDisconnectCredential("apify")}
                              className="rounded border border-err/30 px-2.5 py-1 text-meta text-err hover:bg-err/10"
                            >
                              Disconnect
                            </button>
                          </>
                        )}
                      </div>
                    </div>

                     <div className="rounded border border-linedefault bg-elevated p-3">
                       <div className="flex items-center justify-between">
                         <span className="text-bodysm font-medium text-ink">Bright Data Alternative</span>
                         <span
                           className={`rounded-sm border px-1.5 py-0.2 text-meta ${badgeClass(stateTone(brightdataCredentialState))}`}
                           title="Credential status"
                         >
                           {stateLabel(brightdataCredentialState, "Not configured")}
                         </span>
                       </div>
                       <p className="mt-1 text-meta text-inksecondary">
                         High-volume proxy and dataset extraction provider.
                       </p>
                       <div className="mt-2 flex flex-wrap gap-1.5">
                         <span
                           className={`rounded-sm border px-1.5 py-0.2 text-meta ${badgeClass(stateTone(brightdataCredentialState))}`}
                           title="Credential status"
                         >
                           Cred: {stateLabel(brightdataCredentialState, "Not configured")}
                         </span>
                         <span
                           className={`rounded-sm border px-1.5 py-0.2 text-meta ${badgeClass(stateTone(brightdataDatasetConfigured ? "configured" : "not_configured"))}`}
                           title="Dataset status"
                         >
                           Dataset: {brightdataDatasetConfigured ? "Configured" : "Not configured"}
                         </span>
                         <span
                           className={`rounded-sm border px-1.5 py-0.2 text-meta ${badgeClass(stateTone(brightdataProviderState))}`}
                           title="Provider status"
                         >
                           Provider: {stateLabel(brightdataProviderState, "Unknown")}
                         </span>
                         <span
                           className={`rounded-sm border px-1.5 py-0.2 text-meta ${badgeClass(stateTone(brightdataCapabilityState))}`}
                           title="Capability status"
                         >
                           Capability: {stateLabel(brightdataCapabilityState, "Unknown")}
                         </span>
                       </div>
                       <label className="mt-3 block text-bodysm text-ink">
                         Dataset ID
                         <input
                           type="text"
                           value={brightdataDataset}
                           onChange={(e) => setBrightdataDataset(e.target.value.slice(0, 128))}
                           placeholder="Enter the provider dataset ID"
                           className="mt-1 w-full rounded border border-linedefault bg-surface px-2.5 py-1.5 text-bodysm text-ink focus:outline-none focus:border-accent"
                         />
                       </label>
                       <div className="mt-3 flex flex-wrap gap-2">
                         <button
                           type="button"
                           onClick={(event) => {
                             credentialOpenerRef.current = event.currentTarget;
                             setConnectTarget("brightdata");
                             setCredentialInput("");
                           }}
                           className="rounded border border-linedefault px-2.5 py-1 text-meta text-ink hover:bg-surface"
                         >
                           {brightdataConnected ? "Configure key or dataset" : "Configure key"}
                         </button>
                         <button
                           type="button"
                           onClick={() => handleTestCredential("brightdata", {
                             scope: "installation",
                             datasetId: brightdataDataset || undefined,
                             config: brightdataDataset ? { dataset_id: brightdataDataset } : undefined,
                           })}
                           disabled={testingTarget === "brightdata" || !brightdataDataset}
                           className="rounded border border-linedefault px-2.5 py-1 text-meta text-ink hover:bg-surface disabled:opacity-50"
                         >
                           {testingTarget === "brightdata" ? "Testing…" : "Test dataset"}
                         </button>
                         {brightdataConnected && (
                           <button
                             type="button"
                             onClick={() => handleDisconnectCredential("brightdata")}
                             className="rounded border border-err/30 px-2.5 py-1 text-meta text-err hover:bg-err/10"
                           >
                             Disconnect
                           </button>
                         )}
                       </div>
                     </div>
                  </div>

                  {testResult && (testResult.target === "apify" || testResult.target === "brightdata") && (
                    <div
                      className={`mt-3 rounded border p-2 text-meta ${
                        testResultTone(testResult) === "err"
                          ? "border-err/30 bg-err/10 text-err"
                          : testResultTone(testResult) === "ok"
                            ? "border-ok/30 bg-ok/10 text-ok"
                            : "border-linedefault bg-elevated text-inksecondary"
                      }`}
                    >
                      {testResult.message}
                    </div>
                  )}
                </section>

                {/* Project Instagram Handle */}
                <section className="rounded border border-linedefault bg-surface p-5">
                  <h3 className="text-h3 font-semibold text-ink">Project Instagram Account</h3>
                  <p className="text-bodysm text-inksecondary mt-0.5">
                    Link your brand's public handle to enable automated profile audits and content monitoring.
                  </p>

                  <div className="mt-3 flex max-w-md items-center gap-2">
                    <input
                      type="text"
                      value={igHandleInput}
                      onChange={(e) => setIgHandleInput(e.target.value)}
                      placeholder="@yourbrand"
                      className="flex-1 rounded border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink focus:outline-none focus:border-accent"
                    />
                    <button
                      type="button"
                      onClick={handleSaveInstagramHandle}
                      disabled={savingHandle}
                      className="rounded bg-accent px-3 py-1.5 text-bodysm font-medium text-white hover:bg-accenthover"
                    >
                      {savingHandle ? "Saving…" : "Save Handle"}
                    </button>
                  </div>
                </section>

                {/* Meta Instagram Insights */}
                 <section className="rounded border border-linedefault bg-surface p-5">
                   <div className="flex items-center justify-between">
                     <div>
                       <h3 className="text-h3 font-semibold text-ink">Meta Instagram Insights</h3>
                       <p className="text-bodysm text-inksecondary mt-0.5">
                         Connect an owned account to test its insights capability.
                       </p>
                     </div>
                     <span className={`rounded-sm border px-2 py-0.5 text-meta font-medium ${badgeClass(stateTone(metaCredentialState))}`}>
                       {stateLabel(metaCredentialState, "Not connected")}
                     </span>
                   </div>
                   <label className="mt-4 block max-w-md text-bodysm text-ink">
                     Account ID
                     <input
                       type="text"
                       value={metaAccountId}
                       onChange={(e) => setMetaAccountId(e.target.value.slice(0, 128))}
                       placeholder="Enter the connected account ID"
                       className="mt-1 w-full rounded border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink focus:outline-none focus:border-accent"
                     />
                   </label>
                   <div className="mt-3 flex flex-wrap gap-2">
                     <button
                       type="button"
                       onClick={(event) => {
                         credentialOpenerRef.current = event.currentTarget;
                         setConnectTarget("meta");
                         setCredentialInput("");
                       }}
                       className="rounded bg-accent px-3 py-1.5 text-bodysm font-medium text-white hover:bg-accenthover"
                     >
                       {metaConnected ? "Reconnect Meta" : "Connect Meta"}
                     </button>
                     <button
                       type="button"
                       onClick={() => handleTestCredential("meta", {
                         scope: "project",
                         accountId: metaAccountId || undefined,
                         config: metaAccountId ? { ig_account_id: metaAccountId } : undefined,
                       })}
                       disabled={testingTarget === "meta"}
                       className="rounded border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink hover:bg-surface disabled:opacity-50"
                     >
                       {testingTarget === "meta" ? "Testing…" : "Test Meta"}
                     </button>
                     {metaConnected && (
                       <button
                         type="button"
                         onClick={() => handleDisconnectCredential("meta", config?.project_id)}
                         className="rounded border border-err/30 px-3 py-1.5 text-bodysm text-err hover:bg-err/10"
                       >
                         Disconnect
                       </button>
                     )}
                   </div>
                   <div className="mt-3 flex flex-wrap gap-1.5 text-meta">
                     <span className={`rounded-sm border px-1.5 py-0.2 ${badgeClass(stateTone(metaAccountConfigured ? "configured" : "not_configured"))}`}>
                       Account: {metaAccountConfigured ? "Configured" : "Not configured"}
                     </span>
                     <span className={`rounded-sm border px-1.5 py-0.2 ${badgeClass(stateTone(metaProviderState))}`}>
                       Provider: {stateLabel(metaProviderState, "Unknown")}
                     </span>
                     <span className={`rounded-sm border px-1.5 py-0.2 ${badgeClass(stateTone(metaCapabilityState))}`}>
                       Capability: {stateLabel(metaCapabilityState, "Unknown")}
                     </span>
                   </div>
                   {testResult?.target === "meta" && (
                     <div
                       className={`mt-3 rounded border p-2 text-meta ${
                         testResultTone(testResult) === "err"
                           ? "border-err/30 bg-err/10 text-err"
                           : testResultTone(testResult) === "ok"
                             ? "border-ok/30 bg-ok/10 text-ok"
                             : "border-linedefault bg-elevated text-inksecondary"
                       }`}
                     >
                       {testResult.message}
                     </div>
                   )}
                 </section>

                {/* MCP (Model Context Protocol) */}
                 <section className="rounded border border-linedefault bg-surface p-5">
                   <div className="flex items-center justify-between">
                     <div>
                       <h3 className="text-h3 font-semibold text-ink">Model Context Protocol (MCP)</h3>
                       <p className="text-bodysm text-inksecondary mt-0.5">
                         Register a server, then check its health. Tool access remains default-deny.
                       </p>
                     </div>
                     <span className="text-meta text-inksecondary">
                       {mcpCount === 0 ? "No servers configured" : `${mcpCount} registered`}
                     </span>
                   </div>

                   <div className="mt-4 grid gap-3 sm:grid-cols-2">
                     <label className="text-bodysm text-ink">
                       Server name
                       <input
                         type="text"
                         value={mcpServerName}
                         onChange={(e) => setMcpServerName(e.target.value.slice(0, 64))}
                         placeholder="Research tools"
                         className="mt-1 w-full rounded border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink focus:outline-none focus:border-accent"
                       />
                     </label>
                     <label className="text-bodysm text-ink">
                       Server address
                       <input
                         type="text"
                         value={mcpEndpoint}
                         onChange={(e) => setMcpEndpoint(e.target.value.slice(0, 512))}
                         placeholder="Server address"
                         className="mt-1 w-full rounded border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink focus:outline-none focus:border-accent"
                       />
                     </label>
                     <label className="text-bodysm text-ink">
                       Server credential
                       <input
                         type="password"
                         value={mcpCredential}
                         onChange={(e) => setMcpCredential(e.target.value)}
                         placeholder="Optional"
                         autoComplete="new-password"
                         className="mt-1 w-full rounded border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink focus:outline-none focus:border-accent"
                       />
                     </label>
                     <label className="text-bodysm text-ink">
                       Scope
                       <select
                         value={mcpScope}
                         onChange={(e) => setMcpScope(e.target.value)}
                         className="mt-1 w-full rounded border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink focus:outline-none focus:border-accent"
                       >
                         <option value="project">Project</option>
                         <option value="installation">Installation</option>
                       </select>
                     </label>
                     <div className="flex items-end">
                       <button
                         type="button"
                         onClick={handleRegisterMcp}
                         disabled={mcpRegistering}
                         className="rounded bg-accent px-3 py-1.5 text-bodysm font-medium text-white hover:bg-accenthover disabled:opacity-50"
                       >
                         {mcpRegistering ? "Registering…" : "Register server"}
                       </button>
                     </div>
                   </div>

                   <div className="mt-4 space-y-2">
                     {mcpServers.length === 0 ? (
                       <div className="rounded border border-linedefault bg-elevated p-4 text-center text-bodysm text-inksecondary">
                         No servers registered.
                       </div>
                     ) : (
                       mcpServers.map((s, i) => {
                         const id = mcpServerId(s);
                         const name = mcpDisplayName(s.name || (id ? id : undefined), i);
                         const health = mcpHealthState(s);
                         const approval = normalizedState(s.approval_label);
                         const approvalLabel =
                           approval === "green"
                             ? "Green"
                             : approval === "yellow"
                               ? "Yellow"
                               : approval === "red"
                                 ? "Red"
                                 : "Approval required";
                         return (
                           <div
                             key={`${id || name}-${i}`}
                             className="rounded border border-linedefault bg-elevated p-3 flex items-center justify-between gap-3"
                           >
                             <div className="min-w-0">
                               <span className="text-bodysm font-medium text-ink block truncate">{name}</span>
                               <span className="text-meta text-inksecondary block">
                                 Health: {stateLabel(health, "Not tested")}
                               </span>
                             </div>
                             <div className="flex shrink-0 items-center gap-2">
                               <span className={`rounded-sm border px-2 py-0.5 text-meta font-medium ${badgeClass(stateTone(health))}`}>
                                 {stateLabel(health, "Not tested")}
                               </span>
                               <span className={`rounded-sm border px-2 py-0.5 text-meta font-medium ${badgeClass(approval === "red" ? "err" : approval === "green" ? "ok" : "muted")}`}>
                                 {approvalLabel}
                               </span>
                               <button
                                 type="button"
                                 onClick={() => handleTestCredential("mcp", {
                                   resultTarget: "mcp",
                                   scope: mcpScope,
                                   serverId: id || undefined,
                                   config: id ? { server_id: id } : undefined,
                                 })}
                                 disabled={!id || testingTarget === "mcp"}
                                 className="rounded border border-linedefault px-2 py-1 text-meta text-ink hover:bg-surface disabled:opacity-50"
                               >
                                 {testingTarget === "mcp" ? "Checking…" : "Check health"}
                               </button>
                             </div>
                           </div>
                         );
                       })
                     )}
                   </div>
                   {testResult?.target === "mcp" && (
                     <div
                       className={`mt-3 rounded border p-2 text-meta ${
                         testResultTone(testResult) === "err"
                           ? "border-err/30 bg-err/10 text-err"
                           : testResultTone(testResult) === "ok"
                             ? "border-ok/30 bg-ok/10 text-ok"
                             : "border-linedefault bg-elevated text-inksecondary"
                       }`}
                     >
                       {testResult.message}
                     </div>
                   )}
                 </section>

                 <section className="rounded border border-linedefault bg-surface p-5">
                   <div className="flex items-center justify-between">
                     <div>
                       <h3 className="text-h3 font-semibold text-ink">Vision</h3>
                       <p className="text-bodysm text-inksecondary mt-0.5">
                         Image understanding is shown only when a real provider is available.
                       </p>
                     </div>
                     <span className={`rounded-sm border px-2 py-0.5 text-meta font-medium ${badgeClass(stateTone(visionStatus))}`}>
                       {stateLabel(visionStatus, "Unavailable")}
                     </span>
                   </div>
                   <p className="mt-3 text-bodysm text-inksecondary">
                     {["unavailable", "fake", "not_real", "unknown", ""].includes(normalizedState(visionStatus))
                       ? "No verified vision provider is registered."
                       : "Vision status is reported by the application configuration."}
                   </p>
                 </section>
               </div>
            )}

            {/* TAB 4: FILES & KNOWLEDGE */}
            {tab === "files" && (
              <div className="space-y-4">
                <section className="rounded border border-linedefault bg-surface p-5">
                  <h3 className="text-h3 font-semibold text-ink">Files & Knowledge</h3>
                  <p className="text-bodysm text-inksecondary mt-1">
                    Add project knowledge, sync structured workspace data, or rebuild the selected project’s searchable index.
                  </p>

                  <div className="mt-4 grid gap-3 sm:grid-cols-3">
                    <div className="rounded border border-linedefault bg-elevated p-3">
                      <span className="text-meta text-inkmuted block">Indexed Documents</span>
                      <span className="text-h2 font-bold text-ink mt-0.5 block">
                        {config.project_id !== projectId ? "—" : config.files_knowledge.count_status === "unavailable" ? "Unavailable" : config.files_knowledge.indexed_documents}
                      </span>
                      <span className="mt-1 block text-meta text-inksecondary">Document records, not chunks or vector count.</span>
                    </div>

                    <div className="rounded border border-linedefault bg-elevated p-3">
                      <span className="text-meta text-inkmuted block">Knowledge Files</span>
                      <span className="text-h2 font-bold text-ink mt-0.5 block">
                        {config.project_id !== projectId ? "—" : config.files_knowledge.count_status === "unavailable" ? "Unavailable" : config.files_knowledge.project_files}
                      </span>
                      <span className="mt-1 block text-meta text-inksecondary">Saved project knowledge file records.</span>
                    </div>

                    <div className="rounded border border-linedefault bg-elevated p-3">
                      <span className="text-meta text-inkmuted block">Search Mode</span>
                      <span className="text-h2 font-bold text-ink mt-0.5 block">
                        {config.project_id === projectId ? retrievalModeLabel(config.files_knowledge.search_mode) : "—"}
                      </span>
                      <span className="mt-1 block text-meta text-inksecondary">
                        {config.project_id === projectId
                          ? `${config.files_knowledge.vector_status || "Vector status unavailable"}${config.files_knowledge.vector_reason ? `: ${config.files_knowledge.vector_reason}` : ""}`
                          : "Project retrieval capability."}
                      </span>
                    </div>
                  </div>

                  <div className="mt-5 rounded border border-linedefault bg-elevated p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <h4 className="text-bodysm font-semibold text-ink">Project Knowledge Files</h4>
                        <p className="mt-1 text-meta text-inksecondary">
                          Accepted uploads and searchable text are different: images and scanned PDFs are accepted without OCR.
                        </p>
                      </div>
                      <label className={`inline-flex cursor-pointer items-center rounded bg-accent px-3.5 py-1.5 text-bodysm font-medium text-white hover:bg-accenthover ${(!projectId || syncing || reindexing || uploading) ? "cursor-not-allowed opacity-50" : ""}`}>
                        {uploading ? "Uploading…" : "Add Knowledge File"}
                        <input
                          aria-label="Add Knowledge File"
                          data-testid="knowledge-file-picker"
                          type="file"
                          className="sr-only"
                          disabled={!projectId || syncing || reindexing || uploading}
                          accept={capabilities?.accepted_types.flatMap((type) => type.extensions).join(",")}
                          onChange={handleKnowledgeFileSelected}
                        />
                      </label>
                    </div>

                    {capabilities?.accepted_types?.length ? (
                      <p className="mt-3 text-meta text-inksecondary">
                        Accepted: {capabilities.accepted_types.map((type) => `${type.label}${type.searchable_text ? " (text searchable)" : " (accepted, not searchable)"}`).join(" · ")}
                        {` · Document limit ${formatFileSize(capabilities.max_document_bytes)} · Image limit ${formatFileSize(capabilities.max_image_bytes)}`}
                        {capabilities.ocr_supported ? " · OCR supported" : " · No OCR for images or scanned PDFs"}
                      </p>
                    ) : (
                      <p className="mt-3 text-meta text-inksecondary">Upload type and size limits are unavailable until backend capabilities load.</p>
                    )}

                    {knowledgeNotice && (
                      <p role="status" aria-live="polite" data-testid="knowledge-action-notice" className="mt-3 rounded border border-linedefault bg-surface p-2.5 text-bodysm text-ink">
                        {knowledgeNotice}
                      </p>
                    )}
                    {knowledgeFilesError && <p role="alert" className="mt-3 text-bodysm text-err">{knowledgeFilesError}</p>}
                    {projectId && knowledgeFiles.length > 0 ? (
                      <ul aria-label="Project knowledge files" data-testid="knowledge-file-list" className="mt-3 divide-y divide-linedefault rounded border border-linedefault bg-surface">
                        {knowledgeFiles.map((file) => (
                          <li key={file.file_id} data-file-id={file.file_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
                            <div className="min-w-0">
                              <p className="truncate text-bodysm font-medium text-ink">{file.original_name}</p>
                              <p className="text-meta text-inksecondary">{file.mime_detected || "Unknown type"} · {formatFileSize(file.size)}</p>
                            </div>
                            <span data-testid="knowledge-file-state" className={`rounded-sm border px-2 py-0.5 text-meta ${badgeClass(file.index_status === "indexed" || (!file.index_status && file.indexed) ? "ok" : file.index_status === "failed" || file.index_status === "quarantined" ? "err" : "muted")}`}>
                              {knowledgeFileState(file)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : projectId && !knowledgeFilesError ? (
                      <p data-testid="knowledge-files-empty" className="mt-3 rounded border border-linedefault bg-surface p-3 text-meta text-inksecondary">No project knowledge files yet.</p>
                    ) : null}
                  </div>

                  <div className="mt-5 flex flex-wrap gap-3">
                    <button
                      type="button"
                      onClick={handleSyncWorkspace}
                      data-testid="sync-workspace-data"
                      disabled={!projectId || syncing || reindexing || uploading}
                      className="rounded bg-accent px-3.5 py-1.5 text-bodysm font-medium text-white hover:bg-accenthover"
                    >
                      {syncing ? "Syncing…" : "Sync Workspace Data"}
                    </button>
                    <button
                      type="button"
                      onClick={handleReindex}
                      data-testid="rebuild-knowledge-index"
                      disabled={!projectId || syncing || reindexing || uploading}
                      className="rounded border border-linedefault bg-elevated px-3.5 py-1.5 text-bodysm text-ink hover:bg-surface"
                    >
                      {reindexing ? "Rebuilding…" : "Rebuild Knowledge Index"}
                    </button>
                  </div>
                  <p className="mt-2 text-meta text-inksecondary">Sync Workspace Data imports structured workspace records. Rebuild Knowledge Index indexes workspace text and saved project knowledge files.</p>
                </section>

                <section className="rounded border border-linedefault bg-surface p-5">
                  <h3 className="text-h3 font-semibold text-ink">Storage Details</h3>
                  <div className="mt-2 text-bodysm text-inksecondary space-y-1">
                    <div>Knowledge Files counts saved project upload records.</div>
                    <div>Maximum document upload: {capabilities ? formatFileSize(capabilities.max_document_bytes) : `${config.files_knowledge.max_upload_mb} MB`}</div>
                    {capabilities && <div>Maximum image upload: {formatFileSize(capabilities.max_image_bytes)}</div>}
                  </div>
                </section>
              </div>
            )}

            {/* TAB 5: PRIVACY & SECURITY */}
            {tab === "privacy" && (
              <div className="space-y-4">
                <section className="rounded border border-linedefault bg-surface p-5">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-h3 font-semibold text-ink">OS Credential Vault</h3>
                      <p className="text-bodysm text-inksecondary mt-0.5">
                        Credentials are managed by the application’s protected storage.
                      </p>
                    </div>
                    <span className={`rounded-sm border px-2 py-0.5 text-meta font-medium ${badgeClass(stateTone(vaultConfigured ? "configured" : "not_configured"))}`}>
                      {vaultConfigured ? "Configured" : "Not configured"}
                    </span>
                  </div>

                  <div className="mt-4">
                    <h4 className="text-bodysm font-semibold text-ink mb-2">Stored Credentials</h4>
                    {config.privacy_security.active_credentials.length === 0 ? (
                      <p className="text-meta text-inksecondary bg-elevated p-3 rounded border border-linedefault">
                        No credentials stored.
                      </p>
                    ) : (
                      <div className="rounded border border-linedefault overflow-hidden">
                        <table className="w-full text-left text-bodysm">
                          <thead className="bg-elevated text-meta text-inkmuted border-b border-linedefault">
                            <tr>
                              <th className="px-4 py-2">Scope</th>
                              <th className="px-4 py-2">Credential Target</th>
                              <th className="px-4 py-2">Registered At</th>
                              <th className="px-4 py-2 text-right">Action</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-linedefault bg-surface">
                            {config.privacy_security.active_credentials.map((c, i) => (
                              <tr key={i}>
                                <td className="px-4 py-2 text-ink font-medium capitalize">{c.scope}</td>
                                <td className="px-4 py-2 text-meta text-ink">{credentialDisplayName(c.label)}</td>
                                <td className="px-4 py-2 text-meta text-inksecondary">{c.created_at || "Active"}</td>
                                <td className="px-4 py-2 text-right">
                                  <button
                                    type="button"
                                    onClick={() => {
                                      const target = credentialTarget(c.label);
                                       if (target) {
                                         handleDisconnectCredential(
                                           target,
                                           target === "meta" ? config?.project_id : undefined,
                                         );
                                       }
                                    }}
                                    className="text-err hover:underline text-meta"
                                  >
                                    Remove
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                </section>

                <section className="rounded border border-linedefault bg-surface p-5">
                  <h3 className="text-h3 font-semibold text-ink">Data Privacy Commitment</h3>
                  <p className="text-bodysm text-inksecondary mt-1">
                    Your settings and workspace data remain under your control.
                  </p>
                  <ul className="mt-3 list-disc pl-5 text-meta text-inksecondary space-y-1">
                    <li>No telemetry or analytics beacons are emitted by this software.</li>
                    <li>Marketing strategy documents, customer emails, and audit data remain on your local disk.</li>
                    <li>External AI inference is used only if explicitly configured under AI Settings.</li>
                  </ul>
                </section>
              </div>
            )}

            {/* TAB 6: SYSTEM */}
            {tab === "system" && (
              <div className="space-y-4">
                <section className="rounded border border-linedefault bg-surface p-5">
                  <div className="flex items-center justify-between">
                    <h3 className="text-h3 font-semibold text-ink">System Diagnostics</h3>
                    <span className={`rounded-sm border px-2 py-0.5 text-meta font-medium ${badgeClass(stateTone(config.system.status))}`}>
                      {stateLabel(config.system.status, "Unknown")}
                    </span>
                  </div>
                  <p className="text-bodysm text-inksecondary mt-1">
                    Current application status and runtime information.
                  </p>

                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <div className="rounded border border-linedefault bg-elevated p-3">
                      <span className="text-meta text-inkmuted block">Application Version</span>
                      <span className="text-bodysm font-bold text-ink mt-0.5 block">{config.system.version}</span>
                    </div>

                    <div className="rounded border border-linedefault bg-elevated p-3">
                      <span className="text-meta text-inkmuted block">Primary Orchestrator</span>
                      <span className="text-bodysm font-bold text-ink mt-0.5 block capitalize">
                        {config.system.orchestrator}
                      </span>
                    </div>

                    <div className="rounded border border-linedefault bg-elevated p-3">
                      <span className="text-meta text-inkmuted block">Database Architecture</span>
                      <span className="text-bodysm font-bold text-ink mt-0.5 block">
                        {config.system.database}
                      </span>
                    </div>

                    <div className="rounded border border-linedefault bg-elevated p-3">
                      <span className="text-meta text-inkmuted block">Vector Search (RAG)</span>
                      <span className="text-bodysm font-bold text-ink mt-0.5 block uppercase">
                        {config.system.rag}
                      </span>
                    </div>
                  </div>
                </section>
              </div>
            )}

            {/* TAB 7: ADVANCED */}
            {tab === "advanced" && (
              <div className="space-y-4">
                <section className="rounded border border-linedefault bg-surface p-5">
                  <h3 className="text-h3 font-semibold text-ink">Advanced Developer Options</h3>
                  <p className="text-bodysm text-inksecondary mt-1">
                    Additional runtime options are managed by the application services.
                  </p>

                  <div className="mt-4 rounded border border-linedefault bg-elevated p-3 text-bodysm text-inksecondary">
                    No internal configuration details are displayed here.
                  </div>
                </section>
              </div>
            )}
          </div>
        )}

        {/* TAB 8: SKILLS (DEV-008-SKILLS-OPS) — outside the config gate on
            purpose: this tab reports the library's own measured health, so it
            must stay visible even when /api/settings/config cannot be read. */}
        {tab === "skills" ? (
          <div className="max-w-4xl">
            <SkillList />
          </div>
        ) : null}
      </div>

      {/* Credential Modal / Drawer */}
      {connectTarget && (
        <Modal title={`Configure ${targetName(connectTarget)}`} returnFocusRef={credentialOpenerRef} onClose={() => {
          setConnectTarget(null);
          setCredentialInput("");
        }}>
            <p className="text-bodysm text-inksecondary mt-1">
              Your credential is stored securely and is not displayed after saving.
            </p>

            <div className="mt-4">
              <label className="text-bodysm font-medium text-ink block">
                Credential
                <input
                  type="password"
                  value={credentialInput}
                  onChange={(e) => setCredentialInput(e.target.value)}
                  placeholder={connectTarget === "mcp" ? "Optional server credential" : "Enter credential here…"}
                  autoFocus
                  className="mt-1.5 w-full rounded border border-linedefault bg-elevated px-3 py-2 text-bodysm text-ink focus:outline-none focus:border-accent"
                />
              </label>
            </div>

            {connectTarget === "brightdata" && (
              <label className="mt-3 block text-bodysm font-medium text-ink">
                Dataset ID
                <input
                  type="text"
                  value={brightdataDataset}
                  onChange={(e) => setBrightdataDataset(e.target.value.slice(0, 128))}
                  placeholder="Enter the provider dataset ID"
                  className="mt-1.5 w-full rounded border border-linedefault bg-elevated px-3 py-2 text-bodysm text-ink focus:outline-none focus:border-accent"
                />
              </label>
            )}

            {connectTarget === "meta" && (
              <label className="mt-3 block text-bodysm font-medium text-ink">
                Account ID
                <input
                  type="text"
                  value={metaAccountId}
                  onChange={(e) => setMetaAccountId(e.target.value.slice(0, 128))}
                  placeholder="Enter the connected account ID"
                  className="mt-1.5 w-full rounded border border-linedefault bg-elevated px-3 py-2 text-bodysm text-ink focus:outline-none focus:border-accent"
                />
              </label>
            )}

            <div className="mt-6 flex flex-col-reverse justify-end gap-2.5 sm:flex-row">
              <button
                type="button"
                onClick={() => {
                  setConnectTarget(null);
                  setCredentialInput("");
                }}
                className="rounded border border-linedefault px-3.5 py-1.5 text-bodysm text-ink hover:bg-elevated"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConnectCredential}
                disabled={
                  credentialSaving ||
                  (!credentialInput.trim() &&
                    connectTarget !== "mcp" &&
                    !(connectTarget === "brightdata" && brightdataConnected))
                }
                className="rounded bg-accent px-4 py-1.5 text-bodysm font-medium text-white hover:bg-accenthover disabled:opacity-50"
              >
                {credentialSaving ? "Saving…" : "Save Securely"}
              </button>
            </div>
        </Modal>
      )}
    </div>
  );
}
