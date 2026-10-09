import { useEffect, useState, type ReactNode } from "react";

type ActivityPlacement = "left" | "right";
interface ActivityPreference {
  visible: boolean;
  placement: ActivityPlacement;
}

const ACTIVITY_PREFERENCE_KEY = "omos.activity-rail.v1";

function readActivityPreference(): ActivityPreference {
  try {
    const saved = JSON.parse(window.localStorage.getItem(ACTIVITY_PREFERENCE_KEY) || "null");
    if (saved && typeof saved === "object") {
      return {
        visible: saved.visible === true,
        placement: saved.placement === "left" ? "left" : "right",
      };
    }
  } catch { /* storage may be unavailable; keep the hidden default */ }
  return { visible: false, placement: "right" };
}

/**
 * The existing Live Activity rail is optional and user-positionable. Its
 * visibility and placement persist independently from project/Git data.
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
  const [activityOpen, setActivityOpen] = useState(() => readActivityPreference().visible);
  const [activityPlacement, setActivityPlacement] = useState<ActivityPlacement>(() => readActivityPreference().placement);

  useEffect(() => {
    try {
      window.localStorage.setItem(ACTIVITY_PREFERENCE_KEY, JSON.stringify({
        visible: activityOpen,
        placement: activityPlacement,
      } satisfies ActivityPreference));
    } catch { /* persistence is best-effort in restricted browser contexts */ }
  }, [activityOpen, activityPlacement]);

  const placementSelect = (className: string) => (
    <label className={className}>
      <span className="sr-only">Live activity placement</span>
      <select
        aria-label="Live activity placement"
        value={activityPlacement}
        onChange={(event) => setActivityPlacement(event.target.value as ActivityPlacement)}
        className="rounded-sm border border-linedefault bg-raised px-2 py-1 text-meta text-inksecondary"
      >
        <option value="right">Activity on right</option>
        <option value="left">Activity on left</option>
      </select>
    </label>
  );

  const desktopActivity = activityOpen ? (
    <aside
      className="hidden w-80 shrink-0 flex-col border-linesubtle bg-raised xl:flex"
      style={{
        borderInlineStartWidth: activityPlacement === "right" ? 1 : 0,
        borderInlineEndWidth: activityPlacement === "left" ? 1 : 0,
      }}
    >
      <div className="min-h-0 flex-1 overflow-y-auto">{activity}</div>
    </aside>
  ) : null;

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
          aria-label={activityOpen ? "Hide live activity" : "Show live activity"}
          aria-expanded={activityOpen}
          onClick={() => setActivityOpen((v) => !v)}
          className="rounded-sm border border-linedefault px-2 py-1 text-bodysm text-inksecondary"
        >
          {activityOpen ? "Hide activity" : "Activity"}
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

      {activityPlacement === "left" ? desktopActivity : null}

      {/* center pane */}
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden pt-12 lg:pt-0">
        <div className="hidden h-10 shrink-0 items-center justify-end gap-2 border-b border-linesubtle bg-base px-4 lg:flex">
          {placementSelect("")}
          <button
            type="button"
            onClick={() => setActivityOpen((v) => !v)}
            aria-label={activityOpen ? "Hide live activity" : "Show live activity"}
            aria-expanded={activityOpen}
            className="rounded-sm border border-linedefault px-2 py-1 text-meta text-inksecondary hover:border-accent"
          >
            {activityOpen ? "Hide activity" : "Show activity"}
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </main>

      {activityPlacement === "right" ? desktopActivity : null}

      {/* On small screens the same rail becomes an anchored overlay drawer. */}
      {activityOpen && <button type="button" aria-label="Close live activity drawer" onClick={() => setActivityOpen(false)} className="fixed inset-0 z-30 bg-black/40 xl:hidden" />}
      {activityOpen && (
        <aside className={`fixed bottom-0 top-12 z-40 w-[min(22rem,88vw)] overflow-y-auto border-linesubtle bg-raised shadow-xl xl:hidden ${activityPlacement === "left" ? "left-0 border-r" : "right-0 border-l"}`}>
          <div className="flex items-center justify-between border-b border-linesubtle px-3 py-2">
            {placementSelect("")}
            <button type="button" onClick={() => setActivityOpen(false)} aria-label="Close live activity" className="rounded-sm border border-linedefault px-2 py-1 text-meta text-inksecondary">Close</button>
          </div>
          {activity}
        </aside>
      )}
    </div>
  );
}
