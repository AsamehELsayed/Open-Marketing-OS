import { useEffect, useState } from "react";
import { api, type SpaJob } from "../api/client";
import EmptyState from "../components/chrome/EmptyState";
import PageHeader from "../components/chrome/PageHeader";
import JobCard from "../components/activity/JobCard";
import { useProject } from "../components/shell/project-context";

/**
 * DEV-004 W4: jobs list route — GET /api/jobs?project_id=, JobCards,
 * active-count badge, 5s badge polling only while active jobs exist.
 */

const ACTIVE = new Set([
  "queued",
  "running",
  "executing",
  "pending",
  "measuring",
  "in_progress",
  "started",
]);

export function hasActiveJobs(jobs: SpaJob[]): boolean {
  return jobs.some((j) => ACTIVE.has((j.status || "").toLowerCase()));
}

export default function Jobs() {
  const { projectId } = useProject();
  const [jobs, setJobs] = useState<SpaJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const active = hasActiveJobs(jobs);
  const activeCount = jobs.filter((j) =>
    ACTIVE.has((j.status || "").toLowerCase()),
  ).length;

  // Initial load per project.
  useEffect(() => {
    if (!projectId) {
      setJobs([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .getJobs(projectId)
      .then((rows) => {
        if (!cancelled) {
          setJobs(rows);
          setError(null);
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "failed to load jobs");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Badge polling: refresh every 5s ONLY while active jobs exist.
  useEffect(() => {
    if (!projectId || !active) return;
    const t = window.setInterval(() => {
      api
        .getJobs(projectId)
        .then(setJobs)
        .catch(() => {
          /* keep last-known list on poll failure */
        });
    }, 5000);
    return () => window.clearInterval(t);
  }, [projectId, active]);

  return (
    <div>
      <PageHeader
        title="Jobs"
        description="Background work for this project."
        actions={
          activeCount > 0 ? (
            <span
              className="rounded-sm border border-accent/40 px-2 py-0.5 text-meta font-medium text-accenthover"
              aria-label={`${activeCount} active jobs`}
            >
              {activeCount} active
            </span>
          ) : undefined
        }
      />
      <div className="px-6 py-4">
        {loading ? (
          <p className="text-bodysm text-inksecondary">Loading jobs…</p>
        ) : error ? (
          <p className="text-bodysm text-err">{error}</p>
        ) : jobs.length === 0 ? (
          <EmptyState
            title="No jobs yet"
            hint="Background jobs for this project will appear here."
          />
        ) : (
          <div className="grid max-w-3xl gap-3">
            {jobs.map((j) => (
              <JobCard key={j.id} job={j} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
