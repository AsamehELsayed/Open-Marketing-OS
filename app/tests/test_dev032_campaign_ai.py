"""DEV-032 W2 — natural-language campaign deliverable production and revision.

Every AI call in this module is deterministic and offline. The model is the
real ``ModelRouter`` wired to a scripted fake provider, so routing, the
selected provider/model, and ``model_calls`` telemetry are all genuinely
exercised without credentials or network access.

``app.services.campaign_deliverables`` is W1's file and is not present in this
worker checkout. ``_install_storage`` therefore installs a contract-faithful
stand-in that reproduces W1's published rules (allowed types, required
``type``/``title``/``content_md`` strings, optional ``platform`` string or
null, JSON-serializable provenance, batch idempotency that returns the
original rows unchanged, and expected-version ``revise``). It validates exactly
as W1 does, so sending the wrong item keys fails here rather than in
integration.
"""
from __future__ import annotations

import json
import sys
import types
from hashlib import sha256
from types import SimpleNamespace

import pytest

from app.database import repos
from app.database.sqlite import connect
from app.graphs.account_manager_graph import (
    _current_turn_write_constraints,
    _deliverable_write_intents,
    _explicit_write_intents,
    _filter_forbidden_write_intents,
)
from app.graphs.state import initial_state
from app.services import campaign_production as production

DELIVERABLE_TYPES = (
    "strategy_brief", "social_post", "ad_copy", "creative_brief", "content_calendar",
)


# ---------------------------------------------------------------------------
# deterministic ModelRouter fake
# ---------------------------------------------------------------------------

class ScriptedProvider:
    """A provider that replays a fixed script and records every call.

    ``model`` defaults to empty so the router's own routing decision is what
    the test observes, rather than a name baked into the provider.
    """

    def __init__(self, *, text: str = "", error: Exception | None = None,
                 model: str = ""):
        self.text = text
        self.error = error
        self.model = model
        self.calls: list[dict] = []

    def complete(self, *, system, messages, tools, opts=None):
        self.calls.append({
            "system": system,
            "user": messages[0]["content"] if messages else "",
            "opts": dict(opts or {}),
        })
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.text, usage={
            "input_tokens": 11, "output_tokens": 5, "total_tokens": 16})


def _install_router(monkeypatch, provider):
    """Wire the real ModelRouter to the scripted provider."""
    from app.services.llm.model_router import ModelRouter

    router = ModelRouter(local_provider=provider,
                         default_local_model="dev032-local-default")
    monkeypatch.setattr(production, "_router", lambda: router)
    return router


# ---------------------------------------------------------------------------
# contract-faithful W1 storage stand-in
# ---------------------------------------------------------------------------

class FakeNotFound(LookupError):
    pass


class FakeConflict(RuntimeError):
    pass


class FakeValidation(ValueError):
    pass


def _clean_item(item):
    """W1's ``_validate_item``: the exact accepted shape, nothing more."""
    if not isinstance(item, dict):
        raise FakeValidation("each deliverable item must be an object")
    kind = item.get("type")
    if not isinstance(kind, str) or kind not in DELIVERABLE_TYPES:
        raise FakeValidation("type must be a supported campaign deliverable type")
    title = item.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise FakeValidation("title is required")
    content = item.get("content_md")
    if not isinstance(content, str) or not content.strip():
        raise FakeValidation("content_md is required")
    platform = item.get("platform")
    if platform is not None and not isinstance(platform, str):
        raise FakeValidation("platform must be text or null")
    return {"type": kind, "title": title.strip(), "platform": platform,
            "content_md": content}


