"""DEV-008-SKILLS-OPS W6: the tool permission gate and retry_policy are real.

The defect this suite exists for
-------------------------------
``ToolRecord.permission_level`` was validated in ``__post_init__`` and
advertised through ``Registry.specs()``, and then **read by nothing**. A tool
declared ``yellow`` ran exactly like one declared ``green``. The same was true of
``retry_policy``. A declared field nobody reads is documentation, not a control,
and ``specs()`` handing the level to the model made the product *look* like it
enforced one.

The four things proved here, each structurally rather than by inspection:

1. A tool in ``APPROVAL_REQUIRED_LEVELS`` is **refused** by ``execute`` with no
   approval id, and a spy handler that records every call proves the handler was
   never invoked. Not "an error was returned" -- the handler's call list is
   empty.
2. ``retry_policy="once"`` gives a failed **read** tool exactly one retry,
   observable as exactly one ``tool_started`` with ``status="RETRYING"`` and one
   with ``status="RUNNING"``, both carrying the same ``tool_run_id``.
3. Nothing regressed for a ``green`` tool, including the error strings and the
   single ``tool_runs`` row per call that the frozen v0.2 surface promises.
4. (W6C) retry is an idempotency privilege: a failing **write** /
   ``external_action`` / ``destructive`` tool is NEVER retried -- exactly one
   handler call whatever ``retry_policy`` says -- while a ``read`` tool with the
   same policy retries exactly once. "none" is clamped with the non-reads:
   absence of a declared effect is not evidence of idempotence, so the
   conservative read of "may have acted" wins. This uses the existing
   ``ToolRecord.side_effect`` vocabulary only; no tool's classification changed.

**Policy is untouched.** Every native tool still declares
``permission_level="green"``, and this suite asserts exactly that -- re-declaring
a tool's safety level is a founder decision and is out of scope (plan §8.6). The
gate is therefore armed and idle in production, which is the honest state, and
it is why the gate has to be proven with a synthetic record instead of a real
tool. ``test_no_native_tool_was_reclassified`` is the tripwire for that: if a
future worker quietly promotes a tool, this suite fails and the change has to be
made deliberately. The count is **measured, never asserted**: the plan's "17
tools" figure counted ``side_effect="green"`` source literals, and
``register_state_tools`` builds six tools from one literal, so the real
denominator is 20.
"""
from __future__ import annotations

import inspect
import json

import pytest

from app.database.seed import ensure_seed
from app.database.sqlite import connect
from app.services.tools import build_default_registry
from app.services.tools.registry import (
    APPROVAL_REQUIRED_LEVELS,
    ARG_SUMMARY_MAX_KEYS,
    NEEDS_APPROVAL_STATUS,
    PERMISSION_LEVELS,
    RETRY_BOUNDED_ATTEMPTS,
    RETRY_POLICIES,
    RETRY_SAFE_SIDE_EFFECTS,
    Registry,
    ToolRecord,
)


def _db(tmp_path, name="perm.db"):
    conn = connect(tmp_path / name)
    ensure_seed(conn)
    return conn


class Spy:
    """A handler that records every call, so "was it invoked?" is answerable.

    The refusal test is only meaningful if it can distinguish *not called* from
    *called and returned a failure*. A plain lambda cannot: both look like
    ``ok is False``.
    """

    def __init__(self, results=None, default=None):
        self.calls: list[dict] = []
        self._results = list(results or [])
        self._default = default

    def __call__(self, conn, *, project_id, root, args):
        self.calls.append({"project_id": project_id, "root": root, "args": args})
        if self._results:
            outcome = self._results.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            if callable(outcome):
                return outcome(len(self.calls))
            return outcome
        if self._default is not None:
            return self._default
        return {"ok": True, "served_by": "spy"}

    @property
    def call_count(self) -> int:
        return len(self.calls)


def _record(tool_id, *, permission_level="green", retry_policy="none",
            side_effect="write", **kwargs):
    base = dict(
        tool_id=tool_id,
        source_type="native",
        description=f"synthetic record for {tool_id}",
        parameters={},
        side_effect=side_effect,
    )
    return ToolRecord(permission_level=permission_level, retry_policy=retry_policy,
                      **base, **kwargs)


# ---- 0. the W6C retry idempotency guard ---------------------------------


