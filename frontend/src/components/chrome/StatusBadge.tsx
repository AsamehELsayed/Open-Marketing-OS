/** DEV-004 W1 StatusBadge: restrained status pill. Semantic color only. */
const TONE: Record<string, string> = {
  open: "text-inksecondary border-linedefault",
  pending: "text-warn border-warn/40",
  approved: "text-ok border-ok/40",
  completed: "text-ok border-ok/40",
  done: "text-ok border-ok/40",
  learned: "text-ok border-ok/40",
  rejected: "text-err border-err/40",
  failed: "text-err border-err/40",
  blocked: "text-err border-err/40",
  measuring: "text-accenthover border-accent/40",
  executing: "text-accenthover border-accent/40",
  running: "text-accenthover border-accent/40",
  queued: "text-inksecondary border-linedefault",
  archived: "text-inkmuted border-linesubtle",
};

export default function StatusBadge({ status }: { status: string }) {
  const tone = TONE[(status || "").toLowerCase()] ?? "text-inksecondary border-linedefault";
  return (
    <span
      className={`inline-flex items-center rounded-sm border px-2 py-0.5 text-meta font-medium ${tone}`}
    >
      {status}
    </span>
  );
}
