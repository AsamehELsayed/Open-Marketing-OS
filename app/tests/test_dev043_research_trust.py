"""DEV-043: research trust (no fabricated evidence) and SSRF-safe fetching."""
import io
import json
import socket
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import deps
from app.database.sqlite import connect
from app.routes import business_workspace, campaign_deliverables
from app.services.tools import safe_fetch, web_tools

PLACEHOLDER_HOSTS = ("example.com", "example.org", "@handle")


def _no_placeholder(text):
    assert not any(h in str(text) for h in PLACEHOLDER_HOSTS), text


# ---------------------------------------------------------------- research

def _graph(search_fn=None):
    from app.graphs.account_manager_graph import build_account_manager_graph
    return build_account_manager_graph(search_fn=search_fn)


def _run(graph, text, tid):
    return graph.invoke({"user_request": text, "project_id": "starter"},
                        config={"configurable": {"thread_id": tid}})


def test_deep_research_without_provider_is_truthfully_unavailable(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_URL", raising=False)
    out = _run(_graph(), "deep research competitors vs us", "t-unavail-deep")
    assert out["route"] == "deep_research"
    _no_placeholder(out["final_answer"])
    _no_placeholder(out["worker_results"])
    assert "not made up any research" in out["final_answer"]
    assert not any(r.get("ok") for r in out["worker_results"])
    assert "Sources:" not in out["final_answer"]


def test_deep_research_with_real_results_cites_only_those(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_URL", raising=False)

    def search(query, count):
        return [{"url": "https://rival.test/pricing", "title": "Rival",
                 "snippet": "Plans start at 10"}]

    out = _run(_graph(search), "deep research competitors vs us", "t-real-deep")
    assert "https://rival.test/pricing" in out["final_answer"]
    _no_placeholder(out["final_answer"])


def test_external_research_unavailable_has_no_fake_web_results(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_URL", raising=False)
    out = _run(_graph(), "look up the latest news about coffee prices online", "t-unavail-ext")
    assert out["route"] == "external_research"
    assert out.get("web_results") in ([], None)
    _no_placeholder(out["final_answer"])
    assert "PARTIALLY VERIFIED" not in out["final_answer"]
    assert "not set up" in out["final_answer"]


def test_search_transport_failure_is_unavailable_not_success(monkeypatch):
    def boom(query, count):
        raise RuntimeError("down")
    found = web_tools.research_search("q", 3, transport=boom)
    assert found == {"available": False, "results": [], "reason": "search_failed"}
    assert web_tools.research_search("q", 3, transport=lambda q, c: [])["reason"] == "no_results"


def test_default_search_requires_configuration(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_URL", raising=False)
    assert web_tools.research_search("q")["reason"] == "no_search_provider"


def test_no_placeholder_urls_in_product_code():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("graphs/account_manager_graph.py", "graphs/deep_research_graph.py",
                "services/tools/web_tools.py", "graphs/account_manager.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "https://example.com" not in src, rel


# ---------------------------------------------------------------- SSRF

@pytest.mark.parametrize("url", [
    "ftp://example.org/x", "file:///etc/passwd", "gopher://a/b",
    "http://user:pw@example.org/", "http://example.org:6379/",
    "http://example.org:8080/", "", "http:///nohost",
])
def test_validate_url_rejects_bad_schemes_ports_credentials(url):
    with pytest.raises(safe_fetch.UnsafeURLError):
        safe_fetch.validate_url(url)


@pytest.mark.parametrize("addr", [
    "127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254",
    "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1",
    "224.0.0.1", "64:ff9b::7f00:1", "2002:a9fe:a9fe::", "2001:0:4136:e378:8000:63bf:3fff:fdd2",
])
def test_unsafe_addresses_blocked(addr):
    resolver = lambda host, port, **kw: [(2, 1, 6, "", (addr, port))]
    with pytest.raises(safe_fetch.UnsafeURLError):
        safe_fetch.resolve_public("victim.test", 80, resolver)


def test_any_private_answer_blocks_even_if_another_is_public():
    answers = [(2, 1, 6, "", ("93.184.216.34", 80)), (2, 1, 6, "", ("127.0.0.1", 80))]
    with pytest.raises(safe_fetch.UnsafeURLError):
        safe_fetch.resolve_public("mixed.test", 80, lambda h, p, **k: answers)


def test_integer_and_hex_host_spellings_resolve_to_blocked(monkeypatch):
    for host in ("2130706433", "0x7f000001", "127.1", "localhost"):
        with pytest.raises(safe_fetch.UnsafeURLError):
            safe_fetch.resolve_public(host, 80)


class _Resp:
    def __init__(self, status, headers=None, body=b""):
        self.status = status
        self._h = headers or {}
        self._body = body

        class H:
            def get_content_charset(_s):
                return "utf-8"
        self.headers = H()

    def getheader(self, k):
        return self._h.get(k)

    def getheaders(self):
        return list(self._h.items())

    def read(self, n):
        out, self._body = self._body[:n], self._body[n:]
        return out


class _Conn:
    def __init__(self, resp, log, ip):
        self.resp, self.log, self.ip = resp, log, ip

    def request(self, method, path, headers=None):
        self.log.append((self.ip, path, headers.get("Host")))

    def getresponse(self):
        return self.resp

    def close(self):
        pass


def _opener(responses, log):
    it = iter(responses)
    return lambda parts, ip, timeout: _Conn(next(it), log, ip)


PUBLIC = lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))]


def test_connection_is_pinned_to_validated_ip():
    log = []
    got = safe_fetch.safe_get(
        "https://shop.test/a?b=1", resolver=PUBLIC,
        opener=_opener([_Resp(200, {"Content-Type": "text/html"}, b"<p>hi</p>")], log))
    assert got["body"] == b"<p>hi</p>"
    assert log == [("93.184.216.34", "/a?b=1", "shop.test")]


def test_redirect_to_internal_address_is_blocked():
    calls = {"n": 0}

    def resolver(host, port, **kw):
        calls["n"] += 1
        addr = "93.184.216.34" if host == "shop.test" else "169.254.169.254"
        return [(2, 1, 6, "", (addr, port))]

    log = []
    with pytest.raises(safe_fetch.UnsafeURLError):
        safe_fetch.safe_get(
            "http://shop.test/", resolver=resolver,
            opener=_opener([_Resp(302, {"Location": "http://metadata.internal/latest"})], log))
    assert len(log) == 1  # the internal hop was never connected to


def test_redirect_to_non_http_scheme_blocked_and_loop_bounded():
    with pytest.raises(safe_fetch.UnsafeURLError):
        safe_fetch.safe_get(
            "http://shop.test/", resolver=PUBLIC,
            opener=_opener([_Resp(302, {"Location": "file:///etc/passwd"})], []))
    loop = [_Resp(302, {"Location": "http://shop.test/n"})] * 10
    with pytest.raises(safe_fetch.UnsafeURLError):
        safe_fetch.safe_get("http://shop.test/", resolver=PUBLIC,
                            opener=_opener(loop, []))


def test_non_text_content_rejected_and_body_capped():
    with pytest.raises(safe_fetch.UnsafeURLError):
        safe_fetch.safe_get(
            "http://shop.test/", resolver=PUBLIC,
            opener=_opener([_Resp(200, {"Content-Type": "application/zip"}, b"PK")], []))
    big = b"a" * 5000
    got = safe_fetch.safe_get(
        "http://shop.test/", resolver=PUBLIC, max_bytes=100,
        opener=_opener([_Resp(200, {"Content-Type": "text/plain"}, big)], []))
    assert len(got["body"]) == 100


def test_website_tool_default_transport_refuses_local_targets():
    for url in ("http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
                "http://[::1]/", "file:///etc/hosts"):
        res = web_tools._default_website_transport(url, 2)
        assert res["status"] == "error"
        assert "127.0.0.1" not in json.dumps(res)


def test_social_discovery_fetch_refuses_local_targets():
    from app.services.social import discovery
    assert discovery._fetch_html("http://127.0.0.1:80/", None) == ""
    assert discovery._fetch_html("http://localhost/", None) == ""


# ---------------------------------------------------------------- work package

def _client(tmp_path, monkeypatch):
    db = tmp_path / "wp.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    conn = connect(db)
    for pid, name in (("a", "Alpha Cafe"), ("b", "Beta Gym")):
        conn.execute("INSERT INTO projects(id,name,goal,created_at,updated_at) VALUES(?,?,?,?,?)",
                     (pid, name, "", "2026-01-01", "2026-01-01"))
        conn.execute("INSERT INTO campaigns(id,project_id,title,updated_at) VALUES(?,?,?,?)",
                     (pid + "-c", pid, "Launch", "2026-01-01"))
    conn.commit()
    conn.close()
    app = FastAPI()
    app.include_router(campaign_deliverables.router)
    app.include_router(business_workspace.router)
    return TestClient(app)


def _mk(c, pid, title, body="Text"):
    r = c.post(f"/api/projects/{pid}/campaigns/{pid}-c/deliverables",
               json={"type": "social_post", "title": title, "content_md": body})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _advance(c, pid, d, to):
    base = f"/api/projects/{pid}/campaigns/{pid}-c/deliverables/{d['id']}/status"
    r = c.post(base, json={"expected_version": d["current_version"], "status": to})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_goal_roundtrip_and_validation(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.put("/api/projects/a/goal", json={"goal": "  Get more customer inquiries "}).json()["data"]["goal"] == "Get more customer inquiries"
    assert c.get("/api/projects/a/goal").json()["data"]["goal"] == "Get more customer inquiries"
    assert c.get("/api/projects/b/goal").json()["data"]["goal"] == ""
    assert c.put("/api/projects/a/goal", json={"goal": "  "}).status_code == 422
    assert c.put("/api/projects/a/goal", json={"goal": "x" * 501}).status_code == 422
    assert c.put("/api/projects/nope/goal", json={"goal": "x"}).status_code == 404


def test_work_package_states_missing_info_and_unavailable_research(tmp_path, monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_URL", raising=False)
    c = _client(tmp_path, monkeypatch)
    _mk(c, "a", "Post one")
    wp = c.get("/api/projects/a/campaigns/a-c/work-package").json()["data"]
    assert wp["evidence"]["research"]["state"] == "unavailable"
    assert wp["evidence"]["web_sources"] == []
    assert wp["approval"]["ready_to_export"] is False
    fields = {n["field"] for n in wp["needs_owner_input"]}
    assert {"goal", "audience", "channels", "review"} <= fields
    assert wp["deliverables"][0]["status_label"].startswith("Draft")


def test_work_package_is_project_isolated(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _mk(c, "a", "Alpha secret post")
    assert c.get("/api/projects/b/campaigns/a-c/work-package").status_code == 404
    wp = c.get("/api/projects/b/campaigns/b-c/work-package").json()["data"]
    assert wp["deliverables"] == []


def test_export_approved_only_uses_last_approved_text(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    ok = _mk(c, "a", "Approved post", "APPROVED TEXT")
    ok = _advance(c, "a", _advance(c, "a", ok, "IN_REVIEW"), "APPROVED")
    _mk(c, "a", "Never approved", "SECRET DRAFT")
    # revise the approved one: it reopens as DRAFT, approved text must survive
    r = c.put(f"/api/projects/a/campaigns/a-c/deliverables/{ok['id']}",
              json={"expected_version": ok["current_version"], "content_md": "UNFINISHED EDIT"})
    assert r.status_code == 200
    z = c.get("/api/projects/a/campaigns/a-c/deliverables/exports/package.zip?approved_only=true")
    arch = zipfile.ZipFile(io.BytesIO(z.content))
    blobs = {n: arch.read(n).decode() for n in arch.namelist()}
    joined = "\n".join(blobs.values())
    assert "APPROVED TEXT" in joined
    assert "UNFINISHED EDIT" not in joined and "SECRET DRAFT" not in joined
    assert "NOT INCLUDED" in blobs["00-package-summary.md"]
    # default export behavior unchanged
    plain = zipfile.ZipFile(io.BytesIO(c.get(
        "/api/projects/a/campaigns/a-c/deliverables/exports/package.zip").content))
    assert not any(n.startswith("00-package-summary") for n in plain.namelist())


def test_ready_to_export_when_all_approved(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    d = _mk(c, "a", "Only post")
    _advance(c, "a", _advance(c, "a", d, "IN_REVIEW"), "APPROVED")
    wp = c.get("/api/projects/a/campaigns/a-c/work-package").json()["data"]
    assert wp["approval"]["ready_to_export"] is True
    assert wp["deliverables"][0]["versions_kept"] == 3


def test_truncated_flag_and_address_fallback():
    got = safe_fetch.safe_get(
        "http://shop.test/", resolver=PUBLIC, max_bytes=10,
        opener=_opener([_Resp(200, {"Content-Type": "text/plain"}, b"a" * 50)], []))
    assert got["truncated"] is True and len(got["body"]) == 10

    two = lambda h, p, **k: [(10, 1, 6, "", ("2606:4700::1111", p)),
                             (2, 1, 6, "", ("93.184.216.34", p))]
    tried = []

    class Flaky(_Conn):
        def request(self, *a, **k):
            tried.append(self.ip)
            if ":" in self.ip:
                raise OSError("unreachable")
            super().request(*a, **k)

    resp = _Resp(200, {"Content-Type": "text/plain"}, b"ok")
    got = safe_fetch.safe_get("http://shop.test/", resolver=two,
                              opener=lambda parts, ip, t: Flaky(resp, [], ip))
    assert got["body"] == b"ok" and tried == ["2606:4700::1111", "93.184.216.34"]


def test_external_research_with_results_lists_real_sources_unverified(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_URL", raising=False)

    def search(query, count):
        return [{"url": "https://rival.test/p", "title": "Rival", "snippet": "from 10"}]

    out = _run(_graph(search), "look up the latest news about flour prices online", "t-ext-real")
    assert out["route"] == "external_research"
    assert "https://rival.test/p" in out["final_answer"]
    assert "not checked these pages" in out["final_answer"]
    assert "PARTIALLY VERIFIED" not in out["final_answer"]


# ------------------------------------------------- research evidence persistence

def _ev_conn(tmp_path):
    return connect(tmp_path / "ev.db")


def test_clean_url_rejects_fixture_and_private_hosts():
    from app.services import research_evidence as ev
    assert ev.clean_url("https://www.bbc.co.uk/news/x") == "https://www.bbc.co.uk/news/x"
    for bad in ("https://example.com/a", "http://shop.test/", "ftp://a.com/x",
                "http://localhost:8888/", "http://127.0.0.1/", "http://10.0.0.5/",
                "javascript:alert(1)", "", "https://intranet/", "https://a.example.org/"):
        assert ev.clean_url(bad) == "", bad


def test_leads_persist_scoped_and_never_downgrade(tmp_path):
    from app.services import research_evidence as ev
    path = tmp_path / "ev.db"
    conn = connect(path)
    rows = [{"url": "https://rival.io/p", "title": "Rival", "snippet": "from 10"},
            {"url": "https://example.com/fake", "title": "Fake"}]
    assert ev.record_leads(conn, "a", "", rows, query="q", turn_id="t1") == 1
    assert ev.record_leads(conn, "a", "a-c", rows, query="q", turn_id="t2") == 1
    assert ev.record_leads(conn, "b", "b-c", rows) == 1
    got = ev.list_sources(conn, "a", "a-c")
    assert [g["verification"] for g in got] == ["search_lead"]
    assert ev.mark_inspected(conn, "a", "https://rival.io/p", title="Rival Inc", http_status=200)
    ev.record_leads(conn, "a", "a-c", [{"url": "https://rival.io/p", "title": "Changed"}])
    conn.close()
    conn = connect(path)  # restart
    got = ev.list_sources(conn, "a", "a-c")
    assert len(got) == 1 and got[0]["verification"] == "inspected"
    assert got[0]["title"] == "Rival Inc" and got[0]["inspected_at"]
    # another campaign in the same project sees only project-level research
    assert [g["scope"] for g in ev.list_sources(conn, "a", "other-c")] == ["project"]
    assert ev.list_sources(conn, "zzz", "a-c") == []


def test_website_fetch_marks_source_inspected(tmp_path):
    from app.services import research_evidence as ev
    conn = _ev_conn(tmp_path)
    ev.record_leads(conn, "a", "", [{"url": "https://rival.io/p", "title": "R"}])
    page = {"status": "fetched", "url": "https://rival.io/p", "title": "Rival",
            "http_status": 200, "links": []}
    out = web_tools.t_website_fetch(conn, project_id="a", root="", args={"url": "https://rival.io/p"},
                                    transport=lambda u, t=10: dict(page))
    assert out["ok"]
    assert ev.list_sources(conn, "a", "x")[0]["verification"] == "inspected"
    fail = web_tools.t_website_fetch(conn, project_id="a", root="", args={"url": "https://nope.io/"},
                                     transport=lambda u, t=10: {"status": "error"})
    assert not fail["ok"]
    assert all(s["url"] != "https://nope.io/" for s in ev.list_sources(conn, "a", "x"))


def test_chat_research_reaches_work_package(tmp_path, monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_URL", raising=False)
    from app.graphs.account_manager_graph import build_account_manager_graph
    path = tmp_path / "chat.db"
    seed = connect(path)
    seed.close()
    graph = build_account_manager_graph(
        search_fn=lambda q, n: [{"url": "https://rival.io/p", "title": "Rival",
                                 "snippet": "from 10"}],
        conn_factory=lambda: connect(path))
    out = graph.invoke({"user_request": "look up the latest news about flour prices online",
                        "project_id": "a"},
                       config={"configurable": {"thread_id": "t-persist"}})
    assert out["route"] == "external_research"
    conn = connect(path)
    from app.services import research_evidence as ev
    got = ev.list_sources(conn, "a", "any-campaign")
    assert got and got[0]["url"] == "https://rival.io/p"
    assert got[0]["verification"] == "search_lead"
    assert ev.list_sources(conn, "b", "any-campaign") == []


def test_work_package_lists_sources_with_honest_labels(tmp_path, monkeypatch):
    from app.services import research_evidence as ev
    c = _client(tmp_path, monkeypatch)
    _mk(c, "a", "Post one")
    conn = connect(tmp_path / "wp.db")
    ev.record_leads(conn, "a", "a-c", [{"url": "https://rival.io/p", "title": "Rival"}])
    ev.record_leads(conn, "b", "b-c", [{"url": "https://other.io/", "title": "Other"}])
    wp = c.get("/api/projects/a/campaigns/a-c/work-package").json()["data"]
    srcs = wp["evidence"]["web_sources"]
    assert [s["url"] for s in srcs] == ["https://rival.io/p"]
    assert srcs[0]["verification"] == "search_lead"
    assert "not checked" in srcs[0]["verification_label"]
    ev.mark_inspected(conn, "a", "https://rival.io/p", title="Rival", http_status=200)
    srcs = c.get("/api/projects/a/campaigns/a-c/work-package").json()["data"]["evidence"]["web_sources"]
    assert srcs[0]["verification"] == "inspected"


def test_searxng_setup_is_safe():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "config" / "searxng"
    compose = (root / "docker-compose.yml").read_text("utf-8")
    settings = (root / "settings.yml").read_text("utf-8")
    assert "secret_key" not in settings
    assert "latest" not in re.findall(r"image:\s*(\S+)", compose)[0]
    assert re.search(r"image:\s*\S+@sha256:[0-9a-f]{64}", compose)
    assert '"127.0.0.1:8888:8080"' in compose
    assert "SEARXNG_SECRET=${SEARXNG_SECRET:?" in compose


def test_searxng_setup_script_generates_private_secret(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "setup_searxng", Path(__file__).resolve().parents[2] / "scripts" / "setup_searxng.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    env = tmp_path / "x" / ".env"
    assert mod.main([], env_path=env) == 0
    first = env.read_text("utf-8")
    assert len(first.strip().split("=")[1]) == 64
    mod.main([], env_path=env)
    assert env.read_text("utf-8") == first
    mod.main(["--rotate"], env_path=env)
    assert env.read_text("utf-8") != first


def test_web_search_tool_persists_leads_per_project(tmp_path):
    from app.services import research_evidence as ev
    conn = _ev_conn(tmp_path)
    out = web_tools.t_web_search(
        conn, project_id="a", root="", args={"query": "bakery ideas"},
        transport=lambda q, n: [{"url": "https://rival.io/p", "title": "R", "snippet": "s"},
                                {"url": "https://example.com/fake", "title": "F"}])
    assert out["ok"] and len(out["results"]) == 2
    assert [s["url"] for s in ev.list_sources(conn, "a", "c")] == ["https://rival.io/p"]
    assert ev.list_sources(conn, "b", "c") == []


@pytest.mark.parametrize("phrase", [
    "look up the latest bakery marketing ideas online",
    "find recent bakery marketing trends on the web",
    "look up the latest news about flour prices online",
])
def test_explicit_online_lookup_routes_to_external_research(phrase):
    g = _graph(search_fn=lambda q, n: [
        {"url": "https://rival.io/p", "title": "R", "snippet": "s"}])
    out = _run(g, phrase, "t-lookup-" + phrase[:12])
    assert out["route"] == "external_research", out["route"]
    assert "Which website" not in out["final_answer"]
    assert "rival.io" in out["final_answer"]


def test_explicit_online_lookup_without_provider_never_asks_for_url(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_URL", raising=False)
    out = _run(_graph(), "look up the latest bakery marketing ideas online",
               "t-lookup-nop")
    assert out["route"] == "external_research"
    assert "Which website" not in out["final_answer"]


def test_website_audit_with_url_keeps_routing():
    from app.graphs.intent import classify_intent, detect_web_lookup_intent
    assert not detect_web_lookup_intent("audit https://shop.io website")
    assert classify_intent("audit https://shop.io website")["route"] \
        == "tool_capability"


def test_research_results_survive_model_failure(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_URL", raising=False)
    from app.graphs.account_manager_graph import build_account_manager_graph

    def broken(**kw):
        raise RuntimeError("no provider")

    g = build_account_manager_graph(
        search_fn=lambda q, n: [{"url": "https://rival.io/p", "title": "Rival", "snippet": "s"}],
        complete_fn=broken)
    out = g.invoke({"user_request": "look up the latest news about flour prices online",
                    "project_id": "starter"}, config={"configurable": {"thread_id": "t-mf"}})
    assert "https://rival.io/p" in out["final_answer"]
    assert "couldn't generate" not in out["final_answer"]
    assert "not checked these pages" in out["final_answer"]


def test_clean_url_rejects_obfuscated_hosts_and_credentials():
    from app.services import research_evidence as ev
    for bad in ("http://127.1/", "http://0x7f.0.0.1/", "http://user:pw@a.com/",
                "https://a.com/\x00x", "https://a.com/a b"):
        assert ev.clean_url(bad) == "", bad
    assert ev.clean_url("https://8.8.8.8/x") == "https://8.8.8.8/x"


@pytest.mark.parametrize("phrase", [
    "get more customers online",
    "find my best channel online",
    "Create a campaign to get online orders for my bakery",
    "help me find leads online and write a campaign",
    "research online reputation strategy",
    "I want to get my online store ready",
    "Write a plan to find customers on the internet",
])
def test_broad_online_phrases_do_not_route_to_external_research(phrase):
    from app.graphs.intent import classify_intent, detect_web_lookup_intent
    assert not detect_web_lookup_intent(phrase)
    assert classify_intent(phrase).get("route") != "external_research"