def test_the_constant_buckets_the_whole_existing_vocabulary():
    """The guard reuses the existing vocabulary; nothing new and nothing lost.

    Structurally proves (a) no new side_effect value was invented, (b) every
    vocabulary member is accounted for by the guard, and (c) no tool's declared
    level was re-classified by this change.
    """
    from app.services.tools.registry import SIDE_EFFECTS

    assert set(RETRY_SAFE_SIDE_EFFECTS) == {"read"}
    assert set(RETRY_SAFE_SIDE_EFFECTS) < set(SIDE_EFFECTS), (
        "the guard must name a strict subset of the existing vocabulary"
    )
    assert set(RETRY_SAFE_SIDE_EFFECTS) | set(SIDE_EFFECTS) == set(SIDE_EFFECTS)
    records = build_default_registry().list_tools("starter")
    assert records
    assert all(r.side_effect in SIDE_EFFECTS for r in records)
    assert all(r.permission_level == "green" for r in records), (
        "the retry guard must not need or cause any re-classification"
    )


def test_the_guard_is_read_in_execute_not_decorative():
    """Guards the guard: a refactor that drops it must leave a fingerprint."""
    import inspect

    source = inspect.getsource(Registry.execute)
    assert "_attempts_for" in source
    assert "rec.side_effect" in source
    assert "RETRY_SAFE_SIDE_EFFECTS" in inspect.getsource(
        __import__("app.services.tools.registry", fromlist=["x"]))


def test_every_non_read_side_effect_is_never_retried(tmp_path):
    """The departure's core table: one test per non-read vocabulary member.

    A handler that **fails first and succeeds second** would expose a real
    retry: if the guard leaks, the spy's call list lengthens and the
    ``RETRYING`` start fires. Neither happens, for any of the four clamped
    effects.
    """
    for effect in ("write", "external_action", "destructive", "none"):
        conn = _db(tmp_path, f"guard_{effect}.db")
        reg = Registry()
        spy = Spy([{"ok": False, "status": "failed"}, {"ok": True}])
        reg.register_tool(_record(f"guard.{effect}", retry_policy="once",
                                  side_effect=effect))
        reg.bind_handler(f"guard.{effect}", spy)
        events = _Events()

        out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                          name=f"guard.{effect}", args={}, on_event=events)
        assert spy.call_count == 1, (
            f"side_effect={effect!r} ran {spy.call_count} times: a tool that "
            f"may have already acted must never get a second attempt"
        )
        assert out["ok"] is False, (
            "a single failed attempt must be reported as the call's result, "
            "not papered over by an idempotency-unsafe retry"
        )
        assert [p["status"] for p in events.of("tool_started")] == ["RUNNING"]
        assert events.types().count("tool_failed") == 1
        conn.close()


def test_retry_policy_once_retries_a_read_tool_exactly_once(tmp_path):
    """The flip side: a read keeps its granted policy, because a re-read is safe."""
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy([{"ok": False, "error": "transient", "status": "failed"},
               {"ok": True, "answer": 42}])
    reg.register_tool(_record("guard.read_retry", side_effect="read",
                              retry_policy="once"))
    reg.bind_handler("guard.read_retry", spy)
    events = _Events()

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="guard.read_retry", args={}, on_event=events)
    assert out["ok"] is True and out["answer"] == 42
    assert spy.call_count == 2
    assert [p["status"] for p in events.of("tool_started")] == ["RUNNING", "RETRYING"]
    assert len({p["tool_run_id"] for p in events.of("tool_started")}) == 1
    assert events.types().count("tool_completed") == 1
    conn.close()


def test_bounded_retries_a_read_but_not_a_write(tmp_path):
    """The guard decides whether a retry happens at all, not just sometimes.

    Same policy on both sides of the line: the read gets three attempts, the
    write gets one. If these two ever agree, the guard is decorative.
    """
    conn = _db(tmp_path, "bounded_pair.db")
    reg = Registry()
    read_spy = Spy(default={"ok": False, "status": "failed"})
    write_spy = Spy(default={"ok": False, "status": "failed"})
    reg.register_tool(_record("guard.bounded_read", side_effect="read",
                              retry_policy="bounded"))
    reg.register_tool(_record("guard.bounded_write", side_effect="write",
                              retry_policy="bounded"))
    reg.bind_handler("guard.bounded_read", read_spy)
    reg.bind_handler("guard.bounded_write", write_spy)
    events = _Events()

    reg.execute(conn, project_id="starter", root=str(tmp_path),
                name="guard.bounded_read", args={}, on_event=events)
    reg.execute(conn, project_id="starter", root=str(tmp_path),
                name="guard.bounded_write", args={}, on_event=events)

    assert read_spy.call_count == RETRY_BOUNDED_ATTEMPTS
    assert write_spy.call_count == 1
    statuses = [p["status"] for p in events.of("tool_started")]
    assert statuses == (["RUNNING"] + ["RETRYING"] * (RETRY_BOUNDED_ATTEMPTS - 1)
                        + ["RUNNING"]), statuses
    conn.close()


