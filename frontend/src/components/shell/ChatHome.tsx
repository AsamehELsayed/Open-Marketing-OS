import { useNavigate } from "react-router-dom";
import EmptyState from "../chrome/EmptyState";

/** DEV-004 W2: Account Manager home — prompt + static suggestions. */
const SUGGESTIONS: { label: string; to: string }[] = [
  { label: "Start a blank chat", to: "/app/chat/new" },
  { label: "Draft a campaign", to: "/app/campaigns" },
  { label: "Review approvals", to: "/app/approvals" },
  { label: "Check results", to: "/app/results" },
  { label: "Browse knowledge", to: "/app/knowledge" },
];

export default function ChatHome() {
  const navigate = useNavigate();
  return (
    <div className="p-6">
      <EmptyState
        title="What do you want to work on?"
        hint="Pick a suggestion to get started."
        action={
          <div className="flex flex-wrap justify-center gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s.label}
                type="button"
                onClick={() => navigate(s.to)}
                className="rounded-sm border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink hover:border-accent"
              >
                {s.label}
              </button>
            ))}
          </div>
        }
      />
    </div>
  );
}
