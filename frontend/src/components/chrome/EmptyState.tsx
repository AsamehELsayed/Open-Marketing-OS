import type { ReactNode } from "react";

/** DEV-004 W1 EmptyState: title + hint + optional action. */
export default function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center px-6 py-14 text-center">
      <h2 className="text-h3 font-semibold text-ink">{title}</h2>
      {hint && <p className="mt-2 text-bodysm text-inksecondary">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