def test_the_guard_is_a_clamp_not_a_permission_grant():
    """A read with retry_policy=none still gets one attempt, never more."""
    from app.services.tools.registry import _attempts_for

    assert _attempts_for("once", "read") == 2
    assert _attempts_for("bounded", "read") == RETRY_BOUNDED_ATTEMPTS
    for effect in ("write", "external_action", "destructive", "none"):
        for policy in () or RETRY_POLICIES:
            assert _attempts_for(policy, effect) == 1, (policy, effect)
    assert _attempts_for("once") == 1, "the frozen single-arg call must stay valid"


def test_the_approval_gate_still_applies_to_a_read_with_permission(tmp_path):
    """The retry guard narrows retries; it must not widen anything else."""
    conn = _db(tmp_path, "guard_gate.db")
    reg = Registry()
    spy = Spy()
    reg.register_tool(_record("guard.read_gate", permission_level="yellow",
                              side_effect="read", retry_policy="once"))
    reg.bind_handler("guard.read_gate", spy)

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="guard.read_gate", args={})
    assert out["status"] == NEEDS_APPROVAL_STATUS
    assert spy.call_count == 0
    conn.close()


# ---- 1. the gate ---------------------------------------------------------


def test_yellow_without_approval_is_refused_and_the_handler_never_runs(tmp_path):
    """The acceptance case, asserted on the handler's own call list."""
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy()
    reg.register_tool(_record("perm.yellow", permission_level="yellow"))
    reg.bind_handler("perm.yellow", spy)

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="perm.yellow", args={"do": "something irreversible"})

    assert out["ok"] is False
    assert out["status"] == NEEDS_APPROVAL_STATUS
    assert out["status"] != "failed", (
        "a refusal is not a failure: nothing ran, so calling it failed would "
        "teach a caller to retry a gate"
    )
    assert out["permission_level"] == "yellow"
    assert out["tool_id"] == "perm.yellow"
    assert spy.call_count == 0, f"the gate let the handler run: {spy.calls}"
    conn.close()


def test_red_without_approval_is_refused_too(tmp_path):
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy()
    reg.register_tool(_record("perm.red", permission_level="red"))
    reg.bind_handler("perm.red", spy)

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="perm.red", args={})
    assert out["status"] == NEEDS_APPROVAL_STATUS
    assert spy.call_count == 0
    conn.close()


@pytest.mark.parametrize("approval_id", ["", "   ", None])
def test_a_blank_approval_id_is_not_an_approval(tmp_path, approval_id):
    """Whitespace is not a capability. An empty string must not open the gate."""
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy()
    reg.register_tool(_record("perm.blank", permission_level="yellow"))
    reg.bind_handler("perm.blank", spy)

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="perm.blank", args={}, approval_id=approval_id)
    assert out["status"] == NEEDS_APPROVAL_STATUS
    assert spy.call_count == 0
    conn.close()


def test_with_an_approval_id_the_tool_runs(tmp_path):
    """The gate opens; it is a gate, not a blanket refusal."""
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy()
    reg.register_tool(_record("perm.approved", permission_level="yellow"))
    reg.bind_handler("perm.approved", spy)

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="perm.approved", args={"x": 1}, approval_id="appr-42")
    assert out["ok"] is True
    assert spy.call_count == 1
    assert spy.calls[0]["args"] == {"x": 1}
    conn.close()


def test_green_needs_no_approval_and_behaves_exactly_as_before(tmp_path):
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy()
    reg.register_tool(_record("perm.green", permission_level="green"))
    reg.bind_handler("perm.green", spy)

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="perm.green", args={})
    assert out["ok"] is True and spy.call_count == 1
    conn.close()


