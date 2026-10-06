"""DEV-005 W3 — local llama.cpp loopback adapter + deterministic benchmark harness.

TDD RED spec (written before implementation). Scope: W3 only.
- Fake loopback transport: no network, no model download, no server start.
- Covers: health, discovery, streaming, null missing usage, metadata,
  timeout/error, deterministic benchmark output.
- Does NOT: download models, start servers, train LoRA, choose winner,
  edit router, claim benchmarks ran.
"""
from app.services.llm.base import LLMResponse


class FakeLoopback:
    """Fake llama.cpp OpenAI-compatible server on loopback.

    Routes: GET /health, GET /v1/models, POST /v1/chat/completions
    (stream=False single JSON, stream=True SSE bytes).
    """

    def __init__(self, *, model_id="qwen3-8b-Q4_K_M", usage=None,
                 stream_chunks=None, health=None, timeout_on=None):
        self.model_id = model_id
        self.usage = usage  # None => omit usage block entirely
        self.stream_chunks = list(stream_chunks or ["Hello ", "world"])
        self.health = {"status": "ok"} if health is None else health
        self.timeout_on = set(timeout_on or ())
        self.calls = []

    def request(self, method, path, *, body=None, stream=False):
        self.calls.append((method, path, stream))
        if path in self.timeout_on:
            raise TimeoutError("fake loopback timed out")
        if method == "GET" and path == "/health":
            return {"status_code": 200, "json": dict(self.health), "sse": b""}
        if method == "GET" and path == "/v1/models":
            return {"status_code": 200,
                    "json": {"data": [{"id": self.model_id}]}, "sse": b""}
        if method == "POST" and path == "/v1/chat/completions":
            if stream:
                lines = []
                for c in self.stream_chunks:
                    import json as _j
                    lines.append("data: " + _j.dumps(
                        {"choices": [{"delta": {"content": c,
                                                "reasoning_content": "SECRET-THINK"},
                                      "finish_reason": None}]}, ensure_ascii=False))
                lines.append("data: [DONE]")
                return {"status_code": 200, "json": {},
                        "sse": ("\n".join(lines) + "\n").encode("utf-8")}
            payload = {"choices": [{"message": {"content": "".join(self.stream_chunks)},
                                     "finish_reason": "stop"}],
                       "model": self.model_id}
            if self.usage is not None:
                payload["usage"] = dict(self.usage)
            return {"status_code": 200, "json": payload, "sse": b""}
        return {"status_code": 404, "json": {"error": "not found"}, "sse": b""}


def _provider(transport, **kw):
    from app.services.llm.local_llama import LocalLlamaProvider
    kw.setdefault("model", "qwen3-8b-Q4_K_M")
    kw.setdefault("quantization", "Q4_K_M")
    return LocalLlamaProvider(transport=transport.request, **kw)


def test_health_ready_via_loopback():
    prov = _provider(FakeLoopback())
    assert prov.health(timeout_s=2) is True


def test_discovery_returns_actual_model_identity():
    fake = FakeLoopback(model_id="gemma3-4b-Q4_K_M")
    prov = _provider(fake, model="stale-guess")
    models = prov.list_models()
    assert models == ["gemma3-4b-Q4_K_M"]
    resp = prov.complete(system="s", messages=[{"role": "user", "content": "hi"}],
                         tools=[], opts={})
    assert isinstance(resp, LLMResponse)
    assert resp.usage["model"] == "gemma3-4b-Q4_K_M"


def test_streaming_emits_cumulative_deltas_and_ignores_reasoning():
    fake = FakeLoopback()
    prov = _provider(fake)
    events = []
    resp = prov.complete_streaming(system="s",
                                   messages=[{"role": "user", "content": "hi"}],
                                   tools=[], opts={},
                                   on_event=lambda k, v: events.append((k, v)))
    assert resp.text == "Hello world"
    assert "SECRET-THINK" not in resp.text
    deltas = [v for k, v in events if k == "delta"]
    assert deltas and deltas[-1] == "Hello world"
    assert all("SECRET-THINK" not in d for d in deltas)


def test_missing_usage_stays_null_never_zero_filled():
    fake = FakeLoopback(usage=None)  # server omits usage block
    prov = _provider(fake)
    resp = prov.complete(system="s", messages=[{"role": "user", "content": "hi"}],
                         tools=[], opts={})
    assert resp.usage["input_tokens"] is None
    assert resp.usage["output_tokens"] is None
    assert resp.usage["total_tokens"] is None
    from app.contracts.model_call import ModelCall
    call = prov.to_model_call(resp, turn_id="t1", project_id="starter",
                              route_mode="LOCAL", route_reason="w3 fake loopback")
    assert call.unknown_token_fields() != []
    assert call.totals_consistent() is False


