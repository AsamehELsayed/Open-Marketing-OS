import { useEffect, useRef, useState } from "react";
import {
  formatCost,
  formatLatency,
  formatTokens,
  type ModelCallInfo,
} from "../../api/runtime";

/**
 * DEV-005 W7 + V1 REMEDIATION: ModelBadge with interactive hover card.
 *
 * Displays active model telemetry (provider, model, adapter, quant, route reason)
 * in an accessible popover card with full token breakdown, latency, and local
 * compute / pricing status. Supports hover (with graceful exit delay), click toggle,
 * outside click dismissal, and keyboard Escape navigation.
 */

function line(v: string | undefined, fallback: string): string {
  const s = (v || "").trim();
  return s ? s : fallback;
}

export default function ModelBadge({
  calls,
  live,
}: {
  calls: ModelCallInfo[];
  live: boolean;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const leaveTimerRef = useRef<number | null>(null);

  const current =
    [...calls]
      .reverse()
      .find((call) => call.generationStatus === "failed") ??
    [...calls].reverse().find((call) => {
        const model = call.model.trim().toLowerCase();
        return model !== "" && model !== "unknown";
      }) ?? null;
  const generation = [...calls].reverse().find((call) => call.generationStatus) ?? null;

  const handleMouseEnter = () => {
    if (leaveTimerRef.current !== null) {
      window.clearTimeout(leaveTimerRef.current);
      leaveTimerRef.current = null;
    }
    setOpen(true);
  };

  const handleMouseLeave = () => {
    leaveTimerRef.current = window.setTimeout(() => {
      setOpen(false);
      leaveTimerRef.current = null;
    }, 200);
  };

  useEffect(() => {
    if (!open) return;
    const onMouseDown = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKeyDown);
      if (leaveTimerRef.current !== null) {
        window.clearTimeout(leaveTimerRef.current);
      }
    };
  }, [open]);

  if (!current) {
    const failedProvider = generation?.provider && generation.provider !== "unknown"
      ? ` · ${generation.provider}` : "";
    const message = generation?.generationStatus === "failed"
      ? `generation failed${failedProvider} · provider invocation ${generation.invocationStarted === true ? "started" : generation.invocationStarted === false ? "not started" : "unknown"}`
      : generation?.generationSource === "deterministic"
        ? "deterministic graph response · no model call"
        : generation?.generationStatus === "unavailable"
          ? "generation unavailable · no provider call"
          : "model unknown";
    return (
      <span
        className="rounded-sm border border-linedefault px-2 py-0.5 text-meta text-inkmuted"
        title={message}
      >
        {message}
      </span>
    );
  }

  const provider = current.provider.trim().toLowerCase();
  const actualModelUnknown = current.model.trim().toLowerCase() === "unknown";
  const modelLabel = current.generationStatus === "failed"
    ? `requested: ${current.requestedModel || "unknown"} · actual: ${actualModelUnknown ? "unknown" : current.model}`
    : actualModelUnknown ? "model unknown" : current.model;
  const isBase = provider === "local" && !current.adapter;
  const isLocal = provider === "local";
  const isCloud = provider === "openai" || provider === "openrouter";
  // Never display a local LoRA name on a cloud provider (identity honesty).
  const profile = isCloud ? (current.behaviorProfile || "").trim() : "";
  const extras = [
    isCloud ? `CLOUD ${provider}` : "",
    isCloud ? (profile || "") : "",
    !isCloud && current.adapter ? line(current.adapter, "") : isBase ? "BASE" : "",
    line(current.quantization, ""),
  ].filter(Boolean);

  return (
    <div
      ref={containerRef}
      className="relative inline-block"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
        className="inline-flex max-w-full items-center gap-1.5 rounded-sm border border-linedefault bg-elevated px-2 py-0.5 text-meta text-inksecondary transition-colors hover:border-inksecondary hover:text-ink focus:outline-none focus:ring-1 focus:ring-accent"
      >
        <span
          aria-hidden
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${
            live ? "bg-ok animate-pulse" : "bg-inkmuted"
          }`}
        />
        <span className="truncate font-semibold text-ink">
          {provider} · {modelLabel}
        </span>
        {extras.length > 0 ? (
          <span className="shrink-0">
            {extras.join(" · ")}
          </span>
        ) : null}
        <span className="shrink-0 rounded-sm bg-raised px-1 font-mono text-meta">
          {current.routeMode}
        </span>
        {generation?.generationStatus === "failed" ? (
          <span className="shrink-0 text-meta text-danger">answer generation failed</span>
        ) : null}
        <svg
          className={`h-3 w-3 shrink-0 text-inkmuted transition-transform duration-150 ${
            open ? "rotate-180" : ""
          }`}
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <path d="M3 4.5L6 7.5L9 4.5" />
        </svg>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Model Telemetry Details"
          className="absolute left-0 top-full mt-1.5 z-50 w-80 rounded-md border border-linedefault bg-overlay p-3.5 shadow-xl backdrop-blur text-bodysm"
        >
          {/* Card Header: Model & Provider & Status */}
          <div className="flex items-center justify-between border-b border-linesubtle pb-2">
            <div className="flex items-center gap-1.5">
              <span
                className={`h-2 w-2 rounded-full ${
                  live ? "bg-ok animate-pulse" : "bg-inkmuted"
                }`}
              />
              <span className="font-semibold text-ink">
                {provider} / {modelLabel}
              </span>
            </div>
            {current.quantization ? (
              <span className="rounded bg-raised px-1.5 py-0.5 font-mono text-meta text-inksecondary border border-linesubtle">
                {current.quantization}
              </span>
            ) : null}
          </div>

          {/* Details Section */}
          <div className="mt-2.5 space-y-2 text-bodysm">
            {generation ? (
              <div className="flex items-center justify-between">
                <span className="text-meta text-inkmuted">Generation</span>
                <span className="text-meta text-inksecondary">
                  {generation.generationSource || "unknown"} · {generation.generationStatus || "unknown"}
                </span>
              </div>
            ) : null}
            {generation ? (
              <section aria-label="Transport diagnostics" className="rounded border border-linesubtle bg-raised p-2">
                <p className="font-semibold text-ink">Transport diagnostics</p>
                <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 text-meta">
                  <dt className="text-inkmuted">Network</dt>
                  <dd className="text-right text-ink">
                    {generation.networkAttempted === true
                      ? "Request attempted"
                      : generation.networkAttempted === false
                        ? "Not reached"
                        : "unknown"}
                  </dd>
                  <dt className="text-inkmuted">Requested model</dt>
                  <dd className="truncate text-right font-mono text-ink">{generation.requestedModel || current.requestedModel || "unknown"}</dd>
                  <dt className="text-inkmuted">Route</dt>
                  <dd className="text-right text-ink">{generation.routeMode || current.routeMode}</dd>
                  <dt className="text-inkmuted">Generation</dt>
                  <dd className="text-right text-ink">{generation.generationStatus || "unknown"}</dd>
                  <dt className="text-inkmuted">Provider</dt>
                  <dd className="text-right text-ink">{generation.provider && generation.provider !== "unknown" ? generation.provider : current.provider || "unknown"}</dd>
                  <dt className="text-inkmuted">Actual model</dt>
                  <dd className="truncate text-right font-mono text-ink">{generation.model && generation.model !== "unknown" ? generation.model : current.model && current.model !== "unknown" ? current.model : "unknown"}</dd>
                  <dt className="text-inkmuted">HTTP status</dt>
                  <dd className="text-right text-ink">{generation.httpStatus ?? "unknown"}</dd>
                  <dt className="text-inkmuted">HTTP attempts</dt>
                  <dd className="text-right text-ink">{generation.httpAttemptCount ?? "unknown"}</dd>
                  <dt className="text-inkmuted">HTTP responses</dt>
                  <dd className="text-right text-ink">{generation.httpResponseCount ?? "unknown"}</dd>
                  <dt className="text-inkmuted">Network phase</dt>
                  <dd className="text-right text-ink">{generation.networkPhase || "unknown"}</dd>
                  <dt className="text-inkmuted">Safe failure reason</dt>
                  <dd className="text-right text-ink">{generation.failureReason || "unknown"}</dd>
                  <dt className="text-inkmuted">Latency</dt>
                  <dd className="text-right text-ink">{formatLatency(generation.latencyMs ?? generation.attemptLatencyMs)}</dd>
                </dl>
              </section>
            ) : null}
            {generation?.generationStatus === "failed" ? (
              <section aria-label="Generation failure details" className="rounded border border-err/30 bg-err/5 p-2">
                <p className="font-semibold text-err">Answer generation failed</p>
                <p className="mt-1 text-meta text-inksecondary">
                  {generation.failureReason || "The provider attempt failed; the original cause is unavailable."}
                </p>
                <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-meta">
                  <dt className="text-inkmuted">Requested model</dt><dd className="truncate text-right font-mono text-ink">{generation.requestedModel || "unknown"}</dd>
                  <dt className="text-inkmuted">Actual model</dt><dd className="text-right text-ink">{generation.model && generation.model !== "unknown" ? generation.model : "unknown — no provider response"}</dd>
                  <dt className="text-inkmuted">Failure type</dt><dd className="text-right text-ink">{generation.failureCode || generation.errorType || "unknown"}</dd>
                  <dt className="text-inkmuted">HTTP status</dt><dd className="text-right text-ink">{generation.httpStatus ?? "unknown"}</dd>
                  <dt className="text-inkmuted">Attempt latency</dt><dd className="text-right font-mono text-ink">{formatLatency(generation.attemptLatencyMs)}</dd>
                  <dt className="text-inkmuted">Provider invocation</dt><dd className="text-right text-ink">{generation.invocationStarted === true ? "started" : generation.invocationStarted === false ? "not started" : "unknown"}</dd>
                </dl>
              </section>
            ) : null}
            {generation && (generation.fusedHits != null || generation.lexicalHits != null || generation.vectorHits != null) ? (
              <section aria-label="Retrieved evidence" className="rounded border border-linesubtle bg-raised p-2">
                <p className="font-semibold text-ink">Evidence retrieved</p>
                <p className="mt-1 text-meta text-inksecondary">
                  {generation.retrievalMode || "retrieval mode unknown"} · lexical {generation.lexicalHits ?? "unknown"} · vector {generation.vectorHits ?? "unknown"} · fused {generation.fusedHits ?? "unknown"}
                </p>
                {generation.selectedChunkCount != null ? <p className="mt-1 text-meta text-inksecondary">{generation.selectedChunkCount} selected chunks · {generation.sourceFileCount ?? "unknown"} source files</p> : null}
                {generation.selectedChunkIds?.length ? <p className="mt-1 break-all text-meta text-inksecondary">Selected chunks: {generation.selectedChunkIds.join(", ")}</p> : null}
                {generation.sourceFileIds?.length ? <p className="mt-1 break-all text-meta text-inksecondary">Sources: {generation.sourceFileIds.join(", ")}</p> : null}
                {generation.generationStatus === "failed" ? <p className="mt-1 text-meta text-inkmuted">Retrieved evidence is separate from the answer; no generated answer is available for this attempt.</p> : null}
              </section>
            ) : null}
            {/* Adapter row */}
            <div className="flex items-center justify-between">
              <span className="text-meta text-inkmuted">Adapter</span>
              <span
                className={`truncate font-mono text-meta px-1.5 py-0.5 rounded ${
                  current.adapter
                    ? "bg-raised text-ink font-medium"
                    : isBase
                    ? "bg-raised text-inkmuted"
                    : "text-inksecondary"
                }`}
              >
                {current.adapter
                  ? current.adapter
                  : isBase
                  ? "BASE (None)"
                  : isCloud
                  ? "NONE"
                  : "standard"}
              </span>
            </div>

            {/* Marketing profile row (cloud behavior profile only) */}
            {profile ? (
              <div className="flex items-center justify-between">
                <span className="text-meta text-inkmuted">
                  Marketing Profile
                </span>
                <span className="truncate font-mono text-meta px-1.5 py-0.5 rounded bg-raised text-ink font-medium">
                  {profile}
                </span>
              </div>
            ) : null}

            {/* Route row */}
            <div className="flex items-start justify-between gap-2">
              <span className="shrink-0 text-meta text-inkmuted">Route</span>
              <span className="text-right text-meta text-inksecondary">
                <span className="font-semibold text-ink">
                  {line(current.route, current.routeMode)}
                </span>
                {current.routeReason ? ` · ${current.routeReason}` : ""}
              </span>
            </div>

            {/* Latency */}
            <div className="flex items-center justify-between">
              <span className="text-meta text-inkmuted">Latency</span>
              <span className="font-mono text-meta text-ink">
                {formatLatency(current.latencyMs)}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-meta text-inkmuted">HTTP attempts</span>
              <span className="font-mono text-meta text-ink">
                {current.httpAttemptCount ?? "unknown"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-meta text-inkmuted">HTTP status</span>
              <span className="font-mono text-meta text-ink">
                {current.httpStatus ?? "unknown"}
              </span>
            </div>

            {/* Token Usage Breakdown */}
            <div className="rounded border border-linesubtle bg-raised p-2">
              <div className="mb-1 text-meta font-medium text-inksecondary">
                Token Usage
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-meta">
                <div className="flex justify-between">
                  <span className="text-inkmuted">Input:</span>
                  <span className="font-mono text-ink">
                    {formatTokens(current.inputTokens)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-inkmuted">Output:</span>
                  <span className="font-mono text-ink">
                    {formatTokens(current.outputTokens)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-inkmuted">Cached:</span>
                  <span className="font-mono text-ink">
                    {formatTokens(current.cachedTokens)}
                  </span>
                </div>
                <div className="flex justify-between border-t border-linesubtle pt-0.5 font-medium">
                  <span className="text-inksecondary">Total:</span>
                  <span className="font-mono text-ink">
                    {formatTokens(current.totalTokens)}
                  </span>
                </div>
              </div>
            </div>

            {/* Cost & Compute Note */}
            <div className="border-t border-linesubtle pt-2">
              <div className="flex items-center justify-between text-meta">
                <span className="text-inkmuted">Compute Cost</span>
                <span className="font-mono font-medium text-ok">
                  {formatCost(current.estimatedCostUsd, isLocal)}
                </span>
              </div>
              <p className="mt-1 text-meta text-inkmuted italic leading-tight">
                {current.costNote ||
                  (isLocal
                    ? "local API cost $0.00; local compute cost is not metered"
                    : "cloud metered API usage")}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