def test_a_refusal_is_telemetered_so_it_is_auditable(tmp_path):
    """A gate nobody can see is indistinguishable from a tool that never ran."""
    conn = _db(tmp_path)
    reg = Registry()
    reg.register_tool(_record("perm.audited", permission_level="yellow"))
    reg.bind_handler("perm.audited", Spy())

    reg.execute(conn, project_id="starter", root=str(tmp_path),
                name="perm.audited", args={})
    rows = [dict(r) for r in conn.execute(
        "SELECT tool_id, project_id, status FROM tool_runs").fetchall()]
    assert len(rows) == 1
    assert rows[0]["tool_id"] == "perm.audited"
    assert rows[0]["project_id"] == "starter"
    assert rows[0]["status"] == "failed", (
        "tool_runs.status is a two-value success/failed column; a refusal is "
        "recorded as not-succeeded. Changing that column is not this packet."
    )
    conn.close()


def test_a_refused_tool_is_never_retried(tmp_path):
    """A gate is not a failure to recover from: retry_policy must not touch it."""
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy()
    reg.register_tool(_record("perm.noretry", permission_level="yellow",
                              retry_policy="bounded"))
    reg.bind_handler("perm.noretry", spy)

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="perm.noretry", args={})
    assert out["status"] == NEEDS_APPROVAL_STATUS
    assert spy.call_count == 0
    rows = conn.execute("SELECT COUNT(*) AS n FROM tool_runs").fetchone()["n"]
    assert rows == 1, "one refused call is one row, however generous the policy"
    conn.close()


def test_the_gate_sits_before_the_handler_is_even_looked_up(tmp_path):
    """Placement is the mechanism.

    The gate is checked after identity/scope/enabled and before the handler is
    resolved, so no future pre-handler work can land on the wrong side of it.
    This asserts the observable consequence: a yellow tool with **no handler
    bound** still refuses with ``needs_approval`` rather than reporting
    ``no handler bound`` -- proof the gate ran first.
    """
    conn = _db(tmp_path)
    reg = Registry()
    reg.register_tool(_record("perm.unbound", permission_level="yellow"))

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="perm.unbound", args={})
    assert out["status"] == NEEDS_APPROVAL_STATUS, (
        "the gate must be evaluated before the handler lookup"
    )
    assert "no handler bound" not in out["error"]
    conn.close()


def test_execute_still_defaults_to_the_legacy_call_shape():
    """Every existing call site passes five arguments. That must keep working."""
    sig = inspect.signature(Registry.execute)
    for name in ("conn", "project_id", "root", "name", "args"):
        assert name in sig.parameters
    for name in ("approval_id", "on_event"):
        assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters[name].default is None, (
            f"{name} must default to the old behaviour or every existing "
            f"caller changes"
        )


def test_permission_level_is_actually_read_somewhere_in_the_module():
    """Guards against the field going back to being decorative.

    Reads the module source and proves ``execute``'s own body mentions the
    policy constant. A future refactor that drops the check while leaving the
    dataclass validation intact would otherwise pass every behavioural test
    above only if someone also removed them.
    """
    import inspect as _inspect

    import app.services.tools.registry as module

    source = _inspect.getsource(module.Registry.execute)
    assert "APPROVAL_REQUIRED_LEVELS" in source
    assert "_has_approval" in source
    assert module.APPROVAL_REQUIRED_LEVELS == ("yellow", "red")
    assert set(APPROVAL_REQUIRED_LEVELS) < set(PERMISSION_LEVELS)


# ---- 2. retry_policy -----------------------------------------------------


class _Events:
    """Collects ``(event_type, payload)`` pairs from the registry's ``on_event``."""

    def __init__(self):
        self.seen: list[tuple[str, dict]] = []

    def __call__(self, event_type, payload):
        self.seen.append((event_type, dict(payload or {})))

    def types(self) -> list[str]:
        return [name for name, _ in self.seen]

    def of(self, event_type) -> list[dict]:
        return [payload for name, payload in self.seen if name == event_type]


