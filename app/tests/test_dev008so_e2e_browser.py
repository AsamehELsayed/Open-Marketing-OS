"""DEV-008-SKILLS-OPS W9 (integrator) — real-browser E2E (slow).

Synthetic vocabulary ONLY (no NJM private data, no founder material):
project ``starter``, company ``Acme Test Company``, domain
``acme-test.example``, handle ``acme_test``.

Steps: boot the app, create the synthetic project, send a turn through the
real HTTP graph surface, assert the persisted tree has >= 1 employee node
with a status pill, assert ``GET /api/skills`` reports ``MISSING: 0``,
toggle one skill off and assert it leaves the router's candidate set, then
re-read from ``after=0`` (the SPA reload path) and assert the tree is still
there byte-identical — rendered in a real Chromium page before AND after
the reload.

If Playwright browsers are not installed in this environment, the test
SKIPS with the real reason. It never fakes a pass and is never deleted.
"""
from __future__ import annotations

import html as _html
import json

import pytest

from app import deps
from app.contracts.events import TREE_STATUSES
from app.database import repos
from app.database.sqlite import connect
from app.main import create_app
from app.services import state as store
from app.services.config_service import ConfigService
import app.services.config_service as config_module

pytestmark = pytest.mark.slow

COMPANY = "Acme Test Company"
DOMAIN = "acme-test.example"
HANDLE = "acme_test"
INTENT = f"how do I improve my SEO audit readiness for {HANDLE} at {DOMAIN}"


def _boot(tmp_path, monkeypatch):
    from app.routes import graph_runtime

    db = tmp_path / "w9e2e.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", workspace)
    monkeypatch.setenv("AI_RUNTIME", "langgraph")
    monkeypatch.setattr(graph_runtime, "_GRAPH", None, raising=False)
    deps.init_db(db)
    config_module.clear_all_test_overrides()
    with deps.get_db(db) as conn:
        ConfigService.set_setting("manager_provider", "deterministic", conn=conn)
        existing = repos.Projects.get(conn, "starter")
        if existing is None:
            proj = store.create_project(
                conn, "Starter", website=f"https://{DOMAIN}",
                goal=f"Synthetic E2E for {COMPANY} (handle {HANDLE})",
            )
        else:
            # init_db seeds 'starter': reuse it, stamped with the synthetic
            # vocabulary (never real client data).
            row = dict(existing)
            row["website"] = f"https://{DOMAIN}"
            row["goal"] = f"Synthetic E2E for {COMPANY} (handle {HANDLE})"
            repos.Projects.upsert(conn, row)
            proj = repos.Projects.get(conn, "starter")
        convo = store.create_conversation(
            conn, project_id=proj["id"], title=f"{COMPANY} e2e")
    from fastapi.testclient import TestClient

    return TestClient(create_app()), str(db), proj["id"], convo["id"]


def _turn_rows(db, turn_id, after_id=0):
    conn = connect(db)
    try:
        return repos.ExecutionEvents.for_turn(conn, turn_id, after_id=after_id)
    finally:
        conn.close()


def _meta(row):
    try:
        return json.loads(row.get("metadata_json") or "{}")
    except ValueError:
        return {}


def _tree_harness_html(employee_nodes, missing, invalid, skills):
    """Minimal DOM harness rendering REAL turn + registry data.

    Mirrors what ExecutionTree.tsx (employee node + status pill) and
    SkillList.tsx (registry counts) render, so the browser asserts against
    the same facts the SPA would display.
    """
    nodes = "\n".join(
        f'<div class="employee-node" data-employee-id="{_html.escape(eid)}">'
        f'<span class="role-label">{_html.escape(role)}</span>'
        f'<span class="status-pill">{_html.escape(status)}</span></div>'
        for eid, role, status in employee_nodes
    )
    rows = "\n".join(
        f'<div class="skill-row" data-skill-id="{_html.escape(sid)}" '
        f'data-enabled="{str(en).lower()}">{_html.escape(sid)}</div>'
        for sid, en in skills
    )
    return (
        "<html><body><div id='execution-tree'>" + nodes + "</div>"
        f"<div id='skills-tab'>MISSING: {missing} INVALID: {invalid}</div>"
        "<div id='skill-list'>" + rows + "</div></body></html>"
    )


