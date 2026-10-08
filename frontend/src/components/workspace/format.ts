/** DEV-004 W5: defensive readers for repo-field JSON (Record<string, unknown>). */

export function str(v: unknown): string {
  if (typeof v === "string") return v;
  if (v === null || v === undefined) return "";
  return String(v);
}

export function score(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Campaign rows use zero as the database default for scores not yet supplied. */
export function optionalScore(v: unknown): number | null {
  const value = score(v);
  return value === 0 ? null : value;
}

export function listOf(v: unknown): Record<string, unknown>[] {
  return Array.isArray(v)
    ? v.filter(
        (r): r is Record<string, unknown> =>
          typeof r === "object" && r !== null,
      )
    : [];
}

/** First non-empty string among candidates, truncated for card display. */
export function excerptOf(
  row: Record<string, unknown>,
  keys: string[],
  max = 160,
): string {
  for (const k of keys) {
    const s = str(row[k]).trim();
    if (s) return s.length > max ? `${s.slice(0, max - 1)}…` : s;
  }
  return "";
}

/** Plain string list (e.g. measurement decisions). */
export function stringsOf(v: unknown): string[] {
  return Array.isArray(v)
    ? v.map(str).map((s) => s.trim()).filter(Boolean)
    : [];
}
