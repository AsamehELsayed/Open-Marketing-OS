import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, uploadChatAttachment } from "../../api/client";
import type { AttachmentScope, FileCapabilities, ProjectKnowledgeFile } from "../../api/client";

/** DEV-004 W3: fixed-bottom multiline composer with optimistic send. */
export interface ComposerProps {
  onSend: (text: string, attachmentIds?: string[]) => void | Promise<boolean>;
  sending: boolean;
  scopeKey: string;
  projectLoading: boolean;
  migrateFromScopeKey?: string;
  canSubmit?: boolean;
  placeholder?: string;
  projectId?: string | null;
  conversationId?: string;
  modelSelector?: ReactNode;
}

interface PendingAttachment {
  key: string;
  file: File;
  scope: AttachmentScope;
  uploaded?: ProjectKnowledgeFile;
  state: "selected" | "uploading" | "processing" | "ready" | "error";
  progress?: number;
  detail?: string;
  error?: string;
}

function progressDescription(stage: string, current: number | null, total: number | null): string {
  if (stage === "downloading_model") {
    const received = typeof current === "number" ? `${(current / (1024 * 1024)).toFixed(1)} MB received` : "Receiving model bytes";
    const expected = typeof total === "number" && total > 0 ? ` of ${(total / (1024 * 1024)).toFixed(1)} MB` : "";
    return `Downloading local search model · ${received}${expected}`;
  }
  const labels: Record<string, string> = {
    receiving_file: "Receiving file…", checking_model_cache: "Checking local search model…",
    validating_model_cache: "Checking downloaded model files…", loading_local_model: "Loading local search model…",
    local_model_ready: "Preparing file index…", extracting_file: "Extracting file text…",
    indexing_file: "Indexing file for search…", completed: "File processing complete.",
    failed: "File processing failed.",
  };
  return labels[stage] || "Processing file…";
}

function fileSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function safeFileLabel(name: string): string {
  return name.split(/[\\/]/).pop()?.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 240).trim() || "Unnamed file";
}

function retrievalLabel(mode?: string): string {
  return mode === "FTS_ONLY" ? "FTS" : (mode || "Status unavailable").replace(/_/g, " ");
}

const MAX_HEIGHT = 200;

/** Founder-facing slash commands (must mirror app.graphs.intent.detect_slash_intent). */
const SLASH_COMMANDS = [
  { cmd: "/scrap", arg: "@handle", desc: "Instagram public research (real Apify scrape)" },
  { cmd: "/scrape", arg: "@handle", desc: "Instagram public research (alias of /scrap)" },
  { cmd: "/ig", arg: "@handle", desc: "Instagram public research (alias)" },
  { cmd: "/instagram", arg: "@handle", desc: "Instagram public research (alias)" },
  { cmd: "/audit", arg: "site.com", desc: "Full website marketing audit (live fetch)" },
  { cmd: "/analyze", arg: "site.com", desc: "Website marketing audit (alias)" },
  { cmd: "/analyse", arg: "site.com", desc: "Website marketing audit (alias)" },
  { cmd: "/fetch", arg: "site.com", desc: "Fetch one page" },
  { cmd: "/open", arg: "site.com", desc: "Fetch one page (alias)" },
  { cmd: "/crawl", arg: "site.com", desc: "Crawl several pages" },
];

