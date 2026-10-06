"""DEV-004 W1 acceptance: additive SPA JSON APIs (/api/*).

Patterns follow app/tests/test_w5_pages.py: temp DB via monkeypatched
deps.DB_PATH, TestClient(create_app()). Verifies the {"ok": true, "data"}
envelope, project scoping (no foreign-project leaks), and rename/archive
semantics (archive reuses store.archive_conversation).
"""
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.database import repos
from app.main import create_app
from app.services import state as store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "spa.db"
    monkeypatch.setattr(deps, "DB_PATH", db)
    deps.init_db(db)
    with deps.get_db(db) as conn:
        pa = store.create_project(conn, "Alpha", "https://alpha.example", "Grow")
        pb = store.create_project(conn, "Beta")
        ca = store.create_conversation(conn, project_id=pa["id"], title="Alpha chat")
        cb = store.create_conversation(conn, project_id=pb["id"], title="Beta chat")
        store.add_message(conn, ca["id"], "user", "hello alpha")
        store.add_message(conn, ca["id"], "assistant", "hi back", ["src-a"])
        repos.Approvals.upsert(conn, {"id": "appr-a", "project_id": pa["id"],
                                      "kind": "Copy", "title": "Approve headline",
                                      "body_md": "headline body", "status": "pending",
                                      "fields_json": "{}",
                                      "updated_at": store.now_iso()})
        repos.Approvals.upsert(conn, {"id": "appr-b", "project_id": pb["id"],
                                      "kind": "Copy", "title": "Beta headline",
                                      "body_md": "", "status": "pending",
                                      "fields_json": "{}",
                                      "updated_at": store.now_iso()})
        repos.Campaigns.upsert(conn, {"id": "camp-a", "project_id": pa["id"],
                                      "title": "Alpha campaign", "status": "proposed",
                                      "updated_at": store.now_iso()})
        repos.BackgroundJobs.upsert(conn, {"id": "job-a", "project_id": pa["id"],
                                           "conversation_id": ca["id"], "kind": "research",
                                           "job_type": "research", "brief_md": "",
                                           "status": "queued", "cmd_json": "[]", "cwd": "",
                                           "log_path": "",
                                           "created_at": store.now_iso(),
                                           "updated_at": store.now_iso()})
        repos.Memories.insert(conn, {"id": "mem-a", "project_id": pa["id"],
                                     "kind": "offer", "body_md": "offer body",
                                     "created_at": store.now_iso(),
                                     "updated_at": store.now_iso()})
        repos.Learnings.insert(conn, {"id": "learn-a", "project_id": pa["id"],
                                      "body_md": "learning body", "source": "manual",
                                      "observed_at": store.now_iso()})
        repos.ExecutionEvents.insert(conn, {"project_id": pa["id"],
                                            "conversation_id": ca["id"], "turn_id": "",
                                            "job_id": "job-a", "event_type": "job_queued",
                                            "label": "Queued", "detail": "d",
                                            "metadata_json": "{}",
                                            "created_at": store.now_iso()})
    with TestClient(create_app()) as c:
        yield c, {"pa": pa["id"], "pb": pb["id"], "ca": ca["id"], "cb": cb["id"]}


def _data(r):
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    return body["data"]


def test_projects_envelope_and_fields(client):
    c, ids = client
    data = _data(c.get("/api/projects"))
    row = next(r for r in data if r["id"] == ids["pa"])
    assert set(row) == {"id", "name", "website", "goal"}
    assert row["website"] == "https://alpha.example"


def test_chats_project_scoping(client):
    c, ids = client
    data = _data(c.get(f"/api/chats?project_id={ids['pa']}"))
    got = {row["id"] for row in data}
    assert ids["ca"] in got and ids["cb"] not in got
    assert set(data[0]) == {"id", "title", "project_id", "updated_at"}


def test_chats_search_filter(client):
    c, ids = client
    data = _data(c.get(f"/api/chats?project_id={ids['pa']}&q=alpha"))
    assert [r["id"] for r in data] == [ids["ca"]]
    assert _data(c.get(f"/api/chats?project_id={ids['pa']}&q=zzz")) == []


def test_rename_semantics(client):
    c, ids = client
    r = c.post(f"/api/chats/{ids['ca']}/rename", json={"title": "New title"})
    assert _data(r)["title"] == "New title"
    assert _data(c.get(f"/api/chats/{ids['ca']}/messages")) is not None
    bad = c.post(f"/api/chats/{ids['ca']}/rename", json={"title": "  "})
    assert bad.status_code == 400
    assert c.post("/api/chats/missing/rename", json={"title": "x"}).status_code == 404


