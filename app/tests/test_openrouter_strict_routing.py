"""Strict OpenRouter acceptance policy tests use an in-memory HTTP transport."""
from __future__ import annotations

import json
from decimal import Decimal
from urllib.parse import urlsplit

import httpx
import pytest


MODEL = "apodex/apodex-1.1-mini:free"
BASE_MODEL = "apodex/apodex-1.1-mini"
ENDPOINT = "novita/bf16"
PRICE = {"prompt": "0", "completion": "0", "request": "0"}
SUCCESS = {
    "id": "chatcmpl-loopback",
    "object": "chat.completion",
    "created": 1,
    "model": MODEL,
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "fixture content"},
                  "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
}


def _eligibility_documents():
    return {
        "/api/v1/auth/key": {"data": {"is_free_tier": True}},
        "/api/v1/models": {"data": [{"id": MODEL, "pricing": dict(PRICE)}]},
        "/api/v1/models/apodex/apodex-1.1-mini%3Afree/endpoints": {
            "data": {"id": MODEL, "endpoints": [{
                "provider_name": "Novita", "tag": ENDPOINT, "status": 0,
                "pricing": dict(PRICE),
            }]}
        },
        "/api/v1/endpoints/zdr": {"data": [{
            "model_id": MODEL, "provider_name": "Novita", "tag": ENDPOINT,
        }]},
    }


class Loopback:
    def __init__(self, *, statuses=(200,), documents=None):
        self.statuses = list(statuses)
        self.documents = documents or _eligibility_documents()
        self.requests: list[dict] = []
        self.transport_retries: list[int] = []
        self.url = "https://openrouter.fixture/api/v1"

    def handle_request(self, request):
        path = urlsplit(str(request.url)).path
        record = {"method": request.method, "path": path,
                  "authorization": request.headers.get("Authorization")}
        if request.method == "GET":
            self.requests.append(record)
            body = self.documents.get(path)
            if body is None:
                return httpx.Response(404, json={"error": {"message": "fixture route missing"}},
                                      request=request)
            return httpx.Response(200, json=body, request=request)
        record["json"] = json.loads(request.content)
        self.requests.append(record)
        status = self.statuses.pop(0) if self.statuses else 200
        body = SUCCESS if status == 200 else {"error": {
            "message": "synthetic fixture failure", "type": "fixture_error",
            "code": "fixture_failure",
        }}
        return httpx.Response(status, json=body, request=request)

    def http_transport(self, *, retries=0):
        self.transport_retries.append(retries)
        return httpx.MockTransport(self.handle_request)

    def close(self):
        return


@pytest.fixture
def provider_env(monkeypatch):
    from app.services.llm import openrouter_provider as module

    server = Loopback()
    monkeypatch.setattr(module, "BASE_URL", server.url)
    monkeypatch.setattr(module, "get_key", lambda: "LOOPBACK-ONLY-SECRET")
    monkeypatch.setattr(httpx, "HTTPTransport", server.http_transport)
    try:
        yield server, module
    finally:
        server.close()


def _strict_opts(eligibility, **extra):
    return {
        "model": MODEL,
        "_strict_zero_cost": True,
        "_strict_eligibility": eligibility,
        **extra,
    }


def test_chat_selection_preserves_the_exact_free_model_id():
    from app.services.chat_models import normalize_chat_selection

    models = {"providers": [{"provider": "OPENROUTER", "available": True,
                             "models": [{"id": MODEL}]}]}
    assert normalize_chat_selection("OPENROUTER", MODEL, options=models) == {
        "model_provider": "OPENROUTER", "model_id": MODEL,
    }


def test_unsuffixed_model_is_rejected_before_any_eligibility_request(provider_env):
    server, module = provider_env

    with pytest.raises(module.OpenRouterError, match="exact free model"):
        module.verify_strict_eligibility(BASE_MODEL, endpoint_slug=ENDPOINT)

    assert server.requests == []


