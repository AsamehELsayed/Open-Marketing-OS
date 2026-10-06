import { Link } from "react-router-dom";
import StatusBadge from "../chrome/StatusBadge";
import { excerptOf, score, str } from "./format";

export interface CampaignCounts {
  prospects: number;
  tasks: number;
  experiments: number;
}

/**
 * DEV-004 W5: campaign list card — title, status, goal excerpt, counts.
 * No giant tables. Detail counts are optional (the list endpoint returns
 * repo-field rows only, so the list page passes none).
 */
export default function CampaignCard({
  campaign,
  counts,
}: {
  campaign: Record<string, unknown>;
  counts?: CampaignCounts;
}) {
  const id = str(campaign.id);
  const title = str(campaign.title).trim() || "Untitled campaign";
  const status = str(campaign.status).trim() || "unknown";
  // Repo rows carry no `goal` field; impact/result text is the closest
  // human-readable summary — labelled as focus, never invented.
  const focus = excerptOf(campaign, ["result", "measurement_window"]);
  const scores = (
    [
      ["Impact", score(campaign.impact)],
      ["Confidence", score(campaign.confidence)],
      ["Effort", score(campaign.effort)],
    ] as const
  ).filter(([, v]) => v !== null);

  return (
    <Link
      to={`/app/campaigns/${encodeURIComponent(id)}`}
      className="block rounded border border-linedefault bg-surface p-4 hover:border-accent/50"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="min-w-0 flex-1 truncate text-body font-semibold text-ink">
          {title}
        </h3>
        <StatusBadge status={status} />
      </div>
      {focus && (
        <p className="mt-1.5 line-clamp-2 text-bodysm text-inksecondary">
          {focus}
        </p>
      )}
      <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-meta text-inkmuted">
        {scores.map(([label, v]) => (
          <span key={label}>
            {label} {v}
          </span>
        ))}
        {counts && (
          <span>
            {counts.prospects} prospects · {counts.tasks} tasks ·{" "}
            {counts.experiments} experiments
          </span>
        )}
      </div>
    </Link>
  );
}