def test_once_retries_exactly_once_and_emits_retrying_then_running(tmp_path):
    """The plan's §6.4 case: a failing read tool, then success on the retry."""
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy([{"ok": False, "error": "transient", "status": "failed"},
               {"ok": True, "answer": 42}])
    reg.register_tool(_record("retry.raise_ok", side_effect="read",
                              retry_policy="once"))
    reg.bind_handler("retry.raise_ok", spy)
    events = _Events()

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="retry.raise_ok", args={}, on_event=events)

    assert out["ok"] is True and out["answer"] == 42
    assert spy.call_count == 2, f"once means exactly one retry, got {spy.call_count}"

    started = events.of("tool_started")
    assert [p["status"] for p in started] == ["RUNNING", "RETRYING"], (
        "the frozen contract (§1.3.4) is: first attempt RUNNING, the second "
        f"tool_started for the same tool_run_id is RETRYING. Got "
        f"{[p['status'] for p in started]}"
    )
    assert len({p["tool_run_id"] for p in started}) == 1, (
        "a retry belongs under its first attempt, so both must share one id"
    )
    assert [p["attempt"] for p in started] == [1, 2]
    assert all(p["attempt_limit"] == 2 for p in started)
    assert events.types().count("tool_completed") == 1
    assert events.types().count("tool_failed") == 0
    completed = events.of("tool_completed")[0]
    assert completed["status"] == "COMPLETE" and completed["attempts"] == 2
    conn.close()


def test_once_retries_a_handler_that_raises_too(tmp_path):
    """'Fails' is read both ways: a returned failure and an escaped exception.

    Which one a plan author meant by "a handler that fails once" is genuinely
    ambiguous, so both are proved rather than one being quietly chosen.
    """
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy([RuntimeError("transient upstream"), {"ok": True, "ok_again": True}])
    reg.register_tool(_record("retry.raises", side_effect="read",
                              retry_policy="once"))
    reg.bind_handler("retry.raises", spy)
    events = _Events()

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="retry.raises", args={}, on_event=events)

    assert out["ok"] is True
    assert spy.call_count == 2
    assert [p["status"] for p in events.of("tool_started")] == ["RUNNING", "RETRYING"]
    conn.close()


def test_once_gives_up_after_the_second_failure_and_reports_the_last_error(tmp_path):
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy([{"ok": False, "error": "first", "status": "failed"},
               {"ok": False, "error": "second", "status": "failed"}])
    reg.register_tool(_record("retry.givesup", side_effect="read",
                              retry_policy="once"))
    reg.bind_handler("retry.givesup", spy)
    events = _Events()

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="retry.givesup", args={}, on_event=events)

    assert spy.call_count == 2, "once means once, not 'until it works'"
    assert out["ok"] is False
    assert events.types().count("tool_failed") == 1
    assert events.types().count("tool_completed") == 0
    assert [p["status"] for p in events.of("tool_started")] == ["RUNNING", "RETRYING"]
    conn.close()


def test_a_first_attempt_success_is_never_retried(tmp_path):
    """A retry that fires on success would double a write side effect."""
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy()
    reg.register_tool(_record("retry.first_ok", side_effect="read",
                              retry_policy="once"))
    reg.bind_handler("retry.first_ok", spy)
    events = _Events()

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="retry.first_ok", args={}, on_event=events)
    assert out["ok"] is True
    assert spy.call_count == 1
    assert [p["status"] for p in events.of("tool_started")] == ["RUNNING"]
    conn.close()


def test_retry_policy_none_never_retries(tmp_path):
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy([{"ok": False, "status": "failed"}, {"ok": True}])
    reg.register_tool(_record("retry.none", side_effect="read",
                              retry_policy="none"))
    reg.bind_handler("retry.none", spy)
    events = _Events()

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="retry.none", args={}, on_event=events)
    assert spy.call_count == 1
    assert out["ok"] is False
    assert [p["status"] for p in events.of("tool_started")] == ["RUNNING"]
    conn.close()


def test_retry_policy_bounded_is_bounded_by_the_named_constant(tmp_path):
    """``bounded`` had no frozen value; the choice is a named constant."""
    conn = _db(tmp_path)
    reg = Registry()
    # Always fails. A fixed result list would run dry partway through and the
    # spy would then succeed, which would make this test measure the fixture
    # instead of the retry bound.
    spy = Spy(default={"ok": False, "status": "failed"})
    reg.register_tool(_record("retry.bounded", side_effect="read",
                              retry_policy="bounded"))
    reg.bind_handler("retry.bounded", spy)
    events = _Events()

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="retry.bounded", args={}, on_event=events)
    assert spy.call_count == RETRY_BOUNDED_ATTEMPTS
    assert RETRY_BOUNDED_ATTEMPTS >= 1
    statuses = [p["status"] for p in events.of("tool_started")]
    assert statuses == ["RUNNING"] + ["RETRYING"] * (RETRY_BOUNDED_ATTEMPTS - 1)
    assert out["ok"] is False
    conn.close()


