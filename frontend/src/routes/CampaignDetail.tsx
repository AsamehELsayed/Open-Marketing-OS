import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import EmptyState from "../components/chrome/EmptyState";
import PageHeader from "../components/chrome/PageHeader";
import StatusBadge from "../components/chrome/StatusBadge";
import Tabs from "../components/chrome/Tabs";
import {
  excerptOf,
  listOf,
  optionalScore,
  str,
} from "../components/workspace/format";

/**
 * DEV-004 W5: campaign detail — header (title, status, goal) + six tabs.
 * Renders whatever the API returns defensively: user-friendly subsets only,
 * raw ids / JSON never shown. Tabs with no backing endpoint (Content, Files)
 * render honest EmptyStates instead of fake data.
 *
 * Tab → data mapping (documented, no dedicated content/files endpoint exists
 * in the W1 contract): Overview = campaign fields; Prospects = prospects;
 * Content = none yet; Activity = tasks; Results = experiments; Files = none yet.
 */

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "prospects", label: "Prospects" },
  { id: "content", label: "Content" },
  { id: "activity", label: "Activity" },
  { id: "results", label: "Results" },
  { id: "files", label: "Files" },
];

function Field({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div className="grid grid-cols-[140px_1fr] gap-2 py-1.5 text-bodysm">
      <dt className="text-inkmuted">{label}</dt>
      <dd className="text-ink">{value}</dd>
    </div>
  );
}

function isHttpUrl(s: string): boolean {
  return /^https?:\/\//i.test(s);
}

export default function CampaignDetail() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState("overview");

  useEffect(() => {
    if (!id) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .getCampaign(id)
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
            e instanceof Error ? e.message : "failed to load campaign",
          );
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const campaign = useMemo(
    () =>
      data && typeof data.campaign === "object" && data.campaign !== null
        ? (data.campaign as Record<string, unknown>)
        : null,
    [data],
  );
  const prospects = useMemo(() => listOf(data?.prospects), [data]);
  const tasks = useMemo(() => listOf(data?.tasks), [data]);
  const experiments = useMemo(() => listOf(data?.experiments), [data]);

  if (loading) {
    return (
      <div>
        <PageHeader title="Campaign" />
        <p className="px-6 py-4 text-bodysm text-inksecondary">
          Loading campaign…
        </p>
      </div>
    );
  }
  if (error || !campaign) {
    return (
      <div>
        <PageHeader title="Campaign" />
        <div className="px-6 py-4">
          <p className="text-bodysm text-err">{error ?? "campaign not found"}</p>
        </div>
      </div>
    );
  }

  const title = str(campaign.title).trim() || "Untitled campaign";
  const rawStatus = str(campaign.status).trim() || "unknown";
  const status = ["drafted", "proposed"].includes(rawStatus.toLowerCase())
    ? "Draft"
    : rawStatus;
  const goal = excerptOf(campaign, ["result", "measurement_window"], 240);
  const campaignScores = [
    ["impact", optionalScore(campaign.impact)],
    ["confidence", optionalScore(campaign.confidence)],
    ["effort", optionalScore(campaign.effort)],
    ["cost", optionalScore(campaign.cost)],
  ]
    .filter(([, value]) => value !== null)
    .map(([label, value]) => `${label} ${String(value)}`);

  return (
    <div>
      <PageHeader
        title={title}
        description={goal || undefined}
        actions={<StatusBadge status={status} />}
      />
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      <div className="max-w-3xl px-6 py-4">
        {tab === "overview" && (
          <dl className="divide-y divide-linesubtle rounded border border-linedefault bg-surface px-4 py-2">
            <Field label="Status" value={status} />
            <Field label="Approval level" value={str(campaign.approval_level)} />
            <Field
              label="Scores"
              value={campaignScores.length ? campaignScores.join(" · ") : "Not set"}
            />
            <Field
              label="Measurement window"
              value={str(campaign.measurement_window)}
            />
            <Field label="Result" value={str(campaign.result)} />
            <Field label="Updated" value={str(campaign.updated_at)} />
          </dl>
        )}

        {tab === "prospects" &&
          (prospects.length === 0 ? (
            <EmptyState
              title="No prospects yet"
              hint="Prospects added to this campaign will appear here."
            />
          ) : (
            <ul className="grid gap-3">
              {prospects.map((p, i) => {
                const name = str(p.name).trim() || "Unnamed prospect";
                const pStatus = str(p.status).trim();
                const url = str(p.evidence_url).trim();
                const notes = excerptOf(p, ["notes"]);
                return (
                  <li
                    key={str(p.id) || String(i)}
                    className="rounded border border-linedefault bg-surface p-4"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-body font-medium text-ink">
                        {name}
                      </span>
                      {pStatus && <StatusBadge status={pStatus} />}
                    </div>
                    {url && isHttpUrl(url) && (
                      <a
                        href={url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-1 block truncate text-bodysm text-accenthover"
                      >
                        {url}
                      </a>
                    )}
                    {notes && (
                      <p className="mt-1.5 text-bodysm text-inksecondary">
                        {notes}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          ))}

        {tab === "content" && (
          <EmptyState
            title="No content yet"
            hint="Content drafts for this campaign will appear here once created."
          />
        )}

        {tab === "activity" &&
          (tasks.length === 0 ? (
            <EmptyState
              title="No activity yet"
              hint="Tasks for this campaign will appear here once created."
            />
          ) : (
            <ul className="grid gap-3">
              {tasks.map((t, i) => {
                const tTitle = str(t.title).trim() || "Untitled task";
                const tStatus = str(t.status).trim();
                const lane = str(t.lane).trim();
                const acceptance = excerptOf(t, ["acceptance"]);
                const due = str(t.due_at).trim();
                return (
                  <li
                    key={str(t.id) || String(i)}
                    className="rounded border border-linedefault bg-surface p-4"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-body font-medium text-ink">
                        {tTitle}
                      </span>
                      {tStatus && <StatusBadge status={tStatus} />}
                    </div>
                    <p className="mt-1 text-meta text-inkmuted">
                      {[lane && `lane: ${lane}`, due && `due: ${due}`]
                        .filter(Boolean)
                        .join(" · ") || " "}
                    </p>
                    {acceptance && (
                      <p className="mt-1.5 text-bodysm text-inksecondary">
                        {acceptance}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          ))}

        {tab === "results" &&
          (experiments.length === 0 ? (
            <EmptyState
              title="No experiments yet"
              hint="Experiments linked to this campaign will appear here once created."
            />
          ) : (
            <ul className="grid gap-3">
              {experiments.map((e, i) => {
                const hypothesis =
                  excerptOf(e, ["hypothesis"], 200) || "Untitled experiment";
                const eStatus = str(e.status).trim();
                const metric = str(e.metric).trim();
                const stop = excerptOf(e, ["stop_condition"]);
                return (
                  <li
                    key={str(e.id) || String(i)}
                    className="rounded border border-linedefault bg-surface p-4"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-body font-medium text-ink">
                        {hypothesis}
                      </span>
                      {eStatus && <StatusBadge status={eStatus} />}
                    </div>
                    {(metric || stop) && (
                      <p className="mt-1 text-bodysm text-inksecondary">
                        {[metric && `metric: ${metric}`, stop && `stop: ${stop}`]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          ))}

        {tab === "files" && (
          <EmptyState
            title="No files yet"
            hint="Files attached to this campaign will appear here once added."
          />
        )}
      </div>
    </div>
  );
}
