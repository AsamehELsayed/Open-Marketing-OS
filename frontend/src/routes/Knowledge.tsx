import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import EmptyState from "../components/chrome/EmptyState";
import PageHeader from "../components/chrome/PageHeader";
import { useProject } from "../components/shell/project-context";
import { listOf, str } from "../components/workspace/format";

/**
 * DEV-004 W5: "Project Knowledge" — GET /api/knowledge rendered as
 * Company | Audience | Offers | Positioning | Research | Learnings | Files.
 * User-facing names only; no index/storage jargon anywhere (the API already
 * excludes chroma/fts/chunk/embedding internals — this view adds none).
 */

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded border border-linedefault bg-surface p-4">
      <h2 className="text-h3 font-semibold text-ink">{title}</h2>
      <p className="mt-0.5 text-meta text-inkmuted">{hint}</p>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function ItemList({ items }: { items: Record<string, unknown>[] }) {
  if (items.length === 0) {
    return (
      <p className="text-bodysm text-inksecondary">Nothing recorded yet.</p>
    );
  }
  return (
    <ul className="list-disc space-y-1.5 pl-5 text-bodysm text-ink">
      {items.map((m, i) => (
        <li key={i}>
          {str(m.body_md).trim() || "—"}
          {(str(m.source).trim() || str(m.confidence).trim()) && (
            <span className="text-inkmuted">
              {" "}
              — {str(m.source).trim() || str(m.confidence).trim()}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function Knowledge() {
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
      .getKnowledge(projectId)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(null);
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(
            e instanceof Error ? e.message : "failed to load knowledge",
          );
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const company = useMemo(
    () =>
      data && typeof data.company === "object" && data.company !== null
        ? (data.company as Record<string, unknown>)
        : null,
    [data],
  );
  const audience = useMemo(() => listOf(data?.audience), [data]);
  const offers = useMemo(() => listOf(data?.offers), [data]);
  const positioning = useMemo(() => listOf(data?.positioning), [data]);
  const research = useMemo(() => listOf(data?.research), [data]);
  const learnings = useMemo(() => listOf(data?.learnings), [data]);
  const files = useMemo(() => listOf(data?.files), [data]);

  return (
    <div>
      <PageHeader
        title="Project Knowledge"
        description="What the project knows about the company, its audience, and what works."
      />
      <div className="px-6 py-4">
        {loading ? (
          <p className="text-bodysm text-inksecondary">Loading knowledge…</p>
        ) : error ? (
          <p className="text-bodysm text-err">{error}</p>
        ) : !data ? (
          <EmptyState
            title="No knowledge yet"
            hint="Knowledge for this project will appear here once added."
          />
        ) : (
          <div className="grid max-w-3xl gap-3">
            <Section title="Company" hint="Who this project is for.">
              {!company ? (
                <p className="text-bodysm text-inksecondary">
                  No company details recorded.
                </p>
              ) : (
                <dl className="text-bodysm">
                  {str(company.name) && (
                    <div className="py-0.5">
                      <dt className="inline text-inkmuted">Name: </dt>
                      <dd className="inline text-ink">{str(company.name)}</dd>
                    </div>
                  )}
                  {str(company.website) && (
                    <div className="py-0.5">
                      <dt className="inline text-inkmuted">Website: </dt>
                      <dd className="inline text-ink">
                        {str(company.website)}
                      </dd>
                    </div>
                  )}
                  {str(company.goal) && (
                    <div className="py-0.5">
                      <dt className="inline text-inkmuted">Goal: </dt>
                      <dd className="inline text-ink">{str(company.goal)}</dd>
                    </div>
                  )}
                </dl>
              )}
            </Section>
            <Section title="Audience" hint="Who we are trying to reach.">
              <ItemList items={audience} />
            </Section>
            <Section title="Offers" hint="What we sell and why it matters.">
              <ItemList items={offers} />
            </Section>
            <Section
              title="Positioning"
              hint="How we describe ourselves in the market."
            >
              <ItemList items={positioning} />
            </Section>
            <Section
              title="Research"
              hint="Decisions and findings from past work."
            >
              <ItemList items={research} />
            </Section>
            <Section title="Learnings" hint="What past work taught us.">
              <ItemList items={learnings} />
            </Section>
            <Section title="Files" hint="Documents attached to this project.">
              {files.length === 0 ? (
                <p className="text-bodysm text-inksecondary">
                  No files attached yet.
                </p>
              ) : (
                <ul className="space-y-1.5 text-bodysm text-ink">
                  {files.map((f, i) => (
                    <li
                      key={i}
                      className="flex items-center justify-between gap-2"
                    >
                      <span className="truncate">
                        {str(f.name).trim() || "Unnamed file"}
                      </span>
                      {str(f.status).trim() && (
                        <span className="shrink-0 text-meta text-inkmuted">
                          {str(f.status).trim()}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          </div>
        )}
      </div>
    </div>
  );
}
