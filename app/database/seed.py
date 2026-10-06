"""Minimal idempotent seed. Full Markdown import lives in W3 adapters."""
import json
from datetime import datetime, timezone

from app.database import repos
from app.database.identity import DEFAULT_PROJECT_ID, company_id_for

#: Internal id of the default workspace.
#:
#: DEV-008-PUBLISH-GATE: this used to be the literal ``"njm"``, the initials of
#: the third-party business whose workspace this product was originally built
#: around, and it was written into the database of every fresh install. The
#: constant now lives in `app.database.identity` as ``"starter"`` and is shared
#: by the DDL, the migration path, the parser and every runtime fallback, so it
#: cannot drift between them.
#:
#: It is an *internal* id. It is never presented to the user as their business
#: name — the onboarding wizard writes their real business into this project.
#:
#: Existing installs are untouched: a database that already holds a legacy
#: project id keeps it, and `app.services.state.active_project_id` returns the
#: stored value rather than this default. See `app.database.identity` for the
#: full fresh-versus-historical split.
DEFAULT_PROJECT_ID = DEFAULT_PROJECT_ID
"""Re-exported so existing importers keep working from one canonical location."""

#: Neutral first-run placeholders.
#:
#: DEV-008: this seed previously hard-coded a real, named third-party business
#: (name, website, markets, services). In a source checkout that was merely
#: untidy, but a frozen build ships this database to every user who installs
#: OMOS, which would have embedded a real company's identity and commercial
#: details into every fresh install. The seed is therefore neutral, and the
#: first-run wizard writes the user's actual business via
#: `POST /api/onboarding/business`.
DEFAULT_COMPANY_NAME = "My Business"
DEFAULT_PROJECT_GOAL = "Get more customers"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _existing_project_ids(conn) -> list[str]:
    """Ids of every project already in the database, oldest first.

    Read defensively: `projects` is created by the DDL, but a seeder that raises
    on a partially restored database would turn a recoverable situation into a
    failed app start.
    """
    try:
        return [r["id"] for r in repos.Projects.list(conn) if r.get("id")]
    except Exception:
        return []


def ensure_seed(conn) -> dict[str, int]:
    """Insert-or-replace the single operator company + defaults. Returns counts.

    DEV-008-PUBLISH-GATE — the default workspace is created only on a genuinely
    empty database.

    Previously this unconditionally upserted the default company and then created
    the default project if it was absent. While the default id was the same
    literal the seed used to be a no-op on an existing install, so the phantom
    row never appeared. The moment the default id changed, "upsert the default
    company" plus "create the default project if missing" would have run on
    every pre-existing private install and **created a second, empty project**
    named "My Business" alongside the user's real one — on every app start.

    That is exactly the kind of silent data change a release gate must not ship,
    so the rule is now explicit: if the database already has projects, this
    function seeds nothing project-shaped and only fills in missing settings.
    Fresh installs and existing installs are therefore handled by one code path
    with no migration and nothing destroyed.
    """
    existing = _existing_project_ids(conn)
    fresh_install = not existing

    if fresh_install:
        repos.Companies.upsert(
            conn,
            {
                "id": DEFAULT_PROJECT_ID,
                "name": DEFAULT_COMPANY_NAME,
                "website": "",
                "markets_json": json.dumps({"primary": [], "expansion": []}),
                "languages_json": json.dumps([]),
                "services_json": json.dumps([]),
                "created_at": now_iso(),
            },
        )
        # v0.2: default project mirrors the seed company (idempotent).
        try:
            if repos.Projects.get(conn, DEFAULT_PROJECT_ID) is None:
                repos.Projects.upsert(conn, {
                    "id": DEFAULT_PROJECT_ID, "name": DEFAULT_COMPANY_NAME,
                    "website": "",
                    "goal": DEFAULT_PROJECT_GOAL,
                    "status": "active", "settings_json": json.dumps({}),
                    "created_at": now_iso(), "updated_at": now_iso(),
                })
        except Exception:
            pass

    if repos.Settings.get(conn, "active_project_id") is None:
        # Never invent a project here. If one exists, adopt it; if the database
        # is empty, the fresh-install branch above has just created it.
        repos.Settings.set(
            conn, "active_project_id",
            existing[0] if existing else DEFAULT_PROJECT_ID, now_iso(),
        )
    if repos.Settings.get(conn, "chroma_status") is None:
        repos.Settings.set(conn, "chroma_status", "unknown", now_iso())
    # DEV-003 W1: migrate legacy settings_json.instagram_handle once.
    # Any legacy handle becomes status=LIKELY, source=migration until
    # website-link evidence promotes it (migration rule, plan A).
    for project_id in ([DEFAULT_PROJECT_ID] if fresh_install else existing):
        try:
            proj = repos.Projects.get(conn, project_id)
            if proj is None:
                continue
            try:
                settings = json.loads(proj.get("settings_json") or "{}")
            except Exception:
                settings = {}
            legacy = (settings.get("instagram_handle") or "").strip()
            if not legacy:
                continue
            try:
                existing_ref = repos.SocialAccounts.get(
                    conn, f"{project_id}:instagram", project_id=project_id,
                )
            except ValueError:
                existing_ref = None
            if existing_ref is None:
                repos.SocialAccounts.set_handle(
                    conn, project_id, "instagram", legacy,
                    status="LIKELY", source="migration", evidence_url="",
                )
        except Exception:
            continue
    repos.Events.log(
        conn,
        {"id": f"seed-{now_iso()}", "kind": "seed", "ref_id": DEFAULT_PROJECT_ID,
         "detail_json": "{}", "created_at": now_iso()},
    )
    return {"companies": 1}
