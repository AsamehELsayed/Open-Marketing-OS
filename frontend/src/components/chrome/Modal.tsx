import { useLayoutEffect, useId, useRef, type ReactNode } from "react";

/** DEV-004 W1 Modal: overlay + dialog, Escape/backdrop close. */
export default function Modal({
  title,
  onClose,
  children,
  returnFocusRef,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  returnFocusRef?: { current: HTMLElement | null };
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  const titleId = useId();
  closeRef.current = onClose;
  useLayoutEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ) ?? []).filter((el) => !el.hasAttribute("hidden"));
    (focusable()[0] ?? dialog)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); closeRef.current(); return; }
      if (e.key !== "Tab") return;
      const items = focusable();
      if (!items.length) { e.preventDefault(); dialog?.focus(); return; }
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || !dialog?.contains(document.activeElement))) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !dialog?.contains(document.activeElement))) {
        e.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); (returnFocusRef?.current ?? previous)?.focus(); };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto p-3 sm:p-5" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div aria-hidden="true" onMouseDown={onClose} className="absolute inset-0 bg-black/75 backdrop-blur-[2px]" />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="relative my-auto max-h-[calc(100dvh-1.5rem)] w-full max-w-lg overflow-y-auto rounded-lg border border-linedefault bg-raised p-5 shadow-2xl sm:max-h-[calc(100dvh-2.5rem)] sm:p-6"
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 id={titleId} className="text-h3 font-semibold text-ink">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-sm px-2 py-1 text-bodysm text-inksecondary"
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