def test_every_retry_policy_is_implemented_and_none_means_one_attempt():
    """``RETRY_POLICIES`` is a closed vocabulary: all three must do something."""
    from app.services.tools.registry import _attempts_for

    counts = {policy: _attempts_for(policy, "read") for policy in RETRY_POLICIES}
    assert counts == {"none": 1, "once": 2, "bounded": RETRY_BOUNDED_ATTEMPTS}
    assert _attempts_for("nonsense", "read") == 1, (
        "an unknown policy must fail to one try"
    )
    assert all(value >= 1 for value in counts.values())


def test_retry_still_writes_exactly_one_tool_runs_row(tmp_path):
    """Telemetry is per *call*, not per attempt, so attempts stay auditable."""
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy([{"ok": False, "status": "failed"}, {"ok": True}])
    reg.register_tool(_record("retry.rows", side_effect="read",
                              retry_policy="once"))
    reg.bind_handler("retry.rows", spy)

    reg.execute(conn, project_id="starter", root=str(tmp_path),
                name="retry.rows", args={})
    rows = conn.execute("SELECT COUNT(*) AS n FROM tool_runs").fetchone()["n"]
    assert spy.call_count == 2
    assert rows == 1
    conn.close()


def test_tool_runs_schema_is_created_and_legacy_schema_upgrades_idempotently(tmp_path):
    from app.services.tools.registry import ensure_tool_runs

    fresh = _db(tmp_path, "fresh-schema.db")
    ensure_tool_runs(fresh)
    columns = {row[1] for row in fresh.execute("PRAGMA table_info(tool_runs)")}
    assert {"turn_id", "employee_id", "employee_role", "evidence_count"} <= columns
    fresh.close()

    legacy = _db(tmp_path, "legacy-schema.db")
    legacy.execute("DROP TABLE tool_runs")
    legacy.execute(
        "CREATE TABLE tool_runs (tool_run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
        "tool_id TEXT NOT NULL, provider TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL DEFAULT '', "
        "completed_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', "
        "latency_ms INTEGER NOT NULL DEFAULT 0, cost_note TEXT NOT NULL DEFAULT '')")
    legacy.execute("INSERT INTO tool_runs (tool_run_id, project_id, tool_id) VALUES ('old','p','t')")
    ensure_tool_runs(legacy)
    ensure_tool_runs(legacy)
    columns = {row[1] for row in legacy.execute("PRAGMA table_info(tool_runs)")}
    row = legacy.execute("SELECT * FROM tool_runs WHERE tool_run_id='old'").fetchone()
    assert {"turn_id", "employee_id", "employee_role", "evidence_count"} <= columns
    assert row["tool_run_id"] == "old" and row["evidence_count"] == 0
    assert legacy.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0] == 1
    legacy.close()


def test_standalone_registry_latency_matches_persisted_millisecond_timestamps(tmp_path):
    conn = _db(tmp_path)
    reg = Registry()
    reg.register_tool(_record("timing.standalone", retry_policy="none"))
    reg.bind_handler("timing.standalone", lambda c, *, project_id, root, args: {"ok": True})
    reg.execute(conn, project_id="starter", root=str(tmp_path),
                name="timing.standalone", args={})
    row = conn.execute(
        "SELECT started_at, completed_at, latency_ms, evidence_count, turn_id "
        "FROM tool_runs").fetchone()
    from datetime import datetime
    elapsed = int((datetime.fromisoformat(row["completed_at"])
                   - datetime.fromisoformat(row["started_at"])).total_seconds() * 1000)
    assert elapsed == row["latency_ms"]
    assert row["evidence_count"] == 0 and row["turn_id"] is None
    conn.close()


def test_a_broken_observer_cannot_change_what_a_tool_does(tmp_path):
    """Emission is fail-soft, like every other emission path in the codebase."""
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy()

    def exploding(event_type, payload):
        raise RuntimeError("the observer is broken")

    reg.register_tool(_record("retry.observer", side_effect="read",
                              retry_policy="once"))
    reg.bind_handler("retry.observer", spy)

    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="retry.observer", args={}, on_event=exploding)
    assert out["ok"] is True
    assert spy.call_count == 1
    conn.close()