def test_authenticated_read_only_preflight_verifies_current_model_endpoint_and_zdr(provider_env):
    server, module = provider_env

    eligibility = module.verify_strict_eligibility(MODEL, endpoint_slug=ENDPOINT)

    assert eligibility.model_id == MODEL
    assert eligibility.endpoint_slug == ENDPOINT
    assert eligibility.prompt_price == Decimal("0")
    assert eligibility.completion_price == Decimal("0")
    assert eligibility.request_price == Decimal("0")
    assert eligibility.account_free_tier is True
    assert eligibility.zdr_eligible is True
    assert [request["path"] for request in server.requests] == [
        "/api/v1/auth/key", "/api/v1/models",
        "/api/v1/models/apodex/apodex-1.1-mini%3Afree/endpoints", "/api/v1/endpoints/zdr",
    ]
    assert all(request["method"] == "GET" and
               request["authorization"] == "Bearer LOOPBACK-ONLY-SECRET"
               for request in server.requests)


def test_missing_prompt_or_completion_price_fails_closed(provider_env):
    server, module = provider_env

    server.documents["/api/v1/models"] = {"data": [{
        "id": MODEL, "pricing": {"prompt": "0"},
    }]}
    with pytest.raises(module.OpenRouterError, match="completion price"):
        module.verify_strict_eligibility(MODEL, endpoint_slug=ENDPOINT)

    assert [request["path"] for request in server.requests] == [
        "/api/v1/auth/key", "/api/v1/models",
        "/api/v1/models/apodex/apodex-1.1-mini%3Afree/endpoints", "/api/v1/endpoints/zdr"]


def test_absent_request_price_is_unknown_but_zero_request_cap_is_sent(provider_env):
    server, module = provider_env
    server.documents["/api/v1/models"]["data"][0]["pricing"].pop("request")
    endpoint = server.documents[
        "/api/v1/models/apodex/apodex-1.1-mini%3Afree/endpoints"]["data"]["endpoints"][0]
    endpoint["pricing"].pop("request")

    eligibility = module.verify_strict_eligibility(MODEL, endpoint_slug=ENDPOINT)
    assert eligibility.request_price is None
    module.OpenRouterProvider().complete(
        system="s", messages=[], tools=[], opts=_strict_opts(eligibility))

    request = next(request for request in server.requests if request["method"] == "POST")
    assert request["json"]["provider"]["max_price"]["request"] == 0


def test_zdr_results_must_include_the_exact_endpoint(provider_env):
    server, module = provider_env
    server.documents["/api/v1/endpoints/zdr"] = {"data": [{
        "model_id": MODEL, "provider_name": "Novita", "tag": "novita/other",
    }]}

    with pytest.raises(module.OpenRouterError, match="absent from authenticated ZDR"):
        module.verify_strict_eligibility(MODEL, endpoint_slug=ENDPOINT)

    assert not [request for request in server.requests if request["method"] == "POST"]


def test_strict_request_serializes_exact_model_and_openrouter_policy(provider_env):
    server, module = provider_env
    eligibility = module.verify_strict_eligibility(MODEL, endpoint_slug=ENDPOINT)
    opts = _strict_opts(eligibility, extra_body={
        "usage": {"include": False, "custom_usage_field": "kept"},
        "trace_extension": {"id": "fixture-trace"},
        "provider": {"only": ["other/provider"], "allow_fallbacks": True,
                      "sort": "price"},
    })

    events = []
    result = module.OpenRouterProvider().complete(
        system="fixture system", messages=[], tools=[],
        opts={**opts, "_transport_observer": events.append},
    )

    request = next(request for request in server.requests if request["method"] == "POST")
    body = request["json"]
    assert body["model"] == MODEL
    assert body["provider"] == {
        "only": [ENDPOINT], "allow_fallbacks": False, "zdr": True,
        "data_collection": "deny",
        "max_price": {"prompt": 0, "completion": 0, "request": 0},
        "sort": "price",
    }
    assert body["usage"] == {"include": True, "custom_usage_field": "kept"}
    assert body["trace_extension"] == {"id": "fixture-trace"}
    assert "extra_body" not in body
    assert result.text == "fixture content"
    assert result.usage["requested_model"] == MODEL
    assert result.usage["input"] == 12
    assert result.usage["output"] == 8
    assert any(event["state"] == "provider_request_started" for event in events)


def test_strict_model_mismatch_and_missing_eligibility_block_before_post(provider_env):
    from dataclasses import replace

    server, module = provider_env
    eligibility = module.verify_strict_eligibility(MODEL, endpoint_slug=ENDPOINT)

    for opts in (
        {"model": MODEL, "_strict_zero_cost": True},
        _strict_opts(eligibility, model=BASE_MODEL),
        _strict_opts(replace(eligibility, _verification_token=object())),
    ):
        with pytest.raises(module.OpenRouterError):
            module.OpenRouterProvider().complete(
                system="s", messages=[], tools=[], opts=opts)

    assert not [request for request in server.requests if request["method"] == "POST"]


