"""Tool registry — frozen interface I-1 (ToolRecord capability registry).

Capability layer only: a record describes WHAT a tool does (capability
metadata + safety vocabularies); execution binds by string id via
handlers. Provider/vendor selection is NOT decided here — integration
resolution happens at the call site, never inside the registry.

Every ``Registry.execute`` call is telemetered into ``tool_runs``
(fixed columns only; args / request / response bodies are never
persisted; values matching secret patterns are scrubbed to ``redacted``).

Two declared fields are load-bearing (DEV-008-SKILLS-OPS W6)
-------------------------------------------------------------
Before this change ``permission_level`` and ``retry_policy`` were validated
in ``ToolRecord.__post_init__``, advertised through ``specs()``, and then
**read by nothing**: a tool declared ``yellow`` ran exactly like one declared
``green``, and a tool declared ``retry_policy="once"`` was called exactly once.
A declared field nobody reads is documentation, not a control. Both are now
enforced in ``Registry.execute``:

* :data:`APPROVAL_REQUIRED_LEVELS` may not run without an approval id. The
  refusal is a **gate**: the handler is never looked up and is provably never
  invoked. The returned ``status`` is :data:`NEEDS_APPROVAL_STATUS`, which is
  deliberately not ``"failed"`` -- nothing ran, so nothing failed.
* :data:`RETRY_POLICIES` decides how many attempts a call gets. A refused tool
  is never retried: a gate is not a failure to recover from.
* :data:`RETRY_SAFE_SIDE_EFFECTS` (W6C) clamps the granted attempts to exactly
  one for every ``side_effect`` other than ``"read"``. Retry is therefore
  permitted **only** for read tools: ``write`` / ``external_action`` /
  ``destructive`` never run a second attempt whatever ``retry_policy``
  declares, because retrying a tool that may already have acted can
  double-write durable state. The declared policy stays authoritative for
  read tools, where a repeat is safe. As with the gate, this changes the
  *mechanism's* conditions, not any tool's declared vocabulary.

**This module changes no tool's declared level.** It makes the existing
declaration mean something. Re-classifying a tool as ``yellow``/``red`` is a
founder safety decision and is explicitly out of scope (plan §8.6), so the
mechanism and the policy are deliberately separate: ``APPROVAL_REQUIRED_LEVELS``
is the mechanism's switch, and every native tool still declares whatever its
owner declared.

**Measured today: 20 native tool records, all ``permission_level="green"``, all
``retry_policy="once"``.** (The plan's "17 tools" figure counted ``side_effect=
"green"`` *source literals*: ``register_state_tools`` builds six tools from one
literal in a loop, so 17 literals register 20 tools. The conclusion -- every
tool is green -- is unchanged; only the denominator was wrong.) The gate is
therefore armed and idle in production, which is the honest state, and it is why
the gate has to be proven with a synthetic record rather than with a real tool.

Legacy v0.2 surface is preserved exactly: ``ToolDef``, ``Registry``,
``Registry.register / specs / names / execute`` keep their original
signatures, return shapes, and error strings, so
``build_default_registry`` and all existing call sites and tests keep
working unchanged. ``execute`` has optional keyword-only arguments for
approval, event observation, and trusted per-call execution context; omitting
them preserves the old behaviour for every existing call site.
"""
import re
import sqlite3
import time
import uuid
from contextvars import ContextVar
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.contracts.events import safe_error_message, sanitize_metadata, sanitize_user_text

SOURCE_TYPES = ("native", "integration", "mcp")
SIDE_EFFECTS = ("none", "read", "write", "external_action", "destructive")
PERMISSION_LEVELS = ("green", "yellow", "red")
COST_TYPES = ("free", "metered", "byok")
CREDENTIAL_SCOPES = ("installation", "project", "user")
RETRY_POLICIES = ("none", "once", "bounded")
HEALTH_LABELS = ("unknown", "healthy", "unhealthy")

#: Permission levels that ``execute`` refuses without an approval id.
#:
#: This is the **mechanism's** switch, not a re-classification. No tool's
#: declared ``permission_level`` is changed anywhere in this module; promoting a
#: tool to ``yellow``/``red`` is a founder safety decision and is explicitly out
#: of scope (plan §8.6). Every native tool declares ``green`` today, so this gate
#: is armed and idle -- which is the honest state, and the reason the mechanism
#: has to be tested with a synthetic record rather than with a real tool.
APPROVAL_REQUIRED_LEVELS = ("yellow", "red")

#: ``status`` on a refusal. Deliberately distinct from ``"failed"``: a refusal
#: means the tool never ran, and a caller that retries a failure must not retry
#: a gate.
NEEDS_APPROVAL_STATUS = "needs_approval"

