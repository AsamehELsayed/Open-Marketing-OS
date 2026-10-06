import { useEffect, useRef, useState, type ReactNode } from "react";

/** DEV-004 W1 Dropdown: minimal accessible menu (button + items). */
export default function Dropdown({
  label,
  items,
  align = "left",
}: {
  label: ReactNode;
  items: { id: string; label: string; onSelect: (id: string) => void }[];
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open ]);

  return (
    <div ref={ref} className="relative inline-block">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="rounded-sm border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink"
      >
        {label}
      </button>
      {open && (
        <div
          role="menu"
          className={`absolute ${align === "right" ? "right-0" : "left-0"} z-50 mt-1 min-w-48 rounded-md border border-linedefault bg-overlay py-1 shadow-lg whitespace-nowrap`}
        >
          {items.map((it) => (
            <button
              key={it.id}
              role="menuitem"
              type="button"
              onClick={() => {
                setOpen(false);
                it.onSelect(it.id);
              }}
              className="block w-full px-3 py-2 text-left text-bodysm text-ink hover:bg-elevated"
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
