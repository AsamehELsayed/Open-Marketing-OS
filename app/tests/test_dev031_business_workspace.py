import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import deps
from app.database.sqlite import connect, get_user_version
from app.routes import business_workspace as business_workspace_routes
from app.routes.business_workspace import router
from app.services import business_workspace
from app.services.rag.indexing_service import IndexingService


def _legacy_has_unsupported_citations(markdown, allowed_ids):
    """Golden copy of the pre-DEV-036 predicate for explicit parity checks."""
    import re

    canonical = []
    for match in re.finditer(r"\[([^\]\r\n]*)\]", markdown):
        source_id = match.group(1)
        if ":" not in source_id:
            continue
        if source_id not in allowed_ids:
            return True
        canonical.append((match.start(1), match.end(1), source_id))

    urls = [match.span() for match in re.finditer(
        r"https?://[^\s)\]]+", markdown, re.IGNORECASE)]
    source_like_tokens = re.finditer(
        r"(?<![\w/])([A-Za-z0-9_./-]+:[A-Za-z0-9_./-]+)(?![\w/])", markdown
    )
    for match in source_like_tokens:
        token = match.group(1)
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", token):
            continue
        if any(start <= match.start(1) < end for start, end in urls):
            continue
        if any(start == match.start(1) and end == match.end(1) and source_id == token
               for start, end, source_id in canonical):
            continue
        return True
    return False


@pytest.mark.parametrize(
    ("markdown", "allowed_ids", "expected_rejected", "expected_reason", "expected_form", "expected_token"),
    [
        ("Evidence [doc-a:chunk-a].", {"doc-a:chunk-a"}, False, None, None, None),
        ("Evidence [unknown:chunk-a].", {"doc-a:chunk-a"}, True,
         "unknown_bracketed_id", "bracketed_id", "unknown:chunk-a"),
        ("Evidence unknown:chunk-a.", {"doc-a:chunk-a"}, True,
         "noncanonical_source_like_token", "source_like_token", "unknown:chunk-a."),
        ("Evidence (unknown:chunk-a).", {"doc-a:chunk-a"}, True,
         "noncanonical_source_like_token", "source_like_token", "unknown:chunk-a"),
        ("At 12:30, see https://example.test/path:part.", set(), False, None, None, None),
        ("Bare earlier:token then [unknown:chunk-a].", set(), True,
         "unknown_bracketed_id", "bracketed_id", "unknown:chunk-a"),
        ("[first:bad] then [second:bad]", set(), True,
         "unknown_bracketed_id", "bracketed_id", "first:bad"),
        ("password:credential-secret", set(), True,
         "noncanonical_source_like_token", "source_like_token", "[redacted]"),
    ],
)
def test_citation_diagnostic_preserves_decisions_and_reports_first_failure(
        markdown, allowed_ids, expected_rejected, expected_reason,
        expected_form, expected_token):
    diagnostic = business_workspace_routes.diagnose_unsupported_citations(
        markdown, allowed_ids)

    assert _legacy_has_unsupported_citations(markdown, allowed_ids) is expected_rejected
    assert business_workspace_routes.has_unsupported_citations(
        markdown, allowed_ids) is expected_rejected
    assert (diagnostic is not None) is expected_rejected
    if diagnostic is None:
        assert expected_reason is expected_form is expected_token is None
        return
    assert set(diagnostic) == {"reason_code", "token_form", "token"}
    assert diagnostic == {
        "reason_code": expected_reason,
        "token_form": expected_form,
        "token": expected_token,
    }
    assert len(diagnostic["token"]) <= 96


@pytest.mark.parametrize("sensitive_id", [
    "alice@example.com:customer-1",
    "ghp_FAKEABCDEFGHIJKLMNOPQRSTUV012345:chunk-1",
    "xoxb-fake-AAAA-BBBB-CCCC:chunk-1",
    "+1-202-555-0123:chunk-1",
    "202-555-0123:chunk-1",
    "123-45-6789:chunk-1",
])
def test_citation_diagnostic_redacts_sensitive_identifier_tokens(sensitive_id):
    diagnostic = business_workspace_routes.diagnose_unsupported_citations(
        f"[{sensitive_id}]", set())

    assert diagnostic == {
        "reason_code": "unknown_bracketed_id",
        "token_form": "bracketed_id",
        "token": "[redacted]",
    }
    assert sensitive_id not in json.dumps(diagnostic)


