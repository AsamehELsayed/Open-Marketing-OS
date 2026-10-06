"""DEV-008-SKILLS-OPS W6: the skills API returns measured counts and no paths.

What is actually asserted here
------------------------------
Every number on the wire is a count the loader **measured** on this request.
Nothing in this file compares against a literal library size: the skill count is
data, so a re-pin changes the numbers and none of these tests. What *is* pinned
is the property that makes the numbers trustworthy:

* ``MISSING`` and ``INVALID`` are 0 because the real tree is clean -- asserted
  from the response, and asserted equal to what an independent in-process
  ``load_registry()`` run measures, so the endpoint cannot be reporting a
  hardcoded zero.
* No absolute path reaches the wire. Checked on the raw response **bytes**, not
  on parsed fields, because a path could leak inside any string in the payload.
* An unavailable library is a ``503``, never a ``200`` with an empty list. A
  client cannot tell "no skills" from "the install is broken" if both answer 200.

All HTTP goes through ``TestClient(create_app())`` with a temp database, so the
toggle endpoint is exercised against real ``ConfigService`` writes and a real
loader re-read.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.main import create_app
from app.services.config_service import ConfigService
import app.services.config_service as config_module
from app.services.skills import SETTINGS_DISABLED_KEY, SkillRegistryUnavailable, load_registry

ROOT = Path(__file__).resolve().parents[2]

#: A Windows drive path. The lookbehind stops ``https:`` from matching, so a URL
#: in a trigger prompt is not a false positive -- which matters, because the
#: vendored eval prompts are full of them.
_DRIVE_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")

#: An absolute POSIX path: a rooted segment that is not a URL and not a
#: relative POSIX path we legitimately emit (``.agents/skills/...``).
_POSIX_ABS = re.compile(r'"(/(?:home|Users|var|tmp|opt|usr|root|mnt|srv|etc)/)')

#: A user-home or temp directory *in path position*. The bare words appear in
#: ordinary English inside the vendored eval prompts ("Users sign up but 60%
#: never complete setup"), so only a separator-delimited hit counts as a leak.
_HOME_IN_PATH = re.compile(r"(?:^|[\\/])(?:Users|home|AppData)(?:[\\/]|$)")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "skills_routes.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(deps, "DB_PATH", db)
    monkeypatch.setattr(deps, "ROOT", workspace)
    deps.init_db(db)
    config_module.clear_all_test_overrides()
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client
    config_module.clear_all_test_overrides()


def _data(response):
    """Unwrap the SPA envelope, and refuse anything that is not one."""
    payload = response.json()
    assert payload["ok"] is True, payload
    assert "data" in payload
    return payload["data"]


# ---- GET /api/skills -----------------------------------------------------


def test_get_skills_returns_200_with_the_measured_validation_counts(client):
    response = client.get("/api/skills")
    assert response.status_code == 200
    data = _data(response)

    assert data["missing"] == 0, f"the manifest promises skills that are absent: {data}"
    assert data["invalid"] == [], f"a skill failed to load: {data['invalid']}"
    assert "MISSING: 0" in data["summary"]
    assert "INVALID: 0" in data["summary"]

    # The counts must be internally consistent with the records actually sent.
    assert data["valid"] == len(data["skills"])
    assert data["valid"] == data["total"], (
        "a valid record set smaller than the directory count means something "
        "was dropped rather than reported"
    )
    assert data["total"] >= 1
    assert data["file_count"] >= len(data["skills"])


def test_the_counts_match_an_independent_loader_run(client):
    """The endpoint cannot be reporting a constant zero.

    The same loader is run in-process and the response is compared to it. If the
    route ever grew a hardcoded ``"missing": 0``, this fails the moment the tree
    disagrees -- and the tree is the only thing allowed to change.
    """
    data = _data(client.get("/api/skills"))
    report = load_registry().report
    assert data["total"] == report.total
    assert data["valid"] == report.valid_count
    assert data["missing"] == report.missing_count
    assert data["file_count"] == report.file_count
    assert data["library_checksum"] == report.library_checksum
    assert data["invalid"] == [{"skill_id": sid, "reason_code": reason}
                               for sid, reason in report.invalid]


def test_no_skill_count_literal_appears_in_the_route_source():
    """``== 50`` must not appear in product logic (plan §1.5.2)."""
    source = (ROOT / "app" / "routes" / "skills.py").read_text(encoding="utf-8")
    assert not re.search(r"(?:===|!==|==|!=)\s*\d{2,}\b", source), (
        "the skills route compares against a hardcoded count"
    )
    assert not re.search(r"\b(?:f?[\"'])\s*(?:TOTAL|VALID)\s*:\s*\d+", source)


def test_every_record_carries_the_frozen_fields(client):
    data = _data(client.get("/api/skills"))
    required = ("skill_id", "name", "description", "version", "source", "path",
                "triggers", "related_skills", "unresolved_related", "category",
                "checksum", "eval_count", "enabled", "validation_status")
    for record in data["skills"]:
        for key in required:
            assert key in record, f"{record.get('skill_id')} is missing {key}"
        assert record["validation_status"] == "valid"
        assert record["version"] and re.fullmatch(r"\d+\.\d+\.\d+", record["version"])
        assert record["category"] and record["checksum"]
        assert record["skill_id"] == record["name"]
    assert data["hash_scheme"]


def test_no_absolute_path_reaches_the_wire(client):
    """Checked on the raw bytes, because a path could hide in any string."""
    response = client.get("/api/skills")
    body = response.text

    drive = _DRIVE_PATH.search(body)
    assert drive is None, f"a drive-letter path leaked: {drive.group(0)!r}"
    posix = _POSIX_ABS.search(body)
    assert posix is None, f"an absolute POSIX path leaked: {posix.group(0)!r}"
    home = _HOME_IN_PATH.search(body)
    assert home is None, f"a home/temp directory leaked: {home.group(0)!r}"
    assert str(ROOT) not in body, "the repository root leaked"
    assert "\\\\Users\\\\" not in body and "/Users/" not in body

    data = _data(response)
    assert data["library_root"] == ".agents/skills", (
        "the library location is reported as a relative label on purpose"
    )
    for record in data["skills"]:
        path = record["path"]
        assert not path.startswith("/"), path
        assert not _DRIVE_PATH.search(path), path
        assert not _HOME_IN_PATH.search(path), path
        assert path == f".agents/skills/{record['skill_id']}"


def test_the_endpoint_never_exposes_the_absolute_root_even_on_failure(client, monkeypatch):
    """``SkillRegistryUnavailable`` text lists every root it tried. Do not ship it."""
    from app.services.skills import registry as skills_registry

    def unavailable(explicit=None):
        raise skills_registry.SkillRegistryUnavailable(
            "no marketing-skills library found; tried: "
            + ", ".join(str(ROOT / name) for name in ("a", "b")))

    monkeypatch.setattr("app.routes.skills.load_registry", unavailable)
    response = client.get("/api/skills")
    assert response.status_code == 503
    assert str(ROOT) not in response.text
    assert "SkillRegistryUnavailable" in response.text
    assert "a, b" not in response.text, "the candidate roots leaked into the detail"


# ---- GET /api/skills/manifest -------------------------------------------


def test_get_manifest_returns_the_recorded_pin(client):
    response = client.get("/api/skills/manifest")
    assert response.status_code == 200
    document = _data(response)
    assert document["schema"]
    assert document["hash_scheme"]
    assert document["upstream"]["repository"]
    assert document["upstream"]["version"]
    assert document["upstream"]["commit"]
    assert document["upstream"]["license"]
    assert document["library_checksum"]
    assert document["skills"]

    # The manifest is *read*, never re-derived: an endpoint that recomputed it
    # would make drift undetectable, which is the whole point of recording it.
    recorded = json.loads(
        (ROOT / "docs" / "marketing-skills-manifest.json").read_text(encoding="utf-8"))
    assert document == recorded


def test_manifest_also_leaks_no_absolute_path(client):
    body = client.get("/api/skills/manifest").text
    assert _DRIVE_PATH.search(body) is None
    assert _POSIX_ABS.search(body) is None
    assert _HOME_IN_PATH.search(body) is None
    assert str(ROOT) not in body
    assert "\\\\Users\\\\" not in body and "/Users/" not in body


def test_manifest_absent_is_a_404_not_an_empty_document(client, monkeypatch):
    monkeypatch.setattr("app.routes.skills.read_manifest", lambda *a, **k: None)
    response = client.get("/api/skills/manifest")
    assert response.status_code == 404
    assert "manifest" in response.json()["detail"].lower()


def test_manifest_absent_is_a_404(client, tmp_path, monkeypatch):
    """Proven against a real absent file, not only against a patched reader."""
    monkeypatch.setenv("OMOS_SKILLS_DIR", str(tmp_path / "no-such-library"))
    from app.services.skills import registry as skills_registry

    original = skills_registry._repo_root
    monkeypatch.setattr(skills_registry, "_repo_root", lambda: tmp_path)
    try:
        assert not (tmp_path / "docs" / "marketing-skills-manifest.json").is_file()
        assert client.get("/api/skills/manifest").status_code == 404
    finally:
        monkeypatch.setattr(skills_registry, "_repo_root", original)


# ---- POST /api/skills/{skill_id}/enabled --------------------------------


def _first_skill_id(client) -> str:
    return _data(client.get("/api/skills"))["skills"][0]["skill_id"]


def test_toggling_a_skill_off_is_visible_on_the_next_read(client):
    skill_id = _first_skill_id(client)
    assert _data(client.get("/api/skills"))["skills"][0]["enabled"] is True

    response = client.post(f"/api/skills/{skill_id}/enabled", json={"enabled": False})
    assert response.status_code == 200
    data = _data(response)
    assert data == {"skill_id": skill_id, "enabled": False, "status": "updated"}

    after = _data(client.get("/api/skills"))
    toggled = [r for r in after["skills"] if r["skill_id"] == skill_id]
    assert len(toggled) == 1
    assert toggled[0]["enabled"] is False
    assert [r["skill_id"] for r in after["skills"] if not r["enabled"]] == [skill_id]


def test_toggling_back_on_restores_the_record(client):
    skill_id = _first_skill_id(client)
    client.post(f"/api/skills/{skill_id}/enabled", json={"enabled": False})
    response = client.post(f"/api/skills/{skill_id}/enabled", json={"enabled": True})
    assert response.status_code == 200
    assert _data(response)["enabled"] is True
    assert all(r["enabled"] for r in _data(client.get("/api/skills"))["skills"])


def test_the_toggle_is_persisted_in_the_shared_setting(client, tmp_path):
    """One key, read by the loader -- not a private store the router cannot see."""
    skill_id = _first_skill_id(client)
    client.post(f"/api/skills/{skill_id}/enabled", json={"enabled": False})
    with deps.get_db() as conn:
        stored = ConfigService.get_setting(SETTINGS_DISABLED_KEY, "", conn=conn)
    assert stored == skill_id

    with deps.get_db() as conn:
        ConfigService.set_setting(SETTINGS_DISABLED_KEY, "", conn=conn)


def test_the_toggle_does_not_disturb_the_counts(client):
    """A disabled skill is still a *valid* skill: disabling is not invalidating."""
    skill_id = _first_skill_id(client)
    before = _data(client.get("/api/skills"))
    client.post(f"/api/skills/{skill_id}/enabled", json={"enabled": False})
    after = _data(client.get("/api/skills"))
    for key in ("total", "valid", "missing", "file_count", "library_checksum",
                "summary", "invalid"):
        assert after[key] == before[key], f"{key} changed when a skill was toggled"


def test_the_stored_value_is_a_function_of_the_set_not_the_click_order(client):
    """Two clients toggling the same skills must not leave two different rows."""
    first, second = [r["skill_id"] for r in _data(client.get("/api/skills"))["skills"][:2]]

    client.post(f"/api/skills/{first}/enabled", json={"enabled": False})
    client.post(f"/api/skills/{second}/enabled", json={"enabled": False})
    with deps.get_db() as conn:
        one = ConfigService.get_setting(SETTINGS_DISABLED_KEY, "", conn=conn)

    client.post(f"/api/skills/{first}/enabled", json={"enabled": True})
    client.post(f"/api/skills/{first}/enabled", json={"enabled": False})
    with deps.get_db() as conn:
        two = ConfigService.get_setting(SETTINGS_DISABLED_KEY, "", conn=conn)

    assert one == two == ",".join(sorted({first, second}))


def test_an_unknown_skill_is_a_404_and_writes_nothing(client):
    response = client.post("/api/skills/not-a-real-skill/enabled", json={"enabled": False})
    assert response.status_code == 404
    with deps.get_db() as conn:
        stored = ConfigService.get_setting(SETTINGS_DISABLED_KEY, "", conn=conn)
    assert stored in ("", None), "a refused toggle still wrote to the setting"


def test_a_malformed_toggle_is_refused_outright(client):
    """Fail closed: a body without a boolean ``enabled`` must not toggle anything."""
    skill_id = _first_skill_id(client)
    for body in ({}, {"enabled": None}, {"on": True}, {"enabled": "maybe"}):
        response = client.post(f"/api/skills/{skill_id}/enabled", json=body)
        assert response.status_code == 422, (body, response.status_code)
    with deps.get_db() as conn:
        assert ConfigService.get_setting(SETTINGS_DISABLED_KEY, "", conn=conn) in ("", None)
    assert all(r["enabled"] for r in _data(client.get("/api/skills"))["skills"])


def test_a_url_encoded_skill_id_resolves(client):
    """The client encodes the id; a slug must still round-trip."""
    data = _data(client.get("/api/skills"))
    slug = next(r["skill_id"] for r in data["skills"] if "-" in r["skill_id"])
    response = client.post(f"/api/skills/{slug}/enabled", json={"enabled": False})
    assert response.status_code == 200
    assert _data(response)["skill_id"] == slug


# ---- unavailable library -------------------------------------------------


def test_an_unavailable_library_is_a_503_not_an_empty_200(client, monkeypatch):
    """A broken install must not look like a legitimately empty library."""
    from app.services.skills import registry as skills_registry

    def unavailable(*args, **kwargs):
        raise skills_registry.SkillRegistryUnavailable("no library found")

    monkeypatch.setattr("app.routes.skills.load_registry", unavailable)
    response = client.get("/api/skills")
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()


def test_the_toggle_also_refuses_when_the_library_is_unavailable(client, monkeypatch):
    from app.services.skills import registry as skills_registry

    def unavailable(*args, **kwargs):
        raise skills_registry.SkillRegistryUnavailable("no library found")

    monkeypatch.setattr("app.routes.skills.load_registry", unavailable)
    assert client.post("/api/skills/anything/enabled",
                       json={"enabled": False}).status_code == 503


def test_the_unavailable_class_is_the_one_the_loader_raises():
    """Guards the import in the route against a rename that would 500 instead."""
    from app.services.skills.registry import SkillRegistryUnavailable as FromRegistry

    assert SkillRegistryUnavailable is FromRegistry


# ---- wiring --------------------------------------------------------------


def test_the_router_is_mounted_in_the_app_factory():
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "skills.router" in source
    assert "skills," in source, "app.routes.skills is never imported"


def test_the_three_endpoints_are_registered():
    """Read from the OpenAPI schema, which flattens nested routers.

    ``app.routes`` does not list included routers' endpoints on this FastAPI
    version (they stay as ``_IncludedRouter`` entries), so walking ``app.routes``
    would report the three routes as missing and quietly prove nothing.
    """
    schema = create_app().openapi()
    paths = schema["paths"]
    assert "/api/skills" in paths
    assert "/api/skills/manifest" in paths
    assert "/api/skills/{skill_id}/enabled" in paths
    assert set(paths["/api/skills"]) == {"get"}
    assert set(paths["/api/skills/manifest"]) == {"get"}
    assert set(paths["/api/skills/{skill_id}/enabled"]) == {"post"}


def test_the_three_endpoints_are_reachable_over_http(client):
    """The schema says they exist; only a request proves they are served."""
    assert client.get("/api/skills").status_code == 200
    assert client.get("/api/skills/manifest").status_code == 200
    skill_id = _first_skill_id(client)
    assert client.post(f"/api/skills/{skill_id}/enabled",
                       json={"enabled": False}).status_code == 200


def _code_identifiers(source: str) -> set[str]:
    """Every identifier and non-docstring string literal in ``source``.

    Docstrings are removed first: the module's own prose has to *name* the
    separation in order to assert it, so a text search would be a test that can
    only be satisfied by deleting the explanation.
    """
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


def test_the_route_module_never_imports_the_tool_registry():
    """Skills are knowledge, tools are action (plan §1.4.6). Prove the separation."""
    import ast

    source = (ROOT / "app" / "routes" / "skills.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    assert not [name for name in imported if name.startswith("app.services.tools")], (
        f"the skills route imported the tool registry: {sorted(imported)}"
    )
    # And it must not have borrowed a tool field for its own vocabulary.
    identifiers = _code_identifiers(source)
    assert "permission_level" not in identifiers
    assert "side_effect" not in identifiers
    assert "retry_policy" not in identifiers
