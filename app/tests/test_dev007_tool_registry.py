"""DEV-007 W4 acceptance: frozen I-1 tool registry, telemetry without
secrets, vendor-clean capability layer, parallel fan-out (cap, timeouts,
cost controls, four-capability dispatch). All offline (fakes only)."""
import dataclasses
import inspect
import json
import re
import threading
import time
import uuid
from pathlib import Path
from app.tests.internal_fixtures import requires_internal_runs

import pytest

from app.database.seed import ensure_seed
from app.database.sqlite import connect
from app.services.tools.fanout import fanout, resolve_max_workers
from app.services.tools.registry import (
    COST_TYPES,
    HEALTH_LABELS,
    PERMISSION_LEVELS,
    RETRY_POLICIES,
    SIDE_EFFECTS,
    SOURCE_TYPES,
    CREDENTIAL_SCOPES,
    Registry,
    ToolDef,
    ToolNotFound,
    ToolRecord,
    ensure_tool_runs,
    get_tool,
    list_tools,
    register_tool,
    reset_default_registry,
    tool_status,
)

ROOT = Path(__file__).resolve().parents[2]

_I1_FIELDS = [
    "tool_id", "source_type", "description", "parameters", "side_effect",
    "project_scope_required", "auth_required", "credential_scope",
    "permission_level", "cost_type", "timeout_s", "retry_policy", "enabled",
]


def _db(tmp_path, name="t.db"):
    conn = connect(tmp_path / name)
    ensure_seed(conn)
    return conn


# ---- I-1 conformance ----

def test_i1_frozen_record_shape_and_defaults():
    fields = dataclasses.fields(ToolRecord)
    assert [f.name for f in fields] == _I1_FIELDS
    assert ToolRecord.__dataclass_params__.frozen is True
    assert [f.type for f in fields[:5]] == [str, str, str, dict, str]
    rec = ToolRecord(tool_id="t.a", source_type="native", description="d",
                     parameters={}, side_effect="read")
    assert rec.project_scope_required is True
    assert rec.auth_required is False
    assert rec.credential_scope is None
    assert rec.permission_level == "green"
    assert rec.cost_type == "free"
    assert rec.timeout_s == 60
    assert rec.retry_policy == "once"
    assert rec.enabled is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        rec.tool_id = "x"


