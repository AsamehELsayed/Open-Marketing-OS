import type { LifecycleItem } from "../../hooks/useTurnStream";

/**
 * DEV-008-SKILLS-OPS §1.3.3 / §1.3.4 + DEV-008-SKILLS-OPS-HOTFIX contract §6/§10.
 *
 * WHAT THIS SHOWS, AND WHAT IT DELIBERATELY DOES NOT
 * --------------------------------------------------
 * Every node here is a real, persisted `execution_events` row that arrived on
 * the turn's SSE stream. There are no timers, no simulated progress, no
 * post-hoc replay animation. If a node has not been emitted, it is not drawn.
 *
 * A row is a node iff its `meta.status` is one of the seven `TREE_STATUSES`.
 * That is the frozen producer contract, so the test is the data, not a list of
 * event names maintained in the UI.
 *
 * THE COMPOUND TURN SHAPE (hotfix contract §6)
 * --------------------------------------------
 * The wire (`app/services/skills/tree.py`) parents rows like this:
 *
 *   account_manager_started   parent_id = ""            <-- ROOT
 *   task_planned              parent_id = ""            <-- ROOT as well
 *   employee_queued           parent_id = task_planned
 *   employee_started          parent_id = task_planned
 *   skill/tool/evidence       parent_id = employee_started (of ITS employee)
 *   employee_completed        parent_id = task_planned  <-- sibling, not child
 *   employee_failed           parent_id = task_planned  <-- sibling, not child
 *   synthesis_started/completed parent_id = task_planned
 *
 * Taken literally that renders two sibling roots, a flat team with the
 * fan-in rows smeared across it, and a synthesis row buried in the middle of
 * the team. Contract §6 instead declares:
 *
 *   account_manager_started
 *     └─ task_planned
 *          ├─ <one subtree per employee>
 *          └─ ...
 *     └─ synthesis(phase="completed")     (after the fan-in barrier)
 *
 * so THREE links are derived here rather than read off the wire. Each one is a
 * derivation from the frozen event ORDER, and each is covered by a behavioural
 * test in `app/tests/test_dev008hf_frontend_tree.py`:
 *
 *   1. `task_planned` hangs off `account_manager_started` (both are parentless
 *      on the wire) — this is the "planning root before the team" rule.
 *   2. `employee_completed` / `employee_failed` hang off THEIR OWN employee,
 *      resolved from `meta.employee_id`, so an employee is one subtree and a
 *      failed branch stays attached to the branch that failed.
 *   3. `synthesis_*` hangs off the planning root, as the LAST child, because
 *      §6 emits it only after every required employee finished. The ordering
 *      is not forced: children are sorted by persisted `execution_events.id`,
 *      so "synthesis last" is the producer's own order showing through.
 *
 * Tool, skill and evidence rows are nested by `meta.employee_id` — the OWNING
 * employee — not by their `parent_id` and never as a flat list. `parent_id` is
 * still honoured, and is still the fallback, so a row that carries no
 * `employee_id` still lands where the server put it.
 *
 * LIVE AND RELOADED MUST NOT DISAGREE
 * -----------------------------------
 * `buildExecutionTree` is a pure function of the NODE SET, not of the arrival
 * order: it sorts every level by persisted id before linking. The live stream
 * (`useTurnStream`) and the post-reload replay (`usePersistedTurnTree`) can
 * therefore hand it the same rows in any order and get the identical tree.
 * The equality is asserted directly, from a shuffled input, by
 * `test_live_and_reloaded_events_build_the_identical_tree`.
 *
 * Linkage resolution (frozen, fail-soft, never drop):
 * - `parent_id` empty/absent -> root.
 * - `parent_id` names an id not present in this turn's node set -> rendered as
 *   a ROOT and flagged `orphan`.
 * - `parent_id` resolves to a row carrying a different `turn_id` -> re-parented
 *   to ROOT and flagged `cross_turn_rejected`.
 *   (The server-side key these flags correspond to is `meta.parent_link_status`;
 *   the flags are computed here, in the UI, from evidence already on the wire.)
 * A node is never hidden because its parent is missing. A tree that quietly
 * drops a node is worse than one that shows a node with a warning on it.
 *
 * PRIVATE DELIBERATION: this component reads only the frozen metadata keys
 * from plan §1.3.3 (`status`, `parent_id`, `turn_id`, `thread_id`,
 * `employee_id`, `employee_role`, `skill_id`, `skill_name`, `tool_id`,
 * `tool_run_id`, `duration_ms`, `task_count`, `employee_count`,
 * `evidence_count`, `evidence_kind`, `approval_id`, `action_class`, `route`,
 * `score`, `reason_code`, `category`, `skill_version`, `started_at`,
 * `completed_at`). It never reads or surfaces any private-deliberation field;
 * the server strips those before an event is persisted, and this file is
 * gated by `app/tests/test_dev008so_frontend_wiring.py` to stay clear of them.
 *
 * The `GRAPH_NODES` route list (`api/runtime.ts`) is a different thing entirely
 * and is deliberately NOT reused here: those are graph topology nodes, this is
 * one turn's execution. The frozen node list is untouched.
 */