#: Total attempts (first try + retries) granted by ``retry_policy="bounded"``.
#:
#: The plan froze ``"none"`` and ``"once"`` and left ``"bounded"`` open, so this
#: value is a choice made here rather than a contract: three attempts, i.e. two
#: retries. It is a named constant so changing it is a one-line, reviewable act.
RETRY_BOUNDED_ATTEMPTS = 3

#: The only ``side_effect`` values that may ever run a second attempt (W6C):
#: a read. Retry is an idempotency privilege, not a blanket behaviour --
#: ``write`` / ``external_action`` / ``destructive`` tools are clamped to one
#: attempt whatever ``retry_policy`` declares, because a handler that may have
#: already acted cannot be safely re-run: a lost response or a post-write crash
#: followed by a retry would double-write durable state (the exact failure the
#: nine-column ``tool_runs`` table has no room to unwind). This uses the
#: existing :data:`SIDE_EFFECTS` vocabulary only -- no new field, and no tool's
#: declared classification is changed. ``"none"`` side-effect tools are clamped
#: with the non-reads: absence of a declared effect is not evidence of
#: idempotence, so the conservative read of "may have acted" wins.
RETRY_SAFE_SIDE_EFFECTS = ("read",)

_ATTEMPTS_BY_POLICY = {
    "none": 1,
    "once": 2,
    "bounded": RETRY_BOUNDED_ATTEMPTS,
}

#: Upper bound on how many argument keys reach a ``tool_started`` payload. It is
#: the same bound the frozen tree-metadata contract uses (§1.3.6: at most 12
#: metadata keys per tree event), so an event carrying an argument summary
#: cannot blow the ``metadata_json`` truncation limit.
ARG_SUMMARY_MAX_KEYS = 12

#: Argument *names* whose value is never echoed into an event payload, whatever
#: the value happens to look like.
#:
#: Value-shaped redaction (``_scrub`` -> ``contains_secrets``) only catches
#: strings that match a known credential pattern. A caller who names an argument
#: ``client_secret`` and passes something no pattern recognises would otherwise
#: have it written into a durable event row -- the exact thing the nine-column
#: ``tool_runs`` table deliberately has no room for. So the name is trusted as
#: the signal, and the value is dropped without being inspected.
SECRET_ARG_NAMES = frozenset({
    "auth", "authorization", "bearer", "cookie", "credential", "key", "pass",
    "password", "pat", "secret", "session", "token",
})
SECRET_ARG_SUFFIXES = (
    "_key", "_token", "_secret", "_password", "_credential", "_bearer", "_session",
)

#: What a redacted argument value is replaced with. Matches the string the
#: scrubber already uses so a reader sees one word for "this was withheld".
REDACTED = "redacted"

