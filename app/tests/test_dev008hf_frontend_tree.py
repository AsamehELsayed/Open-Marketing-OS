"""DEV-008-SKILLS-OPS-HOTFIX W2 -- the execution tree the founder actually sees.

Contract §6 (event order) and §10 (founder prompt) are about what the Chat
surface shows, and this file is the proof for the *frontend* half of them.

Why a transpile-and-exec harness instead of a browser
----------------------------------------------------
The frontend has no test runner (plan §2.4) and this run may not add one. But
the rule that matters is not "no browser" -- it is that the parent/child
resolution must be provable WITHOUT a render, because a render only proves the
pixels, not the structure. So the pure builder `buildExecutionTree` is executed
directly, by Node, against the REAL file:

    ExecutionTree.tsx  --tsc-->  CommonJS  --node-->  buildExecutionTree(rows)

`tsc` is the repo's own TypeScript with `--strict`, so a type error fails the
fixture instead of producing a quietly-broken module. Nothing is translated
into Python and re-implemented: if these assertions pass, the function that
ships passed. No dev server, no browser, no DOM, no network.

WHAT IS PROVEN
--------------
1. `test_planning_root_precedes_the_team`          §6 planning root before team
2. `test_tool_events_nest_under_their_owning_employee`  §6 owner, not flat
3. `test_synthesis_is_rendered_after_the_fan_in`   §6 barrier, then synthesis
4. `test_a_failed_branch_stays_visible`            §8 failure is never hidden
5. `test_employee_that_never_started_still_gets_a_visible_branch`  §8 again
6. `test_live_and_reloaded_events_build_the_identical_tree`  reload parity
7. `test_no_persisted_row_is_ever_dropped`         the never-hide invariant
8. static gates (bottom) -- always run, need no toolchain, so this file is
   never a vacuous skip.

On the wire (`app/services/skills/tree.py`) the Account Manager and the plan are
BOTH parentless, the completed/failed rows point at the PLAN rather than at the
employee, and synthesis points at the plan too. The fixtures below reproduce
those quirks exactly, because a fixture that pre-nested the rows would prove
nothing about the derivation the UI has to perform.

If Node or the frontend toolchain is missing the behavioural tests SKIP with the
real reason -- the same convention the Playwright gates in this repo use -- and
the message says exactly what to install. A skip is never reported as a pass.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "frontend"
TREE_TSX = FRONTEND / "src" / "components" / "skills" / "ExecutionTree.tsx"
TURN_STREAM_TS = FRONTEND / "src" / "hooks" / "useTurnStream.ts"
PERSISTED_HOOK_TS = FRONTEND / "src" / "hooks" / "usePersistedTurnTree.ts"
CHAT_TSX = FRONTEND / "src" / "routes" / "Chat.tsx"
TSC_JS = FRONTEND / "node_modules" / "typescript" / "lib" / "tsc.js"

TURN = "turndev008hf0001"

# One employee id per branch, named the way `_employee_id_for` names them.
EMP_WEB = f"{TURN}-emp0"
EMP_IG = f"{TURN}-emp1"
EMP_SEO = f"{TURN}-emp2"
EMP_NOSTART = f"{TURN}-emp3"

Lifecycle = dict[str, Any]
Tree = list[dict[str, Any]]


# --------------------------------------------------------------- fixtures


def _row(
    row_id: int,
    event_type: str,
    label: str,
    status: str,
    *,
    parent: object = "",
    employee_id: str = "",
    employee_role: str = "",
    tool_id: str = "",
    tool_run_id: str = "",
    skill_id: str = "",
    duration_ms: int | None = None,
    detail: str = "",
    extra: dict[str, Any] | None = None,
) -> Lifecycle:
    meta: dict[str, Any] = {
        "status": status,
        "parent_id": parent,
        "turn_id": TURN,
    }
    if employee_id:
        meta["employee_id"] = employee_id
    if employee_role:
        meta["employee_role"] = employee_role
    if tool_id:
        meta["tool_id"] = tool_id
        meta["tool_run_id"] = tool_run_id
    if skill_id:
        meta["skill_id"] = skill_id
        meta["skill_name"] = skill_id
    if duration_ms is not None:
        meta["duration_ms"] = duration_ms
    if extra:
        meta.update(extra)
    return {
        "id": row_id,
        "type": event_type,
        "label": label,
        "detail": detail,
        "meta": meta,
    }


def compound_turn_rows() -> list[Lifecycle]:
    """A three-employee compound turn in which the Instagram branch FAILS.

    Row ids are the persisted `execution_events.id` sequence, so id order IS the
    producer's emission order -- the basis of the after-fan-in rule. Every
    `parent_id` is a STRING because that is what `TreeEmitter._parent_str`
    actually writes, and the fan-in rows point at the PLAN rather than at their
    own employee, exactly as `TreeEmitter.employee_failed` does.
    """
    return [
        _row(1, "account_manager_started", "Account Manager started", "RUNNING",
             detail="analyse the site, instagram, seo and competitors",
             extra={"route": "compound_marketing_task", "thread_id": TURN}),
        _row(2, "task_planned", "Plan ready", "COMPLETE",
             detail="3 task(s) planned", extra={"task_count": 3, "employee_count": 3}),
        # --- branch 1: website. One real skill, one real tool run, succeeds.
        _row(3, "employee_queued", "Assigned to Website", "QUEUED",
             parent="2", employee_id=EMP_WEB, employee_role="website_marketing",
             detail="audit the marketing site"),
        _row(4, "employee_started", "Website working", "RUNNING",
             parent="2", employee_id=EMP_WEB, employee_role="website_marketing"),
        _row(5, "skill_selected", "Using seo-audit", "COMPLETE",
             parent="4", employee_id=EMP_WEB, skill_id="seo-audit"),
        _row(6, "tool_started", "Running website_marketing_audit", "RUNNING",
             parent="4", employee_id=EMP_WEB, tool_id="website_marketing_audit",
             tool_run_id="run-web-1"),
        _row(7, "tool_completed", "Tool website_marketing_audit finished", "COMPLETE",
             parent="4", employee_id=EMP_WEB, tool_id="website_marketing_audit",
             tool_run_id="run-web-1", duration_ms=1204),
        _row(8, "evidence_added", "Evidence added", "COMPLETE",
             parent="4", employee_id=EMP_WEB, detail="3 source(s) · page",
             extra={"evidence_count": 3, "evidence_kind": "page"}),
        _row(9, "employee_completed", "Website finished", "COMPLETE",
             parent="2", employee_id=EMP_WEB, employee_role="website_marketing",
             duration_ms=1500),
        # --- branch 2: instagram. The tool fails, then the employee fails.
        _row(10, "employee_queued", "Assigned to Instagram", "QUEUED",
             parent="2", employee_id=EMP_IG, employee_role="instagram"),
        _row(11, "employee_started", "Instagram working", "RUNNING",
             parent="2", employee_id=EMP_IG, employee_role="instagram"),
        _row(12, "tool_started", "Running instagram_audit", "RUNNING",
             parent="11", employee_id=EMP_IG, tool_id="instagram_audit",
             tool_run_id="run-ig-1"),
        _row(13, "tool_failed", "Tool instagram_audit could not finish", "FAILED",
             parent="11", employee_id=EMP_IG, tool_id="instagram_audit",
             tool_run_id="run-ig-1",
             detail="No Instagram provider was reachable."),
        _row(14, "employee_failed", "Instagram could not finish", "FAILED",
             parent="2", employee_id=EMP_IG, employee_role="instagram",
             duration_ms=903, detail="The request could not be completed."),
        # --- branch 3: seo. No external tool is needed; that is not a failure.
        _row(15, "employee_queued", "Assigned to Seo", "QUEUED",
             parent="2", employee_id=EMP_SEO, employee_role="seo"),
        _row(16, "employee_started", "Seo working", "RUNNING",
             parent="2", employee_id=EMP_SEO, employee_role="seo"),
        _row(17, "employee_completed", "Seo finished", "COMPLETE",
             parent="2", employee_id=EMP_SEO, employee_role="seo", duration_ms=210),
        # --- the fan-in barrier has passed, then synthesis.
        _row(18, "synthesis_started", "Writing response", "RUNNING", parent="2"),
        _row(19, "synthesis_completed", "Response written", "COMPLETE",
             parent="2", duration_ms=41),
    ]


def started_less_failure_rows() -> list[Lifecycle]:
    """An employee whose `employee_started` never persisted, and which failed.

    Contract §8: a failed branch STAYS. A builder that only nests under a real
    started row renders this as a loose leaf; a builder that filtered on success
    renders nothing at all.
    """
    return [
        _row(1, "account_manager_started", "Account Manager started", "RUNNING"),
        _row(2, "task_planned", "Plan ready", "COMPLETE"),
        _row(3, "employee_started", "Website working", "RUNNING",
             parent="2", employee_id=EMP_WEB, employee_role="website_marketing"),
        _row(4, "employee_completed", "Website finished", "COMPLETE",
             parent="2", employee_id=EMP_WEB, employee_role="website_marketing",
             duration_ms=12),
        # parent_id is empty here: the emitter falls back to the task parent,
        # and there is no task parent to fall back to.
        _row(5, "tool_failed", "Tool instagram_audit could not finish", "FAILED",
             parent="", employee_id=EMP_NOSTART, employee_role="instagram",
             tool_id="instagram_audit", tool_run_id="run-ig-2"),
        _row(6, "employee_failed", "Instagram could not finish", "FAILED",
             parent="2", employee_id=EMP_NOSTART, employee_role="instagram",
             duration_ms=8),
        _row(7, "synthesis_completed", "Response written", "COMPLETE", parent="2"),
    ]


# ------------------------------------------------------------- the harness

_DRIVER = r"""
const fs = require("fs");
const Module = require("module");
// The builder is pure. The module's only runtime import is the automatic JSX
// runtime, which is stubbed so `buildExecutionTree` can be called in plain Node
// with no React, no DOM and no bundler.
const stub = {
  Fragment: "Fragment",
  jsx: () => null,
  jsxs: () => null,
  createElement: () => null,
  default: () => null,
  useState: () => [null, () => {}],
  useEffect: () => {},
  useRef: () => ({ current: null }),
};
const load = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "react" || request.indexOf("react/") === 0) return stub;
  return load.apply(this, arguments);
};
const tree = require(process.argv[2]);
if (typeof tree.buildExecutionTree !== "function") {
  throw new Error("the ExecutionTree module does not export buildExecutionTree");
}
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
process.stdout.write(JSON.stringify(cases.map((events) => tree.buildExecutionTree(events))));
"""


def _skip_reason() -> str | None:
    if shutil.which("node") is None:
        return ("node is not on PATH; this gate executes the real "
                "ExecutionTree.tsx with Node and cannot run without it")
    if not TSC_JS.is_file():
        return (f"the frontend toolchain is not installed ({TSC_JS} is missing); "
                "run `npm ci` in ./frontend to install the repo's own TypeScript")
    return None


_TSC_FLAGS = (
    "--module", "commonjs",
    "--target", "es2020",
    "--jsx", "react-jsx",
    "--moduleResolution", "node",
    "--lib", "es2020,dom,dom.iterable",
    "--esModuleInterop",
    "--skipLibCheck",
    "--strict",
)

# The builder's only import is a TYPE import of `LifecycleItem`. A mutant is
# compiled outside `src/`, so that relative path does not resolve there; this
# is the same shape, declared inline. Nothing else in the file is touched.
_LIFECYCLE_ITEM_STUB = (
    "type LifecycleItem = { id: number; type: string; label: string; "
    "detail: string; meta: Record<string, unknown> };"
)
_TYPE_IMPORT = 'import type { LifecycleItem } from "../../hooks/useTurnStream";'


def _compile(
    work: Path,
    entry_name: str,
    entry: Path,
    *,
    require_clean: bool = True,
) -> Path:
    """Compile one TypeScript entry to CommonJS with the repo's own compiler.

    `entry` may be the real file in `src/` (resolved against the frontend root,
    so its relative imports still work) or a mutant written into a temp dir.
    The emitted module is found by name, because tsc nests the output under the
    common source root.

    `require_clean` is False only for MUTANTS. A mutant lives outside
    `frontend/`, so `react/jsx-runtime`'s types are unreachable from it and tsc
    reports JSX errors it would not report in place; those are an artefact of
    the harness, not of the mutation. tsc emits JavaScript anyway, and the
    mutation is proved by the RUNTIME tree, so a mutant only has to emit.
    """
    node = shutil.which("node") or "node"
    out_dir = work / f"out-{entry_name}"
    empty_types = work / "no-global-types"
    out_dir.mkdir(parents=True, exist_ok=True)
    empty_types.mkdir(parents=True, exist_ok=True)
    root_arg = entry if entry.is_absolute() else FRONTEND / "src" / entry

    done = subprocess.run(
        [node, str(TSC_JS), str(root_arg),
         "--outDir", str(out_dir), "--typeRoots", str(empty_types), *_TSC_FLAGS],
        cwd=str(FRONTEND),
        capture_output=True,
        text=True,
    )
    if require_clean:
        assert done.returncode == 0, (
            f"{entry.name} does not compile under the repo's own TypeScript:\n"
            f"{done.stdout}\n{done.stderr}"
        )
    emitted = sorted(out_dir.rglob(f"{entry_name}.js"))
    assert len(emitted) == 1, (
        f"tsc emitted no runnable module for {entry.name} under {out_dir} "
        f"(returncode={done.returncode}):\n{done.stdout}\n{done.stderr}")
    return emitted[0]


def _make_runner(module_js: Path, work: Path) -> Callable[[Sequence[Sequence[Lifecycle]]], list[Tree]]:
    node = shutil.which("node") or "node"
    driver = work / "driver.cjs"
    if not driver.is_file():
        driver.write_text(_DRIVER, encoding="utf-8")
    cases_file = work / f"cases-{module_js.parent.name}.json"

    def _run(cases: Sequence[Sequence[Lifecycle]]) -> list[Tree]:
        cases_file.write_text(json.dumps([list(c) for c in cases]), encoding="utf-8")
        done = subprocess.run(
            [node, str(driver), str(module_js), str(cases_file)],
            cwd=str(FRONTEND),
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, (
            f"buildExecutionTree threw on the fixture:\n{done.stdout}\n{done.stderr}")
        return json.loads(done.stdout)

    return _run


@pytest.fixture(scope="module")
def build() -> Callable[[Sequence[Sequence[Lifecycle]]], list[Tree]]:
    """Transpile the real component once, then call its pure builder per case."""
    reason = _skip_reason()
    if reason:
        pytest.skip(reason)

    work = Path(tempfile.mkdtemp(prefix="dev008hf-tree-"))
    module_js = _compile(work, "ExecutionTree", TREE_TSX)
    return _make_runner(module_js, work)


# ----------------------------------------------------------------- helpers


def flat(nodes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        out.append(node)
        out.extend(flat(node["children"]))
    return out


def ids(nodes: Iterable[dict[str, Any]]) -> list[int]:
    return [node["id"] for node in nodes]


def by_id(tree: Tree, node_id: int) -> dict[str, Any]:
    for node in flat(tree):
        if node["id"] == node_id:
            return node
    raise AssertionError(f"node {node_id} is not in the tree")


def owner_of(tree: Tree, node_id: int) -> int | None:
    """The id of the node that owns `node_id`, or None when it is a root."""
    for node in flat(tree):
        for child in node["children"]:
            if child["id"] == node_id:
                return node["id"]
    return None


def only(nodes: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(nodes)
    assert len(items) == 1, f"expected exactly one node, got {ids(items)}"
    return items[0]


# ------------------------------------------ 1. planning root, then the team

def test_planning_root_precedes_the_team(build):
    """§6: account_manager_started -> task_planned -> the team.

    On the wire the first two are BOTH parentless, so a literal parent_id
    resolver renders two sibling roots and the founder sees the plan floating
    beside the Account Manager with no relationship to it.
    """
    tree = build([compound_turn_rows()])[0]

    assert len(tree) == 1, f"expected one planning root, got {ids(tree)}"
    root = only(tree)
    assert root["type"] == "account_manager_started", root["type"]
    assert root["kind"] == "account_manager", root["kind"]
    assert root["parentLinkStatus"] == "root"

    plan = by_id(tree, 2)
    assert owner_of(tree, 2) == 1, (
        "task_planned must hang off account_manager_started, not sit beside it")
    assert plan["kind"] == "plan"
    assert plan["parentLinkStatus"] == "linked"

    # The plan owns the queue rows and the branch heads, in emission order.
    assert ids(plan["children"]) == [3, 4, 10, 11, 15, 16], ids(plan["children"])
    branches = [child for child in plan["children"]
                if child["type"] == "employee_started"]
    assert [branch["employeeId"] for branch in branches] == [EMP_WEB, EMP_IG, EMP_SEO]

    # Nothing the team did escaped to the top level or outside the plan.
    for row_id in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17):
        assert owner_of(tree, row_id) is not None, (
            f"row {row_id} was dropped out of the plan's subtree")


# ------------------------------------ 2. tools nest under their OWNING employee

def test_tool_events_nest_under_their_owning_employee(build):
    """§6 hard invariant: a tool event belongs to the employee that ran it.

    Nesting by `meta.employee_id`, not by `parent_id` and never as a flat list.
    A second real tool run on the FIRST branch is what makes the assertion
    falsifiable: with one branch, nesting anywhere would look correct.
    """
    rows = compound_turn_rows()
    rows.append(
        _row(9, "tool_started", "Running web_search", "RUNNING",
             parent=4, employee_id=EMP_WEB, tool_id="web_search",
             tool_run_id="run-web-2"))
    rows.sort(key=lambda row: row["id"])
    tree = build([rows])[0]

    web = by_id(tree, 4)
    assert web["employeeId"] == EMP_WEB
    web_tools = [child for child in web["children"] if child["kind"] == "tool"]
    assert sorted(child["toolId"] for child in web_tools) == [
        "web_search", "website_marketing_audit", "website_marketing_audit"], (
        "the website branch must own ALL THREE of its tool rows, not just one")
    assert all(child["employeeId"] == EMP_WEB for child in web_tools)

    ig = by_id(tree, 11)
    assert ig["employeeId"] == EMP_IG
    ig_tools = [child for child in ig["children"] if child["kind"] == "tool"]
    assert [child["toolId"] for child in ig_tools] == [
        "instagram_audit", "instagram_audit"], (
        "the instagram branch owns its started row AND its failed row")
    assert all(child["employeeId"] == EMP_IG for child in ig_tools)

    # A tool run is never a direct child of the plan or of the manager: that is
    # exactly the "flat list" §6 forbids.
    for parent in [*by_id(tree, 2)["children"], *only(tree)["children"]]:
        assert parent["kind"] != "tool", (
            f"tool run {parent['id']} rendered flat under {parent['type']}")
    # The skill playbook nests with the tools of the same branch.
    assert owner_of(tree, 5) == 4, "skill_selected must nest under its employee"
    assert owner_of(tree, 8) == 4, "evidence_added must nest under its employee"


# ------------------------------------------ 3. the synthesis node, after fan-in

def test_synthesis_is_rendered_after_the_fan_in(build):
    """§6: synthesis is emitted only after every required employee finished.

    It is a sibling of the plan, so the whole team renders above it. The
    ordering is the producer's own id order showing through -- the builder
    sorts by persisted id and does not force synthesis last.
    """
    tree = build([compound_turn_rows()])[0]
    top = only(tree)["children"]
    kinds = [child["kind"] for child in top]
    assert kinds[0] == "plan", f"the plan must render first, got {kinds}"
    assert "synthesis" in kinds, f"no synthesis node rendered: {kinds}"
    assert kinds.index("synthesis") > 0, "synthesis rendered before the team"

    synthesis = [child for child in top if child["kind"] == "synthesis"]
    assert ids(synthesis) == [18, 19], ids(synthesis)
    assert synthesis[-1]["type"] == "synthesis_completed"
    assert synthesis[-1]["status"] == "COMPLETE"

    plan = by_id(tree, 2)
    branches = [child for child in plan["children"]
                if child["type"] == "employee_started"]
    assert len(branches) == 3, ids(plan["children"])
    for branch in branches:
        assert branch["id"] < synthesis[0]["id"], (
            "an employee branch was emitted after the fan-in barrier")


# ------------------------------------------------- 4. a failed branch stays

def test_a_failed_branch_stays_visible(build):
    """§8: one branch failing must not hide it, drop it, or cancel a sibling."""
    tree = build([compound_turn_rows()])[0]

    tool_failed = by_id(tree, 13)
    assert tool_failed["type"] == "tool_failed"
    assert tool_failed["kind"] == "tool"
    assert tool_failed["status"] == "FAILED", (
        "a failed tool run must render as a failure, not as complete")
    assert owner_of(tree, 13) == 11, (
        "the failed tool run must stay inside the branch that ran it")

    employee_failed = by_id(tree, 14)
    assert employee_failed["type"] == "employee_failed"
    assert employee_failed["status"] == "FAILED"
    assert employee_failed["employeeId"] == EMP_IG
    assert owner_of(tree, 14) == 11, (
        "employee_failed must nest under its OWN branch, not sit loose on the plan")

    # The branch row itself says the branch failed, so a `RUNNING` started row
    # can never read as a healthy team.
    ig_branch = by_id(tree, 11)
    assert ig_branch["failedDescendant"] is True
    assert ids(ig_branch["children"]) == [12, 13, 14]

    # Siblings are untouched: the website and seo branches completed normally.
    assert by_id(tree, 9)["status"] == "COMPLETE"
    assert by_id(tree, 17)["status"] == "COMPLETE"
    assert by_id(tree, 4)["failedDescendant"] is False
    assert by_id(tree, 16)["failedDescendant"] is False

    # A failure does not truncate the turn: the synthesis still renders.
    assert by_id(tree, 19)["status"] == "COMPLETE"


def test_employee_that_never_started_still_gets_a_visible_branch(build):
    """A failure whose `employee_started` never persisted is still shown.

    The branch node here is DERIVED -- a grouping node, not a persisted event --
    and is flagged as such so it can never be read as one.
    """
    tree = build([started_less_failure_rows()])[0]
    plan = by_id(tree, 2)
    failed = [child for child in plan["children"]
              if child["employeeId"] == EMP_NOSTART]
    assert len(failed) == 1, (
        f"the started-less failure has no branch: {ids(plan['children'])}")
    branch = only(failed)
    assert branch["derived"] is True
    assert branch["id"] == 0, (
        "a derived branch carries no persisted row, so it must not borrow one")
    assert branch["status"] == "FAILED", (
        "a branch that only failed must be FAILED on the branch row")
    assert branch["employeeRole"] == "instagram"

    # Its tool failure and its employee failure are inside that branch, not
    # loose on the plan.
    assert ids(branch["children"]) == [5, 6], ids(branch["children"])
    assert by_id(tree, 5)["status"] == "FAILED"
    assert by_id(tree, 6)["status"] == "FAILED"
    assert owner_of(tree, 6) == 0, "employee_failed must sit inside the branch"
    # The healthy sibling is unaffected.
    assert by_id(tree, 3)["derived"] is False
    assert by_id(tree, 3)["failedDescendant"] is False


def test_a_numeric_parent_id_still_links(build):
    """A `parent_id` that arrives as a JSON number is a link, not "no parent".

    The emitter stringifies it, so this is defence at a trust boundary — but
    reading only strings once flattened an entire team to sibling roots, which
    is precisely the silently-wrong tree this component must never render.
    """
    rows = compound_turn_rows()
    for row in rows:
        parent = row["meta"]["parent_id"]
        row["meta"]["parent_id"] = int(parent) if parent else ""
    tree = build([rows])[0]

    assert len(tree) == 1, f"a numeric parent_id scattered the tree: {ids(tree)}"
    assert ids(by_id(tree, 2)["children"]) == [3, 4, 10, 11, 15, 16]
    assert ids(by_id(tree, 4)["children"]) == [5, 6, 7, 8, 9]


# ------------------------------------- 5. live rows == reloaded rows, exactly

def test_live_and_reloaded_events_build_the_identical_tree(build):
    """The one property that makes a reload trustworthy.

    The live stream and the post-reload replay deliver the SAME persisted rows
    in different orders: SSE interleaves concurrent employee branches, and the
    replay re-reads them from the event table. The builder must return a
    byte-identical tree for all of them, or the founder watches the execution
    change under them by reloading the page.
    """
    rows = compound_turn_rows()
    orders = _arrival_orders(rows)
    trees = dict(zip(_ARRIVAL_ORDERS, build(list(orders.values()))))
    reference = trees["persisted"]
    for label in _ARRIVAL_ORDERS[1:]:
        assert trees[label] == reference, (
            f"the {label} tree disagrees with the persisted-order tree for the "
            f"same rows:\nreference={json.dumps(reference)}\n{label}="
            f"{json.dumps(trees[label])}")
    print(f"\nmeasured: {len(trees)} arrival orders of {len(rows)} persisted rows "
          f"-> one identical tree with {len(reference)} root(s) {ids(reference)}")


def test_terminal_replay_closes_root_employee_tool_and_synthesis(build):
    """Terminal rows project onto the matching started rows after reload."""
    rows = compound_turn_rows()
    rows.extend([
        _row(20, "turn_completed", "Response ready", "COMPLETE"),
    ])
    rows[-1]["meta"] = {}
    tree = build([rows])[0]
    assert build([list(reversed(rows))])[0] == tree
    assert by_id(tree, 1)["status"] == "COMPLETE"
    assert by_id(tree, 4)["status"] == "COMPLETE"
    assert by_id(tree, 6)["status"] == "COMPLETE"
    assert by_id(tree, 11)["status"] == "FAILED"
    assert by_id(tree, 12)["status"] == "FAILED"
    assert by_id(tree, 18)["status"] == "COMPLETE"

    failed_rows = [row for row in rows if row["id"] != 20]
    failed_rows.append({
        "id": 20, "type": "turn_failed", "label": "Interrupted",
        "detail": "", "meta": {},
    })
    failed_tree = build([failed_rows])[0]
    assert by_id(failed_tree, 1)["status"] == "FAILED"
    assert build([list(reversed(failed_rows))])[0] == failed_tree


def test_in_flight_rows_keep_running_until_matching_terminal_events(build):
    rows = [
        _row(1, "account_manager_started", "Account Manager started", "RUNNING"),
        _row(2, "task_planned", "Plan ready", "COMPLETE"),
        _row(3, "employee_started", "Website working", "RUNNING",
             parent="2", employee_id=EMP_WEB, employee_role="website_marketing"),
        _row(4, "tool_started", "Running website audit", "RUNNING",
             parent="3", employee_id=EMP_WEB, tool_id="website_marketing_audit",
             tool_run_id="pending-run"),
        _row(5, "synthesis_started", "Writing response", "RUNNING", parent="2"),
    ]
    tree = build([rows])[0]
    assert by_id(tree, 1)["status"] == "RUNNING"
    assert by_id(tree, 3)["status"] == "RUNNING"
    assert by_id(tree, 4)["status"] == "RUNNING"
    assert by_id(tree, 5)["status"] == "RUNNING"


# --------------------------------------------- 6. nothing is ever dropped

def test_no_persisted_row_is_ever_dropped(build):
    """Every row carrying a tree status is rendered exactly once.

    A tree that quietly drops a node is worse than one that shows it with a
    warning, so the failure mode this forbids is silence, not ugliness.
    """
    cases = build([compound_turn_rows(), started_less_failure_rows()])
    for rows, tree in zip((compound_turn_rows(), started_less_failure_rows()), cases):
        rendered = [node for node in flat(tree) if not node["derived"]]
        assert sorted(node["id"] for node in rendered) == sorted(
            row["id"] for row in rows), (
            "the rendered tree does not contain exactly the persisted rows")
        # Structural nodes are few, marked, and carry no invented event type.
        derived = [node for node in flat(tree) if node["derived"]]
        assert all(node["key"].startswith("branch:") for node in derived)
        assert all(node["type"] == "employee_branch" for node in derived)
        assert all(node["employeeId"] for node in derived)


# ------------------------------- 7. the gate above is falsifiable (mutation)
#
# A green test proves nothing unless it CAN go red. Each mutant below removes
# exactly one of the derivations, and the mutation test asserts the mutant's
# runtime tree differs from the real builder's. If someone later weakens an
# assertion to match a weakened builder, this fails.
#
# Each mutant is a list of (needle, replacement) pairs, because one of the
# invariants is enforced in three places: `rows.sort` normalises the input and
# the two per-level sorts normalise the output. Any ONE of them alone already
# makes the tree order-insensitive, so removing a single one is NOT a
# behavioural change -- the order mutant has to remove all three to actually
# let arrival order through. That redundancy is deliberate (the input sort keeps
# the linking pass simple, the per-level sorts keep derived branches in place),
# and it is why this test removes all three rather than pretending one is
# load-bearing on its own.

_MUTANTS = {
    "no_ownership_nesting": (
        [("OWNED_KINDS.has(row.kind)", "false")],
        "tool/skill/evidence rows stop nesting under their owning employee",
    ),
    "plan_not_under_manager": (
        [("parent = managerRow;", "parent = null;")],
        "task_planned stops hanging off account_manager_started",
    ),
    "synthesis_stays_on_the_plan": (
        [('parent = planningRoot;\n      link = planningRoot ? "linked" : "root";',
          'parent = null;\n      link = "root";')],
        "synthesis stops being ordered after the fan-in",
    ),
    "no_ordering": (
        [
            ("rows.sort(byOrderThenKey);", ""),
            ("node.children.map(toTreeNode).sort(byOrderThenKey)",
             "node.children.map(toTreeNode)"),
            ("roots.sort(byOrderThenKey).map(toTreeNode)", "roots.map(toTreeNode)"),
        ],
        "sibling order follows ARRIVAL order again, so live != reloaded",
    ),
}

# Four arrival orders of the same persisted rows. A mutation is proved by
# breaking the invariant on ANY of them, which is what a behavioural assertion
# in this file would do.
_ARRIVAL_ORDERS = ("persisted", "live", "reloaded", "interleaved")


def _arrival_orders(rows: list[Lifecycle]) -> dict[str, list[Lifecycle]]:
    live = [
        rows[0], rows[1],
        rows[2], rows[9],
        rows[3], rows[10],
        rows[4], rows[11],
        rows[14], rows[5], rows[12],
        rows[6], rows[15],
        rows[7], rows[8], rows[16],
        rows[13], rows[17],
        rows[18],
    ]
    half = len(rows) // 2
    return {
        "persisted": list(rows),
        "live": live,
        "reloaded": list(reversed(rows)),
        "interleaved": list(rows[half:]) + list(rows[:half]),
    }


def test_removing_a_derivation_breaks_the_assertions_that_cover_it(build):
    """Each derivation is load-bearing: delete it and some order renders wrong."""
    reason = _skip_reason()
    if reason:
        pytest.skip(reason)

    work = Path(tempfile.mkdtemp(prefix="dev008hf-mutant-"))
    mutant_src = work / "src"
    mutant_src.mkdir(parents=True, exist_ok=True)
    source = TREE_TSX.read_text(encoding="utf-8")
    rows = compound_turn_rows()
    orders = _arrival_orders(rows)
    for name, events in orders.items():
        assert sorted(ids(events)) == sorted(ids(rows)), (
            f"the {name} arrival order is not a reordering of the same rows")
    baseline = dict(zip(_ARRIVAL_ORDERS, build(list(orders.values()))))

    for name, (edits, description) in _MUTANTS.items():
        mutant_source = source.replace(_TYPE_IMPORT, _LIFECYCLE_ITEM_STUB, 1)
        for needle, replacement in edits:
            assert mutant_source.count(needle) == 1, (
                f"the mutation target for {name!r} is not uniquely present in "
                f"ExecutionTree.tsx: {source.count(needle)} occurrences of "
                f"{needle!r}, {mutant_source.count(needle)} after the earlier "
                "edits of this mutant")
            mutant_source = mutant_source.replace(needle, replacement, 1)
        entry = mutant_src / f"{name}.tsx"
        entry.write_text(mutant_source, encoding="utf-8")
        run = _make_runner(
            _compile(work, name, entry, require_clean=False), work)
        mutant = dict(zip(_ARRIVAL_ORDERS, run(list(orders.values()))))
        differences = [
            order for order in _ARRIVAL_ORDERS
            if mutant[order] != baseline[order]
        ]
        assert differences, (
            f"the {name} mutation changed nothing ({description}) -- the "
            "behavioural assertions cannot be relying on it")
        print(f"\nmeasured {name}: {description} -> differs on {differences}")



# ---------------------------------------------- always-on static gates
# These need no toolchain, so the file is never a vacuous skip.


def test_tree_builder_is_exported_pure_and_ownership_driven():
    """The tested function is the shipped one, and it keys on `employee_id`."""
    source = TREE_TSX.read_text(encoding="utf-8")
    assert "export function buildExecutionTree(" in source
    assert 'meta?.["employee_id"]' in source
    assert "OWNED_KINDS" in source, (
        "tool/skill/evidence rows must be nested by ownership, not flat")
    for forbidden in ("setTimeout", "setInterval", "Math.random", "Date.now",
                      "performance.now", "fetch("):
        assert forbidden not in source, (
            f"the tree builder must be a pure function of its rows: {forbidden}")


def test_both_stream_paths_share_one_lifecycle_filter():
    """The live hook and the replay hook must not keep private copies.

    They used to. The replay skipped `turn_completed` / `turn_failed` while the
    live hook appended them, so a reloaded tree was two nodes shorter than the
    live tree for the very same turn. One exported predicate cannot drift from
    itself, which is what makes requirement (6) above achievable at all.
    """
    live = TURN_STREAM_TS.read_text(encoding="utf-8")
    replay = PERSISTED_HOOK_TS.read_text(encoding="utf-8")
    assert "export const NON_LIFECYCLE_EVENT_TYPES" in live
    assert "export function isLifecycleEventType(" in live
    assert re.search(r"if\s*\(!isLifecycleEventType\(e\.type\)\)", live), (
        "the live hook must apply the shared filter, not inline its own list")
    assert re.search(r"if\s*\(!isLifecycleEventType\(e\.type\)\s*\|\|", replay), (
        "the replay hook must apply the same shared filter")

    # The turn terminals are lifecycle facts, not transport, so neither path
    # may filter them out: they are what makes a live tree and a reloaded tree
    # the same length.
    filter_body = _array_body(live, "NON_LIFECYCLE_EVENT_TYPES")
    for terminal in ("turn_completed", "turn_failed"):
        assert terminal not in filter_body, (
            f"{terminal} is a lifecycle row and must not be filtered from the tree")
    for carried in ("assistant_delta", "model_delta", "assistant_completed",
                    "model_completed", "turn_closed", "stream_end", "message"):
        assert carried in filter_body, (
            f"{carried} must be filtered out of the lifecycle list by BOTH paths")


def _array_body(source: str, name: str) -> str:
    match = re.search(
        r"(?:const|export const)\s+" + re.escape(name) + r"[^=]*=\s*new Set\(\["
        r"(?P<body>.*?)\]\)",
        source,
        re.S,
    )
    assert match, f"no exported Set literal named {name!r} in useTurnStream.ts"
    return match.group("body")


def test_chat_renders_the_tree_from_both_streams_mutually_exclusively():
    """Live while a turn runs, persisted after a reload, never both at once."""
    source = CHAT_TSX.read_text(encoding="utf-8")
    assert re.search(r"<ExecutionTree\s+events=\{stream\.events\}\s*/>", source), (
        "the live <ExecutionTree events={stream.events} /> render is gone")
    assert re.search(
        r"<ExecutionTree\s+events=\{persisted\.events\}\s+live=\{false\}\s*/>",
        source,
    ), "the post-reload <ExecutionTree events={persisted.events} live={false} /> is gone"
    assert "usePersistedTurnTree" in source
    # The persisted path is gated on the ABSENCE of a live turn, so the two
    # trees can never both be on screen.
    assert re.search(r"!activeTurnId\s*&&\s*persisted\.turnId", source), (
        "the replayed tree is not gated on there being no live turn")
    # Chat must not open a second stream: the hooks own the transport.
    assert "streamTurnEvents(" not in source, (
        "Chat must not attach its own stream")