/** The seven visual statuses the wire contract can put on `meta.status`. */
export const TREE_STATUSES = [
  "QUEUED",
  "RUNNING",
  "COMPLETE",
  "FAILED",
  "RETRYING",
  "WAITING_FOR_APPROVAL",
  "CANCELLED",
] as const;

export type TreeStatus = (typeof TREE_STATUSES)[number];

export interface StatusView {
  label: string;
  /** Pill classes. */
  pill: string;
  /** Leading-dot classes. */
  dot: string;
}

/**
 * Render map for every member of `TREE_STATUSES`.
 *
 * Typed as `Record<TreeStatus, StatusView>`, so this is a COMPILE-TIME
 * exhaustive mapping: if the server grows an eighth status, `tsc --noEmit`
 * fails here rather than the UI silently rendering an unstyled pill.
 *
 * `CANCELLED` has no user-facing cancel control in this run (plan §8.2) — the
 * status is still mapped, because a node can carry it and it must not fall
 * through to "unknown".
 *
 * Colours are tokens only (`tokens.css` / `tailwind.config.ts`): accent, ok,
 * warn, err, ink*. Single fixed dark theme; no new colour literals.
 */
export const STATUS_VIEWS: Record<TreeStatus, StatusView> = {
  QUEUED: {
    label: "Queued",
    pill: "border-linedefault bg-elevated text-inksecondary",
    dot: "bg-inkmuted",
  },
  RUNNING: {
    label: "Running",
    pill: "border-accent/40 bg-accent/10 text-accenthover",
    dot: "bg-accent animate-pulse",
  },
  COMPLETE: {
    label: "Complete",
    pill: "border-ok/40 bg-ok/10 text-ok",
    dot: "bg-ok",
  },
  FAILED: {
    label: "Failed",
    pill: "border-err/40 bg-err/10 text-err",
    dot: "bg-err",
  },
  RETRYING: {
    label: "Retrying",
    pill: "border-warn/40 bg-warn/10 text-warn",
    dot: "bg-warn animate-pulse",
  },
  WAITING_FOR_APPROVAL: {
    label: "Waiting for your approval",
    pill: "border-warn/40 bg-warn/10 text-warn",
    dot: "bg-warn",
  },
  CANCELLED: {
    label: "Cancelled",
    pill: "border-linedefault bg-elevated text-inkmuted",
    dot: "bg-inkmuted",
  },
};

/** Shown for a status the frozen enum does not contain. Never guess a tone. */
const UNKNOWN_STATUS_VIEW: StatusView = {
  label: "Unrecognised status",
  pill: "border-linedefault bg-elevated text-inkmuted",
  dot: "bg-inkmuted",
};

