import { useCallback, useEffect, useState } from "react";
import {
  downloadLocalModel,
  formatLocalBytes,
  getLocalRuntime,
  localLifecycleLabel,
  startLocalRuntime,
  stopLocalRuntime,
} from "../../api/localRuntime";
import type { LocalRuntimeStatus } from "../../api/client";

function isBusy(status: LocalRuntimeStatus | null): boolean {
  return Boolean(status && (
    status.download_state === "downloading" ||
    status.verification_state === "verifying" ||
    status.runtime_state === "starting"
  ));
}

function StateRow({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-4">
      <dt className="text-meta text-inksecondary">{label}</dt>
      <dd className="text-meta font-medium text-ink sm:text-right">{localLifecycleLabel(value)}</dd>
    </div>
  );
}

/** Local download is deliberately started only by the Download Model button. */
export default function LocalModelSetup() {
  const [status, setStatus] = useState<LocalRuntimeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [actionFailed, setActionFailed] = useState(false);
  const [actionPending, setActionPending] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getLocalRuntime());
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (!isBusy(status)) return undefined;
    const timer = window.setInterval(() => { void refresh(); }, 1500);
    return () => window.clearInterval(timer);
  }, [status, refresh]);

  const runAction = async (action: () => Promise<LocalRuntimeStatus>) => {
    setActionPending(true);
    setActionFailed(false);
    try {
      setStatus(await action());
      setLoadFailed(false);
    } catch {
      // Keep server details and local filesystem paths out of the settings UI.
      setActionFailed(true);
      await refresh();
    } finally {
      setActionPending(false);
    }
  };

  const downloadActive = status?.download_state === "downloading" || status?.verification_state === "verifying";
  const modelVerified = status?.verification_state === "verified";
  const isReady = status?.runtime_state === "ready";
  const percent = status && status.download_total_bytes > 0
    ? Math.min(100, Math.max(0, Math.round(status.downloaded_bytes * 100 / status.download_total_bytes)))
    : null;

  return (
    <section className="rounded border border-linedefault bg-surface p-5" aria-labelledby="local-model-title">
      <h2 id="local-model-title" className="text-h3 font-semibold text-ink">Local model setup</h2>
      <p className="mt-1 max-w-3xl text-bodysm text-inksecondary">
        Review the pinned model details below. Model bytes are downloaded only when you choose Download Model.
        Starting the runtime never downloads a model.
      </p>

      {loading && <p className="mt-4 text-bodysm text-inksecondary" role="status">Loading Local model status…</p>}
      {loadFailed && (
        <div className="mt-4 rounded border border-err/40 bg-err/10 p-3 text-bodysm text-err" role="alert">
          Local model status could not be loaded. Check the application connection and retry.
          <button type="button" onClick={() => { setLoading(true); void refresh(); }} className="ml-2 underline">Retry</button>
        </div>
      )}

      {status && (
        <>
          <dl className="mt-5 grid gap-x-8 gap-y-3 rounded border border-linedefault bg-elevated p-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <dt className="text-meta text-inksecondary">Model</dt>
              <dd className="mt-0.5 text-bodysm font-semibold text-ink">{status.display_name}</dd>
              <dd className="mt-0.5 break-all font-mono text-meta text-inksecondary">{status.model_id}</dd>
            </div>
            <div>
              <dt className="text-meta text-inksecondary">Source / publisher</dt>
              <dd className="mt-0.5 break-words text-meta text-ink">{status.source} · {status.publisher}</dd>
            </div>
            <div>
              <dt className="text-meta text-inksecondary">License</dt>
              <dd className="mt-0.5 text-meta text-ink">
                {status.license}{" "}
                <a href={status.license_url} target="_blank" rel="noreferrer" className="text-accent underline">
                  View license and notice
                </a>
              </dd>
            </div>
            <div>
              <dt className="text-meta text-inksecondary">Download size</dt>
              <dd className="mt-0.5 text-meta text-ink">{formatLocalBytes(status.model_bytes)} ({status.model_bytes.toLocaleString()} bytes)</dd>
            </div>
            <div>
              <dt className="text-meta text-inksecondary">Temporary disk space required</dt>
              <dd className="mt-0.5 text-meta text-ink">{formatLocalBytes(status.temporary_disk_bytes)} ({status.temporary_disk_bytes.toLocaleString()} bytes)</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-meta text-inksecondary">Expected SHA-256</dt>
              <dd className="mt-0.5 break-all font-mono text-meta text-ink">{status.sha256}</dd>
            </div>
          </dl>

          <div className="mt-4 rounded border border-linedefault p-4">
            <h3 className="text-bodysm font-semibold text-ink">Download and runtime status</h3>
            <dl className="mt-3 space-y-2" aria-live="polite" aria-atomic="true">
              <StateRow label="Download" value={status.download_state} />
              <StateRow label="Checksum verification" value={status.verification_state} />
              <StateRow label="Model activation" value={status.activation_state} />
              <StateRow label="Local runtime" value={status.runtime_state} />
            </dl>
            {status.observed_model_id && (
              <p className="mt-2 break-all text-meta text-inksecondary">
                Runtime model identity: <code>{status.observed_model_id}</code>
              </p>
            )}
            {downloadActive && percent !== null && (
              <div className="mt-3">
                <label htmlFor="local-model-download-progress" className="block text-meta text-inksecondary">
                  Model download progress: {percent}%
                </label>
                <progress id="local-model-download-progress" className="mt-1 w-full" max={100} value={percent} />
              </div>
            )}
            {status.error_code && (
              <p className="mt-3 text-meta text-err" role="status">Status code: {status.error_code.replace(/[^a-zA-Z0-9_-]/g, " ")}</p>
            )}
          </div>

          {actionFailed && <p className="mt-3 text-bodysm text-err" role="alert">The requested Local model action failed. Review the status above and try again.</p>}

          <div className="mt-4 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => { void runAction(downloadLocalModel); }}
              disabled={actionPending || downloadActive || modelVerified}
              className="rounded bg-accent px-4 py-2 text-bodysm font-medium text-white hover:bg-accenthover disabled:cursor-not-allowed disabled:opacity-50"
            >
              {downloadActive ? "Downloading Model…" : modelVerified ? "Model Verified" : "Download Model"}
            </button>
            {!isReady ? (
              <button
                type="button"
                onClick={() => { void runAction(startLocalRuntime); }}
                disabled={actionPending || status.activation_state !== "active" || status.verification_state !== "verified"}
                className="rounded border border-linedefault px-4 py-2 text-bodysm font-medium text-ink disabled:cursor-not-allowed disabled:opacity-50"
              >
                {status.runtime_state === "starting" ? "Starting Runtime…" : "Start Local Runtime"}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => { void runAction(stopLocalRuntime); }}
                disabled={actionPending}
                className="rounded border border-linedefault px-4 py-2 text-bodysm font-medium text-ink disabled:cursor-not-allowed disabled:opacity-50"
              >
                Stop Local Runtime
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