def test_i1_api_signatures_module_and_instance():
    sig = inspect.signature(register_tool)
    assert list(sig.parameters) == ["rec"]
    sig = inspect.signature(get_tool)
    assert list(sig.parameters) == ["tool_id", "project_id"]
    sig = inspect.signature(list_tools)
    assert list(sig.parameters) == ["project_id", "source_type"]
    assert sig.parameters["source_type"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["source_type"].default is None
    sig = inspect.signature(tool_status)
    assert list(sig.parameters) == ["project_id"]
    expected = {
        "register_tool": ["rec"],
        "get_tool": ["tool_id", "project_id"],
        "list_tools": ["project_id", "source_type"],
        "tool_status": ["project_id"],
    }
    for meth, params in expected.items():
        msig = inspect.signature(getattr(Registry, meth))
        assert list(msig.parameters)[1:] == params


def test_i1_record_validation_rejects_bad_vocab():
    base = dict(tool_id="t.v", source_type="native", description="d",
                parameters={}, side_effect="read")
    with pytest.raises(ValueError):
        ToolRecord(**{**base, "tool_id": "   "})
    with pytest.raises(ValueError):
        ToolRecord(**{**base, "source_type": "vendor_x"})
    with pytest.raises(ValueError):
        ToolRecord(**{**base, "side_effect": "maybe"})
    with pytest.raises(ValueError):
        ToolRecord(**{**base, "permission_level": "blue"})
    with pytest.raises(ValueError):
        ToolRecord(**{**base, "cost_type": "expensive"})
    with pytest.raises(ValueError):
        ToolRecord(**{**base, "credential_scope": "org"})
    with pytest.raises(ValueError):
        ToolRecord(**{**base, "retry_policy": "forever"})
    with pytest.raises(ValueError):
        ToolRecord(**{**base, "timeout_s": 0})
    with pytest.raises(ValueError):
        ToolRecord(**{**base, "timeout_s": 601})
    with pytest.raises(ValueError):
        ToolRecord(**{**base, "timeout_s": True})
    with pytest.raises(ValueError):
        ToolRecord(**{**base, "enabled": "yes"})
    with pytest.raises(ValueError):
        ToolRecord(**{**base, "description": ""})
    with pytest.raises(ValueError):
        ToolRecord(**{**base, "parameters": []})
    assert set(SOURCE_TYPES) == {"native", "integration", "mcp"}
    assert set(PERMISSION_LEVELS) == {"green", "yellow", "red"}
    assert set(COST_TYPES) == {"free", "metered", "byok"}
    assert set(SIDE_EFFECTS) == {"none", "read", "write", "external_action", "destructive"}
    assert set(CREDENTIAL_SCOPES) == {"installation", "project", "user"}
    assert set(RETRY_POLICIES) == {"none", "once", "bounded"}
    assert set(HEALTH_LABELS) == {"unknown", "healthy", "unhealthy"}


def test_metadata_completeness_default_registry():
    from app.services.tools import build_default_registry
    reg = build_default_registry()
    names = reg.names()
    assert {"get_project_state", "rag_search", "web_search", "instagram_audit",
            "get_social_accounts", "delegate_to_marketing_pm", "get_job_status",
            "propose_task", "propose_campaign", "request_approval",
            "remember"} <= set(names)
    assert len(names) >= 12
    for name in names:
        rec = reg.get_tool(name, "starter")
        assert rec.tool_id == name
        assert rec.source_type in SOURCE_TYPES
        assert rec.description.strip()
        assert isinstance(rec.parameters, dict) and rec.parameters.get("type") == "object"
        assert rec.side_effect in SIDE_EFFECTS
        assert rec.permission_level in PERMISSION_LEVELS
        assert rec.cost_type in COST_TYPES
        assert rec.credential_scope is None or rec.credential_scope in CREDENTIAL_SCOPES
        assert rec.retry_policy in RETRY_POLICIES
        assert isinstance(rec.timeout_s, int) and 1 <= rec.timeout_s <= 600
        assert rec.project_scope_required is True
        assert rec.auth_required is False
        assert rec.enabled is True
    assert reg.get_tool("propose_task", "starter").side_effect == "write"
    assert reg.get_tool("remember", "starter").side_effect == "write"
    assert reg.get_tool("delegate_to_marketing_pm", "starter").side_effect == "write"
    assert reg.get_tool("web_search", "starter").side_effect == "read"
    status = reg.tool_status("starter")
    assert {s["tool_id"] for s in status} == set(names)
    for s in status:
        assert set(s) == {"tool_id", "enabled", "source_type", "last_health"}
        assert s["last_health"] in HEALTH_LABELS
    assert {r.tool_id for r in reg.list_tools("starter", source_type="native")} == set(names)
    assert reg.list_tools("starter", source_type="mcp") == []


def test_registry_sources_vendor_clean_capability_layer():
    sources = {
        "registry.py": (ROOT / "app" / "services" / "tools" / "registry.py").read_text(encoding="utf-8"),
        "fanout.py": (ROOT / "app" / "services" / "tools" / "fanout.py").read_text(encoding="utf-8"),
    }
    forbidden_modules = (
        "app.services.llm", "app.services.social", "app.services.integrations",
        "app.services.credentials", "app.services.mcp", "app.services.vision",
        "app.services.account_manager", "os.environ",
    )
    vendor_re = re.compile(
        r"(?<![A-Za-z])(apify|brightdata|openai|anthropic|claude|gemini|meta|groq|"
        r"mistral|deepseek|perplexity|proxycurl|datasphere)(?![A-Za-z])", re.I)
    for fname, text in sources.items():
        for mod in forbidden_modules:
            assert mod not in text, (fname, mod)
        m = vendor_re.search(text)
        assert m is None, (fname, m.group(0) if m else "")
    assert 'name == "' not in sources["registry.py"]
    field_names = {f.name for f in dataclasses.fields(ToolRecord)}
    assert not (field_names & {"provider", "vendor", "api_key", "secret", "token", "secret_ref"})
    reg = Registry()
    hits = []

    def h(conn, *, project_id, root, args):
        hits.append(args.get("tag"))
        return {"ok": True, "tag": args.get("tag")}

    for tid in ("files.ingest", "web.search"):
        reg.register_tool(ToolRecord(tool_id=tid, source_type="native",
                                     description="arbitrary capability",
                                     parameters={"type": "object", "properties": {}},
                                     side_effect="none"))
        reg.bind_handler(tid, h)
    assert reg.get_tool("files.ingest", "starter").source_type == "native"
    assert reg.get_tool("web.search", "starter").source_type == "native"
    assert {r.tool_id for r in reg.list_tools("starter")} == {"files.ingest", "web.search"}
    assert hits == []


def test_fail_closed_everywhere(tmp_path):
    reg = Registry()
    for bad in (None, "", "   "):
        with pytest.raises(ValueError):
            reg.get_tool("anything", bad)
        with pytest.raises(ValueError):
            reg.list_tools(bad)
        with pytest.raises(ValueError):
            reg.tool_status(bad)
    with pytest.raises(ToolNotFound):
        reg.get_tool("missing.tool", "starter")
    with pytest.raises(ValueError):
        reg.list_tools("starter", source_type="banana")
    with pytest.raises(ValueError):
        get_tool("anything", "")
    conn = _db(tmp_path, "fc.db")
    out = reg.execute(conn, project_id="  ", root=str(tmp_path), name="anything", args={})
    assert out["ok"] is False and out["error"] == "project_id is required (fail closed)"
    with pytest.raises(ValueError):
        fanout(reg, conn, project_id="", root=str(tmp_path), calls=[])
    with pytest.raises(ValueError):
        fanout(reg, conn, project_id="starter", root=str(tmp_path), calls="nope")
    conn.close()


# ---- legacy v0.2 surface ----

def test_legacy_surface_unchanged(tmp_path):
    conn = _db(tmp_path, "leg.db")
    from app.services.tools import build_default_registry
    reg = build_default_registry()
    assert {"get_project_state", "rag_search", "web_search",
            "delegate_to_marketing_pm", "get_job_status",
            "propose_task", "request_approval"} <= set(reg.names())
    specs = reg.specs()
    assert specs
    for s in specs:
        assert set(s) == {"name", "description", "parameters", "side_effect"}
    by_name = {s["name"]: s for s in specs}
    assert by_name["instagram_audit"]["side_effect"] == "green"
    assert by_name["get_social_accounts"]["side_effect"] == "green"
    with pytest.raises(ValueError, match="duplicate tool"):
        reg.register(ToolDef(name="web_search", description="x", parameters={}))
    with pytest.raises(ValueError, match="unknown side_effect"):
        reg.register(ToolDef(name="odd.tool", description="x", parameters={},
                             side_effect="blue"))
    out = reg.execute(conn, project_id="starter", root=str(tmp_path), name="nope", args={})
    assert out["ok"] is False and out["error"] == "unknown tool: nope"
    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="get_project_state", args="bad")
    assert out["ok"] is False
    assert out["error"] == "tool get_project_state: args must be an object"
    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="get_project_state", args={})
    assert out["ok"] is True and out["state"]["project_id"] == "starter"
    out = reg.execute(conn, project_id=None, root=str(tmp_path),
                      name="get_project_state", args={})
    assert out["ok"] is False and out["error"] == "project_id is required (fail closed)"
    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="rag_search", args={"query": ""})
    assert out["ok"] is False
    conn.close()


