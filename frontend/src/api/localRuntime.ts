import { api } from "./client.ts";
import type { LocalRuntimeStatus } from "./client.ts";

/** Typed wrappers for the managed Local runtime's sanitized SPA contract. */
export const getLocalRuntime = (): Promise<LocalRuntimeStatus> => api.getLocalRuntime();
export const downloadLocalModel = (): Promise<LocalRuntimeStatus> => api.downloadLocalModel();
export const startLocalRuntime = (): Promise<LocalRuntimeStatus> => api.startLocalRuntime();
export const stopLocalRuntime = (): Promise<LocalRuntimeStatus> => api.stopLocalRuntime();

export function localLifecycleLabel(state: string | null | undefined, fallback = "Unknown"): string {
  if (!state) return fallback;
  const labels: Record<string, string> = {
    not_started: "Not downloaded",
    downloading: "Downloading",
    downloaded: "Downloaded; awaiting verification",
    not_verified: "Not verified",
    verifying: "Verifying checksum",
    verified: "Checksum verified",
    inactive: "Not activated",
    active: "Activated",
    stopped: "Stopped",
    starting: "Starting",
    ready: "Ready",
    failed: "Failed",
  };
  return labels[state.toLowerCase()] ?? state.replace(/[_-]+/g, " ");
}

export function formatLocalBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "Unavailable";
  if (bytes < 1024) return `${bytes} bytes`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)} ${units[unit]}`;
}