def test_archive_semantics_reuses_store(client):
    c, ids = client
    with deps.get_db() as conn:
        before = repos.Conversations.get(conn, ids["ca"])
        assert before["status"] == "open"
    out = _data(c.post(f"/api/chats/{ids['ca']}/archive"))
    assert out["id"] == ids["ca"]
    with deps.get_db() as conn:
        after = repos.Conversations.get(conn, ids["ca"])
        assert after["status"] == "archived"
    remaining = {r["id"] for r in _data(c.get(f"/api/chats?project_id={ids['pa']}"))}
    assert ids["ca"] not in remaining  # default status=open hides archived
    archived = {r["id"] for r in
                _data(c.get(f"/api/chats?project_id={ids['pa']}&status=all"))}
    assert ids["ca"] in archived


def test_messages_shape_and_404(client):
    c, ids = client
    data = _data(c.get(f"/api/chats/{ids['ca']}/messages"))
    assert len(data) == 2
    assert data[0]["role"] == "user" and data[0]["body_md"] == "hello alpha"
    assert data[1]["citations"] == ["src-a"]
    assert c.get("/api/chats/missing/messages").status_code == 404


def test_activity_scoped_to_conversation(client):
    c, ids = client
    data = _data(c.get(f"/api/activity?conversation_id={ids['ca']}"))
    assert len(data) == 1
    assert set(data[0]) == {"id", "event_type", "label", "detail"}
    assert data[0]["event_type"] == "job_queued"
    assert _data(c.get(f"/api/activity?conversation_id={ids['cb']}")) == []


def test_jobs_project_scoping(client):
    c, ids = client
    data = _data(c.get(f"/api/jobs?project_id={ids['pa']}"))
    assert len(data) == 1
    assert set(data[0]) == {"id", "kind", "status", "created_at", "updated_at"}
    assert _data(c.get(f"/api/jobs?project_id={ids['pb']}")) == []


def test_approvals_filter_and_scoping(client):
    c, ids = client
    data = _data(c.get(f"/api/approvals?project_id={ids['pa']}&status=pending"))
    assert [a["id"] for a in data] == ["appr-a"]
    assert {"id", "title", "kind", "status", "body_md", "project_id"} <= set(data[0])
    assert data[0]["project_id"] == ids["pa"]
    other = _data(c.get(f"/api/approvals?project_id={ids['pb']}"))
    assert [a["id"] for a in other] == ["appr-b"]


def test_campaigns_list_and_detail(client):
    c, ids = client
    data = _data(c.get(f"/api/campaigns?project_id={ids['pa']}"))
    assert [x["id"] for x in data] == ["camp-a"]
    assert _data(c.get(f"/api/campaigns?project_id={ids['pb']}")) == []
    detail = _data(c.get("/api/campaigns/camp-a"))
    assert detail["campaign"]["title"] == "Alpha campaign"
    assert set(detail) == {"campaign", "prospects", "tasks", "experiments"}
    assert c.get("/api/campaigns/missing").status_code == 404
    # Foreign-project read via explicit project_id is rejected, never leaked.
    assert c.get(f"/api/campaigns/camp-a?project_id={ids['pb']}").status_code == 403


def test_results_summary(client):
    c, ids = client
    data = _data(c.get(f"/api/results/summary?project_id={ids['pa']}"))
    assert data["counts"]["learnings"] == 1
    assert data["decisions"] == []
    assert data["learnings"][0]["body_md"] == "learning body"


def test_knowledge_user_facing_no_internals(client):
    c, ids = client
    data = _data(c.get(f"/api/knowledge?project_id={ids['pa']}"))
    assert set(data) == {"company", "audience", "offers", "positioning",
                         "research", "learnings", "files"}
    assert data["company"]["name"] == "Alpha"
    assert data["offers"][0]["body_md"] == "offer body"
    blob = str(data)
    for banned in ("chroma", "fts", "chunk", "embedding"):
        assert banned not in blob.lower()


def test_settings_overview_is_dead(client):
    c, _ = client
    response = c.get("/api/settings/overview")
    assert response.status_code == 404


def test_root_redirects_to_spa_and_legacy_kept(client):
    """DEV-007R: / is the SPA (303 to /app); legacy home (/legacy) redirects to /app."""
    c, ids = client
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/app"
    legacy = c.get("/legacy", follow_redirects=False)
    assert legacy.status_code == 303 and legacy.headers["location"] == "/app"
    spa = c.get("/app")
    assert spa.status_code == 200 and 'id="root"' in spa.text
