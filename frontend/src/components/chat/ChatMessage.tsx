import { useEffect, useMemo, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import "./chat.css";

/**
 * DEV-004 W3: user + assistant bubbles.
 *
 * - Markdown via marked, sanitized with DOMPurify (XSS-safe inner HTML).
 * - `dir="auto"` on bubble content: mixed Arabic/English long strings
 *   direction-resolve per paragraph without flipping the LTR app chrome.
 * - Code blocks get a copy button (attached post-render, survives
 *   sanitize because it is added as DOM, not HTML).
 * - Assistant actions: copy response + retry (failed only).
 *
 * REGENERATE IS INTENTIONALLY OMITTED: re-running a turn would re-execute
 * its tool side effects (jobs, approvals, web writes) with a fresh turn id
 * and no idempotency key. Retry (which reuses the SAME user text on a new
 * turn only after failure, i.e. nothing completed) is the only safe
 * re-execution path, served by POST /chat/turn/{id}/retry.
 */

export interface ChatMessageProps {
  role: string;
  bodyMd: string;
  failed?: boolean;
  streaming?: boolean;
  turnId?: string | null;
  latencyMs?: number | null;
  onRetry?: (oldTurnId: string) => void;
  /** Sanitized provenance from GET /api/chats/{id}/messages (W3 contract). */
  citations?: unknown[];
  /** Actual response model metadata, when the backend recorded it. */
  model?: { provider?: string | null; model?: string | null; route_mode?: string | null } | null;
  projectId?: string | null;
  conversationId?: string | null;
}

interface DisplayCitation {
  key: string;
  source: "turn_attachment" | "project_rag";
  label: string;
  scopeLabel: string;
  projectId: string;
  chunkId: string;
  retrievalLabel: string;
}

function safeLabel(value: unknown): string {
  if (typeof value !== "string") return "";
  return value.split(/[\\/]/).pop()?.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 240).trim() || "";
}

function safeIdentifier(value: unknown, limit = 160): string {
  if (typeof value !== "string") return "";
  const identifier = value.trim().slice(0, limit);
  return /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(identifier) ? identifier : "";
}

function projectCitations(value: unknown[] | undefined, expectedProjectId?: string | null,
                          expectedConversationId?: string | null): DisplayCitation[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap<DisplayCitation>((item) => {
    if (!item || typeof item !== "object") return [];
    const citation = item as Record<string, unknown>;
    const source = citation.source;
    if (source !== "turn_attachment" && source !== "project_rag") return [];
    const projectId = safeIdentifier(citation.project_id, 64);
    if (!projectId || !expectedProjectId || projectId !== expectedProjectId) return [];
    const chunkId = safeIdentifier(citation.chunk_id);
    if (!chunkId) return [];
    if (source === "turn_attachment") {
      const fileId = safeIdentifier(citation.file_id, 128);
      const conversationId = safeIdentifier(citation.conversation_id, 128);
      const filename = safeLabel(citation.filename ?? citation.file_name);
      if (!conversationId || !expectedConversationId || conversationId !== expectedConversationId || !fileId || !filename) return [];
      return [{ key: `${source}:${fileId}:${chunkId}`, source, label: filename,
        scopeLabel: "This chat attachment", projectId, chunkId, retrievalLabel: "" }];
    }
    if (citation.scope !== "project") return [];
    const fileId = safeIdentifier(citation.file_id, 128);
    const documentId = safeIdentifier(citation.document_id, 128);
    const label = safeLabel(citation.file_name ?? citation.filename) ||
      (fileId ? `Knowledge file ${fileId}` : documentId ? `Knowledge document ${documentId}` : "");
    if (!label) return [];
    const branches = Array.isArray(citation.retrieval_sources)
      ? citation.retrieval_sources.filter((branch): branch is string => branch === "fts" || branch === "semantic")
      : [];
    const retrievalLabel = [...new Set(branches)].map((branch) => branch === "fts" ? "FTS" : "Semantic").join(" + ");
    return [{ key: `${source}:${fileId || documentId || label}:${chunkId}`, source, label,
      scopeLabel: "Business Knowledge", projectId, chunkId, retrievalLabel }];
  });
}

function renderMarkdown(md: string): string {
  const html = marked.parse(md ?? "") as string;
  return DOMPurify.sanitize(html, { ADD_ATTR: ["target", "rel"] });
}

function wrapTables(html: string): string {
  // Scroll long/wide tables inside the bubble instead of breaking layout.
  return html.replace(/<table([\s>])/g, '<div class="table-wrap"><table$1').replace(/<\/table>/g, "</table></div>");
}

