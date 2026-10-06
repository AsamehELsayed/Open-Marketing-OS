"""DEV-008 — distribution packaging contract.

Locks the guarantees a non-developer depends on:
- user data never lands in the read-only install folder,
- a fresh install has a parseable workspace (no FileNotFoundError),
- the friendly-error boundary is actually installed,
- the neutral first-run seed ships no real business identity,
- onboarding writes a company file the real parser can read back,
- the backend only ever binds loopback.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------- app.paths

def test_source_checkout_is_unchanged_by_packaging(monkeypatch):
    """A dev checkout must keep resolving the repository root.

    Guards against a packaging change silently relocating every developer's
    database out from under them.
    """
    from app import paths

    monkeypatch.delenv(paths.ENV_WORKSPACE, raising=False)
    monkeypatch.delenv(paths.ENV_FROZEN, raising=False)
    assert paths.is_frozen() is False
    assert paths.user_data_root() == REPO_ROOT
    assert paths.database_path() == REPO_ROOT / "data" / "marketing.db"


def test_frozen_build_redirects_every_mutable_path(monkeypatch, tmp_path):
    from app import paths

    monkeypatch.setenv(paths.ENV_FROZEN, "1")
    monkeypatch.delenv(paths.ENV_WORKSPACE, raising=False)
    monkeypatch.delenv(paths.ENV_CREDENTIALS, raising=False)
    monkeypatch.delenv(paths.ENV_LOGS, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert paths.is_frozen() is True
    root = paths.user_data_root()
    assert root == tmp_path / "OpenMarketingOS"
    assert root != REPO_ROOT
    assert paths.database_path() == root / "data" / "marketing.db"
    assert paths.credentials_dir() == root / "credentials"
    assert paths.logs_dir() == root / "logs"


def test_no_mutable_path_falls_back_into_the_bundle(monkeypatch, tmp_path):
    """Regression: a missing env var must not resolve into Program Files."""
    from app import paths

    monkeypatch.setenv(paths.ENV_FROZEN, "1")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for var in (paths.ENV_WORKSPACE, paths.ENV_CREDENTIALS, paths.ENV_LOGS,
                paths.ENV_BUNDLE):
        monkeypatch.delenv(var, raising=False)

    bundle = tmp_path / "Program Files" / "Open Marketing OS"
    bundle.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(bundle / "omos-backend.exe"))

    for path in (paths.user_data_root(), paths.credentials_dir(), paths.logs_dir()):
        assert bundle not in path.parents, f"{path} resolved inside the bundle"
        assert not path.is_relative_to(bundle), f"{path} resolved inside the bundle"


def test_ensure_writable_dirs_is_idempotent(monkeypatch, tmp_path):
    from app import paths

    monkeypatch.setenv(paths.ENV_FROZEN, "1")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv(paths.ENV_WORKSPACE, raising=False)
    monkeypatch.delenv(paths.ENV_CREDENTIALS, raising=False)
    monkeypatch.delenv(paths.ENV_LOGS, raising=False)

    paths.ensure_writable_dirs()
    paths.ensure_writable_dirs()  # must not raise
    assert (tmp_path / "OpenMarketingOS" / "data").is_dir()
    assert (tmp_path / "OpenMarketingOS" / "credentials").is_dir()
    assert (tmp_path / "OpenMarketingOS" / "logs").is_dir()


def test_credentials_dir_never_lands_in_a_readonly_install(monkeypatch, tmp_path):
    """The vault default must follow app.paths, not the legacy repo-relative path.

    Note the import shape: `app.services.credentials.__init__` rebinds the name
    `store` to the vault *function*, so `import ...store as x` would hand back
    the function. `importlib.import_module` returns the real backend module.
    """
    cred_store = importlib.import_module("app.services.credentials.store")

    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(tmp_path / "explicit"))
    assert cred_store.default_root() == tmp_path / "explicit"

    monkeypatch.delenv("OMOS_CREDENTIALS_DIR", raising=False)
    monkeypatch.setenv("OMOS_FROZEN", "1")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("OMOS_WORKSPACE_DIR", raising=False)
    assert cred_store.default_root() == tmp_path / "OpenMarketingOS" / "credentials"


# ------------------------------------------------------- workspace bootstrap

def test_fresh_workspace_is_fully_parseable(tmp_path):
    """Every adapter source must exist, or reimport raises FileNotFoundError.

    This is the exact crash a non-developer would hit on a brand-new install
    if the starter workspace were not laid down.
    """
    from app.services.workspace_bootstrap import ensure_workspace
    from app.services.adapters.registry import ADAPTERS
    from app.services.adapters import parsers

    created = ensure_workspace(tmp_path)
    assert created, "bootstrap must create the starter workspace"

    for name, spec in ADAPTERS.items():
        source = tmp_path / spec["source"]
        assert source.is_file(), f"{name}: missing {spec['source']}"
        spec["parse"](tmp_path)  # must not raise


def test_bootstrap_never_overwrites_user_edits(tmp_path):
    from app.services.workspace_bootstrap import ensure_workspace

    ensure_workspace(tmp_path)
    brand = tmp_path / "knowledge" / "brand.md"
    brand.write_text("# Brand\n\nMy own words.\n", encoding="utf-8")
    company = tmp_path / "company" / "company.yaml"
    company.write_text('company:\n  name: "Kept"\n', encoding="utf-8")

    second = ensure_workspace(tmp_path)
    assert second == []
    assert "My own words." in brand.read_text(encoding="utf-8")
    assert "Kept" in company.read_text(encoding="utf-8")


def test_bootstrap_repairs_a_single_deleted_file(tmp_path):
    from app.services.workspace_bootstrap import ensure_workspace

    ensure_workspace(tmp_path)
    (tmp_path / "state" / "experiments.json").unlink()
    created = ensure_workspace(tmp_path)
    assert created == ["state/experiments.json"]
    # Shape matters: the parsers do `data.get("experiments", [])`.
    assert json.loads(
        (tmp_path / "state" / "experiments.json").read_text(encoding="utf-8")
    ) == {"experiments": []}


def test_starter_files_contain_no_invented_business_facts():
    """Prompts and headings only. Fabricated 'facts' would poison every answer."""
    from app.services import workspace_bootstrap as wb

    for rel, content in wb._STARTER.items():
        if rel == "company/company.yaml":
            continue
        assert content.strip(), rel
        for banned in ("Acme Test Company", "acme_test", "Jordan", "Egypt"):
            assert banned not in content, f"{rel} leaks {banned}"


def test_rendered_company_yaml_round_trips_through_the_real_parser(tmp_path):
    from app.services.workspace_bootstrap import apply_business_details
    from app.services.adapters.parsers import parse_company

    target = apply_business_details(
        tmp_path,
        name="Blue Widgets: Ltd",
        website="https://blue.example",
        industry="Manufacturing",
        markets=["Ireland"],
        languages=["English"],
        services=["Widgets"],
    )
    row = parse_company(target)
    assert row["name"] == "Blue Widgets: Ltd"   # colon survived the round trip
    assert row["website"] == "https://blue.example"
    assert row["markets_json"] and "Ireland" in row["markets_json"]
    assert "Widgets" in row["services_json"]


# ------------------------------------------------------------ neutral seed

def test_shipped_seed_contains_no_real_business_identity():
    """A frozen build ships this database to every user who installs OMOS."""
    from app.database import seed

    assert seed.DEFAULT_COMPANY_NAME == "My Business"
    assert seed.DEFAULT_PROJECT_ID  # opaque key only
    blob = (REPO_ROOT / "app" / "database" / "seed.py").read_text(encoding="utf-8")
    for banned in ("acme_test", "@acme_test", "Acme Test Company"):
        assert banned not in blob, f"seed leaks {banned}"


def test_no_user_facing_prompt_names_a_real_business():
    """Regression gate for a leak the audit actually found.

    `_NEEDS_HANDLE` and `_NEEDS_URL` in `app/graphs/tool_capability.py` are shown
    verbatim to end users when a request is missing a handle or a URL. They
    used a real business's handle and domain as the example, so a fresh install
    greeted the user with a third party's identity. Test fixtures may use
    arbitrary strings; shipped prompt text may not name a real business.
    """
    from app.graphs import tool_capability

    for prompt in (tool_capability._NEEDS_HANDLE, tool_capability._NEEDS_URL):
        lowered = prompt.lower()
        for banned in ("acme_test", "starter solution"):
            assert banned not in lowered, f"user-facing prompt names a real business: {prompt}"
        # It must still be a usable example, not an empty placeholder.
        assert "@" in prompt or "https://" in prompt, prompt


def test_fresh_database_carries_no_named_business(tmp_path):
    from app.database.sqlite import connect
    from app.database.seed import ensure_seed
    from app.database import repos

    conn = connect(tmp_path / "fresh.db")
    ensure_seed(conn)
    company = repos.Companies.get(conn, "starter")
    project = repos.Projects.get(conn, "starter")
    assert company["name"] == "My Business"
    assert (company["website"] or "") == ""
    assert project["name"] == "My Business"
    assert (project["website"] or "") == ""
    conn.close()


# ------------------------------------------------------------- onboarding

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import deps, paths
    from app.main import create_app

    monkeypatch.setenv("OMOS_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(deps, "ROOT", tmp_path)
    monkeypatch.setattr(deps, "DB_PATH", tmp_path / "data" / "marketing.db")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    with TestClient(create_app()) as c:
        yield c


def _status(client):
    resp = client.get("/api/onboarding/status")
    assert resp.status_code == 200
    return resp.json()["data"]


def test_onboarding_status_reports_a_clean_first_run(client):
    data = _status(client)
    assert data["business_described"] is False
    assert data["ai"] == {"openrouter_connected": False, "openai_connected": False}
    assert data["starting_points"], "must offer actionable next steps"
    assert data["app_version"]


def test_onboarding_status_never_leaks_a_credential(client):
    body = client.get("/api/onboarding/status").text
    for banned in ("sk-", "sk-or-v1", "Bearer", "vault://"):
        assert banned not in body


def test_create_business_then_status_is_described(client, tmp_path):
    resp = client.post("/api/onboarding/business", json={
        "name": "Blue Widgets",
        "website": "blue.example",          # user types a bare host
        "industry": "Manufacturing",
        "goal": "Win more retail accounts",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["project"]["name"] == "Blue Widgets"
    assert body["project"]["website"] == "https://blue.example"

    data = _status(client)
    assert data["business_described"] is True
    assert data["project"]["name"] == "Blue Widgets"
    assert data["project"]["website"] == "https://blue.example"


def test_create_business_lands_in_the_real_workspace_file(client, tmp_path):
    client.post("/api/onboarding/business", json={"name": "Blue Widgets"})
    written = (tmp_path / "company" / "company.yaml").read_text(encoding="utf-8")
    assert "Blue Widgets" in written


def test_create_business_requires_a_name(client):
    resp = client.post("/api/onboarding/business", json={"name": "   "})
    assert resp.status_code == 400
    assert "name" in resp.json()["detail"].lower()


def test_onboarding_rejects_an_absurdly_long_name(client):
    resp = client.post("/api/onboarding/business", json={"name": "x" * 500})
    assert resp.status_code == 422


def test_reimport_works_after_onboarding(client):
    """The real failure this bootstrap exists to prevent."""
    client.post("/api/onboarding/business", json={"name": "Blue Widgets"})
    resp = client.post("/api/settings/workspace/reimport", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "synced"


def test_reindex_works_on_a_fresh_workspace(client):
    """An empty workspace cannot claim HYBRID without complete model vectors."""
    from app import deps
    from app.database import repos

    resp = client.post("/api/settings/workspace/reindex", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "empty"

    with deps.get_db() as conn:
        mode = repos.Settings.get(conn, "chroma_status")
    recorded = (mode or {}).get("value")
    assert recorded == "fts_only", (
        "an empty workspace without validated model-backed vector coverage must "
        f"remain FTS-only, but it recorded {recorded!r}"
    )


# ------------------------------------------------------ serving guarantees

def test_backend_binds_loopback_only():
    """Never expose OMOS to the LAN. Guards app.py and the launcher."""
    for rel in ("app.py", "launcher/omos_launcher.py"):
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "0.0.0.0" not in text, f"{rel} must not bind all interfaces"
        assert "127.0.0.1" in text, f"{rel} must bind loopback explicitly"


def test_health_endpoint_reports_the_packaged_version():
    from app import paths
    from app.main import create_app

    with TestClient(create_app()) as c:
        payload = c.get("/health").json()
    assert payload["status"] == "ok"
    assert payload["version"] == paths.APP_VERSION


def test_friendly_error_boundary_is_installed():
    """A raw traceback reaching a user is a defect, so probe it directly."""
    from app.contracts.events import safe_error_message

    leaky = "Traceback (most recent call last): File \"app/db.py\" SELECT * FROM users"
    shown = safe_error_message(ValueError(leaky), "Something went wrong.")
    assert "Traceback" not in shown
    assert "SELECT" not in shown
    assert shown


def test_no_env_var_can_supply_a_provider_secret(tmp_path, monkeypatch):
    """Regression guard for the Quick edition's credential path.

    Cloud keys must come from the DPAPI vault only. If any of these env vars
    ever started working again, a user could end up shipping a key in a
    shortcut, a CI log or a bug report.

    Fully isolated: a temp database *and* a temp vault, so a credential left
    over on the developer's own machine can never mask a regression.
    """
    from app import deps
    from app.services.config_service import ConfigService
    from app.tests.test_dev007rfinal_config import MemoryBackend

    cred_store = importlib.import_module("app.services.credentials.store")
    monkeypatch.setenv("OMOS_CREDENTIALS_DIR", str(tmp_path / "vault"))
    monkeypatch.setattr(deps, "DB_PATH", tmp_path / "empty.db")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    deps.init_db(tmp_path / "empty.db")
    cred_store.set_default_backend(MemoryBackend())
    try:
        for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "APIFY_API_TOKEN"):
            monkeypatch.setenv(name, "should-be-ignored")
        assert ConfigService.is_openai_configured() is False
        assert ConfigService.get_openai_api_key() == ""
        assert ConfigService.is_openrouter_configured() is False
        assert ConfigService.get_openrouter_api_key() == ""
        assert ConfigService.is_apify_configured() is False
        assert ConfigService.get_apify_token() == ""
    finally:
        cred_store.set_default_backend(None)
