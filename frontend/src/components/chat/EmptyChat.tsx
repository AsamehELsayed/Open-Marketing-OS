/** DEV-004 W3: empty-thread prompt + suggestion buttons. */
export interface EmptyChatProps {
  onPick: (text: string) => void;
  approvalsCount?: number;
}

const SUGGESTIONS = [
  "Audit our Instagram",
  "Find our biggest marketing opportunity",
  "Review active campaigns",
  "What needs my approval?",
  "Create a 30-day plan",
];

const APPROVAL = "What needs my approval?";

export default function EmptyChat({ onPick, approvalsCount = 0 }: EmptyChatProps) {
  // When pending approvals exist, the approval prompt surfaces first.
  const ordered =
    approvalsCount > 0
      ? [APPROVAL, ...SUGGESTIONS.filter((s) => s !== APPROVAL)]
      : SUGGESTIONS;

  return (
    <div className="mx-auto flex max-w-md flex-col items-center px-6 py-14 text-center">
      <h2 className="text-h3 font-semibold text-ink">What do you want to work on?</h2>
      <p className="mt-2 text-bodysm text-inksecondary">
        {approvalsCount > 0
          ? `You have ${approvalsCount} item${approvalsCount === 1 ? "" : "s"} waiting for approval.`
          : "Pick a suggestion to get started."}
      </p>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {ordered.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="rounded-sm border border-linedefault bg-elevated px-3 py-1.5 text-bodysm text-ink hover:border-accent"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