function isTreeStatus(value: unknown): value is TreeStatus {
  return (
    typeof value === "string" && (TREE_STATUSES as readonly string[]).includes(value)
  );
}

export function statusView(status: TreeStatus): StatusView {
  return STATUS_VIEWS[status] ?? UNKNOWN_STATUS_VIEW;
}

/** `orphan` / `cross_turn_rejected` are plan §1.3.3's two link failures. */
export type ParentLinkStatus = "root" | "linked" | "orphan" | "cross_turn_rejected";

/**
 * What a node IS, read off the frozen event_type catalog. A node's kind decides
 * who owns it in the tree; it is never inferred from the status or the label.
 */
export const TREE_KINDS = [
  "account_manager",
  "plan",
  "employee",
  "outcome",
  "skill",
  "tool",
  "evidence",
  "synthesis",
  "other",
] as const;

export type TreeKind = (typeof TREE_KINDS)[number];

/**
 * event_type -> kind. Keyed on the WIRE catalog, so an unregistered type falls
 * through to `other` and is still linked by its `parent_id` — an unknown row
 * can never make the tree disappear.
 */
const EVENT_KIND_BY_TYPE: Record<string, TreeKind> = {
  account_manager_started: "account_manager",
  task_planned: "plan",
  employee_queued: "employee",
  employee_started: "employee",
  employee_completed: "outcome",
  employee_failed: "outcome",
  skill_selected: "skill",
  skill_loaded: "skill",
  tool_started: "tool",
  tool_completed: "tool",
  tool_failed: "tool",
  evidence_added: "evidence",
  synthesis_started: "synthesis",
  synthesis_completed: "synthesis",
};

/**
 * The kinds an employee OWNS: the work it did, not the fact that it exists.
 *
 * `employee_queued` / `employee_started` are deliberately NOT here. They are
 * the branch HEAD and its sibling, both children of the plan, and folding the
 * queue row into the started row would render "working" above "assigned" and
 * put a child above its own parent. Everything else — tool runs, skill
 * playbooks, evidence, and the completed/failed outcome — belongs under the
 * employee that produced it.
 */
const OWNED_KINDS: ReadonlySet<TreeKind> = new Set<TreeKind>([
  "outcome",
  "tool",
  "skill",
  "evidence",
]);

export interface TreeNode {
  /**
   * `execution_events.id` — the id `meta.parent_id` points at.
   *
   * ZERO for a DERIVED branch node: those carry no persisted row, and SQLite
   * row ids start at 1, so 0 can never collide with a real one. Use `key` to
   * address any node; `key` is unique among siblings AND across the tree.
   */
  id: number;
  /** Unique among siblings and across the whole tree. */
  key: string;
  /**
   * The persisted id this node sorts by. Identical to `id` for a real row; for
   * a derived branch it is the earliest REAL row id of that employee, so a
   * group is still ordered by the producer's own emission order.
   */
  orderId: number;
  kind: TreeKind;
  type: string;
  label: string;
  detail: string;
  status: TreeStatus;
  parentId: string;
  parentLinkStatus: ParentLinkStatus;
  /** `meta.employee_id` — the OWNER used to nest tool/skill/evidence rows. */
  employeeId: string;
  employeeRole: string;
  toolId: string;
  durationMs: number | null;
  /**
   * True only for a grouping node this function created because an
   * `employee_id` had tool/evidence/outcome rows but no `employee_started` /
   * `employee_queued` row to hang them from. It carries no invented event: the
   * label and role come from the employee's own persisted rows, and it is
   * marked in the UI so it can never be read as a persisted event.
   */
  derived: boolean;
  /** True when this node or any node in its subtree is FAILED. */
  failedDescendant: boolean;
  children: TreeNode[];
}