export default function Composer({ onSend, sending, scopeKey, projectLoading, migrateFromScopeKey, canSubmit = true, placeholder, projectId, conversationId, modelSelector }: ComposerProps) {
  // Keep drafts in memory by project/conversation scope. A route/provider
  // update can change the active scope without exposing the previous draft.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const previousScopeRef = useRef(scopeKey);
  const previousProjectLoadingRef = useRef(projectLoading);
  const previousScope = previousScopeRef.current;
  const draft = drafts[scopeKey] ?? (
    migrateFromScopeKey
      ? drafts[migrateFromScopeKey] ?? ""
      : previousProjectLoadingRef.current && !projectLoading && previousScope.startsWith("no-project:")
        ? drafts[previousScope] ?? ""
      : ""
  );
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const [slashIndex, setSlashIndex] = useState(0);
  const pickerRef = useRef<HTMLInputElement>(null);
  const attachmentsRef = useRef<Record<string, PendingAttachment[]>>({});
  const [attachments, setAttachments] = useState<Record<string, PendingAttachment[]>>({});
  const [capabilities, setCapabilities] = useState<FileCapabilities>();
  const [fileError, setFileError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const uploadLockRef = useRef(false);
  const uploadAbortRef = useRef<AbortController | null>(null);
  const activeScopeRef = useRef(scopeKey);
  const scopeVersionRef = useRef(0);
  if (activeScopeRef.current !== scopeKey) {
    activeScopeRef.current = scopeKey;
    scopeVersionRef.current++;
  }
  const selectedFiles = attachments[scopeKey] ?? [];

  const updateFiles = (key: string, update: (files: PendingAttachment[]) => PendingAttachment[]) => {
    const next = { ...attachmentsRef.current, [key]: update(attachmentsRef.current[key] ?? []) };
    attachmentsRef.current = next;
    setAttachments(next);
  };

  useEffect(() => {
    let cancelled = false;
    setCapabilities(undefined);
    setFileError(null);
    if (projectId && conversationId && canSubmit) {
      api.getSettingsConfig(projectId).then((config) => {
        if (cancelled || config.project_id !== projectId) return;
        const knowledge = config.files_knowledge;
        if (!knowledge.accepted_types) throw new Error("File support is unavailable.");
        setCapabilities({ accepted_types: knowledge.accepted_types,
          max_document_bytes: knowledge.max_document_bytes ?? 25 * 1024 * 1024,
          max_image_bytes: knowledge.max_image_bytes ?? 15 * 1024 * 1024,
          ocr_supported: knowledge.ocr_supported ?? false });
      }).catch(() => {
        if (!cancelled) setFileError("File support could not be loaded. Reopen this chat to retry.");
      });
    }
    return () => { cancelled = true; };
  }, [projectId, conversationId, canSubmit]);

  useEffect(() => {
    uploadAbortRef.current?.abort();
    uploadAbortRef.current = null;
    uploadLockRef.current = false;
    setUploading(false);
    setFileError(null);
    return () => { uploadAbortRef.current?.abort(); };
  }, [scopeKey]);

  const chooseFiles = (files: FileList | null) => {
    if (!files || !capabilities || !canSubmit || uploading) return;
    let current = attachmentsRef.current[scopeKey] ?? [];
    const errors: string[] = [];
    for (const file of Array.from(files)) {
      if (current.length >= 10) { errors.push("Attach at most 10 files per message."); break; }
      const extension = `.${file.name.split(".").pop()?.toLowerCase()}`;
      const capability = capabilities.accepted_types.find((type) => type.extensions.includes(extension));
      if (!capability) { errors.push(`${file.name}: unsupported file type.`); continue; }
      if (!file.size || file.size > capability.max_bytes) {
        errors.push(`${file.name}: choose a non-empty file up to ${fileSize(capability.max_bytes)}.`); continue;
      }
      if (current.some((item) => item.file.name === file.name && item.file.size === file.size && item.file.lastModified === file.lastModified)) continue;
      current = [...current, { key: crypto.randomUUID(), file, scope: "turn", state: "selected" }];
    }
    updateFiles(scopeKey, () => current);
    setFileError(errors.length ? errors.join(" ") : null);
    if (pickerRef.current) pickerRef.current.value = "";
  };

  const autoresize = () => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  };

  useEffect(() => {
    autoresize();
  }, [draft]);

  useEffect(() => {
    const oldScope = previousScopeRef.current;
    const justFinishedLoading = previousProjectLoadingRef.current && !projectLoading;
    if (justFinishedLoading && oldScope !== scopeKey && oldScope.startsWith("no-project:")) {
      setDrafts((current) => {
        if (!(oldScope in current) || scopeKey in current) return current;
        return { ...current, [scopeKey]: current[oldScope] };
      });
    }
    if (migrateFromScopeKey && migrateFromScopeKey !== scopeKey) {
      setDrafts((current) => {
        if (!(migrateFromScopeKey in current) || scopeKey in current) return current;
        return { ...current, [scopeKey]: current[migrateFromScopeKey] };
      });
    }
    previousScopeRef.current = scopeKey;
    previousProjectLoadingRef.current = projectLoading;
  }, [migrateFromScopeKey, projectLoading, scopeKey]);

  // Suggestion popup: empty draft typed as "/", or a /command prefix not yet
  // finished with a space — matches the founder request ("when I press /
  // show me the defaults").
  const firstToken = draft.trim().split(/\s+/)[0] ?? "";
  const showSlash = draft.trim().startsWith("/") && !/\s/.test(draft.trim());
  const matches = showSlash
    ? SLASH_COMMANDS.filter((c) => c.cmd.startsWith(firstToken.toLowerCase()) || firstToken === "/")
    : [];
  useEffect(() => {
    setSlashIndex(0);
  }, [firstToken]);

  const applyCommand = (cmd: string) => {
    setDrafts((current) => ({ ...current, [scopeKey]: `${cmd} ` }));
    areaRef.current?.focus();
  };

  const canSend = draft.trim().length > 0 && !sending && !uploading;

  const submit = async () => {
    const text = draft.trim();
    if (!text || sending || !canSubmit || uploadLockRef.current) return;
    const capturedScope = scopeKey;
    const version = scopeVersionRef.current;
    const capturedFiles = attachmentsRef.current[capturedScope] ?? [];
    if (capturedFiles.length && (!projectId || !conversationId || !capabilities)) return;
    uploadLockRef.current = true;
    setUploading(true);
    setFileError(null);
    const controller = new AbortController();
    uploadAbortRef.current = controller;
    const isCurrent = () => activeScopeRef.current === capturedScope && scopeVersionRef.current === version && !controller.signal.aborted;
    const updateFile = (key: string, patch: Partial<PendingAttachment>) => updateFiles(capturedScope,
      (files) => files.map((item) => item.key === key ? { ...item, ...patch } : item));
    const ids: string[] = [];
    try {
      for (const item of capturedFiles) {
        if (!isCurrent()) return;
        let uploaded = item.uploaded;
        if (!uploaded) {
          updateFile(item.key, { state: "uploading", progress: 0, error: undefined });
          let stopProgressPolling: (() => void) | null = null;
          try {
            const progressId = crypto.randomUUID();
            let poll = true;
            const pollHandle = window.setInterval(async () => {
              if (!poll || !isCurrent()) return;
              try {
                const status = await api.getUploadProgress(progressId, projectId!);
                if (status.status === "running") updateFile(item.key, {
                  state: "processing", progress: undefined,
                  detail: progressDescription(status.stage, status.current, status.total),
                });
              } catch { /* polling can race the first upload bytes or terminal response */ }
            }, 400);
            stopProgressPolling = () => { poll = false; window.clearInterval(pollHandle); };
            uploaded = await uploadChatAttachment(item.file, projectId!, conversationId!, item.scope,
              progressId,
              (percent) => { if (isCurrent()) updateFile(item.key, percent === null
                ? { state: "processing", progress: undefined, detail: "Preparing local search…" }
                : { state: "uploading", progress: percent, detail: undefined }); }, controller.signal);
            stopProgressPolling();
          } catch (error) {
            stopProgressPolling?.();
            if (isCurrent()) updateFile(item.key, { state: "error", error: error instanceof Error ? error.message : "Upload failed." });
            throw error;
          }
          updateFile(item.key, { uploaded, state: "ready", progress: undefined });
        }
        if (!isCurrent()) return;
        if (uploaded.extraction === "failed" || uploaded.index_status === "quarantined" || uploaded.index_status === "failed") {
          const message = uploaded.index_status === "quarantined" ? "File quarantined; it cannot be used as evidence."
            : "File processing failed; remove it before sending.";
          updateFile(item.key, { state: "error", error: message });
          throw new Error(message);
        }
        if (item.scope === "turn" && !["ready", "stripped"].includes(uploaded.extraction || "")) {
          throw new Error("Attachment has no ready text evidence. Remove it before sending.");
        }
        ids.push(uploaded.file_id);
      }
      if (!isCurrent()) return;
      const accepted = await onSend(text, ids);
      if (isCurrent() && accepted !== false) {
        setDrafts((current) => ({ ...current, [capturedScope]: "" }));
        updateFiles(capturedScope, () => []);
      }
    } catch (error) {
      if (isCurrent()) setFileError(error instanceof Error ? error.message : "Attachments could not be sent.");
    } finally {
      if (uploadAbortRef.current === controller) {
        uploadAbortRef.current = null;
        uploadLockRef.current = false;
        setUploading(false);
      }
    }
  };

  return (
    <div className="relative border-t border-linesubtle bg-raised px-4 py-3">
      {selectedFiles.length > 0 && (
        <ul aria-label="Message attachments" data-testid="chat-attachment-list" className="mx-auto mb-2 flex w-full max-w-[min(46rem,100%)] flex-wrap gap-2">
          {selectedFiles.map((item) => (
            <li key={item.key} className="min-w-0 max-w-full rounded-md border border-linedefault bg-base p-2 text-meta">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <span className="block break-all font-semibold text-ink">{safeFileLabel(item.file.name)}</span>
                  <span className="text-inksecondary">{safeFileLabel(item.file.name).split(".").pop()?.toUpperCase()} · {fileSize(item.file.size)}</span>
                </div>
                <button type="button" aria-label={`Remove ${safeFileLabel(item.file.name)}`} disabled={uploading || sending}
                  onClick={() => updateFiles(scopeKey, (files) => files.filter((file) => file.key !== item.key))}
                  className="rounded px-2 text-inksecondary focus:outline focus:outline-2 focus:outline-accent disabled:opacity-50">×</button>
              </div>
              <label className="mt-1 block">
                <span className="sr-only">Scope for {safeFileLabel(item.file.name)}</span>
                <select aria-label={`Scope for ${safeFileLabel(item.file.name)}`} value={item.scope} disabled={uploading || sending || Boolean(item.uploaded)}
                  onChange={(event) => updateFiles(scopeKey, (files) => files.map((file) => file.key === item.key
                    ? { ...file, scope: event.target.value as AttachmentScope } : file))}
                  className="max-w-full rounded border border-linedefault bg-raised p-1 text-ink">
                  <option value="turn">This chat</option>
                  <option value="project">Business Knowledge</option>
                </select>
              </label>
              <div role="status" aria-live="polite" className="mt-1 max-w-64 break-words text-inksecondary">
                {item.state === "selected" ? "Uploads when you send" : item.state === "uploading" ? `Uploading ${item.progress ?? 0}%`
                  : item.state === "processing" ? (item.detail || "Processing file…") : item.state === "error" ? item.error
                  : item.scope === "project" ? `Saved to Business Knowledge · ${retrievalLabel(item.uploaded?.search_mode)}` : "Ready for this chat"}
              </div>
              {item.uploaded && item.scope === "project" && <p className="mt-1 max-w-64 text-inkmuted">Removing this chip does not delete saved Business Knowledge.</p>}
            </li>
          ))}
        </ul>
      )}
      {fileError && <p role="alert" className="mx-auto mb-2 max-w-[min(46rem,100%)] text-meta text-err">{fileError}</p>}
      {modelSelector && <div className="mx-auto mb-2 w-full max-w-[min(46rem,100%)]">{modelSelector}</div>}
      {showSlash && matches.length > 0 && (
        <div
          role="listbox"
          aria-label="Slash commands"
          className="absolute bottom-full left-0 right-0 mx-auto mb-1 max-h-72 w-full max-w-[min(46rem,100%)] overflow-y-auto rounded-md border border-linedefault bg-raised shadow-lg"
        >
          {matches.map((c, i) => (
            <button
              type="button"
              key={c.cmd}
              role="option"
              aria-selected={i === slashIndex}
              onMouseEnter={() => setSlashIndex(i)}
              onClick={() => applyCommand(c.cmd)}
              className={`flex w-full items-baseline gap-3 px-4 py-2 text-left text-bodysm ${
                i === slashIndex ? "bg-elevated text-ink" : "text-inksecondary"
              }`}
            >
              <span className="font-mono font-semibold text-ink">{c.cmd}</span>
              <span className="text-inkmuted">{"{" + c.arg + "}"}</span>
              <span className="ml-auto text-inkmuted">{c.desc}</span>
            </button>
          ))}
          <div className="border-t border-linesubtle px-4 py-1.5 text-meta text-inksecondary">
            No argument = uses this project's registered handle / website.
          </div>
        </div>
      )}
      <div className="mx-auto flex w-full max-w-[min(46rem,100%)] items-end gap-2">
        <input ref={pickerRef} type="file" multiple hidden data-testid="chat-file-picker"
          accept={capabilities?.accepted_types.flatMap((type) => type.extensions).join(",") || ""}
          onChange={(event) => chooseFiles(event.target.files)} />
        <button type="button" aria-label="Attach file" title="Attach file" data-testid="chat-attach-button"
          disabled={!canSubmit || !capabilities || uploading || sending || selectedFiles.length >= 10}
          onClick={() => pickerRef.current?.click()}
          className="shrink-0 rounded-md border border-linedefault p-2.5 text-inksecondary focus:outline focus:outline-2 focus:outline-accent disabled:opacity-50">
          <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="m21 11-8.5 8.5a6 6 0 0 1-8.5-8.5l9-9a4 4 0 0 1 5.7 5.7l-9 9a2 2 0 0 1-2.8-2.8l8.5-8.5" />
          </svg>
        </button>
        <textarea
          ref={areaRef}
          rows={1}
          value={draft}
          autoFocus
          placeholder={placeholder ?? "Ask about your marketing… (type / for tools, Enter to send, Shift+Enter for a new line)"}
          aria-label="Chat message"
          // NOTE: never disabled while streaming — long runs must stay
          // responsive; the user can keep drafting, Send stays gated.
          onChange={(e) => setDrafts((current) => ({ ...current, [scopeKey]: e.target.value }))}
          onKeyDown={(e) => {
            if (showSlash && matches.length > 0) {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                const delta = e.key === "ArrowDown" ? 1 : -1;
                setSlashIndex((i) => (i + delta + matches.length) % matches.length);
                return;
              }
              if (e.key === "Enter" || e.key === "Tab") {
                // Accept the highlighted /command instead of sending.
                e.preventDefault();
                applyCommand(matches[slashIndex]?.cmd ?? matches[0].cmd);
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                setDrafts((current) => ({ ...current, [scopeKey]: "" }));
                return;
              }
            }
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          title={sending ? "Working — your draft is kept, Send unlocks when the answer lands" : undefined}
          className="max-h-[200px] min-h-[40px] min-w-0 flex-1 resize-none overflow-y-auto rounded-md border border-linedefault bg-base px-3 py-2.5 text-bodysm text-ink placeholder:text-inkmuted focus:border-accent focus:outline-none disabled:opacity-60"
        />
        <button
          type="button"
          onClick={submit}
          disabled={!canSend || !canSubmit}
          className="shrink-0 rounded-md bg-accent px-4 py-2.5 text-bodysm font-semibold text-accentink disabled:cursor-not-allowed disabled:opacity-50"
        >
          {uploading ? "Preparing…" : sending ? "Sending…" : "Send"}
        </button>
      </div>
      {capabilities && <p className="mx-auto mt-1 max-w-[min(46rem,100%)] text-meta text-inkmuted">
        Files: {capabilities.accepted_types.map((type) => type.label).join(", ")}. Documents up to {fileSize(capabilities.max_document_bytes)}; images up to {fileSize(capabilities.max_image_bytes)}.
        {!capabilities.ocr_supported && " Images and scanned PDFs do not have OCR text support."}
      </p>}
    </div>
  );
}