def test_alias_machinery(tmp_path):
    conn = _db(tmp_path, "al.db")
    reg = Registry()
    reg.register_tool(ToolRecord(tool_id="state.digest", source_type="native",
                                 description="digest",
                                 parameters={"type": "object", "properties": {}},
                                 side_effect="read"))
    reg.bind_handler("state.digest",
                     lambda c, *, project_id, root, args: {"ok": True, "pid": project_id})
    reg.register_alias("get_project_state", "state.digest")
    assert reg.get_tool("get_project_state", "starter").tool_id == "state.digest"
    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="get_project_state", args={})
    assert out["ok"] is True and out["pid"] == "starter"
    assert "state.digest" in reg.names()
    assert "get_project_state" not in reg.names()
    with pytest.raises(ToolNotFound):
        reg.register_alias("x.y", "missing.tool")
    with pytest.raises(ValueError, match="duplicate"):
        reg.register_alias("state.digest", "state.digest")
    with pytest.raises(ValueError):
        reg.register_alias("  ", "state.digest")
    conn.close()


def test_disabled_and_unbound_tool_paths(tmp_path):
    conn = _db(tmp_path, "dis.db")
    reg = Registry()
    reg.register_tool(ToolRecord(tool_id="off.tool", source_type="native",
                                 description="d", parameters={}, side_effect="none",
                                 enabled=False))
    reg.register_tool(ToolRecord(tool_id="bare.tool", source_type="native",
                                 description="d", parameters={}, side_effect="none"))
    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="off.tool", args={})
    assert out["ok"] is False and "disabled" in out["error"]
    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="bare.tool", args={})
    assert out["ok"] is False and "no handler bound" in out["error"]
    assert "off.tool" not in reg.names() and "bare.tool" not in reg.names()
    assert reg.specs() == []
    assert {"off.tool", "bare.tool"} == {r.tool_id for r in reg.list_tools("starter")}
    assert {s["tool_id"] for s in reg.tool_status("starter")} == {"off.tool", "bare.tool"}
    rows = conn.execute("SELECT tool_id, status FROM tool_runs").fetchall()
    assert len(rows) == 2 and all(r["status"] == "failed" for r in rows)
    conn.close()


