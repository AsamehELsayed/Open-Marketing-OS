"""DEV-007R-HOTFIX-3 W2 — website capability tools + provider telemetry.

Covers: registry registration, transport-injected fetch/crawl/audit,
capability dispatch (tool_runs + deterministic synthesis, no-RAG-failure),
and the adapter-identity/usage-telemetry contract for cloud providers.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _db(tmp_path, name="hfx3.db", seed=False):
    from app.database.sqlite import connect
    if seed:
        from app.database.seed import ensure_seed

    p = tmp_path / name
    conn = connect(p)
    if seed:
        ensure_seed(conn)
    return conn


# ------------------------------------------------------------ fake transport

_HOME_HTML = """<html><head><title>Acme Test Company — Custom websites</title>
<meta name="description" content="Acme Test Company builds custom websites and e-commerce.">
<meta name="viewport" content="width=device-width, initial-scale=1">
</head><body>
<h1>Custom websites for growing businesses</h1>
<p>We build custom websites and e-commerce systems.</p>
<a href="/about">About us</a>
<a href="https://instagram.com/acme_test">Instagram</a>
<a href="mailto:hello@acme-test.example">Email us</a>
<a href="https://twitter.com/otheraccount">Twitter</a>
<a href="#top">top</a>
</body></html>"""

_ABOUT_HTML = """<html><head><title>About — Acme Test Company</title></head>
<body><h1>About Acme Test Company</h1><p>Contact us on WhatsApp to get started.</p>
<a href="/extra">More</a><a href="/extra2">More2</a></body></html>"""

_OTHER_HTML = "<html><head><title>Other</title></head><body>offsite</body></html>"


def _fake_transport(pages: dict[str, str | Exception]):
    def _t(url: str, timeout_s: int = 10) -> dict:
        page = pages.get(url)
        if page is None or isinstance(page, Exception):
            raise page or RuntimeError("page missing from fake transport")
        from app.services.tools.web_tools import _parse_result, _PageParser

        parser = _PageParser()
        parser.feed(str(page))
        parser.close()
        return _parse_result(url, url, 200, parser, 12)
    return _t


_ERROR_TRANSPORT_URL = "https://down.example.com"


def _failing_transport(url: str, timeout_s: int = 10) -> dict:
    return {"status": "error", "error": "The website could not be fetched.",
            "latency_ms": 5}


# ------------------------------------------------------------ registry

def test_registry_includes_website_tools():
    from app.services.tools import build_default_registry

    names = build_default_registry().names()
    for expected in ("website_fetch", "website_crawl",
                     "website_marketing_audit"):
        assert expected in names


# ------------------------------------------------------------ tools

def test_website_fetch_happy_path(tmp_path):
    from app.services.tools.web_tools import t_website_fetch

    transport = _fake_transport({"https://acme-test.example": _HOME_HTML})
    out = t_website_fetch(None, project_id="starter", root="",
                          args={"url": "acme-test.example"}, transport=transport)
    assert out["ok"] is True
    assert out["status"] == "PARTIALLY VERIFIED"
    page = out["page"]
    assert page["status"] == "fetched"
    assert page["title"] == "Acme Test Company — Custom websites"
    assert page["meta_description"].startswith("Acme Test Company builds custom websites")
    assert page["h1"] == "Custom websites for growing businesses"
    assert page["has_viewport"] is True
    assert "custom websites" in page["text_head"].lower()
    hrefs = [l["href"] for l in page["links"]]
    assert "/about" in hrefs
    assert "https://instagram.com/acme_test" in hrefs
    assert len(page["links"]) <= 50
    assert page["fetched_at"]


def test_website_fetch_not_accessible(tmp_path):
    from app.services.tools.web_tools import t_website_fetch

    out = t_website_fetch(None, project_id="starter", root="",
                          args={"url": _ERROR_TRANSPORT_URL},
                          transport=_failing_transport)
    assert out["ok"] is False
    assert out["status"] == "NOT ACCESSIBLE"
    assert out["page"] is None
    assert out["url"] == _ERROR_TRANSPORT_URL


def test_website_crawl_same_origin_and_max_pages():
    from app.services.tools.web_tools import t_website_crawl

    pages = {
        "https://acme-test.example": _HOME_HTML,
        "https://acme-test.example/about": _ABOUT_HTML,
        "https://other.example.com/": _OTHER_HTML,
        "https://acme-test.exampleextra": _OTHER_HTML,
        "https://acme-test.exampleextra2": _OTHER_HTML,
        "https://acme-test.exampleextra3": _OTHER_HTML,
    }
    transport = _fake_transport(pages)
    out = t_website_crawl(None, project_id="starter", root="",
                          args={"url": "acme-test.example", "max_pages": 2},
                          transport=transport)
    assert out["ok"] is True
    assert out["status"] == "PARTIALLY VERIFIED"
    assert out["crawled"] == 2
    urls = [p["url"] for p in out["pages"]]
    assert all(u.startswith("https://acme-test.example") for u in urls)
    assert "https://other.example.com/" not in urls
    full = t_website_crawl(None, project_id="starter", root="",
                           args={"url": "acme-test.example", "max_pages": 99},
                           transport=transport)
    assert full["crawled"] <= 10


def test_website_marketing_audit_checklist():
    from app.services.tools.web_tools import t_website_marketing_audit

    transport = _fake_transport({
        "https://acme-test.example": _HOME_HTML,
        "https://acme-test.example/about": _ABOUT_HTML,
    })
    out = t_website_marketing_audit(None, project_id="starter", root="",
                                    args={"url": "https://acme-test.example"},
                                    transport=transport)
    assert out["ok"] is True
    assert out["status"] == "PARTIALLY VERIFIED"
    assert out["confidence"] == "MEDIUM"
    audit = out["audit"]
    cl = audit["checklist"]
    assert cl["has_title"] is True
    assert cl["has_meta_description"] is True
    assert cl["has_h1"] is True
    assert cl["has_cta_keywords"] is True
    assert cl["has_email"] is True
    assert cl["is_https"] is True
    assert cl["social_links"]["instagram"] is True
    assert cl["pages_checked"] == 2
    assert any("Acme Test Company" in v for v in audit["verdict"])


# ------------------------------------------------------------ dispatch

def test_dispatch_audit_persists_tool_run_and_synthesizes(tmp_path):
    from app.graphs.tool_capability import resolve_and_execute
    from unittest.mock import patch

    conn = _db(tmp_path, seed=True)
    transport = _fake_transport({"https://acme-test.example": _HOME_HTML})
    with patch("app.services.tools.web_tools._default_website_transport",
               transport):
        out = resolve_and_execute(
            conn, project_id="starter", capability="website_marketing_audit",
            args={"url": "acme-test.example"}, turn_id="tw1")
    rows = conn.execute(
        "SELECT tool_id, status, provider FROM tool_runs").fetchall()
    assert ("website_marketing_audit", "success", "website_fetcher") \
        in [tuple(r) for r in rows]
    answer = out["answer"]
    assert "Website audit for https://acme-test.example" in answer
    assert "STATUS: PARTIALLY VERIFIED / SOURCE: website_fetch / CONFIDENCE: MEDIUM" in answer
    assert "Custom websites" in answer
    conn.close()


def test_dispatch_audit_autofills_url_from_project(tmp_path):
    from app.graphs.tool_capability import resolve_and_execute
    from unittest.mock import patch

    conn = _db(tmp_path)
    from app.database import repos
    repos.Projects.upsert(conn, {
        "id": "starter", "name": "Acme Test Company",
        "website": "https://acme-test.example", "goal": "", "status": "active",
        "settings_json": "{}",
        "created_at": "2026-09-26T00:00:00+00:00",
        "updated_at": "2026-09-26T00:00:00+00:00"})
    transport = _fake_transport({"https://acme-test.example": _HOME_HTML,
                                 "https://acme-test.example": _HOME_HTML})
    with patch("app.services.tools.web_tools._default_website_transport",
               transport):
        out = resolve_and_execute(
            conn, project_id="starter", capability="website_marketing_audit",
            args={}, turn_id="tw2")
    assert out["url"] == "https://acme-test.example"
    assert "Website audit for https://acme-test.example" in out["answer"]
    rows = conn.execute("SELECT tool_id, status FROM tool_runs").fetchall()
    assert ("website_marketing_audit", "success") in [tuple(r) for r in rows]
    conn.close()


def test_dispatch_missing_url_asks_for_it(tmp_path):
    from app.graphs.tool_capability import resolve_and_execute

    conn = _db(tmp_path)
    out = resolve_and_execute(
        conn, project_id="starter", capability="website_marketing_audit",
        args={}, turn_id="tw3")
    assert out["status"] == "needs_url"
    assert out["route_note"] == "NEEDS_URL"
    assert "Which website should I analyze?" in out["answer"]
    conn.close()


def test_dispatch_failed_transport_is_honest_no_rag(tmp_path):
    from app.graphs.tool_capability import resolve_and_execute
    from unittest.mock import patch

    conn = _db(tmp_path, seed=True)
    with patch("app.services.tools.web_tools._default_website_transport",
               _failing_transport):
        out = resolve_and_execute(
            conn, project_id="starter", capability="website_marketing_audit",
            args={"url": _ERROR_TRANSPORT_URL}, turn_id="tw4")
    assert out["status"] == "failed"
    assert "I couldn't fetch %s right now." % _ERROR_TRANSPORT_URL in out["answer"]
    assert "[Retry]" in out["answer"]
    assert "recorded knowledge" not in out["answer"]
    rows = conn.execute("SELECT tool_id, status FROM tool_runs").fetchall()
    assert ("website_marketing_audit", "failed") in [tuple(r) for r in rows]
    conn.close()


def test_resolve_and_execute_determinism(tmp_path):
    from app.graphs.tool_capability import resolve_and_execute
    from unittest.mock import patch

    conn = _db(tmp_path, seed=True)
    transport = _fake_transport({"https://acme-test.example": _HOME_HTML,
                                 "https://acme-test.example": _HOME_HTML})
    outs = []
    for i in range(2):
        with patch("app.services.tools.web_tools._default_website_transport",
                   transport):
            outs.append(resolve_and_execute(
                conn, project_id="starter",
                capability="website_marketing_audit",
                args={"url": "acme-test.example"}, turn_id=f"td{i}"))
    a, b = outs
    assert a["capability"] == b["capability"]
    assert a["status"] == b["status"]
    assert a["answer"] == b["answer"]
    conn.close()


# ------------------------------------------------------------ telemetry

def _pricing():
    from app.services.llm import model_router as mr

    return mr.load_pricing(ROOT / "config" / "model_pricing.yaml")


class _FakeOpenRouter:
    name = "openrouter"
    model = "openrouter/auto"

    def __init__(self, usage: dict):
        self.usage = usage

    def complete(self, *, system, messages, tools, opts=None):
        from app.services.llm.base import LLMResponse

        return LLMResponse(text="cloud answer", tool_calls=[],
                           usage=dict(self.usage))


def _patch_settings(monkeypatch):
    """Isolate the router from the developer machine's real settings DB."""
    from app.services.config_service import ConfigService

    monkeypatch.setattr(ConfigService, "get_setting",
                        lambda *a, **k: "", raising=True)