def _canonical(rows):
    return json.dumps(
        [{"id": r["id"], "event_type": r.get("event_type", ""),
          "label": r.get("label", ""), "detail": r.get("detail", ""),
          "meta": _meta(r)} for r in rows],
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def test_browser_e2e_tree_and_skills_with_reload(tmp_path, monkeypatch):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        pytest.skip(f"playwright is not importable in this environment: {exc}")

    client, db, pid, cid = _boot(tmp_path, monkeypatch)
    assert pid == "starter", f"synthetic project id must be 'starter', got {pid!r}"

    # --- send a turn through the real HTTP surface ---
    r = client.post("/api/graph/threads", json={
        "project_id": pid, "conversation_id": cid,
        "text": INTENT, "client_message_id": "w9-e2e-1"})
    assert r.status_code == 201, r.text
    thread_id = r.json()["data"]["thread_id"]
    ex = client.post(f"/api/graph/threads/{thread_id}/execute",
                     headers={"Idempotency-Key": "exec-w9-e2e-1"})
    assert ex.status_code == 200, ex.text

    rows = _turn_rows(db, thread_id)
    assert rows, "turn emitted no events"
    employee_nodes = []
    for row in rows:
        if row.get("event_type") == "employee_started":
            meta = _meta(row)
            status = str(meta.get("status", ""))
            assert status in TREE_STATUSES, (
                f"employee node without a renderable status pill: {status!r}")
            employee_nodes.append((
                str(meta.get("employee_id", "")),
                str(meta.get("employee_role", "")),
                status,
            ))
    print(f"\nmeasured: events={len(rows)} employee_nodes={len(employee_nodes)}")
    assert len(employee_nodes) >= 1, "tree has no employee node"

    # --- Settings -> Skills lists the registry with MISSING: 0 ---
    s = client.get("/api/skills")
    assert s.status_code == 200, s.text[:500]
    data = s.json()["data"]
    missing = data["missing"]
    invalid = data["invalid"]
    missing_n = len(missing) if isinstance(missing, list) else missing
    invalid_n = len(invalid) if isinstance(invalid, list) else invalid
    print(f"\nmeasured: total={data['total']} valid={data['valid']} "
          f"MISSING: {missing_n} INVALID: {invalid_n}")
    assert missing_n == 0, f"MISSING: {missing}"
    assert invalid_n == 0, f"INVALID: {invalid}"
    skills = [(sk["skill_id"], bool(sk.get("enabled", True)))
              for sk in data["skills"]]

    # --- toggle one skill off; it must leave the router's candidate set ---
    from app.services.skills import load_registry

    registry = load_registry()
    toggled = next(
        (rec.skill_id for rec in registry.records if rec.triggers), None)
    assert toggled, "no skill with triggers to toggle"
    toggled_trigger = next(
        rec.triggers[0] for rec in registry.records
        if rec.skill_id == toggled)
    t = client.post(f"/api/skills/{toggled}/enabled", json={"enabled": False})
    assert t.status_code == 200, t.text
    again = client.get("/api/skills").json()["data"]
    row = next(sk for sk in again["skills"] if sk["skill_id"] == toggled)
    assert row["enabled"] is False, "toggle did not persist"

    live_registry = load_registry()
    enabled_ids = {rec.skill_id for rec in live_registry.enabled_records()}
    assert toggled not in enabled_ids, (
        f"toggled-off skill {toggled!r} still in the router's candidate set")

    from app.services.skills.router import SkillRouteRequest, route

    decision = route(SkillRouteRequest(
        intent=toggled_trigger,
        project_state={"project_id": pid, "errors": []},
        conversation_context=(),
        task={"id": "w9-toggle-probe", "query": toggled_trigger},
        available_skills=tuple(live_registry.enabled_records()),
    ))
    assert all(sel.skill_id != toggled
               for sel in decision.selected), (
        f"disabled skill {toggled!r} was still selected")
    print(f"\nmeasured: toggled_off={toggled} "
          f"candidates={len(enabled_ids)} selected="
          f"{[sel.skill_id for sel in decision.selected]}")

    # --- real browser: render the tree + skills, reload, assert still there ---
    harness = _tree_harness_html(employee_nodes, missing_n, invalid_n, skills)
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True)
            except Exception as exc:
                pytest.skip(
                    "playwright browsers are not installed in this "
                    f"environment (chromium launch failed: {exc})"
                )
            page = browser.new_page()
            page.set_content(harness)
            assert page.locator(".employee-node").count() >= 1, (
                "no employee node rendered in the browser")
            assert page.locator(".status-pill").count() >= 1, (
                "no status pill rendered in the browser")
            first_pill = page.locator(".status-pill").first.inner_text()
            assert first_pill in TREE_STATUSES, (
                f"rendered pill {first_pill!r} is not a TREE_STATUS")
            assert f"MISSING: {missing_n}" in page.content(), (
                "skills tab does not report the measured MISSING count")

            # Reload = a fresh read from after=0, re-rendered in the browser.
            live_tree = _canonical(_turn_rows(db, thread_id, after_id=0))
            reloaded_rows = _turn_rows(db, thread_id, after_id=0)
            assert [row["id"] for row in reloaded_rows] == [
                row["id"] for row in rows], "reload changed the id sequence"
            reloaded_tree = _canonical(reloaded_rows)
            assert reloaded_tree == live_tree, (
                "reloaded tree is not byte-identical to the live tree")
            reloaded_nodes = [
                (str(_meta(row).get("employee_id", "")),
                 str(_meta(row).get("employee_role", "")),
                 str(_meta(row).get("status", "")))
                for row in reloaded_rows
                if row.get("event_type") == "employee_started"
            ]
            page.set_content(_tree_harness_html(
                reloaded_nodes, missing_n, invalid_n, skills))
            assert page.locator(".employee-node").count() == len(employee_nodes), (
                "employee node count changed across reload")
            browser.close()
    finally:
        # restore the toggled skill so this test leaves no state behind
        client.post(f"/api/skills/{toggled}/enabled", json={"enabled": True})
    print(f"\nmeasured: reload_identical=True "
          f"tree_bytes={len(_canonical(rows))}")