export default function ChatMessage({
  role,
  bodyMd,
  failed,
  streaming,
  turnId,
  latencyMs,
  onRetry,
  citations,
  model,
  projectId,
  conversationId,
}: ChatMessageProps) {
  const isUser = role === "user";
  const [copied, setCopied] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  const html = useMemo(() => wrapTables(renderMarkdown(bodyMd)), [bodyMd]);
  const citationSources = useMemo(() => projectCitations(citations, projectId, conversationId),
    [citations, projectId, conversationId]);

  // Attach per-code-block copy buttons (DOM-level so sanitize can't strip).
  useEffect(() => {
    const root = bodyRef.current;
    if (!root) return;
    const blocks = root.querySelectorAll("pre");
    const cleanups: (() => void)[] = [];
    blocks.forEach((pre) => {
      if (pre.querySelector("[data-code-copy]")) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Copy";
      btn.className = "code-copy-btn";
      btn.setAttribute("data-code-copy", "1");
      const onClick = (ev: MouseEvent) => {
        ev.stopPropagation();
        const code = pre.querySelector("code");
        const text = (code ?? pre).textContent ?? "";
        if (navigator.clipboard?.writeText) {
          navigator.clipboard.writeText(text).then(
            () => {
              btn.textContent = "Copied";
              window.setTimeout(() => {
                btn.textContent = "Copy";
              }, 1500);
            },
            () => {
              btn.textContent = "Failed";
            },
          );
        }
      };
      btn.addEventListener("click", onClick);
      cleanups.push(() => btn.removeEventListener("click", onClick));
      pre.appendChild(btn);
    });
    return () => {
      cleanups.forEach((fn) => fn());
    };
  }, [html]);

  const copyResponse = () => {
    const text = bodyMd ?? "";
    const done = () => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(done, done);
    } else {
      done();
    }
  };

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div
          className="chat-bubble rounded-md border border-linedefault bg-elevated px-3.5 py-2.5 text-bodysm text-ink"
          dir="auto"
        >
          <div className="whitespace-pre-wrap">{bodyMd}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="chat-bubble w-full rounded-md border border-linesubtle bg-raised px-3.5 py-2.5">
        {failed ? (
          <div className="mb-2 rounded-sm border border-err/40 px-2 py-1 text-bodysm text-err">
            This response failed to finish.
          </div>
        ) : null}
        <div
          ref={bodyRef}
          data-testid="answer-content"
          className="chat-md text-ink"
          dir="auto"
          dangerouslySetInnerHTML={{ __html: html || (streaming ? "<p>…</p>" : "") }}
        />
        {!streaming && citationSources.length > 0 ? (
          <ul aria-label="Evidence citations" data-testid="attachment-provenance" className="mt-2 flex flex-wrap gap-1.5 text-meta text-inksecondary">
            {citationSources.map((source) => (
              <li key={source.key} data-citation-source={source.source} className="rounded border border-linedefault px-2 py-1">
                <span dir="auto" className="break-all font-medium text-ink">{source.label}</span>
                <span className="ml-1">· {source.scopeLabel} · Project {source.projectId} · Chunk {source.chunkId}</span>
                {source.retrievalLabel && <span className="ml-1">· {source.retrievalLabel}</span>}
              </li>
            ))}
          </ul>
        ) : null}
        {!streaming && model && (model.provider || model.model) ? (
          <p className="mt-2 text-meta text-inkmuted" data-testid="actual-model">
            Used: {[model.provider, model.model].filter((value): value is string => Boolean(value?.trim())).join(" · ")}
            {model.route_mode ? ` · ${model.route_mode}` : ""}
          </p>
        ) : null}
        <div className="mt-2 flex flex-wrap items-center gap-2 text-meta text-inkmuted">
          {streaming ? <span>Streaming…</span> : null}
          {latencyMs != null && !streaming ? <span>{latencyMs} ms</span> : null}
          <span className="flex-1" />
          <button
            type="button"
            onClick={copyResponse}
            className="rounded-sm border border-linedefault px-2 py-0.5 text-meta text-inksecondary hover:border-accent"
          >
            {copied ? "Copied" : "Copy"}
          </button>
          {failed && turnId && onRetry ? (
            <button
              type="button"
              onClick={() => onRetry(turnId)}
              className="rounded-sm border border-err/40 px-2 py-0.5 text-meta text-err hover:border-err"
            >
              Retry
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
