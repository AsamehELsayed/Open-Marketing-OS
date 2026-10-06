"""Adapter registry: name → source file + parse + repo access.

Single place binding Markdown/JSON sources to tables, so routes, Settings
reimport, and conflict resolution all share one definition. File→DB only;
nothing here ever writes to source files.
"""
from app.database import repos
from app.database.identity import DEFAULT_PROJECT_ID
from app.services.adapters import parsers


def _parse_company_rows(root, conn=None):
    """Company rows whose id follows the workspace's *actual* project id.

    DEV-008-PUBLISH-GATE. The company row id used to be the hardcoded literal
    "njm". That had two consequences: a fresh install created a company row
    named after a real business, and a legacy install that reimported got a
    *second* company row ("starter") alongside its existing one.

    Resolving the id from the database instead fixes both. `app.services.state`
    imports this module, so the import has to be deferred to call time.
    """
    company_id = DEFAULT_PROJECT_ID
    if conn is not None:
        try:
            from app.services.state import active_project_id
            company_id = active_project_id(conn)
        except Exception:
            company_id = DEFAULT_PROJECT_ID
    return [parsers.parse_company(root / "company" / "company.yaml",
                                  company_id=company_id)]


ADAPTERS = {
    "weekly_plan": {
        "source": "production/weekly-plan.md",
        "parse": lambda root: parsers.parse_weekly_plan(root / "production" / "weekly-plan.md"),
        "upsert": repos.Tasks.upsert,
        "get": repos.Tasks.get,
    },
    "actions": {
        "source": "state/actions.json",
        "parse": lambda root: parsers.parse_actions(root / "state" / "actions.json"),
        "upsert": repos.Tasks.upsert,
        "get": repos.Tasks.get,
    },
    "approvals": {
        "source": "production/approval-queue.md",
        "parse": lambda root: parsers.parse_approval_queue(root / "production" / "approval-queue.md"),
        "upsert": repos.Approvals.upsert,
        "get": repos.Approvals.get,
    },
    "backlog": {
        "source": "strategy/opportunity-backlog.md",
        "parse": lambda root: parsers.parse_backlog(root / "strategy" / "opportunity-backlog.md"),
        "upsert": repos.Campaigns.upsert,
        "get": repos.Campaigns.get,
    },
    "experiments": {
        "source": "state/experiments.json",
        "parse": lambda root: parsers.parse_experiments(root / "state" / "experiments.json"),
        "upsert": repos.Experiments.upsert,
        "get": repos.Experiments.get,
    },
    "company": {
        "source": "company/company.yaml",
        "parse": lambda root, conn=None: _parse_company_rows(root, conn),
        # The company row id is derived from the database, not just the file, so
        # this adapter needs the connection. `run_imports` and `resolve_conflict`
        # read this flag rather than guessing from the signature.
        "wants_conn": True,
        "upsert": repos.Companies.upsert,
        "get": repos.Companies.get,
    },
}
