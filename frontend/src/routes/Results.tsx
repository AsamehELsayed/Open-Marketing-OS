import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import EmptyState from "../components/chrome/EmptyState";
import PageHeader from "../components/chrome/PageHeader";
import { useProject } from "../components/shell/project-context";
import { listOf, str, stringsOf } from "../components/workspace/format";

/**
 * DEV-004 W5: results summary — GET /api/results/summary as three answer
 * cards (What happened? / What did we learn? / What do we do next?).
 *
 * Deliberately cards-only: no chart library is installed (only
 * marked+dompurify), and per-status distribution is shown as trivial CSS
 * width bars computed from the real experiments_by_status counts.
 */

function Card({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded border border-linedefault bg-surface p-4">
      <h2 className="text-h3 font-semibold text-ink">{title}</h2>
      <div className="mt-2">{children}</div>
    </section>
  );
}

export default function Results() {
  const { projectId } = useProject();
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) {
      setData(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .getResultsSummary(projectId)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(null);
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "failed to load results");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const counts = useMemo(() => {
    const c =
      data && typeof data.counts === "object" && data.counts !== null
        ? (data.counts as Record<string, unknown>)
        : {};
    const byStatus =
      typeof c.experiments_by_status === "object" && c.experiments_by_status
        ? (c.experiments_by_status as Record<string, unknown>)
        : {};
    return {
      experiments: typeof c.experiments === "number" ? c.experiments : 0,
      measurements: typeof c.measurements === "number" ? c.measurements : 0,
      learnings: typeof c.learnings === "number" ? c.learnings : 0,
      byStatus: Object.entries(byStatus).map(([k, v]) => ({
        status: k || "unknown",
        count: typeof v === "number" ? v : 0,
      })),
    };
  }, [data]);
  const learnings = useMemo(() => listOf(data?.learnings), [data]);
  const decisions = useMemo(() => stringsOf(data?.decisions), [data]);
  const empty = !!data && counts.experiments === 0 && counts.measurements === 0 && counts.learnings === 0;

  const maxStatus = Math.max(1, ...counts.byStatus.map((s) => s.count));

  return (
    <div>
      <PageHeader
        title="Results"
        description="What happened, what we learned, and what to do next."
      />
      <div className="px-6 py-4">
        {loading ? (
          <p className="text-bodysm text-inksecondary">Loading results…</p>
        ) : error ? (
          <p className="text-bodysm text-err">{error}</p>
        ) : !data || empty ? (
          <EmptyState
            title="No results yet"
            hint="Your Account Manager can plan work and propose experiments. Results will appear here when there is real measurement or learning evidence."
            action={<Link to="/app" className="rounded-sm border border-linedefault px-3 py-1.5 text-bodysm text-ink hover:bg-elevated">Open Account Manager</Link>}
          />
        ) : (
          <div className="grid max-w-3xl gap-3">
            <Card title="What happened?">
              <p className="text-bodysm text-inksecondary">
                {counts.experiments} experiments · {counts.measurements}{" "}
                measurements · {counts.learnings} learnings
              </p>
              {counts.byStatus.length > 0 && (
                <ul className="mt-3 space-y-1.5">
                  {counts.byStatus.map((s) => (
                    <li key={s.status} className="flex items-center gap-2">
                      <span className="w-28 shrink-0 truncate text-bodysm text-ink">
                        {s.status}
                      </span>
                      <span
                        className="h-2 rounded-sm bg-accent/70"
                        style={{
                          width: `${Math.max(4, Math.round((s.count / maxStatus) * 120))}px`,
                        }}
                        aria-hidden="true"
                      />
                      <span className="text-meta text-inkmuted">{s.count}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card title="What did we learn?">
              {learnings.length === 0 ? (
                <p className="text-bodysm text-inksecondary">
                  No learnings recorded yet.
                </p>
              ) : (
                <ul className="list-disc space-y-1.5 pl-5 text-bodysm text-ink">
                  {learnings.map((l, i) => (
                    <li key={i}>
                      {str(l.body_md).trim() || "—"}
                      {(str(l.source).trim() || str(l.confidence).trim()) && (
                        <span className="text-inkmuted">
                          {" "}
                          — {str(l.source).trim() || str(l.confidence).trim()}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card title="What do we do next?">
              {decisions.length === 0 ? (
                <p className="text-bodysm text-inksecondary">
                  No evidence-backed recommendation recorded yet.
                </p>
              ) : (
                <ul className="list-disc space-y-1.5 pl-5 text-bodysm text-ink">
                  {decisions.map((d) => (
                    <li key={d}>{d}</li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