def test_strict_mode_rejects_tools_before_provider_transport(provider_env):
    server, module = provider_env
    eligibility = module.verify_strict_eligibility(MODEL, endpoint_slug=ENDPOINT)

    with pytest.raises(module.OpenRouterError, match="does not allow provider tools"):
        module.OpenRouterProvider().complete(
            system="s", messages=[], tools=[{"name": "extra_call"}],
            opts=_strict_opts(eligibility))

    assert not [request for request in server.requests if request["method"] == "POST"]


def test_strict_transient_failure_has_one_post_and_sdk_transport_has_zero_retries(
        provider_env, monkeypatch):
    server, module = provider_env
    server.statuses = [429, 200]
    eligibility = module.verify_strict_eligibility(MODEL, endpoint_slug=ENDPOINT)
    import openai
    sdk_retries = []
    original_openai_init = openai.OpenAI.__init__

    def inspect_sdk(self, *args, **kwargs):
        sdk_retries.append(kwargs.get("max_retries"))
        return original_openai_init(self, *args, **kwargs)

    monkeypatch.setattr(openai.OpenAI, "__init__", inspect_sdk)
    with pytest.raises(module.OpenRouterError):
        module.OpenRouterProvider().complete(
            system="s", messages=[], tools=[], opts=_strict_opts(eligibility))

    posts = [request for request in server.requests if request["method"] == "POST"]
    assert len(posts) == 1
    assert sdk_retries == [0]
    assert server.transport_retries[-1] == 0


def test_normal_request_stays_backward_compatible_without_strict_routing(provider_env):
    server, module = provider_env

    result = module.OpenRouterProvider().complete(
        system="normal system", messages=[], tools=[],
        opts={"model": "ordinary/provider-model"},
    )

    request = next(request for request in server.requests if request["method"] == "POST")
    assert result.text == "fixture content"
    assert request["json"]["model"] == "ordinary/provider-model"
    assert request["json"]["usage"] == {"include": True}
    assert "provider" not in request["json"]


def test_normal_mode_keeps_the_existing_single_bounded_retry(provider_env):
    server, module = provider_env
    server.statuses = [429, 200]

    result = module.OpenRouterProvider().complete(
        system="normal system", messages=[], tools=[],
        opts={"model": "ordinary/provider-model"},
    )

    assert result.text == "fixture content"
    posts = [request for request in server.requests if request["method"] == "POST"]
    assert len(posts) == 2
    assert all(request["json"]["model"] == "ordinary/provider-model" for request in posts)


def test_campaign_strict_mode_rejects_auto_and_calls_router_once_when_explicit(
        provider_env, monkeypatch):
    from types import SimpleNamespace
    from app.services import campaign_production

    _server, module = provider_env
    eligibility = module.verify_strict_eligibility(MODEL, endpoint_slug=ENDPOINT)

    class FakeRouter:
        def __init__(self):
            self.calls = []

        def complete(self, conn, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(text="one batch"), SimpleNamespace(
                provider="openrouter", model=MODEL, requested_model=MODEL,
                route_mode="OPENROUTER", route_reason="explicit",
                call_id="call-once", total_tokens=3, estimated_cost_usd=0.0)

    router = FakeRouter()
    monkeypatch.setattr(campaign_production, "_router", lambda: router)
    with pytest.raises(campaign_production.DeliverableGenerationError,
                       match="explicit OPENROUTER"):
        campaign_production.complete(
            None, turn_id="turn-strict", project_id="fictional", system="s", user="u",
            provider="AUTO", model_id=MODEL, strict_zero_cost=True,
            strict_eligibility=eligibility)
    assert router.calls == []

    text, identity = campaign_production.complete(
        None, turn_id="turn-strict", project_id="fictional", system="s", user="u",
        provider="OPENROUTER", model_id=MODEL, strict_zero_cost=True,
        strict_eligibility=eligibility)
    assert text == "one batch"
    assert identity["model"] == MODEL
    assert len(router.calls) == 1
    assert router.calls[0]["mode"] == "OPENROUTER"
    assert router.calls[0]["model_override"] == MODEL
    assert router.calls[0]["tools"] == []
    assert router.calls[0]["opts"]["_strict_zero_cost"] is True