interface NodeRec {
  id: number;
  key: string;
  orderId: number;
  kind: TreeKind;
  type: string;
  label: string;
  detail: string;
  status: TreeStatus;
  parentId: string;
  parentLinkStatus: ParentLinkStatus;
  employeeId: string;
  employeeRole: string;
  toolId: string;
  durationMs: number | null;
  derived: boolean;
  turnId: string;
  children: NodeRec[];
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * `meta.parent_id` as a link target.
 *
 * The emitter stringifies it (`TreeEmitter._parent_str`), so a string is the
 * normal case; a finite number is accepted too rather than treated as "no
 * parent", because silently flattening a whole team over a JSON number would be
 * exactly the "quietly wrong tree" this component exists to avoid.
 */
function linkId(value: unknown): string {
  if (typeof value === "string") return value.trim();
  return num(value) == null ? "" : String(value);
}

/** `paid_media` -> `Paid Media`, `seo` -> `Seo`; matches the emitter's label. */
function roleLabel(role: string): string {
  const cleaned = role.replace(/_/g, " ").trim();
  if (!cleaned) return "Team member";
  return cleaned.replace(/\b[a-z]/g, (char) => char.toUpperCase());
}

/**
 * Follow `meta.parent_id` for a row the ownership rules did not claim.
 * Fails soft and says which failure it was; never silently re-parents.
 */
function wireLink(
  node: NodeRec,
  byId: Map<number, NodeRec>,
): { parent: NodeRec | null; link: ParentLinkStatus } {
  if (!node.parentId) return { parent: null, link: "root" };
  const parentId = Number(node.parentId);
  const parent = Number.isFinite(parentId) ? byId.get(parentId) ?? null : null;
  if (!parent || parent.id === node.id) return { parent: null, link: "orphan" };
  if (node.turnId && parent.turnId && node.turnId !== parent.turnId) {
    // A parent link across turns is rejected, not followed: the row behind it
    // belongs to a different turn's tree.
    return { parent: null, link: "cross_turn_rejected" };
  }
  return { parent, link: "linked" };
}

function toTreeNode(node: NodeRec): TreeNode {
  const children = node.children.map(toTreeNode).sort(byOrderThenKey);
  const { turnId, children: _children, ...rest } = node;
  return {
    ...rest,
    failedDescendant:
      rest.status === "FAILED" || children.some((child) => child.failedDescendant),
    children,
  };
}

/**
 * Persisted order. Sorting here — not relying on the order the rows arrive in —
 * is what makes the live tree and the reloaded tree the same tree.
 */
function byOrderThenKey(
  a: { orderId: number; key: string },
  b: { orderId: number; key: string },
): number {
  if (a.orderId !== b.orderId) return a.orderId - b.orderId;
  return a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
}

/**
 * Build the tree from the lifecycle rows of ONE turn.
 *
 * Exported and pure so the assembly rule is inspectable without a browser: it
 * takes rows, it touches no hook, no network and no clock. It is a function of
 * the node SET — the input order is irrelevant — so the live SSE stream and
 * the post-reload persisted replay converge on the same structure.
 */
export function buildExecutionTree(events: LifecycleItem[]): TreeNode[] {
  const rows: NodeRec[] = [];
  for (const ev of events) {
    const status = ev.meta?.["status"];
    if (!isTreeStatus(status)) continue;
    rows.push({
      id: ev.id,
      key: String(ev.id),
      orderId: ev.id,
      kind: EVENT_KIND_BY_TYPE[ev.type] ?? "other",
      type: ev.type,
      label: ev.label || ev.type,
      detail: ev.detail,
      status,
      parentId: linkId(ev.meta?.["parent_id"]),
      parentLinkStatus: "root",
      employeeId: text(ev.meta?.["employee_id"]),
      employeeRole: text(ev.meta?.["employee_role"]),
      toolId: text(ev.meta?.["tool_id"]),
      durationMs: num(ev.meta?.["duration_ms"]),
      derived: false,
      turnId: text(ev.meta?.["turn_id"]),
      children: [],
    });
  }
  if (rows.length === 0) return [];
  rows.sort(byOrderThenKey);

  // Lifecycle outcomes are emitted as separate persisted rows. Project them
  // onto the corresponding started row so a completed replay cannot leave a
  // branch looking active. With no matching outcome, retain the wire status
  // (especially RUNNING for a live, in-flight turn).
  const employeeOutcomes = new Map<string, TreeStatus>();
  const toolOutcomes = new Map<string, TreeStatus>();
  const eventById = new Map(events.map((event) => [event.id, event]));
  const synthesisOutcome = rows.some((row) => row.type === "synthesis_completed")
    ? "COMPLETE"
    : null;
  let terminalTurnStatus: TreeStatus | null = null;
  for (const event of events) {
    if (event.type === "turn_completed") terminalTurnStatus = "COMPLETE";
    else if (event.type === "turn_failed") terminalTurnStatus = "FAILED";
  }
  for (const row of rows) {
    if (row.kind === "outcome" && row.employeeId) {
      employeeOutcomes.set(row.employeeId, row.status);
    }
    if (row.type === "tool_completed" || row.type === "tool_failed") {
      const runId = text(eventById.get(row.id)?.meta?.["tool_run_id"]);
      if (runId) toolOutcomes.set(runId, row.status);
    }
  }
  for (const row of rows) {
    if (row.type === "account_manager_started" && terminalTurnStatus) {
      row.status = terminalTurnStatus;
    } else if ((row.type === "employee_started" || row.type === "employee_queued") && row.employeeId) {
      row.status = employeeOutcomes.get(row.employeeId) ?? row.status;
    } else if (row.type === "tool_started") {
      const runId = text(eventById.get(row.id)?.meta?.["tool_run_id"]);
      if (runId) row.status = toolOutcomes.get(runId) ?? row.status;
    } else if (row.type === "synthesis_started" && synthesisOutcome) {
      row.status = synthesisOutcome;
    }
  }

  const byId = new Map<number, NodeRec>();
  for (const row of rows) byId.set(row.id, row);

  // The planning root: the Account Manager, or the plan when that row is the
  // first of the two to exist. §6 nests the plan under the manager.
  const managerRow = rows.find((row) => row.kind === "account_manager") ?? null;
  const planRow = rows.find((row) => row.kind === "plan") ?? null;
  const planningRoot = managerRow ?? planRow;

  // ---- employee branches: one per meta.employee_id, keyed by OWNER ------
  const roles = new Map<string, string>();
  for (const row of rows) {
    if (row.employeeId && row.employeeRole && !roles.has(row.employeeId)) {
      roles.set(row.employeeId, row.employeeRole);
    }
  }
  const branchOf = new Map<string, NodeRec>();
  for (const row of rows) {
    if (row.employeeId && row.kind === "employee" && !branchOf.has(row.employeeId)) {
      branchOf.set(row.employeeId, row);
    }
  }
  // employee_started outranks employee_queued as the branch head: the queued
  // row stays a child of the plan, the started row is what the team hangs off.
  for (const row of rows) {
    if (row.employeeId && row.type === "employee_started") {
      branchOf.set(row.employeeId, row);
    }
  }
  // An employee whose own rows exist but whose `employee_started` never landed
  // still gets a branch, or a failed branch would render as a loose leaf.
  const derivedBranches: NodeRec[] = [];
  for (const row of rows) {
    if (!row.employeeId || branchOf.has(row.employeeId)) continue;
    const role = roles.get(row.employeeId) ?? "";
    const failed = rows.some(
      (other) => other.employeeId === row.employeeId && other.status === "FAILED",
    );
    const branch: NodeRec = {
      id: 0,
      key: `branch:${row.employeeId}`,
      orderId: row.id,
      kind: "employee",
      type: "employee_branch",
      label: `${roleLabel(role)} branch`,
      detail: "",
      status: failed ? "FAILED" : row.status,
      parentId: "",
      parentLinkStatus: "linked",
      employeeId: row.employeeId,
      employeeRole: role,
      toolId: "",
      durationMs: null,
      derived: true,
      turnId: row.turnId,
      children: [],
    };
    branchOf.set(row.employeeId, branch);
    derivedBranches.push(branch);
  }

  // ---- link every persisted row ----------------------------------------
  const roots: NodeRec[] = [];
  for (const row of rows) {
    const branch = row.employeeId ? branchOf.get(row.employeeId) ?? null : null;
    let parent: NodeRec | null = null;
    let link: ParentLinkStatus = "root";

    if (row.kind === "account_manager") {
      // The planning root itself: no parent. A second one (a retry) also
      // stays a root rather than being grafted anywhere.
    } else if (row.kind === "plan") {
      // Derivation 1: the plan is a CHILD of the Account Manager.
      parent = managerRow;
      link = managerRow ? "linked" : "root";
    } else if (row.kind === "synthesis") {
      // Derivation 3: synthesis is a sibling of the plan, emitted only after
      // the fan-in barrier, so it sorts after every employee subtree.
      parent = planningRoot;
      link = planningRoot ? "linked" : "root";
    } else if (branch && branch !== row && OWNED_KINDS.has(row.kind)) {
      // Derivation 2: owned rows nest under the employee that OWNS them,
      // resolved from meta.employee_id rather than from parent_id.
      parent = branch;
      link = "linked";
    } else {
      const wired = wireLink(row, byId);
      parent = wired.parent;
      link = wired.link;
      // The plan row never landed, so the wire left the whole team parentless.
      // §6 still puts the team under the Account Manager root; nest it there
      // instead of scattering sibling roots, and keep the flag honest.
      if (!parent && !planRow && row.employeeId && planningRoot && planningRoot !== row) {
        parent = planningRoot;
        link = "linked";
      }
    }

    row.parentLinkStatus = link;
    if (parent) parent.children.push(row);
    else roots.push(row);
  }

  // ---- attach the derived branches to the plan (or the manager) ----------
  for (const branch of derivedBranches) {
    const anchor = planRow ?? managerRow;
    branch.parentLinkStatus = anchor ? "linked" : "root";
    if (anchor) anchor.children.push(branch);
    else roots.push(branch);
  }

  return roots.sort(byOrderThenKey).map(toTreeNode);
}

function walk(nodes: TreeNode[], visit: (node: TreeNode) => void): void {
  for (const node of nodes) {
    visit(node);
    walk(node.children, visit);
  }
}

function countStatus(nodes: TreeNode[], status: TreeStatus): number {
  let total = 0;
  walk(nodes, (node) => {
    if (node.status === status) total += 1;
  });
  return total;
}

function countKind(nodes: TreeNode[], kind: TreeKind): number {
  let total = 0;
  walk(nodes, (node) => {
    if (node.kind === kind) total += 1;
  });
  return total;
}

function unlinkedCount(nodes: TreeNode[]): number {
  let total = 0;
  walk(nodes, (node) => {
    if (node.parentLinkStatus === "orphan") total += 1;
    else if (node.parentLinkStatus === "cross_turn_rejected") total += 1;
  });
  return total;
}

function NodeRow({ node, depth, live }: { node: TreeNode; depth: number; live: boolean }) {
  const view = statusView(node.status);
  // DEV-008-SKILLS-OPS W12: a persisted replay shows the same render map, but
  // must not animate as though it were live — only the `animate-pulse`
  // class on RUNNING/RETRYING dots is dropped for `live: false`. The frozen
  // STATUS_VIEWS map itself is untouched.
  const dotClasses = live ? view.dot : view.dot.replace(" animate-pulse", "");
  return (
    <li className="relative">
      <div
        className="flex flex-wrap items-center gap-2 py-1.5 pl-3 pr-1"
        style={{ marginLeft: `${Math.min(depth, 6) * 14}px` }}
      >
        <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${dotClasses}`} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-bodysm text-ink">{node.label}</span>
          {node.detail ? (
            <span className="mt-0.5 block break-words text-meta text-inksecondary">
              {node.detail}
            </span>
          ) : null}
          {node.employeeRole || node.durationMs != null ? (
            <span className="mt-0.5 block text-meta text-inkmuted">
              {node.employeeRole}
              {node.durationMs != null
                ? `${node.employeeRole ? " · " : ""}${node.durationMs} ms`
                : ""}
            </span>
          ) : null}
        </span>
        {node.derived ? (
          <span className="rounded-sm border border-linedefault bg-elevated px-1.5 py-0.5 text-meta text-inkmuted">
            grouped by employee_id
          </span>
        ) : null}
        {node.parentLinkStatus === "orphan" ? (
          <span className="rounded-sm border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-meta text-warn">
            No parent in this turn
          </span>
        ) : null}
        {node.parentLinkStatus === "cross_turn_rejected" ? (
          <span className="rounded-sm border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-meta text-warn">
            Link from another turn rejected
          </span>
        ) : null}
        {/* A branch whose subtree failed says so on the branch itself, so a
            `employee_failed` leaf can never read as a healthy team. */}
        {node.failedDescendant && node.status !== "FAILED" ? (
          <span className="rounded-sm border border-err/40 bg-err/10 px-1.5 py-0.5 text-meta text-err">
            branch failed
          </span>
        ) : null}
        <span className={`rounded-sm border px-1.5 py-0.5 text-meta font-medium ${view.pill}`}>
          {view.label}
        </span>
      </div>
      {node.children.length > 0 ? (
        <ul className="border-l border-linesubtle">
          {node.children.map((child) => (
            <NodeRow key={child.key} node={child} depth={depth + 1} live={live} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/**
 * DEV-008-SKILLS-OPS W12: `live` defaults to true (the original SSE path) and
 * is set to false ONLY by the persisted-turn replay path, where the panel is
 * re-rendering persisted safe rows and must not animate. No visual state of
 * the live path is changed.
 */
export default function ExecutionTree({
  events,
  live = true,
}: {
  events: LifecycleItem[];
  live?: boolean;
}) {
  const nodes = buildExecutionTree(events);
  if (nodes.length === 0) return null;

  const failed = countStatus(nodes, "FAILED");
  const waiting = countStatus(nodes, "WAITING_FOR_APPROVAL");
  const unlinked = unlinkedCount(nodes);
  const branches = countKind(nodes, "employee");
  const tools = countKind(nodes, "tool");

  return (
    <section
      className="rounded-md border border-linesubtle bg-raised px-3 py-2"
      aria-label="Execution tree"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-metag font-medium text-ink">Execution tree</h2>
        <span className="text-meta text-inkmuted">
          {branches} {branches === 1 ? "branch" : "branches"} · {tools}{" "}
          {tools === 1 ? "tool run" : "tool runs"}
        </span>
        {failed > 0 ? (
          <span className="rounded-sm border border-err/40 bg-err/10 px-1.5 py-0.5 text-meta text-err">
            {failed} failed
          </span>
        ) : null}
        {waiting > 0 ? (
          <span className="rounded-sm border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-meta text-warn">
            {waiting} awaiting approval
          </span>
        ) : null}
      </div>
      {unlinked > 0 ? (
        <p className="mt-1 text-meta text-inkmuted">
          {unlinked} {unlinked === 1 ? "step is" : "steps are"} shown flat: the
          parent link could not be resolved inside this turn.
        </p>
      ) : null}
      <ul className="mt-1.5">
        {nodes.map((node) => (
          <NodeRow key={node.key} node={node} depth={0} live={live} />
        ))}
      </ul>
    </section>
  );
}
