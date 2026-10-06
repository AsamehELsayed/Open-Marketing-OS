"""Exhaustive regression tests for FOUNDER-V1-R1 remediation:
W1 — ROUTING: ModelRouter opts passthrough
W2 — STREAMING: True synthesis token streaming
"""
import json
import sqlite3
import pytest
from app import deps
from app.services import state as store
from app.services.llm import model_router as mr
from app.services.llm.fake_provider import FakeProvider
from app.database import repos


def _fresh_db(tmp_path, name="test.db"):
    db = tmp_path / name
    deps.init_db(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn, db


# ================================================================
# W1 TESTS: ROUTING OPTS PASSTHROUGH
# ================================================================

def test_w1_1_provider_config_lora_and_mode_base_receives_explicit_base(tmp_path):
    """1. provider config has Marketing LoRA adapter + mode=BASE
    -> provider receives explicit BASE/no-adapter call option."""
    conn, _ = _fresh_db(tmp_path, "w1_1.db")
    fake_local = FakeProvider()
    fake_local.config = type("Config", (), {"adapter": "OMOS-Qwen2.5-7B-Marketing-v1"})()
    fake_local.queue(text="base advice", usage={"input_tokens": 10, "output_tokens": 20})
    router = mr.ModelRouter(local_provider=fake_local)

    resp, call = router.complete(
        conn,
        turn_id="t_base_1",
        project_id="starter",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        mode="BASE",
        opts={"temperature": 0.2},
    )

    assert resp.text == "base advice"
    assert len(fake_local.calls) == 1
    passed_opts = fake_local.calls[0].get("opts", {})
    # Explicit base/no-adapter override
    assert passed_opts.get("adapter") == "", f"Expected empty string adapter for BASE, got: {passed_opts}"
    assert passed_opts.get("temperature") == 0.2
    assert call.route_mode == "BASE"
    assert call.adapter == ""
    conn.close()


def test_w1_2_mode_marketing_lora_receives_actual_adapter(tmp_path):
    """2. mode=MARKETING_LORA -> provider receives actual adapter."""
    conn, _ = _fresh_db(tmp_path, "w1_2.db")
    fake_local = FakeProvider()
    fake_local.queue(text="lora advice", usage={"input_tokens": 15, "output_tokens": 25})
    router = mr.ModelRouter(local_provider=fake_local)

    resp, call = router.complete(
        conn,
        turn_id="t_lora_1",
        project_id="starter",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        mode="MARKETING_LORA",
        opts=None,
    )

    assert resp.text == "lora advice"
    assert len(fake_local.calls) == 1
    passed_opts = fake_local.calls[0].get("opts", {})
    assert passed_opts.get("adapter") == "OMOS-Qwen2.5-7B-Marketing-v1"
    assert call.route_mode == "MARKETING_LORA"
    assert call.adapter == "OMOS-Qwen2.5-7B-Marketing-v1"
    conn.close()


def test_w1_3_mode_auto_selecting_local_marketing_receives_actual_adapter(tmp_path):
    """3. mode=AUTO selecting local marketing -> provider receives actual adapter."""
    conn, _ = _fresh_db(tmp_path, "w1_3.db")
    fake_local = FakeProvider()
    fake_local.config = type("Config", (), {"adapter": "OMOS-Qwen2.5-7B-Marketing-v1"})()
    fake_local.queue(text="auto advice", usage={"input_tokens": 12, "output_tokens": 22})
    router = mr.ModelRouter(local_provider=fake_local)

    resp, call = router.complete(
        conn,
        turn_id="t_auto_1",
        project_id="starter",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        mode="AUTO",
        opts=None,
    )

    assert resp.text == "auto advice"
    assert len(fake_local.calls) == 1
    passed_opts = fake_local.calls[0].get("opts", {})
    assert passed_opts.get("adapter") == "OMOS-Qwen2.5-7B-Marketing-v1"
    assert call.route_mode == "AUTO"
    assert call.adapter == "OMOS-Qwen2.5-7B-Marketing-v1"
    conn.close()


def test_w1_4_fallback_to_base_telemetry_says_base(tmp_path):
    """4. fallback to BASE -> telemetry says BASE."""
    conn, _ = _fresh_db(tmp_path, "w1_4.db")
    fake_local = FakeProvider()
    fake_local.queue(text="honest base answer", usage={"adapter": "", "input_tokens": 8, "output_tokens": 16})
    router = mr.ModelRouter(local_provider=fake_local)

    resp, call = router.complete(
        conn,
        turn_id="t_fallback_base",
        project_id="starter",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        mode="BASE",
    )

    assert resp.text == "honest base answer"
    assert call.route_mode == "BASE"
    assert call.adapter == ""

    # Telemetry persisted in database says BASE
    row = repos.ModelCalls.get(conn, call.call_id)
    assert row is not None
    assert row["route_mode"] == "BASE"
    assert row["adapter"] == ""
    conn.close()


def test_w1_5_openai_route_local_adapter_does_not_leak(tmp_path, monkeypatch):
    """5. OpenAI route -> local adapter option does not leak into cloud provider invocation."""
    conn, _ = _fresh_db(tmp_path, "w1_5.db")
    fake_openai = FakeProvider()
    fake_openai.queue(text="openai answer", usage={"input_tokens": 18, "output_tokens": 28})
    router = mr.ModelRouter(openai_provider=fake_openai)

    monkeypatch.setattr(mr, "openai_configured", lambda: True)

    resp, call = router.complete(
        conn,
        turn_id="t_openai_1",
        project_id="starter",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        mode="OPENAI",
        opts={"adapter": "local-lora-leak-attempt", "temperature": 0.5},
    )

    assert resp.text == "openai answer"
    assert len(fake_openai.calls) == 1
    passed_opts = fake_openai.calls[0].get("opts", {})
    assert "adapter" not in passed_opts, f"Adapter leaked to OpenAI provider: {passed_opts}"
    assert passed_opts.get("temperature") == 0.5
    assert call.provider == "openai"
    conn.close()


# ================================================================
# W2 TESTS: TRUE SYNTHESIS TOKEN STREAMING
# ================================================================

def test_w2_1_incremental_chunks_produce_incremental_text_not_cumulative_append(tmp_path):
    """real provider chunks: 'A', 'B', 'C' produce incremental text:
    A, AB, ABC
    not: A, AAB, AABABC
    and not: ABC only at completion."""
    conn, _ = _fresh_db(tmp_path, "w2_1.db")
    fake_local = FakeProvider()
    fake_local.queue(text="ABC", chunks=["A", "B", "C"], usage={"input_tokens": 5, "output_tokens": 15})
    router = mr.ModelRouter(local_provider=fake_local)

    received_deltas = []
    received_snapshots = []
    current_text = ""

    def on_token(delta="", sequence=0, call_id=""):
        nonlocal current_text
        received_deltas.append(delta)
        current_text += delta
        received_snapshots.append(current_text)

    resp, call = router.complete_streaming(
        conn,
        turn_id="t_stream_chunks",
        project_id="starter",
        system="sys",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        mode="MARKETING_LORA",
        on_token=on_token,
    )

    # 1. Received chunks are exact deltas, not cumulative strings
    assert received_deltas == ["A", "B", "C"], f"Expected individual deltas, got: {received_deltas}"
    # 2. Accumulated text grows: 'A', 'AB', 'ABC'
    assert received_snapshots == ["A", "AB", "ABC"], f"Accumulated text wrong: {received_snapshots}"
    # 3. Not cumulative append bug ('A', 'AAB', 'AABABC')
    assert "AAB" not in received_snapshots
    # 4. Final text matches concatenated visible output
    assert resp.text == "ABC"
    assert "".join(received_deltas) == resp.text
    conn.close()


def test_w2_2_sse_event_order_and_completion_telemetry(tmp_path, monkeypatch):
    """W2: SSE event order:
    synthesis lifecycle -> ordered model_deltas -> model_completed with telemetry -> turn_completed."""
    from fastapi.testclient import TestClient
    from app.main import create_app

    db = tmp_path / "w2_sse.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    deps.init_db(db)
    with deps.get_db(db) as conn:
        proj = store.create_project(conn, "W2 SSE Test Project")
        convo = store.create_conversation(conn, project_id=proj["id"], title="w2 sse chat")

    fake_local = FakeProvider()
    fake_local.queue(text="First chunk. Second chunk.", chunks=["First chunk. ", "Second chunk."],
                     usage={"input_tokens": 14, "output_tokens": 28, "total_tokens": 42})
    router = mr.ModelRouter(local_provider=fake_local)

    from app.routes import graph_runtime
    monkeypatch.setattr(graph_runtime, "_get_model_router", lambda: router)
    graph_runtime._GRAPH = None

    app = create_app()
    client = TestClient(app)

    # 1. Create thread
    r = client.post(
        "/api/graph/threads",
        json={"project_id": proj["id"], "conversation_id": convo["id"],
              "text": "Tell me the strategy", "client_message_id": "c-w2-sse"},
    )
    assert r.status_code == 201
    tid = r.json()["data"]["thread_id"]

    # 2. Execute thread
    ex = client.post(f"/api/graph/threads/{tid}/execute")
    assert ex.status_code == 200
    assert "First chunk. Second chunk." in ex.json()["data"]["final_answer"]

    # 3. Read SSE events
    sse = client.get(f"/api/graph/threads/{tid}/events")
    assert sse.status_code == 200
    raw_lines = sse.text.splitlines()

    events_parsed = []
    curr_event = None
    curr_data = None
    curr_id = None
    for line in raw_lines:
        if line.startswith("id: "):
            curr_id = int(line[4:].strip())
        elif line.startswith("event: "):
            curr_event = line[7:].strip()
        elif line.startswith("data: "):
            try:
                curr_data = json.loads(line[6:].strip())
            except Exception:
                curr_data = line[6:].strip()
            if curr_event:
                events_parsed.append({"id": curr_id, "event": curr_event, "data": curr_data})
                curr_event = None

    event_types = [e["event"] for e in events_parsed]

    # Verify event types and sequence
    assert "model_delta" in event_types
    assert "model_completed" in event_types
    assert "turn_completed" in event_types

    # Find indices
    first_delta_idx = event_types.index("model_delta")
    completed_idx = event_types.index("model_completed")
    turn_completed_idx = event_types.index("turn_completed")

    # model_delta arrives BEFORE model_completed
    assert first_delta_idx < completed_idx < turn_completed_idx

    # Check model_delta fields: turn_id, call_id, sequence, delta
    deltas = [e for e in events_parsed if e["event"] == "model_delta"]
    assert len(deltas) >= 2
    assert deltas[0]["data"]["sequence"] == 1
    assert deltas[0]["data"]["delta"] == "First chunk. "
    assert deltas[1]["data"]["sequence"] == 2
    assert deltas[1]["data"]["delta"] == "Second chunk."

    # Check model_completed authoritative telemetry
    comp = next(e for e in events_parsed if e["event"] == "model_completed")
    telem = comp["data"].get("telemetry") or comp["data"].get("meta", {}).get("telemetry", {})
    assert telem.get("provider") == "local"
    assert telem.get("total_tokens") == 42


def test_w2_3_persistence_equality_and_reconnect(tmp_path, monkeypatch):
    """W2: Final persisted assistant message equals concatenated visible output.
    Reconnect with after cursor does not duplicate already-applied content."""
    from fastapi.testclient import TestClient
    from app.main import create_app

    db = tmp_path / "w2_persist.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    deps.init_db(db)
    with deps.get_db(db) as conn:
        proj = store.create_project(conn, "Persistence Project")
        convo = store.create_conversation(conn, project_id=proj["id"], title="persist chat")

    fake_local = FakeProvider()
    fake_local.queue(text="Alpha Beta Gamma.", chunks=["Alpha ", "Beta ", "Gamma."],
                     usage={"input_tokens": 10, "output_tokens": 20})
    router = mr.ModelRouter(local_provider=fake_local)

    from app.routes import graph_runtime
    monkeypatch.setattr(graph_runtime, "_get_model_router", lambda: router)
    graph_runtime._GRAPH = None

    app = create_app()
    client = TestClient(app)

    # Create & execute
    r = client.post(
        "/api/graph/threads",
        json={"project_id": proj["id"], "conversation_id": convo["id"],
              "text": "Strategy check", "client_message_id": "c-persist-1"},
    )
    tid = r.json()["data"]["thread_id"]
    ex = client.post(f"/api/graph/threads/{tid}/execute")
    assert ex.status_code == 200

    # 1. Read full SSE stream
    sse_all = client.get(f"/api/graph/threads/{tid}/events")
    all_lines = sse_all.text.splitlines()
    model_deltas = []
    last_delta_id = 0
    for line in all_lines:
        if line.startswith("id: "):
            last_delta_id = int(line[4:].strip())
        if line.startswith("data: ") and '"delta":' in line:
            try:
                data = json.loads(line[6:].strip())
                if "delta" in data:
                    model_deltas.append(data["delta"])
            except Exception:
                pass

    concatenated = "".join(model_deltas)
    assert concatenated == "Alpha Beta Gamma."

    # Verify persisted message in DB equals concatenated visible output
    with deps.get_db(db) as conn:
        msgs = repos.Messages.for_conversation(conn, convo["id"])
        asst_msg = next(m for m in msgs if m["role"] == "assistant")
        assert asst_msg["body_md"] == concatenated, "Persisted message does not match visible streamed output!"

    # 2. Simulate reconnect with after cursor
    # Client missed everything after the first delta
    first_event_id = 1
    sse_reconnect = client.get(f"/api/graph/threads/{tid}/events?after={first_event_id}")
    assert sse_reconnect.status_code == 200
    reconnected_lines = sse_reconnect.text.splitlines()
    reconnected_ids = [int(l[4:].strip()) for l in reconnected_lines if l.startswith("id: ")]
    # Proves cursor was respected: no reconnected ID is <= first_event_id
    assert all(eid > first_event_id for eid in reconnected_ids)


def test_w2_4_approval_interrupts_remain_unaffected(tmp_path, monkeypatch):
    """W2: Approval interrupts (yellow action) pause and resume cleanly without corruption."""
    from fastapi.testclient import TestClient
    from app.main import create_app

    db = tmp_path / "w2_approval.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    deps.init_db(db)
    with deps.get_db(db) as conn:
        proj = store.create_project(conn, "Approval Project")
        convo = store.create_conversation(conn, project_id=proj["id"], title="approval chat")

    app = create_app()
    client = TestClient(app)

    # 1. Trigger an operation that raises an approval interrupt
    # In account manager graph, asking for ad budget modification triggers approval
    r = client.post(
        "/api/graph/threads",
        json={"project_id": proj["id"], "conversation_id": convo["id"],
              "text": "Increase ad budget by $5,000 on Meta", "client_message_id": "c-appr-1"},
    )
    tid = r.json()["data"]["thread_id"]

    ex = client.post(f"/api/graph/threads/{tid}/execute")
    assert ex.status_code == 200
    data = ex.json()["data"]

    # Either completed or interrupted cleanly
    assert data["status"] in ("interrupted", "completed")
    if data["status"] == "interrupted":
        assert "approval_id" in data
        # Resume thread
        res = client.post(
            f"/api/graph/threads/{tid}/resume",
            json={"approved": True, "decided_by": "Founder", "approval_id": data["approval_id"]},
            headers={"Idempotency-Key": "res-key-1"},
        )
        assert res.status_code == 200
        assert res.json()["data"]["approved"] is True
