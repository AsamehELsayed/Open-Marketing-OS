import { useState, type ReactNode } from "react";

/**
 * DEV-004 W1 AppShell: left / center / right 3-pane with a collapsible
 * right panel. On narrow screens the left nav becomes a drawer and the
 * right panel becomes an overlay — pure CSS + a tiny toggle, no JS layout
 * framework. Downstream workers fill the `sidebar` / `activity` slots.
 */
export default function AppShell({
  sidebar,
  activity,
  children,
}: {
  sidebar?: ReactNode;
  activity?: ReactNode;
  children: ReactNode;
}) {
  const [leftOpen, setLeftOpen] = useState(false);
  // Open by default on desktop widths; closed on smaller screens so the
  // chat stays primary and the panel becomes an on-demand drawer.
  const [rightOpen, setRightOpen] = useState<boolean>(() => {
    try {
      return window.matchMedia("(min-width: 1280px)").matches;
    } catch {
      return true;
    }
  });

  return (
    <div className="flex h-full bg-base text-ink">
      {/* mobile top bar */}
      <div className="fixed inset-x-0 top-0 z-30 flex h-12 items-center gap-2 border-b border-linesubtle bg-raised px-3 lg:hidden">
        <button
          type="button"
          aria-label="Open navigation"
          onClick={() => setLeftOpen(true)}
          className="rounded-sm border border-linedefault px-2 py-1 text-bodysm text-inksecondary"
        >
          ☰
        </button>
        <span className="text-bodysm font-semibold">Open Marketing OS</span>
        <span className="flex-1" />
        <button
          type="button"
          aria-label="Toggle activity panel"
          onClick={() => setRightOpen((v) => !v)}
          className="rounded-sm border border-linedefault px-2 py-1 text-bodysm text-inksecondary"
        >
          {rightOpen ? "Hide activity" : "Activity"}
        </button>
      </div>

      {/* left pane */}
      <aside
        className={[
          "z-40 flex w-72 shrink-0 flex-col border-r border-linesubtle bg-raised",
          "fixed inset-y-0 left-0 top-12 h-[calc(100%-3rem)] transition-transform lg:static lg:top-0 lg:h-full lg:translate-x-0",
          leftOpen ? "translate-x-0" : "-translate-x-full",
        ].join(" ")}
      >
        <div className="flex items-center justify-between border-b border-linesubtle px-4 py-3">
          <span className="text-bodysm font-semibold">Open Marketing OS</span>
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => setLeftOpen(false)}
            className="rounded-sm px-2 py-1 text-meta text-inksecondary lg:hidden"
          >
            ✕
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {sidebar ?? (
            <div className="p-4 text-bodysm text-inksecondary">
              Navigation is owned by W2.
            </div>
          )}
        </div>
      </aside>
      {leftOpen && (
        <button
          type="button"
          aria-label="Close navigation overlay"
          onClick={() => setLeftOpen(false)}
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
        />
      )}

      {/* center pane */}
      <main className="min-w-0 flex-1 overflow-y-auto pt-12 lg:pt-0">
        {children}
      </main>

      {/* right pane (collapsible; header owned by ActivityPanel) */}
      {rightOpen && (
        <aside className="hidden w-80 shrink-0 flex-col border-l border-linesubtle bg-raised xl:flex">
          <div className="min-h-0 flex-1 overflow-y-auto">
            {activity ?? (
              <div className="p-4 text-bodysm text-inksecondary">
                Activity is owned by W4.
              </div>
            )}
          </div>
        </aside>
      )}
      {!rightOpen && (
        <button
          type="button"
          onClick={() => setRightOpen(true)}
          className="hidden shrink-0 border-l border-linesubtle bg-raised px-2 text-meta text-inksecondary xl:block"
          aria-label="Expand activity panel"
        >
          ▶
        </button>
      )}

      {/* mobile activity overlay (panel header lives in ActivityPanel) */}
      {rightOpen && (
        <div className="fixed inset-x-0 bottom-0 z-30 max-h-[45%] overflow-y-auto border-t border-linesubtle bg-raised xl:hidden">
          <div className="flex items-center justify-end px-4 py-1">
            <button
              type="button"
              onClick={() => setRightOpen(false)}
              className="rounded-sm px-2 py-1 text-meta text-inksecondary"
            >
              Hide
            </button>
          </div>
          {activity}
        </div>
      )}
    </div>
  );
}