def test_execute_updates_health(tmp_path):
    conn = _db(tmp_path, "h.db")
    reg = Registry()
    reg.register_tool(ToolRecord(tool_id="h.ok", source_type="native",
                                 description="d", parameters={}, side_effect="none"))
    reg.bind_handler("h.ok", lambda c, *, project_id, root, args: {"ok": True})
    reg.register_tool(ToolRecord(tool_id="h.bad", source_type="native",
                                 description="d", parameters={}, side_effect="none"))
    reg.bind_handler("h.bad", lambda c, *, project_id, root, args:
                     {"ok": False, "error": "x", "status": "failed"})
    assert reg.tool_status("starter")[0]["last_health"] == "unknown"
    reg.execute(conn, project_id="starter", root=str(tmp_path), name="h.ok", args={})
    reg.execute(conn, project_id="starter", root=str(tmp_path), name="h.bad", args={})
    health = {s["tool_id"]: s["last_health"] for s in reg.tool_status("starter")}
    assert health == {"h.ok": "healthy", "h.bad": "unhealthy"}
    reg.set_health("h.bad", "unknown")
    assert {s["tool_id"]: s["last_health"] for s in reg.tool_status("starter")}["h.bad"] == "unknown"
    with pytest.raises(ValueError):
        reg.set_health("h.bad", "green-ish")
    conn.close()


def test_module_level_default_registry():
    reset_default_registry()
    try:
        rec = get_tool("get_project", "starter")
        assert rec.tool_id == "get_project"
        listed = list_tools("starter")
        assert "get_project" in {r.tool_id for r in listed}
        status = tool_status("starter")
        assert status
        assert all(set(s) == {"tool_id", "enabled", "source_type", "last_health"}
                   for s in status)
        assert all(s["last_health"] in HEALTH_LABELS for s in status)
        tid = f"probe.{uuid.uuid4().hex[:10]}"
        register_tool(ToolRecord(tool_id=tid, source_type="native",
                                 description="probe",
                                 parameters={"type": "object", "properties": {}},
                                 side_effect="none"))
        assert get_tool(tid, "starter").tool_id == tid
        with pytest.raises(ValueError, match="duplicate tool"):
            register_tool(ToolRecord(tool_id=tid, source_type="native",
                                     description="p2", parameters={}, side_effect="none"))
        with pytest.raises(ValueError):
            list_tools("starter", source_type="nope")
    finally:
        reset_default_registry()


# ---- telemetry ----

