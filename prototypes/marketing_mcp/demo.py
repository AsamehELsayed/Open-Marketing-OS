"""Run with python -m prototypes.marketing_mcp.demo; synthetic temporary state only."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID

from app.database import repos
from app.database.sqlite import connect
from app.services import campaign_deliverables as deliverables
from .host import DraftHost, canonical_json

PROJECT = "fixture-alpha"
CAMPAIGN = "fixture-campaign"
COPY = "A simple marketing workflow starts with a reviewed draft."


def public_evidence(evidence):
    """Summarize synthetic identifiers for committed evidence, after assertions."""
    output = json.loads(json.dumps(evidence))
    digest = output["payload"].pop("idempotency_key")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Expected a SHA-256 digest before evidence sanitization")
    draft = {k: output["payload"][k] for k in (
        "project_id", "campaign_id", "deliverable_id", "approved_version", "platform", "text")}
    computed = hashlib.sha256(canonical_json(draft).encode("utf-8")).hexdigest()
    if computed != digest:
        raise ValueError("Evidence digest does not match its canonical synthetic payload")
    output["payload"]["idempotency_digest"] = {
        "algorithm": "SHA-256", "hex_length": len(digest), "value_omitted": True,
        "matches_canonical_payload": True}
    for index, row in enumerate(output["tool_runs"], start=1):
        row["tool_run_id"] = f"synthetic-run-{index}"
    output["evidence_sanitization"] = "Synthetic digest omitted; operational run identifiers summarized"
    return output


def seed(conn):
    for project in (PROJECT, "fixture-beta"):
        repos.Projects.upsert(conn, {"id": project, "name": "Synthetic fixture",
            "created_at": "2026-01-01", "updated_at": "2026-01-01"})
    repos.Campaigns.upsert(conn, {"id": CAMPAIGN, "project_id": PROJECT,
        "title": "Synthetic campaign", "updated_at": "2026-01-01"})
    # Fixed fixture identities only. All editorial transitions use real services.
    # No UUID/time patch exists anywhere on the preparation/execution path.
    with patch("app.services.campaign_deliverables.uuid.uuid4",
               side_effect=[UUID(int=1), UUID(int=2), UUID(int=3)]):
        return deliverables.create_manual(conn, PROJECT, CAMPAIGN,
            {"type": "social_post", "title": "Reviewed fixture", "platform": "linkedin", "content_md": COPY})


def approve(conn, row):
    for status in ("IN_REVIEW", "APPROVED"):
        row = deliverables.transition(conn, PROJECT, CAMPAIGN, row["id"], row["current_version"], status)
    return row


def main():
    scratch = Path(__file__).with_name(".scratch")
    scratch.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="omos-offline-mcp-", dir=scratch) as temp:
        conn = connect(Path(temp) / "fixture.sqlite")
        try:
            row = seed(conn)
            host = DraftHost(conn, PROJECT)
            default_denied = host.prepare(CAMPAIGN, row["id"], row["current_version"])
            host.allow_fixture()
            unapproved_denied = host.prepare(CAMPAIGN, row["id"], row["current_version"])
            row = approve(conn, row)
            first = host.prepare(CAMPAIGN, row["id"], row["current_version"])
            second = host.prepare(CAMPAIGN, row["id"], row["current_version"])
            if not first["ok"] or first != second:
                raise RuntimeError("Offline proof failed")
            print(json.dumps(public_evidence({"sdk": "mcp==1.30.0", "default_denied": not default_denied["ok"],
                "unapproved_denied": not unapproved_denied["ok"], "deterministic": first == second,
                "payload": first["payload"], "protocol_exchanges": host.transport.exchanges,
                "active_sessions_after_calls": host.transport.active_sessions,
                "audit": host.gateway.audit_rows(),
                "tool_runs": [dict(r) for r in conn.execute("SELECT * FROM tool_runs")]}),
                sort_keys=True, ensure_ascii=False))
        finally:
            conn.close()


if __name__ == "__main__":
    main()
