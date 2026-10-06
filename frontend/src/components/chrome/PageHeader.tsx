import type { ReactNode } from "react";

/** DEV-004 W1 PageHeader: title + optional description + actions row. */
export default function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-linesubtle px-6 py-5">
      <div className="min-w-0">
        <h1 className="text-h2 font-semibold text-ink">{title}</h1>
        {description && (
          <p className="mt-1 max-w-2xl text-bodysm text-inksecondary">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