def test_telemetry_never_persists_secrets(tmp_path):
    conn = _db(tmp_path, "tel.db")
    reg = Registry()
    reg.register_tool(ToolRecord(tool_id="probe.echo", source_type="native",
                                 description="echo probe",
                                 parameters={"type": "object", "properties": {}},
                                 side_effect="none"))
    reg.bind_handler("probe.echo",
                     lambda c, *, project_id, root, args: {
                         "ok": True, "provider": "sk-EXAMPLE-PROVIDERLEAK-NOT-REAL",
                         "echo_keys": sorted(args)})
    # DEV-008-PUBLISH-GATE -- LEAK-DETECTION SENTINELS, NOT CREDENTIALS.
    #
    # This test proves the registry never echoes a secret back in a tool result,
    # so the values below deliberately imitate the *shape* of an OpenAI key and a
    # GitHub PAT. They are literal nonsense: no account, no access, no network
    # call, no credential anywhere. They are kept format-realistic on purpose --
    # a sentinel that does not look like a secret would not exercise the
    # redaction path this test is about.
    #
    # They are marked here, and `scripts/package/secret_scan.py` carries an
    # explicit allowlist entry for this file so a real finding elsewhere still
    # fails the gate rather than being drowned out by these.
    secret_sk = "sk-EXAMPLE-NOT-A-REAL-KEY-abcdefghijklmnop"
    secret_gh = "ghp_EXAMPLE0000000000000000000000NOTREAL"
    out = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="probe.echo",
                      args={"token": secret_sk, "pat": secret_gh,
                            "client_secret": "hunter2hunter2"})
    assert out["ok"] is True
    bad = reg.execute(conn, project_id="starter", root=str(tmp_path),
                      name="api_key=zzzLEAKVALUE999", args={})
    assert bad["ok"] is False
    fc = reg.execute(conn, project_id="", root=str(tmp_path),
                     name="probe.echo", args={})
    assert fc["ok"] is False
    rows = [dict(r) for r in conn.execute("SELECT * FROM tool_runs").fetchall()]
    assert len(rows) >= 3
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tool_runs)").fetchall()]
    assert cols == ["tool_run_id", "project_id", "tool_id", "provider",
                    "started_at", "completed_at", "status", "latency_ms",
                    "cost_note", "turn_id", "employee_id", "employee_role",
                    "evidence_count"]
    dumped = json.dumps(rows, default=str)
    for s in (secret_sk, secret_gh, "hunter2hunter2", "LEAKVALUE999",
              "PROVIDERLEAK123456"):
        assert s not in dumped, s
    from app.services.rag.secrets import contains_secrets
    assert contains_secrets(dumped) is False
    assert any(r["tool_id"] == "redacted" for r in rows)
    assert any(r["provider"] == "redacted" for r in rows)
    assert any(r["tool_id"] == "probe.echo" for r in rows)
    assert {r["status"] for r in rows} <= {"success", "failed"}
    assert all(isinstance(r["latency_ms"], int) and r["latency_ms"] >= 0 for r in rows)
    ok_rows = [r for r in rows if r["status"] == "success"]
    assert ok_rows and all(r["cost_note"] == "free" for r in ok_rows)
    fc_rows = [r for r in rows if r["project_id"] == ""]
    assert fc_rows and all(r["cost_note"] == "" for r in fc_rows)
    conn.close()


@requires_internal_runs
def test_telemetry_fragment_ddl_drift(tmp_path):
    from app.services.tools.registry import TOOL_RUNS_DDL, TOOL_RUNS_INDEXES
    fragment_path = ROOT / "development" / "runs" / "DEV-007" / "migrations" / "w4.sql"
    fragment = fragment_path.read_text(encoding="utf-8")
    executable = "\n".join(line.split("--", 1)[0] for line in fragment.splitlines())
    assert "DROP" not in executable.upper()
    assert "user_version" not in executable
    assert "PRAGMA" not in executable.upper()
    a = connect(tmp_path / "frag.db")
    a.executescript(fragment)
    a.executescript(fragment)
    a.commit()
    b = connect(tmp_path / "code.db")
    ensure_tool_runs(b)
    ensure_tool_runs(b)

    def cols(c):
        return [(r[1], r[2], r[3], r[4], r[5])
                for r in c.execute("PRAGMA table_info(tool_runs)")]

    def idx(c):
        return sorted(r[1] for r in c.execute("PRAGMA index_list(tool_runs)"))

    # The historical fragment remains the nine-column baseline. Runtime
    # self-ensure additively appends employee provenance without rewriting it.
    legacy_columns = cols(a)
    current_columns = cols(b)
    assert current_columns[:len(legacy_columns)] == legacy_columns
    assert [column[0] for column in current_columns[len(legacy_columns):]] == [
        "turn_id", "employee_id", "employee_role", "evidence_count"]
    assert idx(a) == idx(b)
    info = {r[1]: r for r in a.execute("PRAGMA table_info(tool_runs)")}
    assert info["project_id"][3] == 1
    a.close()
    b.close()