TOOL_RUNS_DDL = (
    "CREATE TABLE IF NOT EXISTS tool_runs ("
    "tool_run_id TEXT PRIMARY KEY,"
    "project_id TEXT NOT NULL,"
    "tool_id TEXT NOT NULL,"
    "provider TEXT NOT NULL DEFAULT '',"
    "started_at TEXT NOT NULL DEFAULT '',"
    "completed_at TEXT NOT NULL DEFAULT '',"
    "status TEXT NOT NULL DEFAULT '',"
    "latency_ms INTEGER NOT NULL DEFAULT 0,"
    "cost_note TEXT NOT NULL DEFAULT '',"
    "turn_id TEXT, employee_id TEXT, employee_role TEXT,"
    "evidence_count INTEGER NOT NULL DEFAULT 0)"
)
TOOL_RUNS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_tool_runs_project ON tool_runs(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_tool_runs_tool ON tool_runs(tool_id, started_at)",
)
_TOOL_RUN_INSERT = (
    "INSERT INTO tool_runs (tool_run_id, project_id, tool_id, provider,"
    " started_at, completed_at, status, latency_ms, cost_note, turn_id,"
    " employee_id, employee_role, evidence_count)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

# Employee orchestration owns the single authoritative row for its tool call.
# The registry still owns telemetry for ordinary standalone calls.
_EMPLOYEE_TOOL_CONTEXT: ContextVar[dict | None] = ContextVar(
    "employee_tool_telemetry_context", default=None)


def employee_tool_telemetry_context(**values):
    """Temporarily suppress registry's duplicate row for an employee call."""
    class _Context:
        def __enter__(self):
            self.token = _EMPLOYEE_TOOL_CONTEXT.set({**values, "suppress": True})
            return self

        def __exit__(self, exc_type, exc, tb):
            _EMPLOYEE_TOOL_CONTEXT.reset(self.token)
            return False
    return _Context()

_LEGACY_TOOL_DEFAULTS: dict[str, dict] = {
    "propose_task": {"side_effect": "write"},
    "propose_campaign": {"side_effect": "write"},
    "request_approval": {"side_effect": "write"},
    "remember": {"side_effect": "write"},
    "delegate_to_marketing_pm": {"side_effect": "write"},
}

# User constraints are scoped to one graph turn and enter execution only
# through the optional trusted ``execution_context`` argument. Keep this
# mapping explicit: tool IDs are the governed boundary; model arguments and
# descriptions are not authority for whether an action is allowed.
_GOVERNED_WRITE_ACTIONS = {
    "propose_campaign": "campaign",
    "propose_task": "task",
    "reject_task": "task",
    "propose_experiment": "experiment",
    "request_approval": "approval",
    "remember": "memory",
    "delegate_to_marketing_pm": "delegation",
}


def _constraint_block_action(tool_id: str, rec: "ToolRecord",
                             execution_context: dict | None) -> str | None:
    """Return the action blocked by a trusted per-call constraint, if any."""
    if not isinstance(execution_context, dict):
        return None
    action = _GOVERNED_WRITE_ACTIONS.get(tool_id)
    if execution_context.get("analysis_only") is True:
        if action is not None:
            return action
        if rec.side_effect in ("write", "external_action", "destructive"):
            # Use the more specific governed action when one is known. For
            # future or extension tools, use the validated canonical
            # side_effect label instead of exposing an arbitrary tool ID.
            return action or rec.side_effect
        return None
    if action is None:
        return None
    forbidden = execution_context.get("forbidden_actions")
    if isinstance(forbidden, (list, tuple, set, frozenset)) and action in forbidden:
        return action
    return None


class ToolNotFound(LookupError):
    """Raised by get_tool / list resolution when a tool_id does not exist."""


@dataclass(frozen=True)
class ToolRecord:
    """Frozen I-1 tool metadata (capability registry row, no handler)."""

    tool_id: str
    source_type: str
    description: str
    parameters: dict
    side_effect: str
    project_scope_required: bool = True
    auth_required: bool = False
    credential_scope: str | None = None
    permission_level: str = "green"
    cost_type: str = "free"
    timeout_s: int = 60
    retry_policy: str = "once"
    enabled: bool = True

    def __post_init__(self):
        if not isinstance(self.tool_id, str) or not self.tool_id.strip():
            raise ValueError("tool_id must be a non-empty string")
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(f"tool {self.tool_id}: unknown source_type {self.source_type}")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(f"tool {self.tool_id}: description must be a non-empty string")
        if not isinstance(self.parameters, dict):
            raise ValueError(f"tool {self.tool_id}: parameters must be a dict")
        if self.side_effect not in SIDE_EFFECTS:
            raise ValueError(f"tool {self.tool_id}: unknown side_effect {self.side_effect}")
        if not isinstance(self.project_scope_required, bool):
            raise ValueError(f"tool {self.tool_id}: project_scope_required must be bool")
        if not isinstance(self.auth_required, bool):
            raise ValueError(f"tool {self.tool_id}: auth_required must be bool")
        if self.credential_scope is not None and self.credential_scope not in CREDENTIAL_SCOPES:
            raise ValueError(f"tool {self.tool_id}: unknown credential_scope {self.credential_scope}")
        if self.permission_level not in PERMISSION_LEVELS:
            raise ValueError(f"tool {self.tool_id}: unknown permission_level {self.permission_level}")
        if self.cost_type not in COST_TYPES:
            raise ValueError(f"tool {self.tool_id}: unknown cost_type {self.cost_type}")
        if isinstance(self.timeout_s, bool) or not isinstance(self.timeout_s, int) or not 1 <= self.timeout_s <= 600:
            raise ValueError(f"tool {self.tool_id}: timeout_s must be an int in 1..600")
        if self.retry_policy not in RETRY_POLICIES:
            raise ValueError(f"tool {self.tool_id}: unknown retry_policy {self.retry_policy}")
        if not isinstance(self.enabled, bool):
            raise ValueError(f"tool {self.tool_id}: enabled must be bool")


@dataclass
class ToolDef:
    """Legacy v0.2 tool definition (unchanged public shape)."""

    name: str
    description: str
    parameters: dict
    side_effect: str = "green"
    handler: object = None


def ensure_tool_runs(conn) -> None:
    """Idempotent self-ensure of the tool_runs table + indexes (no DDL elsewhere)."""
    conn.execute(TOOL_RUNS_DDL)
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(tool_runs)")}
    additive = (
        ("turn_id", "TEXT"), ("employee_id", "TEXT"),
        ("employee_role", "TEXT"),
        ("evidence_count", "INTEGER NOT NULL DEFAULT 0"),
    )
    for name, declaration in additive:
        if name not in columns:
            conn.execute(f"ALTER TABLE tool_runs ADD COLUMN {name} {declaration}")
    for stmt in TOOL_RUNS_INDEXES:
        conn.execute(stmt)
    conn.commit()


def _scrub(value, limit: int = 200) -> str:
    text = sanitize_user_text(value, limit)
    if not text:
        return ""
    if "[REDACTED]" in text:
        return "redacted"
    try:
        from app.services.rag.secrets import contains_secrets
        if contains_secrets(text):
            return "redacted"
    except Exception:
        return "redacted"
    return text


def _insert_tool_run(conn, row: tuple) -> None:
    if len(row) == 9:
        row = tuple(row) + (None, None, None, 0)
    try:
        conn.execute(_TOOL_RUN_INSERT, row)
    except sqlite3.OperationalError:
        ensure_tool_runs(conn)
        conn.execute(_TOOL_RUN_INSERT, row)
    conn.commit()


def _attempts_for(retry_policy: str, side_effect: str = "none") -> int:
    """Attempts a call actually gets, after the W6C idempotency guard.

    ``retry_policy`` grants a number of attempts; ``side_effect`` decides
    whether any retry may be executed at all. Only :data:`RETRY_SAFE_SIDE_EFFECTS`
    (a read) keeps its granted policy -- a tool that may have already acted is
    always clamped to one attempt, because a lost-response retry of a write can
    double-write durable state. Read tools keep the full policy: retrying a
    read is safe in every vocabulary this module knows. The second parameter
    defaults to ``"none"`` so the frozen single-argument signature keeps
    working (an effect-less tool gets one attempt either way).
    """
    granted = _ATTEMPTS_BY_POLICY.get(str(retry_policy or ""), 1)
    if str(side_effect) not in RETRY_SAFE_SIDE_EFFECTS:
        return 1
    return granted


def _has_approval(approval_id) -> bool:
    """True when the caller supplied a usable approval id.

    Deliberately the *whole* of the supplied check: an approval id is a
    capability token that the approval/resume layer (owned by the graph packet)
    mints and validates, and re-deriving it here would mean two places deciding
    what counts as approved. What this module guarantees is narrower and is what
    it can actually prove -- **no id, no run**.
    """
    return bool(str(approval_id if approval_id is not None else "").strip())


def _is_secret_arg_name(name) -> bool:
    """True when an argument *name* says its value must not be echoed.

    The name is normalised the same way ``app.contracts.events`` normalises a
    metadata key, so ``Client-Secret``, ``client_secret`` and ``CLIENT SECRET``
    are one decision rather than three.
    """
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(name or "").strip()).strip("_").lower()
    if not normalized:
        return False
    return normalized in SECRET_ARG_NAMES or normalized.endswith(SECRET_ARG_SUFFIXES)


