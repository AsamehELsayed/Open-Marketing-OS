"""Pure Markdown/JSON parsers. No DB, no invented fields — only captured text."""
import json
import re
from pathlib import Path

from app.database.identity import DEFAULT_PROJECT_ID

PRIORITY_RE = re.compile(r"^## Priority (\d+)\s*[—-]\s*(.+)$", re.MULTILINE)
KV_RE = re.compile(r"^-\s*([A-Za-z_/]+):\s*(.+)$", re.MULTILINE)
BACKLOG_RE = re.compile(r"^## (opp-\d+)\s*[—-]\s*(.+)$", re.MULTILINE)
APPROVAL_RE = re.compile(r"^## ID:\s*(\S+)\s*$", re.MULTILINE)


def _section(text: str, start: int, pattern: re.Pattern) -> str:
    m = pattern.search(text, start)
    end = m.start() if m else len(text)
    return text[start:end]


def parse_weekly_plan(path: str | Path) -> list[dict]:
    """Max-3 priorities → task rows. Lane left empty unless OWNER/LANE names a known lane."""
    text = Path(path).read_text(encoding="utf-8")
    tasks = []
    for m in PRIORITY_RE.finditer(text):
        body = _section(text, m.end(), PRIORITY_RE)
        lane = ""
        lane_m = re.search(r"OWNER/LANE:\s*([a-z\-+ /]+)", body)
        if lane_m:
            lane = lane_m.group(1).strip().split()[0].strip("+/,")
        tasks.append({
            "id": f"weekly-p{m.group(1)}",
            "campaign_id": None,
            "title": m.group(2).strip(),
            "lane": lane,
            "status": "open",
            "acceptance": "",
            "job_id": None,
            "due_at": None,
        })
    return tasks[:3]


def parse_approval_queue(path: str | Path) -> list[dict]:
    """## ID: blocks → approval rows; all bullets preserved in fields_json."""
    text = Path(path).read_text(encoding="utf-8")
    approvals = []
    matches = list(APPROVAL_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]
        fields = dict(KV_RE.findall(body))
        status = "pending"
        status_line = next((ln for ln in body.splitlines() if "STATUS:" in ln), "")
        if "APPROVED" in status_line:
            status = "approved"
        elif "RETURNED" in status_line:
            status = "returned"
        approvals.append({
            "id": m.group(1).strip(),
            "kind": fields.get("TYPE", ""),
            "title": m.group(1).strip(),
            "body_md": body.strip()[:4000],
            "status": status,
            "fields_json": json.dumps(fields),
            "decided_by": None,
            "decided_at": None,
        })
    return approvals


def parse_backlog(path: str | Path) -> list[dict]:
    """## opp-XX — title + `- key: value` lines → campaign rows."""
    text = Path(path).read_text(encoding="utf-8")
    campaigns = []
    matches = list(BACKLOG_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]
        kv = dict(KV_RE.findall(body))
        try:
            campaigns.append({
                "id": kv.get("id", m.group(1)).strip(),
                "title": kv.get("title", m.group(2)).strip(),
                "status": kv.get("status", "proposed").strip(),
                "impact": int(kv.get("impact", 0)),
                "confidence": int(kv.get("confidence", 0)),
                "effort": int(kv.get("effort", 0)),
                "cost": int(kv.get("cost", 0)),
                "approval_level": kv.get("approval_level", "Green").split("(")[0].strip(),
                "measurement_window": kv.get("measurement_window"),
                "result": kv.get("result"),
                "learning_ref": kv.get("learning"),
                "updated_at": "2026-09-16T00:00:00+00:00",
            })
        except ValueError:
            continue
    return campaigns


def parse_actions(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tasks = []
    for a in data.get("actions", []):
        tasks.append({
            "id": a.get("id", ""),
            "campaign_id": None,
            "title": a.get("actual", "")[:300],
            "lane": ",".join(a.get("lanes", [])),
            "status": "open" if a.get("status") == "READY_FOR_APPROVAL" else (a.get("status", "open").lower()),
            "acceptance": "",
            "job_id": None,
            "due_at": None,
        })
    return [t for t in tasks if t["id"]]


def parse_experiments(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    experiments = []
    for e in data.get("experiments", []):
        experiments.append({
            "id": e.get("id", ""),
            "campaign_id": None,
            "hypothesis": e.get("hypothesis", ""),
            "metric": e.get("metric", ""),
            "window_days": 0,
            "start_date": None if e.get("start_date") == "TBD (post-approval)" else e.get("start_date"),
            "next_review": None,
            "status": (e.get("status", "drafted").lower()),
            "stop_condition": e.get("stop_condition", ""),
        })
    return [x for x in experiments if x["id"]]


def parse_company(path: str | Path, company_id: str = DEFAULT_PROJECT_ID) -> dict:
    """Minimal `company.yaml` reader (no yaml dep): name/website/markets/languages/services.

    Handles the known shape — `company:` + `markets:` sections with one level
    of nesting and `- item` lists. Unknown keys are ignored, never invented.

    `company_id` is a parameter rather than a literal because the row id must
    follow the *workspace's* project id. A fresh install writes `starter`; an
    install that already holds a legacy project reimports into that same id
    rather than creating a second, parallel company row. `run_imports` passes
    the resolved active project id.
    """
    name, website = "", ""
    markets: dict[str, list[str]] = {"primary": [], "expansion": []}
    languages: list[str] = []
    services: list[str] = []
    section, subsection, target = "", "", ""

    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0 and line.endswith(":"):
            section, subsection, target = line[:-1], "", ""
            continue
        if indent == 2 and line.endswith(":") and section == "markets":
            subsection = line[:-1]
            continue
        if line.startswith("- "):
            item = line[2:].strip().strip("\"'")
            if section == "markets" and subsection in markets:
                markets[subsection].append(item)
            elif section == "languages":
                languages.append(item)
            elif section == "known_services":
                services.append(item)
            continue
        if ":" in line and indent == 2 and section == "company":
            key, _, value = line.partition(":")
            value = value.strip().strip("\"'")
            if key.strip() == "name":
                name = value
            elif key.strip() == "website":
                website = value
    return {
        "id": company_id,
        "name": name,
        "website": website,
        "markets_json": json.dumps(markets),
        "languages_json": json.dumps(languages),
        "services_json": json.dumps(services),
        "created_at": "2026-09-16T00:00:00+00:00",
    }