def test_openrouter_row_no_lora_adapter(tmp_path, monkeypatch):
    from app.database import repos
    from app.services.llm import model_router as mr

    monkeypatch.setattr(mr, "openrouter_configured_default", lambda: True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _patch_settings(monkeypatch)
    conn = _db(tmp_path)
    fake = _FakeOpenRouter({"input": 10, "output": 5, "reasoning": 2,
                            "actual_model": "vendor/model-x"})
    router = mr.ModelRouter(openrouter_provider=fake, pricing=_pricing(),
                            default_openrouter_model="openrouter/auto")
    _, call = router.complete(conn, turn_id="t-or", project_id="starter",
                              system="s", messages=[], tools=[],
                              mode="OPENROUTER",
                              behavior_profile="OMOS Marketing v1")
    assert call.provider == "openrouter"
    assert call.adapter == ""
    assert call.behavior_profile == "OMOS Marketing v1"
    assert call.requested_model == "openrouter/auto"
    assert call.model == "vendor/model-x"
    assert call.total_tokens == 15
    row = repos.ModelCalls.get(conn, call.call_id)
    assert row["adapter"] == ""
    assert row["behavior_profile"] == "OMOS Marketing v1"
    assert row["requested_model"] == "openrouter/auto"
    assert row["total_tokens"] == 15
    conn.close()


def test_local_row_keeps_adapter_no_profile(tmp_path, monkeypatch):
    from app.services.llm import model_router as mr
    from app.services.llm.fake_provider import FakeProvider

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    conn = _db(tmp_path)
    fake_local = FakeProvider()
    fake_local.queue(text="local answer",
                     usage={"input_tokens": 100, "output_tokens": 50})
    router = mr.ModelRouter(local_provider=fake_local, pricing=_pricing())
    _, call = router.complete(conn, turn_id="t-lo", project_id="starter",
                              system="s", messages=[], tools=[], mode="LOCAL",
                              behavior_profile="OMOS Marketing v1")
    assert call.provider == "local"
    assert call.adapter == "OMOS-Qwen2.5-7B-Marketing-v1"
    assert call.behavior_profile == ""
    assert call.total_tokens == 150
    conn.close()


def test_total_reconciliation_reported_and_unknown(tmp_path, monkeypatch):
    from app.services.llm import model_router as mr

    monkeypatch.setattr(mr, "openrouter_configured_default", lambda: True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _patch_settings(monkeypatch)
    conn = _db(tmp_path)
    reported = _FakeOpenRouter({"input": 10, "output": 5, "total_tokens": 15,
                                "actual_model": "vendor/model-x"})
    router = mr.ModelRouter(openrouter_provider=reported, pricing=_pricing())
    _, call = router.complete(conn, turn_id="t-r1", project_id="starter",
                              system="s", messages=[], tools=[],
                              mode="OPENROUTER")
    assert call.total_tokens == 15
    unknown = _FakeOpenRouter({})
    router2 = mr.ModelRouter(openrouter_provider=unknown, pricing=_pricing())
    _, call2 = router2.complete(conn, turn_id="t-r2", project_id="starter",
                                system="s", messages=[], tools=[],
                                mode="OPENROUTER")
    assert call2.total_tokens is None
    conn.close()


def test_openrouter_usage_carries_raw_total():
    from app.services.llm.openrouter_provider import _usage_of

    class _U:
        def model_dump(self):
            return {"prompt_tokens": 10, "completion_tokens": 5,
                    "total_tokens": 15}

    out = _usage_of(_U())
    assert out["total_tokens"] == 15
