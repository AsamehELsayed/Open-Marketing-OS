"""Presentation helpers: display labels + human activity feed.

Read-only mappings from internal state to business language. No business
rules live here — statuses, approvals, and decisions are owned by services.
"""
from datetime import datetime

from app.database import repos
from app.services import state as store

STATUS_LABELS = {
    "proposed": "Draft", "drafted": "Draft", "approved": "Ready",
    "executing": "Running", "measuring": "Measuring", "learned": "Completed",
    "rejected": "Closed", "blocked": "Blocked",
    "open": "Open", "in_progress": "In progress", "done": "Done",
    "pending": "Needs review", "returned": "Changes requested",
}

STATUS_BADGE = {
    "Draft": "draft", "Ready": "ready", "Running": "running",
    "Measuring": "measuring", "Completed": "completed", "Closed": "",
    "Blocked": "blocked", "Open": "", "In progress": "running",
    "Done": "completed", "Needs review": "pending", "Changes requested": "pending",
}


def label(status: str) -> str:
    return STATUS_LABELS.get(status, status.replace("_", " ").title())


def badge(status: str) -> str:
    return STATUS_BADGE.get(label(status), "")


def greeting(now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def focus_campaign(conn, project_id: str | None = None) -> dict | None:
    campaigns = repos.Campaigns.list(conn, project_id)
    for wanted in (("approved", "executing", "measuring"), ("drafted", "proposed")):
        for c in campaigns:
            if c["status"] in wanted:
                return c
    return campaigns[0] if campaigns else None


def prospect_count(conn, campaign_id: str | None, project_id: str | None = None) -> int:
    if not campaign_id:
        return 0
    return len(repos.Prospects.list(conn, campaign_id, project_id))


_EVENT_TEXT = {
    "approval_decision": lambda d: f"Approval {d.get('decision', '')}: {d.get('by', '')}.",
    "task_status": lambda d: "A task changed status.",
    "campaign_status": lambda d: "A campaign moved to a new stage.",
    "measurement": lambda d: f"Measurement decision recorded ({d.get('decision', '')}).",
    "job_completed": lambda d: "Background work finished.",
    "job_failed": lambda d: "Background work needs a look.",
    "conflict_resolved": lambda d: "A data conflict was resolved.",
    "reindex": lambda d: "Knowledge search was refreshed.",
    "import": lambda d: "Workspace files were synced.",
}


def recent_activity(conn, limit: int = 5) -> list[dict]:
    items = []
    for e in reversed(repos.Events.list(conn)):
        text = _EVENT_TEXT.get(e["kind"])
        if text is None:
            continue
        try:
            import json
            sentence = text(json.loads(e["detail_json"] or "{}"))
        except (ValueError, TypeError):
            sentence = text({})
        items.append({"when": (e["created_at"] or "")[:16].replace("T", " "), "text": sentence})
        if len(items) >= limit:
            break
    return items


def next_step(conn, dashboard: dict, project_id: str | None = None) -> dict:
    if dashboard["pending_approvals"]:
        return {"text": "Your marketing team is waiting on you. Review what's ready.",
                "cta": "Review approvals", "href": "/approvals"}
    if dashboard["conflicts"]:
        return {"text": "Your files and app disagree on something. Pick which one wins.",
                "cta": "Resolve conflicts", "href": "/settings"}
    if not repos.Campaigns.list(conn, project_id):
        return {"text": "No campaigns yet. Sync your workspace to get started.",
                "cta": "Open settings", "href": "/settings"}
    return {"text": "Nothing needs your attention.",
            "cta": "Ask Account Manager what to do next", "href": "/chat"}