def test_the_argument_summary_leaks_neither_a_secret_nor_a_path(tmp_path):
    """The event stream is a durable record; it is not a request-body channel.

    Two different defences, and this proves both: a value that *looks* like a
    credential is caught by the scrubber, and a value passed under a name that
    *says* credential is dropped without being inspected at all. The second is
    the one that matters -- ``hunter2hunter2`` matches no secret pattern, so
    only the name can protect it.
    """
    conn = _db(tmp_path)
    reg = Registry()
    spy = Spy()
    reg.register_tool(_record("retry.args", retry_policy="none"))
    reg.bind_handler("retry.args", spy)
    events = _Events()

    reg.execute(
        conn, project_id="starter", root=str(tmp_path), name="retry.args",
        args={"token": "sk-EXAMPLE-NOT-A-REAL-KEY-abcdefghijklmnop",
              "where": "C:\\Users\\Example\\secret\\place.txt",
              "client_secret": "hunter2hunter2",
              "OPENROUTER_API_KEY": "unrecognisable-value-123456",
              # ``pat`` is a secret name the scrubber does NOT recognise as a
              # value, so it isolates the name-based defence from the
              # value-based one: nothing else here would protect it.
              "pat": "ghp_whatever",
              "many": {str(i): i for i in range(ARG_SUMMARY_MAX_KEYS + 6)}},
        on_event=events,
    )
    payload = events.of("tool_started")[0]
    dumped = json.dumps(payload)
    assert "sk-EXAMPLE" not in dumped, "a value-shaped secret reached the payload"
    assert "hunter2hunter2" not in dumped, (
        "a name-shaped secret reached the payload: value redaction only catches "
        "strings that match a known pattern"
    )
    assert "unrecognisable-value-123456" not in dumped, (
        "the argument-name check is case/separator insensitive and must be"
    )
    assert "ghp_whatever" not in dumped, (
        "a secret name the scrubber does not recognise must still be withheld"
    )
    assert "Example" not in dumped, "an absolute path reached the event payload"

    args = payload["args"]
    assert args["pat"] == "redacted"
    assert args["token"] == "redacted"
    # ``client_secret`` and ``OPENROUTER_API_KEY`` both scrub to ``[REDACTED]``
    # as *names*, which would silently collapse onto one dict key. The summary
    # must keep both entries rather than dropping one without saying so.
    assert sum(1 for key in args if key.startswith("[REDACTED]")) == 2, args
    assert all(value == "redacted" for key, value in args.items()
               if key.startswith("[REDACTED]"))
    assert len(args) == 6, (
        "every argument supplied must appear exactly once -- the summary may "
        f"clamp its size, but it must never drop an argument silently: {args}"
    )
    assert args["many"] == "<dict>", (
        "a non-scalar is reported by type, never by content"
    )
    assert "place.txt" not in dumped
    conn.close()


def test_a_secret_named_argument_is_redacted_whatever_its_value():
    """The name is the signal, and the value is never inspected to decide.

    Note what is deliberately *not* covered: ``session_id``, ``project_id`` and
    friends are identifiers, not credentials, and a ``*_id`` suffix rule would
    redact half the useful arguments in the product. A caller who wants a
    session id withheld should pass it as a token.
    """
    from app.services.tools.registry import REDACTED, _is_secret_arg_name

    for name in ("token", "api_key", "client_secret", "API-KEY", "PASSWORD",
                 "x_credential", "authorization", "session_token", "bearer"):
        assert _is_secret_arg_name(name), name
    for name in ("query", "url", "count", "username", "keyword", "monkey_business",
                 "session_id", "project_id"):
        assert not _is_secret_arg_name(name), name
    assert REDACTED == "redacted"


def test_the_tool_started_payload_carries_the_frozen_tree_keys(tmp_path):
    """§1.3.2 freezes the key names; the payload must be projectable onto them."""
    conn = _db(tmp_path)
    reg = Registry()
    reg.register_tool(_record("retry.keys", side_effect="read",
                              retry_policy="once"))
    reg.bind_handler("retry.keys", Spy([{"ok": False, "status": "failed"},
                                         {"ok": True}]))
    events = _Events()

    reg.execute(conn, project_id="starter", root=str(tmp_path),
                name="retry.keys", args={}, on_event=events)

    started = events.of("tool_started")
    for payload in started:
        for key in ("status", "tool_id", "tool_run_id"):
            assert key in payload, f"{key} is a frozen tool_started metadata key"
        assert payload["status"] in ("RUNNING", "RETRYING")
        assert payload["tool_id"] == "retry.keys"
    conn.close()