def _arg_summary(args: dict) -> dict:
    """A flat, scrubbed summary of one call's arguments -- never the arguments.

    The registry persists no request body (``tool_runs`` is nine fixed columns),
    and an event stream is a *durable* record. Echoing raw arguments into
    ``tool_started`` would therefore create a side channel that stores exactly
    what the telemetry table deliberately does not. Three passes, in order:

    1. a name that says "credential" has its value replaced without being read;
    2. everything else is scrubbed (secrets and absolute paths collapse to
       ``redacted``) and clamped;
    3. a non-scalar is reported by type name rather than by content.
    """
    summary: dict = {}
    for key, value in list(args.items())[:ARG_SUMMARY_MAX_KEYS]:
        # The secret decision is made on the RAW name, before any scrubbing.
        # ``sanitize_user_text`` redacts a bare ``OPENROUTER_API_KEY`` -- it
        # cannot tell that string from a credential value -- so sanitising first
        # would destroy the very signal this check reads, and the value behind a
        # redacted key would then be published under it.
        secret = _is_secret_arg_name(key)
        name = sanitize_user_text(str(key), 64) or "arg"
        if name in summary:
            # Scrubbing two names can collapse them onto the same string (every
            # secret-ish name becomes ``[REDACTED]``), and a dict would then
            # silently drop one argument. A summary that loses an argument
            # without saying so is worse than a slightly ugly key, so the
            # collision is made visible instead.
            suffix = 2
            while f"{name}#{suffix}" in summary:
                suffix += 1
            name = f"{name}#{suffix}"
        if secret:
            summary[name] = REDACTED
        elif value is None or isinstance(value, (str, int, float, bool)):
            summary[name] = _scrub(value, 80)
        else:
            summary[name] = f"<{type(value).__name__}>"
    return summary