def test_partial_usage_maps_provider_semantics():
    fake = FakeLoopback(usage={"prompt_tokens": 100, "completion_tokens": 50,
                               "total_tokens": 150})
    prov = _provider(fake)
    resp = prov.complete(system="s", messages=[{"role": "user", "content": "hi"}],
                         tools=[], opts={})
    assert (resp.usage["input_tokens"], resp.usage["output_tokens"],
            resp.usage["total_tokens"]) == (100, 50, 150)
    from app.contracts.model_call import ModelCall
    call = prov.to_model_call(resp, turn_id="t1", project_id="starter",
                              route_mode="LOCAL", route_reason="w3 fake loopback")
    assert call.totals_consistent() is True
    assert call.local_api_cost_usd() == 0.0


def test_quantization_adapter_metadata_carried():
    fake = FakeLoopback()
    prov = _provider(fake, quantization="Q4_K_M", adapter="")
    resp = prov.complete(system="s", messages=[{"role": "user", "content": "hi"}],
                         tools=[], opts={})
    assert resp.usage["quantization"] == "Q4_K_M"
    assert resp.usage["adapter"] == ""
    assert resp.usage["provider"] == "local"
    call = prov.to_model_call(resp, turn_id="t1", project_id="starter",
                              route_mode="LOCAL", route_reason="w3 fake loopback")
    assert call.quantization == "Q4_K_M"
    assert call.provider == "local"
    assert call.local_api_cost_usd() == 0.0


def test_timeout_and_error_surface_as_runtime_error():
    fake = FakeLoopback(timeout_on={"/v1/chat/completions"})
    prov = _provider(fake)
    try:
        prov.complete(system="s", messages=[{"role": "user", "content": "hi"}],
                      tools=[], opts={})
        assert False, "must raise"
    except RuntimeError as e:
        assert "timed out" in str(e).lower() or "timeout" in str(e).lower()
    bad = FakeLoopback()
    bad.request = lambda *a, **k: {"status_code": 500,
                                   "json": {"error": "boom"}, "sse": b""}
    prov2 = _provider(bad)
    try:
        prov2.complete(system="s", messages=[{"role": "user", "content": "hi"}],
                       tools=[], opts={})
        assert False, "must raise"
    except RuntimeError:
        pass


def test_non_loopback_base_url_rejected_fail_closed():
    from app.services.llm.local_config import LocalLlamaConfig
    try:
        LocalLlamaConfig(base_url="http://192.168.1.10:8080")
        assert False, "must raise"
    except ValueError:
        pass


def test_cpu_gpu_neutral_config_defaults():
    from app.services.llm.local_config import LocalLlamaConfig
    cfg = LocalLlamaConfig()
    assert cfg.base_url.startswith("http://127.0.0.1")
    assert cfg.n_gpu_layers == 0  # neutral: CPU fallback unless opted in
    assert cfg.n_ctx >= 4096
    assert cfg.timeout_s > 0 and cfg.health_timeout_s > 0


def test_benchmark_harness_deterministic_and_sliced():
    from app.services.llm.local_benchmark import (BenchmarkManifest,
                                                  HeldoutCase, run_benchmark)
    manifest = BenchmarkManifest(
        model="qwen3-8b", weight_hash="sha256:PINNED-EXAMPLE",
        runtime="llama.cpp", runtime_version="pinned-test",
        quantization="Q4_K_M", gguf_path="models/qwen3-8b-Q4_K_M.gguf",
        cpu="test-cpu", ram_gb=32.0, gpu="none", vram_gb=0.0,
        backend="CPU", server_flags="--ctx-size 8192",
    )
    cases = [
        HeldoutCase(case_id="ar-1", lang_slice="arabic_msa",
                    prompt="ما هو عرضك؟", reference="رد"),
        HeldoutCase(case_id="eg-1", lang_slice="egyptian",
                    prompt="العرض ايه؟", reference="رد"),
        HeldoutCase(case_id="en-1", lang_slice="english",
                    prompt="What is the offer?", reference="reply"),
        HeldoutCase(case_id="mix-1", lang_slice="mixed",
                    prompt="What is العرض؟", reference="reply رد"),
    ]

    def fake_complete(case, manifest):
        from app.services.llm.local_benchmark import CaseResult
        return CaseResult(case_id=case.case_id, lang_slice=case.lang_slice,
                          output_text="output " + case.case_id,
                          ttft_ms=50.0, latency_ms=200.0,
                          output_tokens=10, ram_gb=8.0, vram_gb=0.0,
                          quality=1.0)

    clock = [1000.0]

    def fake_clock():
        clock[0] += 1.0
        return clock[0]

    out1 = run_benchmark(manifest, cases, fake_complete, clock=fake_clock)
    clock[0] = 1000.0
    out2 = run_benchmark(manifest, cases, fake_complete, clock=fake_clock)
    assert out1 == out2  # deterministic
    assert {r["lang_slice"] for r in out1["results"]} == {
        "arabic_msa", "egyptian", "english", "mixed"}
    for key in ("ttft_ms", "latency_ms", "tok_per_s", "ram_gb",
                "vram_gb", "quality"):
        assert key in out1["summary"], key
    row = out1["matrix_rows"][0]
    for col in ("model", "weight_hash", "runtime", "quant", "lang_slice",
                "ttft_ms", "p50_ms", "p95_ms", "tok_s", "ram_gb",
                "vram_gb", "notes"):
        assert col in row, col
    assert out1["notes"].startswith("NOT_RUN")