def test_citation_diagnostic_bounds_offending_tokens():
    secret = "[api_key:sk-" + "x" * 160 + "]"
    diagnostic = business_workspace_routes.diagnose_unsupported_citations(secret, set())
    long_token = "a" * 160 + ":" + "b" * 160
    bounded = business_workspace_routes.diagnose_unsupported_citations(long_token, set())

    assert diagnostic == {
        "reason_code": "unknown_bracketed_id",
        "token_form": "bracketed_id",
        "token": "[redacted]",
    }
    assert len(bounded["token"]) <= 96


def test_v13_migration_and_client_profile_revision(tmp_path, monkeypatch):
    db = tmp_path / "dev031.db"
    conn = connect(db)
    assert get_user_version(conn) == 14
    conn.close()
    monkeypatch.setattr(deps, "DB_PATH", db)
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        made = client.post("/api/projects", json={"business_name": "Northstar Coffee"})
        assert made.status_code == 200
        row = made.json()["data"]
        pid = row["project_id"]
        assert row["industry"] == ""
        assert json.loads(row["generation_json"]) == {}
        partial = client.post("/api/projects", json={
            "business_name": "Partial Profile",
            "website": "https://partial.example",
            "industry": "Coffee",
            "audience": "Hybrid workers",
        })
        assert partial.status_code == 200
        assert partial.json()["data"]["website"] == "https://partial.example"
        assert partial.json()["data"]["industry"] == "Coffee"
        assert partial.json()["data"]["audience"] == "Hybrid workers"
        invalid_create = client.post("/api/projects", json={
            "business_name": "Invalid Profile", "industry": ["not", "text"]
        })
        assert invalid_create.status_code == 422
        with deps.get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM projects WHERE name='Invalid Profile'").fetchone()[0] == 0
        assert client.get("/api/projects/unknown/business-profile").status_code == 404
        upd = client.put(f"/api/projects/{pid}/business-profile", json={"expected_revision": 1, "audience": "Remote workers"})
        assert upd.status_code == 200
        assert upd.json()["data"]["audience"] == "Remote workers"
        assert client.put(f"/api/projects/{pid}/business-profile", json={"expected_revision": 1, "industry": "Cafe"}).status_code == 409
        assert client.put(f"/api/projects/{pid}/business-brief", json={"expected_revision": 1, "brief_md": "manual brief"}).status_code == 200
        assert client.post(f"/api/projects/{pid}/business-brief/generate", json={}).status_code == 409


def test_isolated_project_reindex_ignores_shared_root(tmp_path):
    root = tmp_path / "root"; (root / "knowledge").mkdir(parents=True)
    (root / "knowledge" / "shared.md").write_text("Shared root fact", encoding="utf-8")
    conn = connect(tmp_path / "isolated.db")
    conn.execute("INSERT INTO projects(id,name,settings_json,created_at,updated_at) VALUES('client','Client','{\"isolated_client_workspace\":true}','now','now')")
    conn.commit()
    report = IndexingService(conn, root, project_id="client").build_or_update()
    assert report["discovered"] == 0
    assert conn.execute("SELECT COUNT(*) FROM documents WHERE project_id='client'").fetchone()[0] == 0
    conn.close()


def test_concurrent_legacy_profile_reads_initialize_once(tmp_path, monkeypatch):
    db = tmp_path / "legacy-project.db"
    conn = connect(db)
    conn.execute(
        "INSERT INTO projects(id,name,website,created_at,updated_at) VALUES(?,?,?,?,?)",
        ("legacy", "Legacy Client", "https://legacy.example", "now", "now"),
    )
    conn.commit()
    conn.close()

    both_read_missing = threading.Barrier(2)
    original_now = business_workspace.now

    def synchronized_now():
        both_read_missing.wait(timeout=5)
        return original_now()

    monkeypatch.setattr(business_workspace, "now", synchronized_now)

    def read_profile():
        local = sqlite3.connect(db, timeout=5)
        local.row_factory = sqlite3.Row
        try:
            return business_workspace.get_profile(local, "legacy")
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _index: read_profile(), range(2)))
    assert [row["business_name"] for row in rows] == ["Legacy Client", "Legacy Client"]

    conn = connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM business_profiles WHERE project_id='legacy'"
    ).fetchone()[0] == 1
    conn.close()


