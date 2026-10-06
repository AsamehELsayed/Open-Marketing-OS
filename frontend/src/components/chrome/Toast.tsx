import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export interface ToastItem {
  id: number;
  message: string;
  tone: "info" | "success" | "error";
}

const ToastCtx = createContext<{ push: (message: string, tone?: ToastItem["tone"]) => void }>({
  push: () => {},
});

/** DEV-004 W1 tiny context-based toaster. Wrap the app once in ToastProvider. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((message: string, tone: ToastItem["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setItems((prev) => [...prev.slice(-3), { id, message, tone }]);
    window.setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);
  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div aria-live="polite" className="fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
        {items.map((t) => (
          <div
            key={t.id}
            className={[
              "rounded-md border px-3 py-2 text-bodysm",
              t.tone === "success" && "border-ok/40 bg-raised text-ink",
              t.tone === "error" && "border-err/40 bg-raised text-ink",
              t.tone === "info" && "border-linedefault bg-raised text-ink",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}
