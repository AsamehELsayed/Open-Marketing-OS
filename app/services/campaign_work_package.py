"""Owner-readable campaign work package (read-only view over existing data).

Assembles one campaign's goal, audience, offer, channels, deliverables,
approval state and evidence into the shape a small business owner reviews.
No new tables and no generation: everything here is read from the existing
campaign intake, business profile and deliverable/revision records, and each
fact is labelled by where it came from so recommendations are never shown as
established market facts.

Evidence classes (never merged):
  user_provided   - what the owner told us (profile, goal)
  own_documents   - passages retrieved from the owner's uploaded knowledge
  web_sources     - sources stored by real research runs (research_evidence);
                    each is a search lead or an inspected page, never merged
  ai_suggested    - deliverables written by the AI, with their review status
  missing         - things the owner still needs to supply
"""
from __future__ import annotations

import json

from app.database import repos
from app.services import campaign_deliverables as deliverables
from app.services import research_evidence
from app.services.business_workspace import get_profile

_PROFILE_PROMPTS = (
    ("audience", "Who are your customers?"),
    ("offer", "What exactly are you offering?"),
    ("differentiators", "What makes you different from alternatives?"),
    ("location", "Where do your customers find you?"),
)
_TYPE_LABELS = {
    "strategy_brief": "Strategy brief", "social_post": "Social post",
    "ad_copy": "Ad copy", "creative_brief": "Creative brief",
    "content_calendar": "Content calendar",
}
_STATUS_LABELS = {"DRAFT": "Draft - not reviewed", "IN_REVIEW": "Waiting for your review",
                  "APPROVED": "Approved"}


def _intake(campaign: dict) -> dict:
    try:
        wf = json.loads(campaign.get("workflow_json") or "{}")
    except (TypeError, ValueError):
        return {}
    intake = wf.get("campaign_intake") if isinstance(wf, dict) else None
    return intake if isinstance(intake, dict) else {}


def _text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _provenance(revision: dict) -> dict:
    try:
        data = json.loads(revision.get("provenance_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def research_status() -> dict:
    """Whether real web research can run on this installation right now."""
    from app.services.tools.web_tools import search_configured
    if search_configured():
        return {"state": "available", "message":
                "Web research is connected. Sources are listed only when a "
                "real search returned them."}
    return {"state": "unavailable", "message":
            "Web research is not connected, so nothing here was looked up "
            "online. Suggestions below are ideas to check, not researched "
            "facts. You can add your own market knowledge to your business "
            "profile, or connect a web search in Settings."}


def build(conn, project_id: str, campaign_id: str) -> dict:
    campaign = deliverables._require_scope(conn, project_id, campaign_id)
    profile = get_profile(conn, project_id) or {}
    project = repos.Projects.get(conn, project_id) or {}
    intake = _intake(campaign)

    goal = _text(intake.get("objective"), 500) or _text(project.get("goal"), 500)
    audience = _text(intake.get("target_audience"), 400) or _text(profile.get("audience"), 400)
    channels = [c.strip()[:80] for c in (intake.get("channels") or [])
                if isinstance(c, str) and c.strip()][:12]
    duration = _text(intake.get("duration"), 100)
    offer = _text(profile.get("offer"), 400)

    rows = deliverables.list_deliverables(conn, project_id, campaign_id)
    items, own_docs, web_sources = [], [], []
    seen_docs: set[str] = set()
    for row in rows:
        revs = repos.CampaignDeliverables.history(
            conn, project_id, campaign_id, row["id"])
        ai_written = any(r.get("operation") == "GENERATED" for r in revs)
        for rev in revs:
            if rev.get("operation") != "GENERATED":
                continue
            prov = _provenance(rev)
            for ev in prov.get("evidence") or []:
                if not isinstance(ev, dict):
                    continue
                label = _text(ev.get("file_name") or ev.get("path"), 200)
                if label and label not in seen_docs:
                    seen_docs.add(label)
                    own_docs.append({"label": label,
                                     "excerpt": _text(ev.get("text"), 300)})
        items.append({
            "id": row["id"], "type": row["type"],
            "type_label": _TYPE_LABELS.get(row["type"], row["type"]),
            "title": row["title"], "platform": row.get("platform"),
            "status": row["status"],
            "status_label": _STATUS_LABELS.get(row["status"], row["status"]),
            "version": row["current_version"],
            "versions_kept": len(revs), "written_by_ai": ai_written,
        })

    for src in research_evidence.list_sources(conn, project_id, campaign_id):
        web_sources.append({
            "url": src["url"], "title": src["title"], "snippet": src["snippet"][:300],
            "retrieved_at": src["retrieved_at"], "found_by": src["provider"],
            "scope": src["scope"],
            "verification": src["verification"],
            "verification_label": ("Page opened and read by OMOS"
                                   if src["verification"] == "inspected"
                                   else "Search result only - page not checked"),
        })

    counts = {"total": len(items),
              "approved": sum(1 for i in items if i["status"] == "APPROVED"),
              "in_review": sum(1 for i in items if i["status"] == "IN_REVIEW"),
              "draft": sum(1 for i in items if i["status"] == "DRAFT")}
    needs_owner = []
    if not goal:
        needs_owner.append({"field": "goal", "question": "What do you want this campaign to achieve?"})
    if not audience:
        needs_owner.append({"field": "audience", "question": "Who is this campaign for?"})
    if not channels:
        needs_owner.append({"field": "channels", "question": "Where will you post or advertise?"})
    for field, question in _PROFILE_PROMPTS:
        if field not in ("audience",) and not _text(profile.get(field), 400):
            needs_owner.append({"field": field, "question": question})
    if counts["draft"] or counts["in_review"]:
        needs_owner.append({"field": "review", "question":
                            f"Review {counts['draft'] + counts['in_review']} deliverable(s) "
                            "and approve or ask for changes."})

    user_provided = {k: v for k, v in (
        ("goal", goal), ("audience", audience), ("offer", offer),
        ("channels", ", ".join(channels)), ("duration", duration),
        ("business", _text(profile.get("description"), 400)),
        ("what makes you different", _text(profile.get("differentiators"), 400)),
        ("location", _text(profile.get("location"), 200))) if v}
    return {
        "campaign": {"id": campaign["id"], "title": campaign.get("title", ""),
                     "status": campaign.get("status", "")},
        "goal": goal, "target_audience": audience, "offer": offer,
        "channels": channels, "duration": duration,
        "deliverables": items,
        "approval": {**counts, "ready_to_export": bool(counts["total"]) and
                     counts["approved"] == counts["total"]},
        "evidence": {
            "user_provided": user_provided,
            "own_documents": own_docs[:10],
            "web_sources": web_sources[:10],
            "ai_suggested": [i["title"] for i in items if i["written_by_ai"]][:20],
            "research": research_status(),
        },
        "needs_owner_input": needs_owner,
    }