def _install_storage(monkeypatch):
    """Install the W1 contract stand-in and return its call recorder."""
    state: dict = {"rows": {}, "batches": {}, "calls": []}
    bucket = lambda project_id, campaign_id: (project_id, campaign_id)

    def _scope(conn, project_id, campaign_id):
        if not isinstance(project_id, str) or not project_id.strip():
            raise FakeValidation("project_id is required")
        if not isinstance(campaign_id, str) or not campaign_id.strip():
            raise FakeValidation("campaign_id is required")
        row = conn.execute(
            "SELECT id FROM campaigns WHERE id=? AND project_id=?",
            (campaign_id, project_id)).fetchone()
        if row is None:
            raise FakeNotFound("unknown campaign")

    def list_deliverables(conn, project_id, campaign_id):
        _scope(conn, project_id, campaign_id)
        rows = state["rows"].get(bucket(project_id, campaign_id), {})
        return [dict(row) for row in rows.values()]

    def get_generated_batch(conn, project_id, campaign_id, idempotency_key):
        _scope(conn, project_id, campaign_id)
        batch = (project_id, campaign_id, idempotency_key)
        ids = state["batches"].get(batch)
        if ids is None:
            return None
        rows = state["rows"].get(bucket(project_id, campaign_id), {})
        return [dict(rows[did]) for did in ids if did in rows]

    def get_deliverable(conn, project_id, campaign_id, deliverable_id):
        _scope(conn, project_id, campaign_id)
        row = state["rows"].get(bucket(project_id, campaign_id), {}).get(deliverable_id)
        if row is None:
            raise FakeNotFound("unknown deliverable")
        return dict(row)

    def save_generated_batch(conn, project_id, campaign_id, idempotency_key,
                             items, provenance):
        state["calls"].append({
            "fn": "save_generated_batch",
            "project_id": project_id, "campaign_id": campaign_id,
            "idempotency_key": idempotency_key,
            "items": [dict(item) for item in (items or [])],
            "provenance": provenance,
        })
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise FakeValidation("idempotency_key is required")
        if not isinstance(items, list) or not items:
            raise FakeValidation("items must be a non-empty list")
        clean = [_clean_item(item) for item in items]
        if not isinstance(provenance, dict):
            raise FakeValidation("provenance must be an object")
        try:
            json.dumps(provenance, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise FakeValidation("provenance must be JSON serializable") from exc
        _scope(conn, project_id, campaign_id)

        batch = (project_id, campaign_id, idempotency_key)
        rows = state["rows"].setdefault(bucket(project_id, campaign_id), {})
        if batch in state["batches"]:
            # W1: a repeated key returns the original rows unchanged.
            return [dict(rows[did]) for did in state["batches"][batch]]

        ids = []
        for ordinal, item in enumerate(clean):
            did = "del_" + sha256(
                f"{project_id}\0{campaign_id}\0{idempotency_key}\0{ordinal}".encode()
            ).hexdigest()[:32]
            rows[did] = {
                "id": did, "project_id": project_id, "campaign_id": campaign_id,
                **item, "status": "DRAFT", "current_version": 1,
                "generation_idempotency_key": idempotency_key,
                "generation_ordinal": ordinal,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
            ids.append(did)
        state["batches"][batch] = ids
        return [dict(rows[did]) for did in ids]

    def revise(conn, project_id, campaign_id, deliverable_id, expected_version,
               changes):
        state["calls"].append({
            "fn": "revise", "project_id": project_id, "campaign_id": campaign_id,
            "deliverable_id": deliverable_id, "expected_version": expected_version,
            "changes": dict(changes or {}),
        })
        if not isinstance(expected_version, int) or expected_version < 1:
            raise FakeValidation("expected_version must be a positive integer")
        if not isinstance(changes, dict) or not changes:
            raise FakeValidation("at least one editable field is required")
        if set(changes) - {"type", "title", "platform", "content_md"}:
            raise FakeValidation("unsupported editable field")
        _scope(conn, project_id, campaign_id)
        rows = state["rows"].get(bucket(project_id, campaign_id), {})
        current = rows.get(deliverable_id)
        if current is None:
            raise FakeNotFound("unknown deliverable")
        if current["current_version"] != expected_version:
            raise FakeConflict("deliverable changed; reload the latest version")
        candidate = {field: changes.get(field, current[field])
                     for field in ("type", "title", "platform", "content_md")}
        current.update(_clean_item(candidate))
        current["status"] = ("DRAFT" if current["status"] == "APPROVED"
                             else current["status"])
        current["current_version"] = expected_version + 1
        return dict(current)

    module = types.ModuleType("app.services.campaign_deliverables")
    module.save_generated_batch = save_generated_batch
    module.get_generated_batch = get_generated_batch
    module.list_deliverables = list_deliverables
    module.get_deliverable = get_deliverable
    module.revise = revise
    monkeypatch.setitem(sys.modules, "app.services.campaign_deliverables", module)
    # Graph code imports this lazily from the already-loaded package. Replacing
    # only sys.modules is insufficient when import order cached the old module
    # on app.services; patch both references and let monkeypatch restore them.
    import app.services as services
    monkeypatch.setattr(services, "campaign_deliverables", module, raising=False)
    return state


# ---------------------------------------------------------------------------
# scripted model output
# ---------------------------------------------------------------------------

def _block(kind, title, *, platform="", facts="", evidence="No project evidence available.",
           suggestions="Keep the tone plain.", unknowns="No price is stated."):
    platform_attr = f' platform="{platform}"' if platform else ' platform=""'
    return (
        f'## DELIVERABLE type="{kind}" title="{title}"{platform_attr}\n\n'
        f"## Persisted user facts\n{facts or 'No persisted facts supplied.'}\n\n"
        f"## Retrieved evidence\n{evidence}\n\n"
        f"## AI suggestions\n{suggestions}\n\n"
        f"## Unknown or unsupported information\n{unknowns}\n"
    )


def _social(title="Launch post", *, platform="instagram", facts=None,
            evidence="Huila region lots [doc-a:chunk-1].",
            suggestions="Lead with the tasting notes.",
            unknowns="No price, discount, or metric is stated anywhere."):
    return _block("social_post", title, platform=platform,
                  facts=(facts or "Business name: Northstar Roasters\nIndustry: coffee\n"
                         "Audience: Northstar Roasters customers\n"
                         "Offer: small-batch coffee"),
                  evidence=evidence, suggestions=suggestions,
                  unknowns=unknowns)


def _ad(title="Search ad", *, platform="google ads"):
    return _block("ad_copy", title, platform=platform,
                  facts=("Business name: Northstar Roasters\nIndustry: coffee\n"
                         "Audience: Northstar Roasters customers\n"
                         "Offer: small-batch coffee"),
                  evidence="Huila region lots [doc-a:chunk-1].",
                  suggestions="Keep the headline concise.",
                  unknowns="No budget or cost-per-click figure is stated.")


def _calendar(title="Four-week calendar"):
    return _block("content_calendar", title,
                  facts=("Business name: Northstar Roasters\nIndustry: coffee\n"
                         "Audience: Northstar Roasters customers\n"
                         "Offer: small-batch coffee"),
                  evidence="Huila region lots [doc-a:chunk-1].",
                  suggestions="Publish twice per week.",
                  unknowns="No posting dates were confirmed by the user.")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _seed_project(conn, project_id, *, name, website, brief_md=""):
    from app.services import business_workspace as workspace

    repos.Projects.upsert(conn, {
        "id": project_id, "name": name, "website": website,
        "goal": "Improve conversion", "status": "active", "settings_json": "{}",
        "created_at": "now", "updated_at": "now",
    })
    workspace.get_profile(conn, project_id)  # creates the row
    conn.execute(
        "UPDATE business_profiles SET business_name=?, industry=?, audience=?,"
        " offer=?, brief_md=?, updated_at=? WHERE project_id=?",
        (name, "coffee", f"{name} customers", "small-batch coffee",
         brief_md, "now", project_id))
    conn.commit()


def _conn(tmp_path):
    conn = connect(tmp_path / "dev032-campaign-ai.sqlite")
    repos.Companies.upsert(conn, {
        "id": "co1", "name": "Test", "website": "", "created_at": "now"})
    repos.Conversations.upsert(conn, {
        "id": "c1", "company_id": "co1", "project_id": "p1", "title": "Test",
        "created_at": "now", "updated_at": "now"})
    repos.Turns.upsert(conn, {
        "id": "t1", "conversation_id": "c1", "project_id": "p1",
        "status": "running", "created_at": "now", "updated_at": "now"})
    _seed_project(conn, "p1", name="Northstar Roasters",
                  website="https://northstar.test",
                  brief_md="## Persisted user facts\nNorthstar is a small roastery.")
    _seed_project(conn, "p2", name="Harbor Clinic",
                  website="https://harbor.test",
                  brief_md="## Persisted user facts\nHarbor SECRET-BRIEF-XYZ is a clinic.")
    return conn


def _graph(conn):
    from app.graphs.account_manager_graph import build_account_manager_graph

    return build_account_manager_graph(
        conn_factory=lambda: conn, project_lookup=lambda _pid: {"id": "p1"},
        complete_fn=lambda **_kwargs: "Prepared the requested work.",
        employee_fn=lambda _task, _role: {"findings": [], "sources": []},
    )


def _state(text, *, turn_id="t1", project_id="p1",
           provider="LOCAL", model_id="dev032-selected-model"):
    return initial_state(project_id=project_id, conversation_id="c1",
                         turn_id=turn_id, user_request=text,
                         model_provider=provider, model_id=model_id)


def _scoped_retrieval(monkeypatch):
    """Retrieval fake that only answers for the turn project, and records it."""
    seen: list[str] = []

    def retrieve_scoped(conn, query, *, project_id, **_kwargs):
        seen.append(project_id)
        if project_id != "p1":
            return {"hits": [], "mode": "FTS_ONLY"}
        return {"hits": [
            {"document_id": "doc-a", "chunk_id": "chunk-1", "path": "notes.md",
             "text": "Northstar sources Huila region lots."},
        ], "mode": "FTS_ONLY"}

    monkeypatch.setattr("app.services.rag.scoped_retrieval.retrieve_scoped",
                        retrieve_scoped)
    return seen


def _stored(state, conn, project_id, campaign_id):
    """Ordered rows exactly as W1 would return them for this scope."""
    from app.services import campaign_deliverables

    return campaign_deliverables.list_deliverables(conn, project_id, campaign_id)


def _live_row(state, project_id, campaign_id, deliverable_id):
    """The stored row itself, so a test can simulate a concurrent editor."""
    return state["rows"][(project_id, campaign_id)][deliverable_id]


def _insert_manual_deliverable(state, project_id, campaign_id, *, kind, title,
                               platform=None):
    """Seed a W1-shaped manual row beside AI-generated deliverables."""
    item = _clean_item({
        "type": kind, "title": title, "platform": platform,
        "content_md": f"Manual {title} for test coverage.",
    })
    did = "manual_" + sha256(
        f"{project_id}\0{campaign_id}\0{title}".encode()
    ).hexdigest()[:24]
    row = {
        "id": did, "project_id": project_id, "campaign_id": campaign_id,
        **item, "status": "DRAFT", "current_version": 1,
        "generation_idempotency_key": None, "generation_ordinal": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    state["rows"].setdefault((project_id, campaign_id), {})[did] = row
    return dict(row)


def _insert_manual_ad(state, project_id, campaign_id, *, title, platform):
    return _insert_manual_deliverable(
        state, project_id, campaign_id, kind="ad_copy", title=title,
        platform=platform)


def _model_calls(conn, turn_id):
    return [dict(row) for row in
            repos.ModelCalls.for_turn(conn, turn_id, "p1")]


# ---------------------------------------------------------------------------
# explicit intent and gates
# ---------------------------------------------------------------------------

def test_plain_campaign_request_does_not_request_deliverables():
    intents = _explicit_write_intents("Create a marketing campaign for my new product.")
    assert intents["campaign"]["title"] == "Campaign: my new product"
    assert intents["deliverable"] is None


def test_explicit_deliverable_request_is_a_generation_intent():
    intents = _explicit_write_intents(
        "Create a marketing campaign to improve website conversion with an "
        "ad copy and a content calendar for 4 weeks.")
    assert intents["campaign"]
    assert intents["deliverable"]["generate"]["types"] == [
        "ad_copy", "content_calendar"]


def test_mixed_create_and_publish_preserves_external_action_intent():
    intents = _explicit_write_intents(
        "Create a marketing campaign with an ad copy, then publish it on Meta "
        "and spend $100.")

    assert intents["campaign"]
    assert intents["deliverable"]["generate"]["types"] == ["ad_copy"]
    assert intents["external_action"]["platform"] == "Meta"

    campaign_only = _explicit_write_intents(
        "Create a marketing campaign, then publish it on Meta.")
    assert campaign_only["campaign"]
    assert campaign_only["external_action"]["action"] == "publish"

    deliverable_only = _explicit_write_intents(
        "Create an ad copy and publish it on Meta.")
    assert deliverable_only["deliverable"]["generate"]["types"] == ["ad_copy"]
    assert deliverable_only["external_action"]["action"] == "publish"


def test_deliverable_generation_does_not_displace_the_propose_campaign_gate():
    intents = _explicit_write_intents(
        "Create a marketing campaign with a social post.")
    assert intents["campaign"]["title"].startswith("Campaign:")
    assert intents["campaign"]["approval_level"] == "Green"
    # Extended metadata travels beside the tool payload, never inside it.
    assert "objective" not in intents["campaign"]
    assert intents["campaign_intake"]["objective"]


def test_a_question_about_channels_stays_prose():
    assert _deliverable_write_intents(
        "Which channels should we post on for our social media post?",
        allow_revision=True) == {}
    assert _explicit_write_intents(
        "Which channels should we post on for our social media post?") == {}


def test_revision_verb_on_a_new_campaign_generates_instead_of_revising():
    intents = _explicit_write_intents(
        "Create a campaign and rewrite the ad copy for the launch.")
    assert intents["deliverable"]["generate"]["types"] == ["ad_copy"]


@pytest.mark.parametrize(
    ("text", "is_revision"),
    [
        ("Rewrite the ad copy to be shorter.", True),
        ("Update the ad copy.", True),
        ("I want to update the ad copy", True),
        ("Shorten our Instagram caption", True),
        ("Make the meta ads ad copy punchier.", True),
        # An incidental verb inside a creation request is not a revision.
        ("Create a social post about our update policy.", False),
        ("Write a content calendar for our new product launch.", False),
    ],
)
def test_revision_detection_requires_a_named_existing_deliverable(text, is_revision):
    assert production.looks_like_revision(text) is is_revision
    intent = _deliverable_write_intents(text, allow_revision=True)
    if is_revision:
        assert "revision" in intent
    else:
        assert intent.get("generate", {}).get("types") == production.resolved_types(text)


def test_audience_is_only_recorded_when_explicitly_named():
    assert production.campaign_intake(
        "Create a campaign. Submit this work for approval and pause.",
        objective="x")["target_audience"] == ""
    assert production.campaign_intake(
        "Create a campaign targeting our existing customers.",
        objective="x")["target_audience"] == "existing customers"


def test_analysis_only_turn_never_proposes_a_deliverable():
    text = "Don't create anything yet. Create a campaign with a social post."
    assert _current_turn_write_constraints(text)["analysis_only"] is True
    assert _explicit_write_intents(text) == {}


def test_forbidden_deliverable_action_blocks_the_write():
    intents = _explicit_write_intents(
        "Create a marketing campaign with a social post.")
    filtered, blocked = _filter_forbidden_write_intents(
        intents, {"forbidden_actions": ["deliverable"], "analysis_only": False})
    assert "deliverable" in blocked
    assert filtered["deliverable"] is None
    assert filtered["requires_approval"] is False


# ---------------------------------------------------------------------------
# request parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Write a strategy brief", ["strategy_brief"]),
        ("make an ad copy", ["ad_copy"]),
        ("a social post and a creative brief", ["social_post", "creative_brief"]),
        ("give me the content calendar", ["content_calendar"]),
        # An unsupported kind names no supported type, so the standard five run.
        ("create a campaign with a press release", list(DELIVERABLE_TYPES)),
        ("create a campaign for our store", list(DELIVERABLE_TYPES)),
    ],
)
def test_requested_types_map_to_the_five_supported_kinds(text, expected):
    assert production.resolved_types(text) == expected
    assert all(kind in DELIVERABLE_TYPES for kind in expected)


def test_campaign_intake_records_only_what_the_user_said():
    intake = production.campaign_intake(
        "Create a marketing campaign to improve website conversion with an ad "
        "copy and a content calendar on Instagram and email for 4 weeks, "
        "targeting our existing customers.",
        objective="improve website conversion", turn_id="t1")
    assert intake["objective"] == "improve website conversion"
    assert intake["target_audience"] == "existing customers"
    assert intake["channels"] == ["instagram", "email"]
    assert intake["duration"] == "4 weeks"
    assert intake["request_facts"]["requested_types"] == [
        "ad_copy", "content_calendar"]
    # Nothing invented: no budget, metric, price, or date.
    serialized = json.dumps(intake).lower()
    for invented in ("budget", "roas", "$", "conversion rate"):
        assert invented not in serialized


# ---------------------------------------------------------------------------
# generation through the graph
# ---------------------------------------------------------------------------

def test_explicit_request_generates_and_persists_requested_types(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage = _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_ad() + _calendar())
    _install_router(monkeypatch, provider)
    _scoped_retrieval(monkeypatch)

    out = _graph(conn).invoke(
        _state("Create a marketing campaign to improve website conversion with "
               "an ad copy and a content calendar."),
        config={"configurable": {"thread_id": "t1"}})

    result = out["write_results"]["deliverables"]
    assert result["ok"] is True
    assert result["status"] == "saved"
    assert result["types"] == ["ad_copy", "content_calendar"]
    assert result["provider"] == "local"
    assert result["evidence_count"] == 1

    call = storage["calls"][0]
    assert call["fn"] == "save_generated_batch"
    assert call["project_id"] == "p1"
    assert call["campaign_id"] == out["write_results"]["campaign"]["campaign_id"]
    assert len(call["items"]) == 2
    for item in call["items"]:
        assert set(item) == {"type", "title", "platform", "content_md"}
        assert isinstance(item["title"], str) and item["title"]
        assert isinstance(item["content_md"], str) and item["content_md"]
    assert json.loads(json.dumps(call["provenance"]))["turn_id"] == "t1"

    assert "Campaign deliverables saved:" in out["final_answer"]
    assert "ad_copy del_" in out["final_answer"]
    assert "content_calendar del_" in out["final_answer"]


def test_selected_provider_and_model_reach_the_router(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_social())
    _install_router(monkeypatch, provider)
    _scoped_retrieval(monkeypatch)

    out = _graph(conn).invoke(
        _state("Create a marketing campaign with a social post.",
               provider="LOCAL", model_id="dev032-selected-model"),
        config={"configurable": {"thread_id": "t1"}})

    result = out["write_results"]["deliverables"]
    assert result["ok"] is True
    assert result["model"] == "dev032-selected-model"
    assert provider.calls[0]["opts"]["model"] == "dev032-selected-model"
    calls = _model_calls(conn, "t1")
    assert len(calls) == 1
    assert calls[0]["provider"] == "local"
    assert calls[0]["model"] == "dev032-selected-model"
    assert calls[0]["project_id"] == "p1"


def test_auto_selection_drops_a_stale_model_instead_of_guessing(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_social())
    _install_router(monkeypatch, provider)
    _scoped_retrieval(monkeypatch)

    out = _graph(conn).invoke(
        _state("Create a marketing campaign with a social post.",
               provider="AUTO", model_id="dev032-selected-model"),
        config={"configurable": {"thread_id": "t1"}})

    result = out["write_results"]["deliverables"]
    assert result["ok"] is True
    # AUTO routes itself and refuses a specific model, so the router's own
    # default is used rather than the selection left behind.
    assert result["model"] == "dev032-local-default"
    assert provider.calls[0]["opts"].get("model") is None


def test_generation_reads_only_the_turn_project_profile_brief_and_evidence(
        tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_social())
    _install_router(monkeypatch, provider)
    seen_projects = _scoped_retrieval(monkeypatch)

    from app.services import business_workspace as workspace

    profile_reads: list[str] = []
    real_get_profile = workspace.get_profile

    def recording_get_profile(conn_, project_id):
        profile_reads.append(project_id)
        return real_get_profile(conn_, project_id)

    monkeypatch.setattr(workspace, "get_profile", recording_get_profile)

    out = _graph(conn).invoke(
        _state("Create a marketing campaign with a social post."),
        config={"configurable": {"thread_id": "t1"}})

    assert out["write_results"]["deliverables"]["ok"] is True
    assert set(profile_reads) == {"p1"}
    assert seen_projects == ["p1"]
    prompt = provider.calls[0]["user"]
    assert "Northstar Roasters" in prompt
    assert "Huila region lots" in prompt
    assert "doc-a:chunk-1" in prompt
    assert "Harbor" not in prompt
    assert "SECRET-BRIEF-XYZ" not in prompt


def test_generation_without_a_turn_project_fails_closed(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage = _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_social())
    _install_router(monkeypatch, provider)

    result = production.generate_batch(
        conn, project_id="", campaign_id="camp-1", turn_id="t1",
        user_request="Create a social post.", types=["social_post"])

    assert result["ok"] is False
    assert "project" in result["error"].lower()
    assert provider.calls == []
    assert storage["calls"] == []


def test_scoped_retrieval_failures_do_not_invent_evidence(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage = _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_block(
        "social_post", "Launch post", platform="instagram",
        facts="Northstar Roasters sells small-batch coffee.",
        evidence="No project evidence available.",
        suggestions="Lead with the tasting notes.",
        unknowns="No price is stated."))
    _install_router(monkeypatch, provider)

    def failing_retrieval(*_args, **_kwargs):
        raise RuntimeError("lexical index unavailable")

    monkeypatch.setattr("app.services.rag.scoped_retrieval.retrieve_scoped",
                        failing_retrieval)

    out = _graph(conn).invoke(
        _state("Create a marketing campaign with a social post."),
        config={"configurable": {"thread_id": "t1"}})

    result = out["write_results"]["deliverables"]
    assert result["ok"] is False
    assert result["failure_code"] == "output_validation"
    assert result["preserved_existing"] is True
    assert "persisted fact" in result["error"].lower()
    assert storage["calls"] == []
    assert _stored(storage, conn, "p1",
                   out["write_results"]["campaign"]["campaign_id"]) == []

    # Retrieval failed closed: the model received zero evidence, and the
    # campaign has no persisted deliverable that could present its unsupported
    # generated profile claim as fact.
    request = json.loads(provider.calls[0]["user"])
    assert request["evidence"] == []
    context = production.build_generation_context(
        conn, project_id="p1",
        user_request="Create a marketing campaign with a social post.")
    assert context["evidence"] == []
    assert context["evidence_ids"] == []
    assert context["retrieval_error"].startswith("RuntimeError")
    assert "doc-a:chunk-1" not in provider.calls[0]["user"]


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------

def test_retried_turn_reuses_the_original_rows_and_preserves_edits(
        tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage = _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_social() + _ad())
    _install_router(monkeypatch, provider)
    _scoped_retrieval(monkeypatch)

    graph = _graph(conn)
    request = "Create a marketing campaign with a social post and an ad copy."
    first = graph.invoke(_state(request),
                         config={"configurable": {"thread_id": "t1"}})
    campaign_id = first["write_results"]["campaign"]["campaign_id"]
    ids = list(first["write_results"]["deliverables"]["created"])
    assert len(ids) == 2
    assert first["write_results"]["deliverables"]["idempotent_replay"] is False

    # The user edits and approves one deliverable after generation.
    _live_row(storage, "p1", campaign_id, ids[0]).update({
        "content_md": "# Hand-edited by the user\n",
        "current_version": 2, "status": "APPROVED"})

    second = graph.invoke(_state(request),
                          config={"configurable": {"thread_id": "t1"}})
    result = second["write_results"]["deliverables"]
    assert result["ok"] is True
    assert result["idempotent_replay"] is True
    assert result["created"] == ids

    after = {row["id"]: row for row in _stored(storage, conn, "p1", campaign_id)}
    assert len(after) == 2
    assert after[ids[0]]["content_md"] == "# Hand-edited by the user\n"
    assert after[ids[0]]["status"] == "APPROVED"
    assert after[ids[0]]["current_version"] == 2
    assert after[ids[1]]["content_md"] != "# Hand-edited by the user\n"
    # Replay returned the original rows before a second W1 write, preserving
    # the edited content, status, version, and original idempotency key.
    assert len(storage["calls"]) == 1
    assert storage["calls"][0]["idempotency_key"] == \
        production.batch_idempotency_key(
            project_id="p1", campaign_id=campaign_id, turn_id="t1")


def test_idempotency_key_is_scoped_to_project_campaign_and_turn():
    first = production.batch_idempotency_key(
        project_id="p1", campaign_id="c1", turn_id="t1")
    assert first == production.batch_idempotency_key(
        project_id="p1", campaign_id="c1", turn_id="t1")
    assert first != production.batch_idempotency_key(
        project_id="p2", campaign_id="c1", turn_id="t1")
    assert first != production.batch_idempotency_key(
        project_id="p1", campaign_id="c1", turn_id="t2")


# ---------------------------------------------------------------------------
# campaign metadata
# ---------------------------------------------------------------------------

def test_campaign_metadata_records_only_the_requested_facts(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _install_storage(monkeypatch)
    _install_router(monkeypatch, ScriptedProvider(text=_ad() + _calendar()))
    _scoped_retrieval(monkeypatch)

    out = _graph(conn).invoke(
        _state("Create a marketing campaign to improve website conversion with "
               "an ad copy and a content calendar on Instagram for 4 weeks, "
               "targeting our existing customers."),
        config={"configurable": {"thread_id": "t1"}})

    campaign = repos.Campaigns.get(
        conn, out["write_results"]["campaign"]["campaign_id"], "p1")
    metadata = json.loads(campaign["workflow_json"])
    intake = metadata["campaign_intake"]
    assert intake["objective"] == "improve website conversion"
    assert intake["target_audience"] == "existing customers"
    assert intake["channels"] == ["instagram"]
    assert intake["duration"] == "4 weeks"
    assert intake["request_facts"]["turn_id"] == "t1"
    assert set(intake["request_facts"]["requested_types"]) == {
        "ad_copy", "content_calendar"}
    # The provenance the campaign proposal already stored is preserved.
    assert metadata["turn_id"] == "t1"
    assert metadata["assigned_role"] == "account_manager"


def test_campaign_metadata_is_not_written_to_a_foreign_project(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _install_storage(monkeypatch)
    repos.Campaigns.upsert(conn, {
        "id": "foreign", "project_id": "p2", "title": "Other client",
        "status": "drafted", "impact": 0, "confidence": 0, "effort": 0, "cost": 0,
        "approval_level": "Green", "workflow_json": "{}", "updated_at": "now"})

    merged = production.apply_campaign_metadata(
        conn, "p1", "foreign", production.campaign_intake("Write an ad copy."))

    assert merged == {}
    assert json.loads(
        repos.Campaigns.get(conn, "foreign", "p2")["workflow_json"]) == {}


# ---------------------------------------------------------------------------
# revision
# ---------------------------------------------------------------------------

def _generate_pair(conn, monkeypatch):
    storage = _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_social("Instagram launch")
                                + _ad("Search ad", platform="google ads"))
    _install_router(monkeypatch, provider)
    _scoped_retrieval(monkeypatch)
    out = _graph(conn).invoke(
        _state("Create a marketing campaign with a social post and an ad copy."),
        config={"configurable": {"thread_id": "t1"}})
    assert out["write_results"]["deliverables"]["ok"] is True
    return storage, provider, out["write_results"]["campaign"]["campaign_id"]


def test_revision_targets_one_deliverable_and_saves_a_new_version(
        tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage, provider, campaign_id = _generate_pair(conn, monkeypatch)
    rows = {row["type"]: row for row in _stored(storage, conn, "p1", campaign_id)}
    target, untouched = rows["ad_copy"], rows["social_post"]
    provider.text = _ad("Search ad (shorter)")

    out = _graph(conn).invoke(
        _state("Rewrite the ad copy to be shorter.", turn_id="t2"),
        config={"configurable": {"thread_id": "t2"}})

    result = out["write_results"]["deliverables"]
    assert result["ok"] is True
    assert result["deliverable_id"] == target["id"]
    assert result["version"] == 2

    revise_calls = [call for call in storage["calls"] if call["fn"] == "revise"]
    assert len(revise_calls) == 1
    call = revise_calls[0]
    assert call["project_id"] == "p1"
    assert call["campaign_id"] == campaign_id
    assert call["deliverable_id"] == target["id"]
    assert call["expected_version"] == 1
    # The editorial kind is stable, so it is never sent as an edit.
    assert "type" not in call["changes"]
    assert call["changes"]["content_md"].startswith("## Persisted user facts")

    after = {row["id"]: row for row in _stored(storage, conn, "p1", campaign_id)}
    assert after[target["id"]]["current_version"] == 2
    assert after[untouched["id"]]["current_version"] == 1
    assert len(after) == 2


def test_revision_reopens_an_approved_deliverable_as_draft(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage, provider, campaign_id = _generate_pair(conn, monkeypatch)
    rows = {row["type"]: row for row in _stored(storage, conn, "p1", campaign_id)}
    target = rows["ad_copy"]
    _live_row(storage, "p1", campaign_id, target["id"]).update(
        {"status": "APPROVED", "current_version": 4})
    provider.text = _ad("Search ad (approved edit)")

    result = production.revise_deliverable(
        conn, project_id="p1", campaign_id=campaign_id, turn_id="t2",
        deliverable_id=target["id"], instruction="Rewrite the ad copy.",
        provider="LOCAL", model_id="dev032-fake-model")

    assert result["ok"] is True
    assert result["version"] == 5
    after = {row["id"]: row for row in _stored(storage, conn, "p1", campaign_id)}
    assert after[target["id"]]["status"] == "DRAFT"
    assert after[rows["social_post"]["id"]]["current_version"] == 1


def test_ambiguous_revision_asks_one_focused_clarification(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage = _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_ad("Search ad", platform="google ads"))
    _install_router(monkeypatch, provider)
    _scoped_retrieval(monkeypatch)
    out = _graph(conn).invoke(
        _state("Create a marketing campaign with an ad copy."),
        config={"configurable": {"thread_id": "t1"}})
    campaign_id = out["write_results"]["campaign"]["campaign_id"]
    _insert_manual_ad(storage, "p1", campaign_id,
                      title="Meta ad", platform="meta ads")
    before = [dict(row) for row in _stored(storage, conn, "p1", campaign_id)]
    calls_before = len(storage["calls"])
    model_calls_before = len(provider.calls)

    provider.text = _ad("Anything", platform="google ads")
    out = _graph(conn).invoke(
        _state("Update the ad copy.", turn_id="t2"),
        config={"configurable": {"thread_id": "t2"}})

    result = out["write_results"]["deliverables"]
    assert result["ok"] is False
    assert result["status"] == "needs_clarification"
    assert "which one to revise" in result["error"].lower()
    assert [row["title"] for row in result["candidates"]] == ["Search ad", "Meta ad"]
    # One focused question, no model call, and no write of any kind.
    assert _stored(storage, conn, "p1", campaign_id) == before
    assert len(storage["calls"]) == calls_before
    assert len(provider.calls) == model_calls_before
    assert result["error"] in out["final_answer"]


def test_revision_resolves_a_single_matching_platform(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage = _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_ad("Search ad", platform="google ads"))
    _install_router(monkeypatch, provider)
    _scoped_retrieval(monkeypatch)
    out = _graph(conn).invoke(
        _state("Create a marketing campaign with an ad copy."),
        config={"configurable": {"thread_id": "t1"}})
    campaign_id = out["write_results"]["campaign"]["campaign_id"]
    meta_row = _insert_manual_ad(storage, "p1", campaign_id,
                                 title="Meta ad", platform="meta ads")
    provider.text = _ad("Meta ad (tighter)", platform="meta ads")

    out = _graph(conn).invoke(
        _state("Rewrite the meta ads ad copy to be tighter.", turn_id="t2"),
        config={"configurable": {"thread_id": "t2"}})

    result = out["write_results"]["deliverables"]
    assert result["ok"] is True
    assert result["deliverable_id"] == meta_row["id"]
    revise_calls = [call for call in storage["calls"] if call["fn"] == "revise"]
    assert len(revise_calls) == 1
    assert revise_calls[0]["deliverable_id"] == meta_row["id"]


def test_revision_of_missing_requested_type_does_not_fall_back_to_another_type(
        tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage, provider, campaign_id = _seed_one(conn, monkeypatch)
    before = [dict(row) for row in _stored(storage, conn, "p1", campaign_id)]
    writes_before = len(storage["calls"])
    model_calls_before = len(provider.calls)

    out = _graph(conn).invoke(
        _state("Rewrite the ad copy to be shorter.", turn_id="t2"),
        config={"configurable": {"thread_id": "t2"}})

    result = out["write_results"]["deliverables"]
    assert result["ok"] is False
    assert result["status"] == "needs_clarification"
    assert "ad_copy" in result["error"]
    assert result["candidates"] == [{
        "id": before[0]["id"], "type": "social_post",
        "title": before[0]["title"], "platform": before[0]["platform"],
    }]
    assert before[0]["title"] in out["final_answer"]
    assert _stored(storage, conn, "p1", campaign_id) == before
    assert len(storage["calls"]) == writes_before
    assert len(provider.calls) == model_calls_before


def test_revision_with_multiple_campaigns_requires_explicit_campaign(
        tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage, provider, first_campaign_id = _seed_one(conn, monkeypatch)
    second_campaign_id = "campaign-second"
    repos.Campaigns.upsert(conn, {
        "id": second_campaign_id, "project_id": "p1",
        "title": "Campaign: Winter Launch", "status": "drafted",
        "impact": 0, "confidence": 0, "effort": 0, "cost": 0,
        "approval_level": "Green", "workflow_json": "{}",
        "updated_at": "later",
    })
    repos.Campaigns.upsert(conn, {
        "id": "campaign-foreign", "project_id": "p2",
        "title": "SECRET Harbor Campaign", "status": "drafted",
        "impact": 0, "confidence": 0, "effort": 0, "cost": 0,
        "approval_level": "Green", "workflow_json": "{}",
        "updated_at": "later",
    })
    _insert_manual_deliverable(
        storage, "p1", second_campaign_id, kind="social_post",
        title="Winter social post", platform="instagram")
    _insert_manual_deliverable(
        storage, "p2", "campaign-foreign", kind="social_post",
        title="SECRET Harbor Post", platform="instagram")
    before_first = [dict(row) for row in
                    _stored(storage, conn, "p1", first_campaign_id)]
    before_second = [dict(row) for row in
                     _stored(storage, conn, "p1", second_campaign_id)]
    writes_before = len(storage["calls"])
    model_calls_before = len(provider.calls)

    out = _graph(conn).invoke(
        _state("Rewrite the social post to be more concise.", turn_id="t2"),
        config={"configurable": {"thread_id": "t2"}})

    result = out["write_results"]["deliverables"]
    assert result["ok"] is False
    assert result["status"] == "needs_clarification"
    candidates = result["campaign_candidates"]
    assert {item["campaign_id"] for item in candidates} == {
        first_campaign_id, second_campaign_id}
    assert all(item["deliverables"] for item in candidates)
    assert "SECRET Harbor Campaign" not in str(candidates)
    assert "SECRET Harbor Post" not in str(candidates)
    assert "Winter Launch" in out["final_answer"]
    assert "SECRET Harbor" not in out["final_answer"]
    assert _stored(storage, conn, "p1", first_campaign_id) == before_first
    assert _stored(storage, conn, "p1", second_campaign_id) == before_second
    assert len(storage["calls"]) == writes_before
    assert len(provider.calls) == model_calls_before

    provider.text = _social("Winter social post")
    selected = _graph(conn).invoke(
        _state("Rewrite the social post in Winter Launch to be more concise.",
               turn_id="t3"),
        config={"configurable": {"thread_id": "t3"}})
    selected_result = selected["write_results"]["deliverables"]
    assert selected_result["ok"] is True
    assert selected_result["campaign_id"] == second_campaign_id
    assert selected_result["deliverable_id"] == before_second[0]["id"]


def test_revision_without_a_campaign_fails_without_writing(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage = _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_social())
    _install_router(monkeypatch, provider)

    out = _graph(conn).invoke(
        _state("Rewrite the ad copy to be shorter.", turn_id="t2"),
        config={"configurable": {"thread_id": "t2"}})

    result = out["write_results"]["deliverables"]
    assert result["ok"] is False
    assert "no campaign to revise" in result["error"].lower()
    assert storage["calls"] == []
    assert provider.calls == []
    assert "No campaign deliverables were saved." in out["final_answer"]


# ---------------------------------------------------------------------------
# malformed and unavailable output
# ---------------------------------------------------------------------------

def _seed_one(conn, monkeypatch):
    storage = _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_social())
    _install_router(monkeypatch, provider)
    _scoped_retrieval(monkeypatch)
    out = _graph(conn).invoke(
        _state("Create a marketing campaign with a social post."),
        config={"configurable": {"thread_id": "t1"}})
    campaign_id = out["write_results"]["campaign"]["campaign_id"]
    return storage, provider, campaign_id


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Here are three great posts for you!",
         "required '## DELIVERABLE' block format"),
        (_block("press_release", "Launch"),
         "unsupported type"),
        (_block("social_post", "Launch", platform="instagram",
                facts="Northstar Roasters.", evidence="See [other-client:chunk-9].",
                suggestions="Be direct.", unknowns="No price is stated."),
         "not part of this project's scoped knowledge"),
        (_block("social_post", "Launch", platform="instagram",
                facts="Northstar Roasters.", evidence="No project evidence available.",
                suggestions="Be direct.").split("## Unknown")[0],
         "did not separate"),
        (_block("ad_copy", "Wrong type", platform="google ads",
                facts="Northstar Roasters.",
                evidence="Huila lots [doc-a:chunk-1].",
                suggestions="Be direct.", unknowns="No budget is stated."),
         "but this request asked for"),
    ],
)
def test_malformed_output_preserves_existing_deliverables(
        tmp_path, monkeypatch, text, reason):
    conn = _conn(tmp_path)
    storage, provider, campaign_id = _seed_one(conn, monkeypatch)
    before = [dict(row) for row in _stored(storage, conn, "p1", campaign_id)]
    calls_before = len(storage["calls"])

    # Exercise validation against the seeded campaign with a fresh turn key;
    # replay of t1 would correctly return the original batch before validation.
    provider.text = text
    result = production.generate_batch(
        conn, project_id="p1", campaign_id=campaign_id, turn_id="t2",
        user_request="Create a marketing campaign with a social post.",
        types=["social_post"], provider="LOCAL", model_id="dev032-fake-model")
    assert result["ok"] is False
    assert result["failure_code"] == "output_validation"
    assert result["preserved_existing"] is True
    assert reason in result["error"]
    assert _stored(storage, conn, "p1", campaign_id) == before
    assert len(storage["calls"]) == calls_before


def test_empty_completion_preserves_existing_deliverables(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage, provider, campaign_id = _seed_one(conn, monkeypatch)
    before = [dict(row) for row in _stored(storage, conn, "p1", campaign_id)]
    calls_before = len(storage["calls"])

    provider.text = "   "
    result = production.generate_batch(
        conn, project_id="p1", campaign_id=campaign_id, turn_id="t2",
        user_request="Create a social post.", types=["social_post"],
        provider="LOCAL", model_id="dev032-fake-model")

    assert result["ok"] is False
    assert result["failure_code"] == "empty_completion"
    assert result["preserved_existing"] is True
    assert _stored(storage, conn, "p1", campaign_id) == before
    assert len(storage["calls"]) == calls_before


@pytest.mark.parametrize("retry_output", ["provider_error", "invalid_output"])
def test_saved_batch_replay_precedes_provider_error_or_invalid_output(
        tmp_path, monkeypatch, retry_output):
    conn = _conn(tmp_path)
    storage, provider, campaign_id = _seed_one(conn, monkeypatch)
    first = _stored(storage, conn, "p1", campaign_id)
    target = first[0]
    _live_row(storage, "p1", campaign_id, target["id"]).update({
        "content_md": "User-edited content survives replay.",
        "status": "APPROVED", "current_version": 3,
    })
    before = [dict(row) for row in _stored(storage, conn, "p1", campaign_id)]
    writes_before = len(storage["calls"])
    provider.calls.clear()
    if retry_output == "provider_error":
        provider.error = RuntimeError("retry provider failure")
    else:
        provider.text = "Malformed retry output."

    result = production.generate_batch(
        conn, project_id="p1", campaign_id=campaign_id, turn_id="t1",
        user_request="Create a marketing campaign with a social post.",
        types=["social_post"], provider="LOCAL", model_id="dev032-fake-model")

    assert result["ok"] is True
    assert result["idempotent_replay"] is True
    assert result["created"] == [target["id"]]
    assert result["deliverables"] == before
    assert provider.calls == []
    assert len(storage["calls"]) == writes_before


@pytest.mark.parametrize(
    "fabricated_claim",
    [
        "Price the coffee at $49.",
        "Suggest a 40% off discount.",
        "Conversion rate increased by 38%.",
    ],
)
def test_unsupported_high_risk_facts_are_rejected_without_overwriting(
        tmp_path, monkeypatch, fabricated_claim):
    conn = _conn(tmp_path)
    storage, provider, campaign_id = _seed_one(conn, monkeypatch)
    before = [dict(row) for row in _stored(storage, conn, "p1", campaign_id)]
    writes_before = len(storage["calls"])
    provider.text = _social(suggestions=fabricated_claim)

    result = production.generate_batch(
        conn, project_id="p1", campaign_id=campaign_id, turn_id="t2",
        user_request="Create a social post about our coffee offer.",
        types=["social_post"], provider="LOCAL", model_id="dev032-fake-model")

    assert result["ok"] is False
    assert result["failure_code"] == "output_validation"
    assert result["preserved_existing"] is True
    assert "unsupported" in result["error"].lower()
    assert _stored(storage, conn, "p1", campaign_id) == before
    assert len(storage["calls"]) == writes_before


def test_unsupported_persisted_fact_is_rejected_without_overwriting(
        tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage, provider, campaign_id = _seed_one(conn, monkeypatch)
    before = [dict(row) for row in _stored(storage, conn, "p1", campaign_id)]
    writes_before = len(storage["calls"])
    provider.text = _social(
        facts="Northstar Roasters uses certified organic beans.")

    result = production.generate_batch(
        conn, project_id="p1", campaign_id=campaign_id, turn_id="t2",
        user_request="Create a social post.", types=["social_post"],
        provider="LOCAL", model_id="dev032-fake-model")

    assert result["ok"] is False
    assert result["failure_code"] == "output_validation"
    assert result["preserved_existing"] is True
    assert "persisted fact" in result["error"].lower()
    assert _stored(storage, conn, "p1", campaign_id) == before
    assert len(storage["calls"]) == writes_before


def test_supported_request_price_discount_and_date_are_grounded(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage = _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_social(
        facts="Current offer: $18, 20% off until 2026-10-15.",
        unknowns="Additional offer terms are unknown."))
    _install_router(monkeypatch, provider)
    _scoped_retrieval(monkeypatch)
    out = _graph(conn).invoke(
        _state("Create a marketing campaign with a social post. Current offer: "
               "$18, 20% off until 2026-10-15."),
        config={"configurable": {"thread_id": "t1"}})

    result = out["write_results"]["deliverables"]
    assert result["ok"] is True
    assert result["deliverables"][0]["content_md"].startswith(
        "## Persisted user facts\nCurrent offer: $18, 20% off until 2026-10-15.")
    assert len(storage["calls"]) == 1


def test_citation_validator_import_failure_fails_closed_and_preserves_rows(
        tmp_path, monkeypatch):
    import builtins

    conn = _conn(tmp_path)
    storage, provider, campaign_id = _seed_one(conn, monkeypatch)
    before = [dict(row) for row in _stored(storage, conn, "p1", campaign_id)]
    writes_before = len(storage["calls"])
    original_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "app.routes.business_workspace":
            raise ImportError("synthetic citation-validator outage")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    result = production.generate_batch(
        conn, project_id="p1", campaign_id=campaign_id, turn_id="t2",
        user_request="Create a social post.", types=["social_post"],
        provider="LOCAL", model_id="dev032-fake-model")

    assert result["ok"] is False
    assert result["failure_code"] == "output_validation"
    assert result["preserved_existing"] is True
    assert "citation validator is unavailable" in result["error"].lower()
    assert _stored(storage, conn, "p1", campaign_id) == before
    assert len(storage["calls"]) == writes_before


def test_retrieved_evidence_statement_requires_a_valid_citation(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage, provider, campaign_id = _seed_one(conn, monkeypatch)
    before = [dict(row) for row in _stored(storage, conn, "p1", campaign_id)]
    writes_before = len(storage["calls"])
    provider.text = _social(evidence="Huila region lots have a bright profile.")

    result = production.generate_batch(
        conn, project_id="p1", campaign_id=campaign_id, turn_id="t2",
        user_request="Create a social post.", types=["social_post"],
        provider="LOCAL", model_id="dev032-fake-model")

    assert result["ok"] is False
    assert result["failure_code"] == "output_validation"
    assert result["preserved_existing"] is True
    assert "citation" in result["error"].lower()
    assert _stored(storage, conn, "p1", campaign_id) == before
    assert len(storage["calls"]) == writes_before


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (_social(), "omitted requested deliverable type(s): ad_copy"),
        (_social("First post") + _social("Duplicate post"),
         "more than one block for requested type 'social_post'"),
    ],
)
def test_incomplete_or_duplicate_batch_preserves_existing_deliverables(
        tmp_path, monkeypatch, text, reason):
    conn = _conn(tmp_path)
    storage, provider, campaign_id = _seed_one(conn, monkeypatch)
    before = [dict(row) for row in _stored(storage, conn, "p1", campaign_id)]
    calls_before = len(storage["calls"])
    provider.text = text

    result = production.generate_batch(
        conn, project_id="p1", campaign_id=campaign_id, turn_id="t2",
        user_request="Create a social post and an ad copy.",
        types=["social_post", "ad_copy"],
        provider="LOCAL", model_id="dev032-fake-model")

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["failure_code"] == "output_validation"
    assert result["preserved_existing"] is True
    assert reason in result["error"]
    assert _stored(storage, conn, "p1", campaign_id) == before
    assert len(storage["calls"]) == calls_before


def test_unavailable_provider_reports_honestly_and_saves_nothing(
        tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage = _install_storage(monkeypatch)
    _install_router(monkeypatch, ScriptedProvider(
        error=RuntimeError("provider fixture failure")))
    _scoped_retrieval(monkeypatch)

    out = _graph(conn).invoke(
        _state("Create a marketing campaign with a social post."),
        config={"configurable": {"thread_id": "t1"}})

    result = out["write_results"]["deliverables"]
    assert result["ok"] is False
    assert "could not generate deliverables" in result["error"]
    assert result["preserved_existing"] is True
    assert storage["calls"] == []
    assert out["write_results"]["campaign"]["ok"] is True
    assert "Campaign draft created successfully." in out["final_answer"]
    assert "No campaign deliverables were saved." in out["final_answer"]
    assert "manually" in out["final_answer"]


def test_missing_router_reports_honestly_and_saves_nothing(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage = _install_storage(monkeypatch)
    monkeypatch.setattr(production, "_router", lambda: None)
    _scoped_retrieval(monkeypatch)

    out = _graph(conn).invoke(
        _state("Create a marketing campaign with a social post."),
        config={"configurable": {"thread_id": "t1"}})

    result = out["write_results"]["deliverables"]
    assert result["ok"] is False
    assert result["failure_code"] == "router_unavailable"
    assert "No AI provider is configured" in result["error"]
    assert storage["calls"] == []


def test_stale_revision_conflict_preserves_the_newer_version(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    storage, provider, campaign_id = _seed_one(conn, monkeypatch)
    target_id = _stored(storage, conn, "p1", campaign_id)[0]["id"]

    # Another editor lands version 7 between this revision's read and write.
    _live_row(storage, "p1", campaign_id, target_id)["current_version"] = 7
    from app.services import campaign_deliverables

    real_get = campaign_deliverables.get_deliverable

    def stale_get(conn_, project_id, camp_id, deliverable_id):
        row = dict(real_get(conn_, project_id, camp_id, deliverable_id))
        if deliverable_id == target_id:
            row["current_version"] = 1
        return row

    monkeypatch.setattr(campaign_deliverables, "get_deliverable", stale_get)
    provider.text = _social("Launch post (stale write)")

    result = production.revise_deliverable(
        conn, project_id="p1", campaign_id=campaign_id, turn_id="t2",
        deliverable_id=target_id, instruction="Rewrite the social post.",
        provider="LOCAL", model_id="dev032-fake-model")

    assert result["ok"] is False
    assert result["preserved_existing"] is True
    assert "could not be saved" in result["error"]
    assert _live_row(storage, "p1", campaign_id, target_id)["current_version"] == 7


def test_generation_never_publishes_or_spends(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _install_storage(monkeypatch)
    _install_router(monkeypatch, ScriptedProvider(text=_ad()))
    _scoped_retrieval(monkeypatch)

    out = _graph(conn).invoke(
        _state("Create a marketing campaign with an ad copy."),
        config={"configurable": {"thread_id": "t1"}})

    assert out["write_results"]["deliverables"]["ok"] is True
    assert out["write_results"].get("external_action") is None
    used = {row["tool_id"] for row in conn.execute(
        "SELECT DISTINCT tool_id FROM tool_runs WHERE project_id=?",
        ("p1",)).fetchall()}
    assert used == {"propose_campaign"}
    assert repos.Approvals.list(conn, project_id="p1") == []


def test_mixed_campaign_creation_blocks_external_action_without_calling_it(
        tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _install_storage(monkeypatch)
    provider = ScriptedProvider(text=_ad())
    _install_router(monkeypatch, provider)
    _scoped_retrieval(monkeypatch)

    out = _graph(conn).invoke(
        _state("Create a marketing campaign with an ad copy, then publish it "
               "on Meta and spend $100."),
        config={"configurable": {"thread_id": "t1"}})

    writes = out["write_results"]
    assert writes["campaign"]["ok"] is True
    assert writes["deliverables"]["ok"] is True
    assert writes["external_action"]["status"] == "blocked"
    assert writes["external_action"]["approval_required"] is True
    used = {row["tool_id"] for row in conn.execute(
        "SELECT DISTINCT tool_id FROM tool_runs WHERE project_id=?", ("p1",)
    ).fetchall()}
    assert used == {"propose_campaign"}
    assert "blocked" in out["final_answer"].lower()
    assert "not performed" in out["final_answer"].lower()
    assert "no spend occurred" in out["final_answer"].lower()
    assert "No content was published" in out["final_answer"]
