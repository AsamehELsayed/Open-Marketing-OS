/** DEV-004 W1 Tabs: underline tab row, controlled. */
export interface TabItem {
  id: string;
  label: string;
}

export default function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: TabItem[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-linesubtle px-6">
      {tabs.map((t) => {
        const selected = t.id === active;
        return (
          <button
            key={t.id}
            role="tab"
            aria-selected={selected}
            type="button"
            onClick={() => onChange(t.id)}
            className={[
              "whitespace-nowrap border-b-2 px-3 py-2.5 text-bodysm",
              selected
                ? "border-accent font-semibold text-ink"
                : "border-transparent text-inksecondary hover:text-ink",
            ].join(" ")}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
