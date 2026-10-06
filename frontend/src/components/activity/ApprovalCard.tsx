import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { SpaApproval } from "../../api/client";
import { idempotencyKeyFor } from "../../api/runtime";
import {
  resumeApproval,
  type ApprovalDecision,
} from "../../api/runtimeClient";

/**
 * DEV-005 W7: approval card with resume UX.
 *
 * Approve / Reject call the W6 JSON endpoint
 * `POST /api/approvals/{id}/resume` (W0 `ApprovalResume` shape) with a
 * STABLE idempotency key derived per approval id — retries reuse the same
 * key so resume/replay cannot duplicate side effects. While W6 is not
 * deployed (404/501) the card falls back to the legacy `/approvals`
 * queue link instead of failing the decision. Green pass-through needs
 * no key; yellow/red always send one.
 */

const EXCERPT_LEN = 240;

export default function ApprovalCard({
  approval,
  onDecided,
}: {
  approval: SpaApproval;
  onDecided?: (id: string, decision: ApprovalDecision) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState<ApprovalDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<ApprovalDecision | null>(null);
  const [w6Missing, setW6Missing] = useState(false);

  // Stable per approval: computed once, reused across retries.
  const idempotencyKey = useMemo(
    () => idempotencyKeyFor(approval.id),
    [approval.id],
  );

  const body = approval.body_md || "";
  const excerpt =
    body.length > EXCERPT_LEN ? `${body.slice(0, EXCERPT_LEN)}…` : body;
  const pending = (approval.status || "").toLowerCase() === "pending" && !done;

  const decide = async (decision: ApprovalDecision) => {
    if (busy || done) return;
    setBusy(decision);
    setError(null);
    const res = await resumeApproval(approval.id, decision, "web-ui", idempotencyKey);
    setBusy(null);
    if (!res.resumed) {
      // W6 not deployed yet — offer the legacy queue, keep the decision.
      setW6Missing(true);
      return;
    }
    if (!res.ok) {
      setError("The approval could not be resumed.");
      return;
    }
    setDone(decision);
    onDecided?.(approval.id, decision);
  };

  const linkCls =
    "rounded-sm border border-linedefault px-2.5 py-1 text-bodysm text-inksecondary hover:text-ink disabled:opacity-50";
  const statusLabel = done ?? approval.status ?? "";

  return (
    <div className="rounded-sm border border-linedefault bg-raised px-4 py-3">
      <div className="flex items-start justify-between gap-2">
        <h3 className="min-w-0 flex-1 text-bodysm font-semibold text-ink">
          {approval.title || "Untitled approval"}
        </h3>
        {approval.kind && (
          <span className="shrink-0 text-meta text-inkmuted">
            {approval.kind}
          </span>
        )}
      </div>
      {excerpt && (
        <p className="mt-1 whitespace-pre-wrap break-words text-bodysm text-inksecondary">
          {expanded ? body : excerpt}
        </p>
      )}
      <dl className="mt-2 grid gap-x-4 gap-y-1 text-meta text-inksecondary sm:grid-cols-2">
        {approval.requested_by && <div><dt className="inline font-semibold">Requested by: </dt><dd className="inline">{approval.requested_by}</dd></div>}
        {approval.task && <div><dt className="inline font-semibold">Task: </dt><dd className="inline">{approval.task.title || approval.task.id}</dd></div>}
        {approval.campaign && <div><dt className="inline font-semibold">Campaign: </dt><dd className="inline">{approval.campaign.title || approval.campaign.id}</dd></div>}
        {approval.project_id && <div><dt className="inline font-semibold">Project: </dt><dd className="inline">{approval.project_id}</dd></div>}
        {(approval.title || approval.kind) && <div><dt className="inline font-semibold">Action: </dt><dd className="inline">{approval.title || approval.kind}</dd></div>}
        <div><dt className="inline font-semibold">Status: </dt><dd className="inline">{statusLabel}</dd></div>
      </dl>
      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        {body.length > EXCERPT_LEN && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className={linkCls}
          >
            {expanded ? "Collapse" : "Review"}
          </button>
        )}
        {pending ? (
          <>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void decide("approved")}
              className={linkCls}
            >
              {busy === "approved" ? "Approving…" : "Approve"}
            </button>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void decide("rejected")}
              className={linkCls}
            >
              {busy === "rejected" ? "Rejecting…" : "Request changes"}
            </button>
          </>
        ) : (
          <span className="text-meta text-inksecondary">{statusLabel ? `Status: ${statusLabel}` : null}</span>
        )}
      </div>
      {error ? (
        <p className="mt-1.5 text-meta text-err" role="alert">
          {error} — retry reuses the same idempotency key.
        </p>
      ) : null}
      {w6Missing && pending ? (
        <p className="mt-1.5 text-meta text-inkmuted">
          Decide in the{" "}
          <Link to="/app/approvals" className="underline">
            approvals queue
          </Link>
          .
        </p>
      ) : null}
      {done ? (
        <p className="mt-1.5 text-meta text-ok" aria-live="polite">
          Recorded: {done}.
        </p>
      ) : null}
    </div>
  );
}