def test_telemetry_self_ensures_on_fresh_db(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    reg = Registry()
    reg.register_tool(ToolRecord(tool_id="t.fresh", source_type="native",
                                 description="d", parameters={}, side_effect="none"))
    reg.bind_handler("t.fresh", lambda c, *, project_id, root, args: {"ok": True})
    out = reg.execute(conn, project_id="starter", root=str(tmp_path), name="t.fresh", args={})
    assert out["ok"] is True
    rows = conn.execute("SELECT status FROM tool_runs").fetchall()
    assert [r["status"] for r in rows] == ["success"]
    conn.close()


# ---- fan-out ----

def test_fanout_concurrency_cap_respected(tmp_path, monkeypatch):
    conn = _db(tmp_path, "cap.db")
    monkeypatch.setenv("MAX_AGENT_CONCURRENCY", "2")
    assert resolve_max_workers(None) == 2
    assert resolve_max_workers(99) == 4
    assert resolve_max_workers("bogus") == 2
    assert resolve_max_workers(1) == 1
    assert resolve_max_workers(0) == 2
    monkeypatch.delenv("MAX_AGENT_CONCURRENCY")
    assert resolve_max_workers(None) == 4
    monkeypatch.setenv("MAX_AGENT_CONCURRENCY", "2")

    lock = threading.Lock()
    state = {"cur": 0, "peak": 0}

    def slow(conn, *, project_id, root, args):
        with lock:
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
        time.sleep(0.06)
        with lock:
            state["cur"] -= 1
        return {"ok": True}

    reg = Registry()
    for i in range(6):
        tid = f"cap.t{i}"
        reg.register_tool(ToolRecord(tool_id=tid, source_type="native",
                                     description="d", parameters={}, side_effect="none"))
        reg.bind_handler(tid, slow)
    calls = [{"tool_id": f"cap.t{i}", "args": {}} for i in range(6)]
    t0 = time.perf_counter()
    out = fanout(reg, conn, project_id="starter", root=str(tmp_path), calls=calls)
    wall = time.perf_counter() - t0
    assert len(out) == 6 and all(r["ok"] for r in out)
    assert 1 <= state["peak"] <= 2, state
    assert wall < 0.45
    conn.close()


def test_fanout_four_capabilities_parallel(tmp_path):
    conn = _db(tmp_path, "fan.db")
    barrier = threading.Barrier(4, timeout=5.0)
    reg = Registry()

    def make(tag):
        def h(conn, *, project_id, root, args):
            barrier.wait(timeout=5.0)
            return {"ok": True, "tag": tag}
        return h

    specs = [
        ("web.research", "native"),
        ("instagram.public_research", "integration"),
        ("competitor.research", "integration"),
        ("knowledge.rag_retrieval", "native"),
    ]
    for tid, src in specs:
        reg.register_tool(ToolRecord(tool_id=tid, source_type=src,
                                     description=f"fake {tid}",
                                     parameters={"type": "object", "properties": {}},
                                     side_effect="read", timeout_s=30))
        reg.bind_handler(tid, make(tid))
    assert {r.source_type for r in reg.list_tools("starter")} == {"native", "integration"}
    calls = [{"tool_id": tid, "args": {"q": tid}} for tid, _ in specs]
    t0 = time.perf_counter()
    out = fanout(reg, conn, project_id="starter", root=str(tmp_path),
                 calls=calls, max_workers=4)
    wall = time.perf_counter() - t0
    assert [r["ok"] for r in out] == [True] * 4, out
    assert [r["tool_id"] for r in out] == [tid for tid, _ in specs]
    assert [r["tag"] for r in out] == [tid for tid, _ in specs]
    assert wall < 5.0
    conn.close()


def test_fanout_cost_controls(tmp_path):
    conn = _db(tmp_path, "cost.db")
    reg = Registry()
    ran = []

    def h(conn, *, project_id, root, args):
        ran.append(args.get("who"))
        return {"ok": True}

    for tid, cost in (("free.a", "free"), ("paid.b", "metered"),
                      ("paid.c", "byok"), ("free.d", "free")):
        reg.register_tool(ToolRecord(tool_id=tid, source_type="native",
                                     description="d", parameters={}, side_effect="none",
                                     cost_type=cost))
        reg.bind_handler(tid, h)
    out = fanout(reg, conn, project_id="starter", root=str(tmp_path), max_workers=2,
                 allow_metered=False,
                 calls=[{"tool_id": "free.a"}, {"tool_id": "paid.b"},
                        {"tool_id": "paid.c"}])
    assert out[0]["ok"] is True
    assert out[1]["ok"] is False and "cost controls" in out[1]["error"]
    assert out[2]["ok"] is False and "cost controls" in out[2]["error"]
    assert len(ran) == 1
    ran.clear()
    out = fanout(reg, conn, project_id="starter", root=str(tmp_path), max_workers=4,
                 allow_metered=True, max_metered=1,
                 calls=[{"tool_id": "paid.b"}, {"tool_id": "paid.c"},
                        {"tool_id": "free.d"}])
    assert out[0]["ok"] is True
    assert out[1]["ok"] is False and "cost controls" in out[1]["error"]
    assert out[2]["ok"] is True
    assert len(ran) == 2
    conn.close()


def test_fanout_per_tool_timeout(tmp_path):
    conn = _db(tmp_path, "to.db")
    reg = Registry()

    def sleepy(conn, *, project_id, root, args):
        time.sleep(1.2)
        return {"ok": True, "late": True}

    reg.register_tool(ToolRecord(tool_id="slow.tool", source_type="native",
                                 description="d", parameters={}, side_effect="none",
                                 timeout_s=1))
    reg.bind_handler("slow.tool", sleepy)
    t0 = time.perf_counter()
    out = fanout(reg, conn, project_id="starter", root=str(tmp_path),
                 calls=[{"tool_id": "slow.tool"}])
    wall = time.perf_counter() - t0
    assert len(out) == 1
    assert out[0]["ok"] is False and "timed out" in out[0]["error"]
    assert out[0]["tool_id"] == "slow.tool"
    assert wall < 1.35
    time.sleep(0.35)
    conn.close()


def test_fanout_order_unknown_and_malformed(tmp_path):
    conn = _db(tmp_path, "ord.db")
    reg = Registry()
    reg.register_tool(ToolRecord(tool_id="ok.t", source_type="native",
                                 description="d", parameters={}, side_effect="none"))
    reg.bind_handler("ok.t", lambda c, *, project_id, root, args: {"ok": True, "n": 1})
    calls = [
        "not-a-dict",
        {"no_id": True},
        {"tool_id": "ghost.tool"},
        {"tool_id": "ok.t", "args": {"a": 1}},
        {"args": {}},
        {"tool_id": "ok.t", "args": ["bad"]},
        {"name": "ok.t"},
    ]
    out = fanout(reg, conn, project_id="starter", root=str(tmp_path),
                 calls=calls, max_workers=2)
    assert len(out) == len(calls)
    assert out[0]["ok"] is False and "object" in out[0]["error"]
    assert out[1]["ok"] is False and "tool_id or name" in out[1]["error"]
    assert out[2]["ok"] is False and "unknown tool" in out[2]["error"]
    assert out[3]["ok"] is True and out[3]["tool_id"] == "ok.t"
    assert out[4]["ok"] is False and "tool_id or name" in out[4]["error"]
    assert out[5]["ok"] is False and "args must be an object" in out[5]["error"]
    assert out[6]["ok"] is True and out[6]["tool_id"] == "ok.t"
    conn.close()


def test_fanout_sequential_fallback_in_memory():
    import sqlite3
    mem = sqlite3.connect(":memory:")
    reg = Registry()
    reg.register_tool(ToolRecord(tool_id="mem.t", source_type="native",
                                 description="d", parameters={}, side_effect="none"))
    reg.bind_handler("mem.t", lambda c, *, project_id, root, args: {"ok": True, "m": 1})
    out = fanout(reg, mem, project_id="starter", root=".",
                 calls=[{"tool_id": "mem.t"}, {"tool_id": "mem.t"}])
    assert [r["ok"] for r in out] == [True, True]
    assert all(r["tool_id"] == "mem.t" for r in out)
    mem.close()