def test_isolated_client_reimport_skips_shared_workspace_files(tmp_path, monkeypatch):
    from app.routes import api_spa

    db = tmp_path / "reimport.db"
    conn = connect(db)
    conn.execute(
        "INSERT INTO projects(id,name,settings_json,created_at,updated_at) "
        "VALUES('client','Client','{\"isolated_client_workspace\":true}','now','now')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(deps, "DB_PATH", db)

    def unexpected_import(*_args, **_kwargs):
        raise AssertionError("isolated client must not import shared workspace files")

    monkeypatch.setattr(api_spa.store, "run_imports", unexpected_import)
    app = FastAPI()
    app.include_router(api_spa.router)
    with TestClient(app) as client:
        response = client.post(
            "/api/settings/workspace/reimport", json={"project_id": "client"}
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "skipped"
    assert data["project_id"] == "client"
    assert "not imported into this client" in data["message"]


def test_generation_labels_scoped_evidence_and_requires_overwrite(tmp_path, monkeypatch):
    from app.routes import business_workspace as routes
    from app.routes import graph_runtime
    from app.services.rag import scoped_retrieval

    db = tmp_path / "generation.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    app = FastAPI()
    app.include_router(routes.router)
    app.state.profile = None

    observed = {}

    def fake_retrieval(_conn, _query, **kwargs):
        observed["project_id"] = kwargs["project_id"]
        return {"hits": [{"document_id": "doc-a", "chunk_id": "chunk-a",
                          "path": "northstar.md", "text": "Northstar sells oat lattes."}]}

    monkeypatch.setattr(scoped_retrieval, "retrieve_scoped", fake_retrieval)

    class FakeRouter:
        def complete(self, _conn, **_kwargs):
            return SimpleNamespace(text=(
                "## Persisted user facts\nNorthstar Coffee is the business.\n"
                "## Retrieved evidence\nOat lattes [doc-a:chunk-a].\n"
                "## AI suggestions\nTest a weekday offer.\n"
                "## Unknown or unsupported information\nBudget is unknown."
            )), SimpleNamespace(provider="fake", model="fake-model")

    monkeypatch.setattr(graph_runtime, "_get_model_router", lambda: FakeRouter())
    with TestClient(app) as client:
        created = client.post("/api/projects", json={"business_name": "Northstar Coffee"})
        pid = created.json()["data"]["project_id"]
        generated = client.post(f"/api/projects/{pid}/business-brief/generate", json={})
        assert generated.status_code == 200
        brief = generated.json()["data"]
        assert observed["project_id"] == pid
        assert "[doc-a:chunk-a]" in brief["brief_md"]
        assert json.loads(brief["generation_json"]) == {
            "status": "generated", "provider": "fake", "model": "fake-model", "error": ""
        }
        assert client.post(f"/api/projects/{pid}/business-brief/generate", json={}).status_code == 409

        manual = client.put(f"/api/projects/{pid}/business-brief", json={
            "expected_revision": brief["brief_revision"], "brief_md": "Founder-edited version"
        })
        assert manual.status_code == 200
        manual_revision = manual.json()["data"]["brief_revision"]
        before_citation_rejection = client.get(
            f"/api/projects/{pid}/business-brief").json()["data"]
        invalid_calls = []
        sensitive_ids = (
            "alice@example.com:customer-1",
            "ghp_FAKEABCDEFGHIJKLMNOPQRSTUV012345:chunk-1",
            "xoxb-fake-AAAA-BBBB-CCCC:chunk-1",
            "+1-202-555-0123:chunk-1",
            "202-555-0123:chunk-1",
            "123-45-6789:chunk-1",
        )
        for index, sensitive_id in enumerate(sensitive_ids):
            rejected_marker = f"PRIVATE-MODEL-OUTPUT-MARKER-{index}"

            class InvalidCitationRouter:
                def complete(self, _conn, **_kwargs):
                    invalid_calls.append(1)
                    return SimpleNamespace(text=(
                        "## Persisted user facts\nNorthstar Coffee.\n"
                        f"## Retrieved evidence\nOat lattes [{sensitive_id}].\n"
                        f"## AI suggestions\n{rejected_marker} Try a weekday offer.\n"
                        "## Unknown or unsupported information\nBudget is unknown."
                    )), SimpleNamespace(provider="fake", model="fake-model")

            monkeypatch.setattr(
                graph_runtime, "_get_model_router", lambda: InvalidCitationRouter())
            invalid_citation = client.post(
                f"/api/projects/{pid}/business-brief/generate",
                json={"overwrite_confirmed": True})
            assert invalid_citation.status_code == 503
            assert invalid_citation.json()["detail"]["code"] == "invalid_model_output"
            assert invalid_citation.json()["detail"]["provider"] == "fake"
            assert invalid_citation.json()["detail"]["model"] == "fake-model"
            assert "unsupported or noncanonical source reference" in invalid_citation.json()["detail"]["error"]
            assert invalid_citation.json()["detail"]["citation_diagnostic"] == {
                "reason_code": "unknown_bracketed_id",
                "token_form": "bracketed_id",
                "token": "[redacted]",
            }
            assert rejected_marker not in json.dumps(invalid_citation.json())
            assert sensitive_id not in json.dumps(invalid_citation.json())
            saved = client.get(f"/api/projects/{pid}/business-brief").json()["data"]
            assert saved == before_citation_rejection
            assert rejected_marker not in json.dumps(saved)
            assert sensitive_id not in json.dumps(saved)
            assert saved["brief_md"] == "Founder-edited version"
            assert saved["brief_revision"] == manual_revision
        assert invalid_calls == [1, 1, 1, 1, 1, 1]

        for citation_form in (
            "Source: invented:reference",
            "(invented:reference)",
            "[invented.source:reference]",
            "invented.source:reference",
        ):
            class CitationFormatRouter:
                def complete(self, _conn, **_kwargs):
                    return SimpleNamespace(text=(
                        "## Persisted user facts\nNorthstar Coffee.\n"
                        f"## Retrieved evidence\nOat lattes {citation_form}.\n"
                        "## AI suggestions\nTry a weekday offer.\n"
                        "## Unknown or unsupported information\nBudget is unknown."
                    )), SimpleNamespace(provider="fake", model="fake-model")

            monkeypatch.setattr(graph_runtime, "_get_model_router", lambda: CitationFormatRouter())
            rejected = client.post(
                f"/api/projects/{pid}/business-brief/generate",
                json={"overwrite_confirmed": True},
            )
            assert rejected.status_code == 503
            assert rejected.json()["detail"]["code"] == "invalid_model_output"
            saved = client.get(f"/api/projects/{pid}/business-brief").json()["data"]
            assert saved["brief_md"] == "Founder-edited version"
            assert saved["brief_revision"] == manual_revision

        class MissingSectionsRouter:
            def complete(self, _conn, **_kwargs):
                return SimpleNamespace(text="Northstar is a coffee business."), SimpleNamespace(provider="fake", model="fake-model")

        monkeypatch.setattr(graph_runtime, "_get_model_router", lambda: MissingSectionsRouter())
        missing_sections = client.post(f"/api/projects/{pid}/business-brief/generate", json={"overwrite_confirmed": True})
        assert missing_sections.status_code == 503
        assert "did not separate facts, evidence, suggestions" in missing_sections.json()["detail"]["error"]
        saved = client.get(f"/api/projects/{pid}/business-brief").json()["data"]
        assert saved["brief_md"] == "Founder-edited version"
        assert saved["brief_revision"] == manual_revision

        class CredentialFailureRouter:
            def complete(self, _conn, **_kwargs):
                from app.services.llm.model_router import ModelCallFailure
                raise ModelCallFailure(
                    "Provider request failed.", provider="openai", model="configured-model",
                    route_mode="AUTO", route_reason="test", invocation_started=True,
                    failure_code="authentication",
                )

        monkeypatch.setattr(graph_runtime, "_get_model_router", lambda: CredentialFailureRouter())
        failed = client.post(f"/api/projects/{pid}/business-brief/generate", json={"overwrite_confirmed": True})
        assert failed.status_code == 503
        assert "rejected its credentials" in failed.json()["detail"]["error"]
        saved = client.get(f"/api/projects/{pid}/business-brief").json()["data"]
        assert saved["brief_md"] == "Founder-edited version"
        assert saved["brief_revision"] == manual_revision

        class NoReadyProviderRouter:
            def complete(self, _conn, **_kwargs):
                raise RuntimeError("Local AI is unavailable and OpenAI is not configured")

        monkeypatch.setattr(graph_runtime, "_get_model_router", lambda: NoReadyProviderRouter())
        unavailable = client.post(f"/api/projects/{pid}/business-brief/generate", json={"overwrite_confirmed": True})
        assert unavailable.status_code == 503
        detail = unavailable.json()["detail"]
        assert detail["code"] == "no_provider_ready"
        assert detail["provider"] == ""
        assert detail["model"] == ""
        assert "local model is unavailable" in detail["error"]
        saved = client.get(f"/api/projects/{pid}/business-brief").json()["data"]
        assert saved["brief_md"] == "Founder-edited version"
        assert saved["brief_revision"] == manual_revision
