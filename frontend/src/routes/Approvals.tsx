import { useEffect, useMemo, useState } from "react";
import { api, type SpaApproval } from "../api/client";
import ApprovalCard from "../components/activity/ApprovalCard";
import EmptyState from "../components/chrome/EmptyState";
import PageHeader from "../components/chrome/PageHeader";
import Tabs from "../components/chrome/Tabs";
import { useProject } from "../components/shell/project-context";

/**
 * DEV-004 W4: central approvals queue — GET /api/approvals?project_id=,
 * ApprovalCards with a status filter and empty states.
 *
 * DEV-005 W7: cards resume via `POST /api/approvals/{id}/resume` (W6)
 * with a stable idempotency key; `onDecided` refreshes the row status
 * locally so approve/reject is visible without a reload.
 */

const FILTERS = [
  { id: "all", label: "All" },
  { id: "pending", label: "Pending" },
  { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
];

export default function Approvals() {
  const { projectId } = useProject();
  const [approvals, setApprovals] = useState<SpaApproval[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    if (!projectId) {
      setApprovals([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .getApprovals({ project_id: projectId })
      .then((rows) => {
        if (!cancelled) {
          setApprovals(rows);
          setError(null);
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "failed to load approvals");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const shown = useMemo(
    () =>
      filter === "all"
        ? approvals
        : approvals.filter(
            (a) => (a.status || "").toLowerCase() === filter,
          ),
    [approvals, filter],
  );

  return (
    <div>
      <PageHeader
        title="Approvals"
        description="Items waiting for your decision."
      />
      <Tabs tabs={FILTERS} active={filter} onChange={setFilter} />
      <div className="px-6 py-4">
        {loading ? (
          <p className="text-bodysm text-inksecondary">Loading approvals…</p>
        ) : error ? (
          <p className="text-bodysm text-err">{error}</p>
        ) : shown.length === 0 ? (
          <EmptyState
            title={
              filter === "all" ? "No approvals yet" : `No ${filter} approvals`
            }
            hint={
              filter === "pending"
                ? "Nothing is waiting for review right now."
                : "Approvals for this project will appear here."
            }
          />
        ) : (
          <div className="grid max-w-3xl gap-3">
            {shown.map((a) => (
              <ApprovalCard
                key={a.id}
                approval={a}
                onDecided={(id, decision) =>
                  setApprovals((prev) =>
                    prev.map((row) =>
                      row.id === id
                        ? {
                            ...row,
                            status:
                              decision === "approved" ? "approved" : "rejected",
                          }
                        : row,
                    ),
                  )
                }
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
