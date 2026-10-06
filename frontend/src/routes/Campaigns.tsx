import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import EmptyState from "../components/chrome/EmptyState";
import PageHeader from "../components/chrome/PageHeader";
import { useProject } from "../components/shell/project-context";
import CampaignCard from "../components/workspace/CampaignCard";

/** DEV-004 W5: campaign list — GET /api/campaigns?project_id=, cards, empty state. */
export default function Campaigns() {
  const { projectId } = useProject();
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) {
      setRows([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .getCampaigns(projectId)
      .then((list) => {
        if (!cancelled) {
          setRows(list);
          setError(null);
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(
            e instanceof Error ? e.message : "failed to load campaigns",
          );
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  return (
    <div>
      <PageHeader
        title="Campaigns"
        description="Outreach campaigns for this project."
      />
      <div className="px-6 py-4">
        {loading ? (
          <p className="text-bodysm text-inksecondary">Loading campaigns…</p>
        ) : error ? (
          <p className="text-bodysm text-err">{error}</p>
        ) : rows.length === 0 ? (
          <EmptyState
            title="No campaigns yet"
            hint="Your Account Manager creates campaigns when you ask it to plan and coordinate marketing work."
            action={<Link to="/app" className="rounded-sm border border-linedefault px-3 py-1.5 text-bodysm text-ink hover:bg-elevated">Open Account Manager</Link>}
          />
        ) : (
          <div className="grid max-w-3xl gap-3">
            {rows.map((c, i) => (
              <CampaignCard
                key={String(c.id ?? i)}
                campaign={c}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