def _emit_tool_event(on_event, event_type: str, payload: dict) -> None:
    """Fail-soft emission, matching ``app.services.manager_loop._emit``.

    A broken observer must never change what a tool does, so an exception in the
    callback is swallowed here exactly as it is on every other emission path in
    the codebase.
    """
    if not callable(on_event):
        return
    try:
        on_event(event_type, dict(payload or {}))
    except Exception:
        return


class Registry:
    """Capability registry: I-1 API + legacy v0.2 register/specs/names/execute."""

    def __init__(self):
        self._records: dict[str, ToolRecord] = {}
        self._handlers: dict[str, Callable] = {}
        self._aliases: dict[str, str] = {}
        self._health: dict[str, str] = {}
        self._order: list[str] = []

    @staticmethod
    def _require_project(project_id) -> None:
        if not project_id or not str(project_id).strip():
            raise ValueError("project_id is required (fail closed)")

    def _resolve(self, tool_id) -> str:
        tid = str(tool_id if tool_id is not None else "")
        for _ in range(8):
            if tid in self._aliases:
                tid = self._aliases[tid]
            else:
                break
        if tid not in self._records:
            raise ToolNotFound(f"unknown tool: {tool_id}")
        return tid

    def register_tool(self, rec: ToolRecord) -> None:
        if not isinstance(rec, ToolRecord):
            raise ValueError("register_tool expects a ToolRecord")
        if rec.tool_id in self._records or rec.tool_id in self._aliases:
            raise ValueError(f"duplicate tool: {rec.tool_id}")
        self._records[rec.tool_id] = rec
        self._health.setdefault(rec.tool_id, "unknown")
        self._order.append(rec.tool_id)

    def bind_handler(self, tool_id, handler) -> None:
        tid = self._resolve(tool_id)
        if handler is not None and not callable(handler):
            raise ValueError(f"tool {tid}: handler must be callable")
        self._handlers[tid] = handler

    def register_alias(self, alias, tool_id) -> None:
        alias = str(alias if alias is not None else "").strip()
        if not alias:
            raise ValueError("alias must be a non-empty string")
        tid = self._resolve(tool_id)
        if alias in self._records or alias in self._aliases:
            raise ValueError(f"duplicate alias/tool: {alias}")
        self._aliases[alias] = tid

    def get_tool(self, tool_id: str, project_id: str) -> ToolRecord:
        self._require_project(project_id)
        return self._records[self._resolve(tool_id)]

    def list_tools(self, project_id: str, *, source_type: str | None = None) -> list[ToolRecord]:
        self._require_project(project_id)
        if source_type is not None and source_type not in SOURCE_TYPES:
            raise ValueError(f"unknown source_type: {source_type}")
        recs = [self._records[tid] for tid in self._order if tid in self._records]
        if source_type is not None:
            recs = [r for r in recs if r.source_type == source_type]
        return recs

    def tool_status(self, project_id: str) -> list[dict]:
        self._require_project(project_id)
        out = []
        for tid in self._order:
            rec = self._records.get(tid)
            if rec is None:
                continue
            out.append({"tool_id": rec.tool_id, "enabled": rec.enabled,
                        "source_type": rec.source_type,
                        "last_health": self._health.get(tid, "unknown")})
        return out

    def set_health(self, tool_id, last_health: str) -> None:
        tid = self._resolve(tool_id)
        health = str(last_health if last_health is not None else "").strip()
        if health not in HEALTH_LABELS:
            raise ValueError(f"unknown health label: {last_health}")
        self._health[tid] = health

    def register(self, tool: ToolDef):
        if tool.side_effect not in ("green", "yellow", "red"):
            raise ValueError(f"tool {tool.name}: unknown side_effect {tool.side_effect}")
        if tool.name in self._records or tool.name in self._aliases:
            raise ValueError(f"duplicate tool: {tool.name}")
        defaults = _LEGACY_TOOL_DEFAULTS.get(tool.name, {})
        rec = ToolRecord(
            tool_id=tool.name,
            source_type="native",
            description=tool.description,
            parameters=tool.parameters if isinstance(tool.parameters, dict) else {},
            side_effect=defaults.get("side_effect", "read"),
            project_scope_required=defaults.get("project_scope_required", True),
            auth_required=defaults.get("auth_required", False),
            credential_scope=defaults.get("credential_scope"),
            permission_level=tool.side_effect,
            cost_type=defaults.get("cost_type", "free"),
            timeout_s=defaults.get("timeout_s", 60),
            retry_policy=defaults.get("retry_policy", "once"),
            enabled=True,
        )
        self._records[rec.tool_id] = rec
        self._handlers[rec.tool_id] = tool.handler
        self._health.setdefault(rec.tool_id, "unknown")
        self._order.append(rec.tool_id)
        return None

    def _advertised(self) -> list[ToolRecord]:
        out = []
        for tid in self._order:
            rec = self._records.get(tid)
            if rec is None or not rec.enabled or self._handlers.get(tid) is None:
                continue
            out.append(rec)
        return out

    def specs(self) -> list[dict]:
        return [{"name": rec.tool_id, "description": rec.description,
                 "parameters": rec.parameters, "side_effect": rec.permission_level}
                for rec in self._advertised()]

    def catalog(self) -> list[dict]:
        """Return executable capability metadata for application-side routing.

        ``specs()`` is the frozen legacy model-facing shape.  The Account
        Manager also needs the actual safety and availability fields when it
        selects a tool without relying on provider-native function calling.
        This catalog contains no handlers, credentials, or secret values.
        """
        return [
            {
                "name": tool_id,
                "description": rec.description,
                "parameters": rec.parameters,
                "side_effect": rec.side_effect,
                "permission_level": rec.permission_level,
                "auth_required": rec.auth_required,
                "credential_scope": rec.credential_scope,
                "cost_type": rec.cost_type,
                "source_type": rec.source_type,
                "enabled": rec.enabled,
                "handler_available": self._handlers.get(tool_id) is not None,
            }
            for tool_id in self._order
            if (rec := self._records.get(tool_id)) is not None
        ]

    def names(self) -> list[str]:
        return sorted(rec.tool_id for rec in self._advertised())

    def _attempt(self, handler, safe_name, conn, *, project_id, root, args) -> dict:
        """Run the handler exactly once and return its result dict.

        The whole of the legacy v0.2 fault mapping lives here, unchanged, so a
        single-attempt call and a retried call produce byte-identical results.
        The trailing ``sanitize_metadata`` / force-generic pass stays inside
        because it is conditional on the handler having returned a dict at all.
        """
        raw_result = None
        try:
            raw_result = handler(conn, project_id=project_id, root=root, args=args)
            if not isinstance(raw_result, dict):
                result = {"ok": False, "error": f"tool {safe_name}: handler must return dict",
                          "status": "failed"}
            else:
                result = dict(raw_result)
                result.setdefault("ok", True)
        except ValueError as exc:
            result = {"ok": False, "error": safe_error_message(
                exc, "The tool could not be completed."), "status": "failed"}
        except KeyError:
            result = {"ok": False, "error": "The requested item was not found.",
                      "status": "failed"}
        except Exception:
            result = {"ok": False, "error": "The tool could not be completed.",
                      "status": "failed"}
        if raw_result is not None and isinstance(raw_result, dict):
            result = sanitize_metadata(result)
            if "error" in raw_result:
                result["error"] = safe_error_message(
                    raw_result.get("error"),
                    "The tool could not be completed.",
                    force_generic=True,
                )
        return result

    def execute(self, conn, *, project_id: str, root, name: str, args: dict,
                approval_id: str | None = None, on_event=None,
                execution_context: dict | None = None) -> dict:
        """Execute one tool and return its result dict. Never raises a tool fault.

        ``approval_id``
            Without it, a tool in :data:`APPROVAL_REQUIRED_LEVELS` is **refused**
            and its handler is never invoked. See the module docstring.

        ``on_event``
            Optional ``(event_type, payload)`` callback, following the convention
            already used by ``app.services.manager_loop`` and
            ``app.graphs.tool_capability``. When supplied, the registry emits
            ``tool_started`` once per attempt -- ``status="RUNNING"`` for the
            first and ``status="RETRYING"`` for each retry, which is the frozen
            tree contract (plan §1.3.4: a *second* ``tool_started`` for the same
            ``tool_run_id`` carries ``RETRYING``) -- plus exactly one
            ``tool_completed`` or ``tool_failed`` for the logical call.
            Retries exist only for read tools (W6C: :data:`RETRY_SAFE_
            SIDE_EFFECTS`); a write / external_action / destructive tool emits
            a single ``RUNNING`` attempt even when it fails, because a second
            attempt could double-apply an effect.

            It defaults to ``None``, so no existing call site changes behaviour
            at all: the callers that emit their own tool events today keep doing
            so, and the retry path is observable only to a caller that asks to
            observe it. One ``tool_run_id`` covers every attempt of one call, so
            the tree can group a retry under its first attempt.
        """
        t0 = time.perf_counter()
        started = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        tool_run_id = uuid.uuid4().hex
        raw_name = name if name is not None else ""
        safe_name = sanitize_user_text(raw_name, 128)
        pid_ok = bool(project_id and str(project_id).strip())
        if not pid_ok:
            result = {"ok": False, "error": "project_id is required (fail closed)",
                      "status": "failed"}
            self._telemetry(conn, project_id="", tool_id=safe_name, provider="",
                            started=started, t0=t0, result=result, cost_note="",
                            tool_run_id=tool_run_id)
            return result
        pid = str(project_id)
        try:
            tid = self._resolve(raw_name)
        except ToolNotFound:
            result = {"ok": False, "error": f"unknown tool: {safe_name}",
                      "status": "failed"}
            self._telemetry(conn, project_id=pid, tool_id=safe_name, provider="",
                            started=started, t0=t0, result=result, cost_note="",
                            tool_run_id=tool_run_id)
            return result
        rec = self._records[tid]
        if not isinstance(args, dict):
            result = {"ok": False, "error": f"tool {safe_name}: args must be an object",
                      "status": "failed"}
            self._telemetry(conn, project_id=pid, tool_id=tid, provider=rec.source_type,
                            started=started, t0=t0, result=result, cost_note=rec.cost_type,
                            tool_run_id=tool_run_id)
            return result
        if not rec.enabled:
            result = {"ok": False, "error": f"tool {safe_name} is disabled",
                      "status": "failed"}
            self._telemetry(conn, project_id=pid, tool_id=tid, provider=rec.source_type,
                            started=started, t0=t0, result=result, cost_note=rec.cost_type,
                            tool_run_id=tool_run_id)
            return result

        # ---- current-turn user constraint gate -----------------------
        # This runs before approval checks and, critically, before even
        # looking up the handler. The context is call-local and is never
        # persisted or inferred from tool arguments.
        blocked_action = _constraint_block_action(tid, rec, execution_context)
        if blocked_action is not None:
            result = {
                "ok": False,
                "status": "blocked_by_user_constraint",
                "action": blocked_action,
            }
            duration_ms = max(0, int((time.perf_counter() - t0) * 1000))
            _emit_tool_event(on_event, "tool_completed", {
                "tool": tid,
                "tool_id": tid,
                "tool_run_id": tool_run_id,
                "status": "BLOCKED_BY_USER_CONSTRAINT",
                "action": blocked_action,
                "duration_ms": duration_ms,
                "attempts": 0,
                "attempt_limit": 0,
                "ok": False,
            })
            self._telemetry(conn, project_id=pid, tool_id=tid, provider=rec.source_type,
                            started=started, t0=t0, result=result, cost_note=rec.cost_type,
                            tool_run_id=tool_run_id)
            return result

        # ---- the gate -------------------------------------------------
        # Placed after identity/scope/enabled and *before* the handler is even
        # looked up. Nothing that can touch the outside world may run before it,
        # and a future edit that adds handler pre-work cannot land on the wrong
        # side of this line by accident.
        if rec.permission_level in APPROVAL_REQUIRED_LEVELS and not _has_approval(approval_id):
            result = {"ok": False,
                      "error": f"tool {safe_name}: {rec.permission_level} permission "
                               f"requires an approval id before it can run",
                      "status": NEEDS_APPROVAL_STATUS,
                      "tool_id": tid,
                      "permission_level": rec.permission_level,
                      "side_effect": rec.side_effect}
            self._telemetry(conn, project_id=pid, tool_id=tid, provider=rec.source_type,
                            started=started, t0=t0, result=result, cost_note=rec.cost_type,
                            tool_run_id=tool_run_id)
            return result

        handler = self._handlers.get(tid)
        if handler is None:
            result = {"ok": False, "error": f"tool {safe_name}: no handler bound",
                      "status": "failed"}
            self._telemetry(conn, project_id=pid, tool_id=tid, provider=rec.source_type,
                            started=started, t0=t0, result=result, cost_note=rec.cost_type,
                            tool_run_id=tool_run_id)
            return result

        # ---- the retry guard (W6C) ---------------------------------
        # ``rec`` is bound and the gate just passed, so nothing has run yet:
        # computing the attempt budget here means the guard is evaluated
        # before the handler is resolved, on the same side of the gate. A
        # side effect that may have acted -- write / external_action /
        # destructive, and "none" as the conservative fallback -- is clamped
        # to a single attempt whatever ``retry_policy`` declares.
        attempts = _attempts_for(rec.retry_policy, rec.side_effect)
        result = {}
        used = 1
        for used in range(1, attempts + 1):
            _emit_tool_event(on_event, "tool_started", {
                # ``tool`` is the payload key every existing consumer reads;
                # ``tool_id`` is the name the frozen tree-metadata contract uses.
                # Both are emitted because the payload feeds two vocabularies.
                "tool": tid,
                "tool_id": tid,
                "status": "RUNNING" if used == 1 else "RETRYING",
                "tool_run_id": tool_run_id,
                "attempt": used,
                "attempt_limit": attempts,
                "permission_level": rec.permission_level,
                "args": _arg_summary(args),
            })
            result = self._attempt(handler, safe_name, conn, project_id=project_id,
                                  root=root, args=args)
            if result.get("ok"):
                break

        provider = rec.source_type
        declared = result.get("provider")
        if isinstance(declared, str) and declared.strip():
            provider = sanitize_user_text(declared.strip(), 64)
        self._health[tid] = "healthy" if result.get("ok") else "unhealthy"
        duration_ms = max(0, int((time.perf_counter() - t0) * 1000))
        _emit_tool_event(on_event, "tool_completed" if result.get("ok") else "tool_failed", {
            "tool": tid,
            "tool_id": tid,
            "tool_run_id": tool_run_id,
            "status": "COMPLETE" if result.get("ok") else "FAILED",
            "duration_ms": duration_ms,
            "attempts": used,
            "attempt_limit": attempts,
            # No ``obs``: the result body is deliberately never echoed onto the
            # wire (see ``_arg_summary``), only whether it succeeded and why not.
            "ok": bool(result.get("ok")),
            "error": _scrub(result.get("error", ""), 200),
        })
        self._telemetry(conn, project_id=pid, tool_id=tid, provider=provider,
                        started=started, t0=t0, result=result, cost_note=rec.cost_type,
                        tool_run_id=tool_run_id)
        return result

    @staticmethod
    def _telemetry(conn, *, project_id, tool_id, provider, started, t0, result,
                   cost_note, tool_run_id: str | None = None) -> None:
        context = _EMPLOYEE_TOOL_CONTEXT.get()
        if context and context.get("suppress"):
            return
        status = result.get("status")
        if status == "blocked_by_user_constraint":
            outcome = "blocked"
        else:
            outcome = "success" if bool(result.get("ok")) else "failed"
        latency_ms = max(0, int((time.perf_counter() - t0) * 1000))
        completed = datetime.fromisoformat(started) + timedelta(milliseconds=latency_ms)
        row = (
            tool_run_id or uuid.uuid4().hex,
            _scrub(project_id, 64),
            _scrub(tool_id, 128),
            _scrub(provider, 64),
            started,
            completed.isoformat(timespec="milliseconds"),
            outcome,
            latency_ms,
            _scrub(cost_note, 32),
            None, None, None, 0,
        )
        try:
            _insert_tool_run(conn, row)
        except Exception:
            pass


