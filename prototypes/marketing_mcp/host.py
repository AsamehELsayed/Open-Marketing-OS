"""Bounded composition of OMOS controls; never loaded by the production app."""
import hashlib
import json

from app.services import campaign_deliverables as deliverables
from app.services.mcp import McpGateway
from app.services.tools.registry import Registry, ToolRecord, ensure_tool_runs
from .transport import ENDPOINT, TOOL, FixtureTransport

SERVER_ID = "offline-marketing-fixture"


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class DraftHost:
    def __init__(self, conn, project_id):
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("Trusted project identity is required")
        self.conn = conn
        self.project_id = project_id
        self.transport = FixtureTransport()
        self.gateway = McpGateway(transport=self.transport)
        self.gateway.register_mcp_server(SERVER_ID, ENDPOINT, project_id, None)
        self.tools = self.gateway.discover_mcp_tools(SERVER_ID)
        ensure_tool_runs(conn)
        self.registry = self._registry(enabled=False)
        self._payload = None

    def _registry(self, *, enabled):
        registry = Registry()
        registry.register_tool(ToolRecord(tool_id=TOOL, source_type="mcp",
            description="Prepare an approved offline campaign draft",
            parameters={"type": "object", "required": ["campaign_id", "deliverable_id", "expected_version"]},
            side_effect="none", permission_level="yellow", cost_type="free",
            retry_policy="none", timeout_s=30, enabled=enabled))
        registry.bind_handler(TOOL, self._execute)
        return registry

    def allow_fixture(self):
        """Explicit host choice for this fixture/project only; discovery never does this."""
        self.gateway.set_mcp_tool_allowlist(SERVER_ID, self.project_id, [TOOL])
        self.registry = self._registry(enabled=True)

    def _approved(self, project_id, args):
        if project_id != self.project_id or set(args) != {"campaign_id", "deliverable_id", "expected_version"}:
            raise ValueError("Invalid scoped request")
        version = args["expected_version"]
        if type(version) is not int or version < 1:
            raise ValueError("Invalid expected version")
        row = deliverables.get_deliverable(self.conn, project_id, args["campaign_id"], args["deliverable_id"])
        if row["status"] != "APPROVED" or row["current_version"] != version:
            raise ValueError("The exact current approved version is required")
        snapshots = deliverables.history(self.conn, project_id, args["campaign_id"], args["deliverable_id"])
        fields = ("content_md", "platform", "type", "title")
        if not any(r["version"] == version and r["status"] == "APPROVED"
                   and all(r[k] == row[k] for k in fields) for r in snapshots):
            raise ValueError("Missing matching approved revision")
        if row["type"] != "social_post" or row["platform"] != "linkedin":
            raise ValueError("Only LinkedIn social posts are supported by this fixture")
        valid, _, parts = deliverables.validate_final_copy(row["content_md"],
            deliverable_type=row["type"], platform=row["platform"])
        if not valid:
            raise ValueError("Invalid final copy")
        return {"project_id": project_id, "campaign_id": row["campaign_id"],
                "deliverable_id": row["id"], "approved_version": version,
                "platform": row["platform"], "text": parts["final_copy"]}

    def _execute(self, conn, *, project_id, root, args):
        if conn is not self.conn:
            raise ValueError("Unexpected workspace connection")
        # Revalidate inside the actual registered handler: an arbitrary nonempty
        # Registry approval_id cannot authorize unapproved or stale content.
        draft = self._approved(project_id, args)
        self.gateway.grant_approval(SERVER_ID, project_id, TOOL)
        try:
            result = self.gateway.execute_mcp_tool(SERVER_ID, project_id, TOOL, {"draft": draft})
        finally:
            self.gateway.revoke_approval(SERVER_ID, project_id, TOOL)
        digest = hashlib.sha256(canonical_json(draft).encode("utf-8")).hexdigest()
        expected = {**draft, "status": "draft", "publish": False, "idempotency_key": digest}
        if not result.get("ok") or canonical_json(result.get("payload")) != canonical_json(expected):
            raise ValueError("Invalid formatter result")
        # Detect changes during the round trip before releasing an artifact.
        if self._approved(project_id, args) != draft:
            raise ValueError("Approved content changed during preparation")
        self._payload = expected
        # Raw content never goes through registry telemetry/event metadata.
        return {"ok": True, "digest": digest}

    def prepare(self, campaign_id, deliverable_id, expected_version):
        args = dict(campaign_id=campaign_id, deliverable_id=deliverable_id, expected_version=expected_version)
        self._payload = None
        approval = None
        try:
            draft = self._approved(self.project_id, args)
            approval = hashlib.sha256(canonical_json(draft).encode("utf-8")).hexdigest()
        except (ValueError, LookupError):
            pass  # Still record the refusal through the real registry gate.
        result = self.registry.execute(self.conn, project_id=self.project_id, root=None,
            name=TOOL, args=args, approval_id=approval)
        if not result.get("ok") or self._payload is None:
            return {"ok": False, "status": result.get("status", "failed")}
        return {"ok": True, "payload": self._payload}
