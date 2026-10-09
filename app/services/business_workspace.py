"""Project-scoped profile and brief persistence for DEV-031."""
import json
from datetime import datetime, timezone

FIELDS = ("business_name", "website", "industry", "description", "audience",
          "location", "offer", "differentiators")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_profile_fields(payload):
    """Validate and normalize only supplied, supported profile fields."""
    values = {}
    for field in FIELDS:
        if field not in payload:
            continue
        value = payload[field]
        if not isinstance(value, str) or len(value) > 4000:
            raise ValueError(f"{field} must be text up to 4000 characters")
        values[field] = value.strip()
    if "business_name" in values and not values["business_name"]:
        raise ValueError("business_name is required")
    return values


def get_profile(conn, project_id):
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if project is None:
        return None
    row = conn.execute("SELECT * FROM business_profiles WHERE project_id=?", (project_id,)).fetchone()
    if row is None:
        stamp = now()
        conn.execute("INSERT OR IGNORE INTO business_profiles(project_id,business_name,website,created_at,updated_at) VALUES(?,?,?,?,?)",
                     (project_id, project["name"], project["website"], stamp, stamp))
        conn.commit()
        row = conn.execute("SELECT * FROM business_profiles WHERE project_id=?", (project_id,)).fetchone()
    result = dict(row)
    result.update({"name": project["name"], "project_website": project["website"]})
    return result


def update_profile(conn, project_id, payload):
    row = get_profile(conn, project_id)
    if row is None:
        return None
    if payload.get("expected_revision") != row["profile_revision"]:
        raise RuntimeError("stale profile revision")
    values = validate_profile_fields(payload)
    if "business_name" in values:
        conn.execute("UPDATE projects SET name=?,updated_at=? WHERE id=?", (values["business_name"], now(), project_id))
    if "website" in values:
        conn.execute("UPDATE projects SET website=?,updated_at=? WHERE id=?", (values["website"], now(), project_id))
    assignments = ",".join(f"{k}=?" for k in values)
    if assignments:
        conn.execute(f"UPDATE business_profiles SET {assignments},profile_revision=profile_revision+1,updated_at=? WHERE project_id=? AND profile_revision=?",
                     (*values.values(), now(), project_id, row["profile_revision"]))
    else:
        conn.execute("UPDATE business_profiles SET profile_revision=profile_revision+1,updated_at=? WHERE project_id=? AND profile_revision=?",
                     (now(), project_id, row["profile_revision"]))
    if conn.execute("SELECT changes()").fetchone()[0] != 1:
        conn.rollback()
        raise RuntimeError("stale profile revision")
    conn.commit()
    return get_profile(conn, project_id)


def get_brief(conn, project_id):
    row = get_profile(conn, project_id)
    if row is None:
        return None
    return {k: row[k] for k in ("project_id", "brief_md", "sources_json", "generation_json", "brief_revision")}


def save_brief(conn, project_id, payload):
    row = get_profile(conn, project_id)
    if row is None:
        return None
    if payload.get("expected_revision") != row["brief_revision"]:
        raise RuntimeError("stale brief revision")
    brief = payload.get("brief_md")
    if not isinstance(brief, str) or len(brief) > 50000:
        raise ValueError("brief_md must be text up to 50000 characters")
    sources = payload.get("sources", json.loads(row["sources_json"] or "[]"))
    if not isinstance(sources, list):
        raise ValueError("sources must be a list")
    conn.execute("UPDATE business_profiles SET brief_md=?,sources_json=?,brief_revision=brief_revision+1,updated_at=? WHERE project_id=? AND brief_revision=?",
                 (brief, json.dumps(sources), now(), project_id, row["brief_revision"]))
    if conn.execute("SELECT changes()").fetchone()[0] != 1:
        conn.rollback()
        raise RuntimeError("stale brief revision")
    conn.commit()
    return get_brief(conn, project_id)