ToolRegistry = Registry

_DEFAULT_REGISTRY: Registry | None = None


def _default() -> Registry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        from app.services.tools import build_default_registry
        _DEFAULT_REGISTRY = build_default_registry()
    return _DEFAULT_REGISTRY


def reset_default_registry() -> None:
    """Drop the lazily built module-level registry (tests / re-init)."""
    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None


def register_tool(rec: ToolRecord) -> None:
    _default().register_tool(rec)


def get_tool(tool_id: str, project_id: str) -> ToolRecord:
    return _default().get_tool(tool_id, project_id)


def list_tools(project_id: str, *, source_type: str | None = None) -> list[ToolRecord]:
    return _default().list_tools(project_id, source_type=source_type)


def tool_status(project_id: str) -> list[dict]:
    return _default().tool_status(project_id)


__all__ = [
    "ToolRecord", "ToolDef", "Registry", "ToolRegistry", "ToolNotFound",
    "SOURCE_TYPES", "SIDE_EFFECTS", "PERMISSION_LEVELS", "COST_TYPES",
    "CREDENTIAL_SCOPES", "RETRY_POLICIES", "HEALTH_LABELS",
    "APPROVAL_REQUIRED_LEVELS", "NEEDS_APPROVAL_STATUS",
    "RETRY_BOUNDED_ATTEMPTS", "RETRY_SAFE_SIDE_EFFECTS", "ARG_SUMMARY_MAX_KEYS",
    "SECRET_ARG_NAMES", "SECRET_ARG_SUFFIXES", "REDACTED",
    "TOOL_RUNS_DDL", "TOOL_RUNS_INDEXES", "ensure_tool_runs",
    "register_tool", "get_tool", "list_tools", "tool_status",
    "reset_default_registry",
]
