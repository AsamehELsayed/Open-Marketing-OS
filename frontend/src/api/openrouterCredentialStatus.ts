export type OpenRouterCredentialReadiness =
  | "not_configured"
  | "configured"
  | "missing"
  | "unreadable"
  | "empty";

/** Project backend readiness into safe UI state; historical health/connected
 * booleans are deliberately ignored because they do not prove decryption. */
export function projectOpenRouterCredentialStatus(value: unknown): {
  status: OpenRouterCredentialReadiness;
  connected: boolean;
  needsReconfiguration: boolean;
} {
  const candidate = typeof value === "string" ? value.trim().toLowerCase() : "";
  const status: OpenRouterCredentialReadiness =
    candidate === "not_configured" || candidate === "configured" ||
    candidate === "missing" || candidate === "unreadable" || candidate === "empty"
      ? candidate
      : "unreadable";
  return {
    status,
    connected: status === "configured",
    needsReconfiguration: status === "missing" || status === "unreadable" || status === "empty",
  };
}