# ---- 3. nothing regressed ------------------------------------------------


def test_no_native_tool_was_reclassified():
    """The tripwire for plan §8.6.

    Every native tool declares ``permission_level="green"`` -- that is the
    *approval* vocabulary, and it is the one the gate reads. This packet
    implements the gate; it does not get to decide what the gate gates. If this
    test ever fails, a tool was promoted and that must be a deliberate, reviewed
    act, not a side effect of a refactor.

    Note the two vocabularies are genuinely different and are kept apart:
    ``ToolRecord.side_effect`` is ``none/read/write/external_action/destructive``
    (what the tool *does*) while ``permission_level`` is ``green/yellow/red``
    (whether a human must approve it first). The plan's "all 17 tools declare
    side_effect='green'" wording refers to the legacy ``ToolDef.side_effect``
    literal, which ``Registry.register`` maps onto ``permission_level``; the
    ``ToolRecord`` field of the same name carries the effect vocabulary, and
    measured over the real default registry it is 15 read / 5 write, not "green".

    Both are asserted here so neither can drift unnoticed, and the *count* is
    never asserted -- only the shape of the distribution.
    """
    from app.services.tools.registry import SIDE_EFFECTS

    registry = build_default_registry()
    records = registry.list_tools("starter")
    assert records, "the default registry is unexpectedly empty"

    levels: dict[str, int] = {}
    effects: dict[str, int] = {}
    for record in records:
        levels[record.permission_level] = levels.get(record.permission_level, 0) + 1
        effects[record.side_effect] = effects.get(record.side_effect, 0) + 1

    assert levels == {"green": len(records)}, (
        "a tool's declared permission_level changed: "
        f"{sorted(levels.items())} across {len(records)} tools. Re-classifying "
        f"a tool is a founder policy decision, not an implementation detail."
    )
    assert set(effects) <= set(SIDE_EFFECTS)
    assert effects, "the effect vocabulary was measured as empty"


def test_the_declared_vocabulary_is_unchanged():
    assert PERMISSION_LEVELS == ("green", "yellow", "red")
    assert RETRY_POLICIES == ("none", "once", "bounded")
    assert NEEDS_APPROVAL_STATUS == "needs_approval"


def test_legacy_error_strings_are_unchanged(tmp_path):
    """The v0.2 surface promises exact error strings. Spot-check the real ones."""
    conn = _db(tmp_path)
    reg = Registry()
    reg.register_tool(_record("legacy.ok", retry_policy="none"))
    reg.bind_handler("legacy.ok", lambda c, *, project_id, root, args: {"ok": True})
    reg.register_tool(_record("legacy.bare", retry_policy="none"))
    reg.register_tool(_record("legacy.off", retry_policy="none", enabled=False))

    assert reg.execute(conn, project_id="", root="", name="legacy.ok", args={})["error"] \
        == "project_id is required (fail closed)"
    assert reg.execute(conn, project_id="starter", root="", name="nope", args={})["error"] \
        == "unknown tool: nope"
    assert reg.execute(conn, project_id="starter", root="", name="legacy.ok",
                       args="bad")["error"] == "tool legacy.ok: args must be an object"
    assert "no handler bound" in reg.execute(
        conn, project_id="starter", root="", name="legacy.bare", args={})["error"]
    assert "is disabled" in reg.execute(
        conn, project_id="starter", root="", name="legacy.off", args={})["error"]
    conn.close()


def test_a_handler_must_still_return_a_dict(tmp_path):
    conn = _db(tmp_path)
    reg = Registry()
    reg.register_tool(_record("legacy.notdict", retry_policy="none"))
    reg.bind_handler("legacy.notdict", lambda c, *, project_id, root, args: ["nope"])
    out = reg.execute(conn, project_id="starter", root="", name="legacy.notdict", args={})
    assert out["ok"] is False
    assert out["error"] == "tool legacy.notdict: handler must return dict"
    conn.close()


def test_health_still_tracks_the_last_attempt(tmp_path):
    conn = _db(tmp_path)
    reg = Registry()
    reg.register_tool(_record("legacy.health", side_effect="read",
                              retry_policy="once"))
    reg.bind_handler("legacy.health", lambda c, *, project_id, root, args: {"ok": False})
    reg.execute(conn, project_id="starter", root="", name="legacy.health", args={})
    assert reg.tool_status("starter")[0]["last_health"] == "unhealthy"
    conn.close()
