"""Persisted operation binding over existing OMOS approvals, not a new approval engine.

Only a trusted local composition root may construct/enable this host. Never expose
enable(), transport.arm(), or approval decisions as model-callable tools.
"""
import json
import re
import uuid

from app.database import repos
from app.services import campaign_deliverables as deliverables
from app.services.mcp import McpGateway
from app.services.tools.registry import Registry, ToolRecord, ensure_tool_runs
from prototypes.marketing_mcp.host import DraftHost as EditorialHost
from .contract import WRITE, READBACK, WINDOW, DATE, canonical, digest, payload_for

LOCAL_TOOL = 'postiz_create_approved_draft'
SERVER = 'postiz-dev051-local'
KIND = 'postiz_exact_draft'


class ControlledDraftHost:
    # Reuse the PR19 exact-scope/revision/history/final-copy validator unchanged.
    _approved = EditorialHost._approved

    def __init__(self, conn, transport):
        if conn.in_transaction:
            raise ValueError('Committed dedicated connection required')
        self.conn, self.transport = conn, transport
        self.binding = transport.binding
        self.project_id = self.binding.project
        self.enabled = False
        ensure_tool_runs(conn)
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS postiz_draft_operations (
                operation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                request_json TEXT NOT NULL, payload_json TEXT NOT NULL,
                approval_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
                baseline_json TEXT, post_id TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS postiz_draft_audit (
                seq INTEGER PRIMARY KEY, project_id TEXT NOT NULL,
                operation_id TEXT NOT NULL, event TEXT NOT NULL, at TEXT NOT NULL
            );
        ''')
        self.gateway = McpGateway(transport=transport, vault=transport)
        self.gateway.register_mcp_server(SERVER, self.binding.endpoint,
            self.project_id, self.binding.secret_ref)
        self.registry = self._registry(False)

    def _registry(self, enabled):
        registry = Registry()
        registry.register_tool(ToolRecord(tool_id=LOCAL_TOOL, source_type='mcp',
            description='Create one separately approved Postiz draft',
            parameters={'type': 'object', 'required': ['operation_id', 'approval_id']},
            side_effect='write', auth_required=True, credential_scope='project',
            permission_level='yellow', cost_type='free', retry_policy='none',
            timeout_s=15, enabled=enabled))
        registry.bind_handler(LOCAL_TOOL, self._execute)
        return registry

    def enable(self):
        # Discovery alone NEVER enables; unchanged read-only host has its own policy.
        self.enabled = False
        self.transport.enabled = False
        self.registry = self._registry(False)
        self.gateway.set_mcp_tool_allowlist(SERVER, self.project_id, [])
        self.gateway.discover_mcp_tools(SERVER)
        self.gateway.set_mcp_tool_allowlist(SERVER, self.project_id, [WRITE, READBACK])
        self.transport.enabled = True
        self.registry = self._registry(True)
        self.enabled = True

    def _audit(self, operation, event):
        self.conn.execute('INSERT INTO postiz_draft_audit(project_id,operation_id,event,at) VALUES(?,?,?,?)',
            (self.project_id, operation, event, deliverables.now()))

    def _operation(self, operation):
        self.conn.row_factory = __import__('sqlite3').Row
        row = self.conn.execute('SELECT * FROM postiz_draft_operations WHERE operation_id=? AND project_id=?',
            (operation, self.project_id)).fetchone()
        if row is None:
            raise ValueError('Operation unavailable')
        return dict(row)

    def _exact(self, request):
        draft = self._approved(self.project_id, request)
        payload = payload_for(draft['text'], self.transport.channel)
        authority = {'project': self.project_id, 'organization': self.binding.organization,
            'endpoint': self.binding.endpoint, 'secret_ref': self.binding.secret_ref,
            'channel': self.transport.channel, 'operation': WRITE, 'draft': draft, 'payload': payload}
        return digest(authority), payload

    def prepare(self, campaign_id, deliverable_id, expected_version):
        """Local only. Caller reviews exact payload before the separate decision."""
        if self.conn.in_transaction:
            raise ValueError('Committed dedicated connection required')
        request = dict(campaign_id=campaign_id, deliverable_id=deliverable_id,
            expected_version=expected_version)
        operation, payload = self._exact(request)
        with deliverables._atomic(self.conn):
            row = self.conn.execute('SELECT approval_id FROM postiz_draft_operations WHERE operation_id=?',
                (operation,)).fetchone()
            if row is None:
                approval = uuid.uuid4().hex
                repos._insert(self.conn, repos.Approvals.TABLE, {'id': approval, 'project_id': self.project_id,
                    'kind': KIND, 'title': 'Create one text-only Postiz draft',
                    'body_md': 'Draft only. No publishing. Exact payload:\n\n' + canonical(payload),
                    'status': 'pending', 'fields_json': canonical({'operation_id': operation}),
                    'updated_at': deliverables.now()}, commit=False)
                self.conn.execute('INSERT INTO postiz_draft_operations VALUES(?,?,?,?,?,?,?,?,?)',
                    (operation, self.project_id, canonical(request), canonical(payload), approval,
                     'prepared', None, None, deliverables.now()))
                self._audit(operation, 'prepared')
        # Return private content only to the local review surface, never Registry telemetry.
        result = self.status(operation)
        result['payload'] = payload
        return result

    def status(self, operation):
        row = self._operation(operation)
        approval = repos.Approvals.get(self.conn, row['approval_id'], self.project_id)
        return {'operation_id': operation, 'approval_id': row['approval_id'],
            'approval_status': approval['status'] if approval else 'missing',
            'state': row['state'], 'post_id': row['post_id'],
            'organization': self.binding.organization, 'channel': self.transport.channel}

    def _remote(self, name, payload):
        self.transport.arm(name, payload)
        self.gateway.grant_approval(SERVER, self.project_id, name)
        try:
            result = self.gateway.execute_mcp_tool(SERVER, self.project_id, name, payload)
            if not result.get('ok'):
                raise RuntimeError('Postiz outcome uncertain')
            return result
        finally:
            self.transport.disarm()
            self.gateway.revoke_approval(SERVER, self.project_id, name)

    def execute(self, operation_id, approval_id, *, project_id=None):
        if self.conn.in_transaction:
            raise ValueError('Committed dedicated connection required')
        project = self.project_id if project_id is None else project_id
        result = self.registry.execute(self.conn, project_id=project, root=None, name=LOCAL_TOOL,
            args={'operation_id': operation_id, 'approval_id': approval_id}, approval_id=approval_id)
        # Do not leak internal Registry error strings or argument values.
        return {k: result[k] for k in ('ok', 'state', 'post_id') if k in result}

    def _execute(self, conn, *, project_id, root, args):
        if (conn is not self.conn or conn.in_transaction or project_id != self.project_id or not self.enabled
                or set(args) != {'operation_id', 'approval_id'}):
            return {'ok': False, 'state': 'denied'}
        operation = args['operation_id']
        if not isinstance(operation, str) or not re.fullmatch('[0-9a-f]{64}', operation):
            with deliverables._atomic(conn):
                self._audit('', 'denied_or_uncertain')
            return {'ok': False, 'state': 'denied'}
        try:
            with deliverables._atomic(conn):
                row = self._operation(operation)
                if args['approval_id'] != row['approval_id']:
                    raise ValueError('Approval denied')
                if row['state'] != 'prepared':
                    # Durable terminal/in-flight states never dispatch again, even after restart.
                    self._audit(operation, 'replay_blocked')
                    return {'ok': row['state'] == 'succeeded', 'state': row['state'], 'post_id': row['post_id']}
                exact, payload = self._exact(json.loads(row['request_json']))
                approval = repos.Approvals.get(conn, row['approval_id'], self.project_id)
                if (exact != operation or canonical(payload) != row['payload_json'] or not approval
                        or approval['kind'] != KIND or approval['status'] != 'approved'
                        or not approval['decided_by'] or not approval['decided_at']
                        or approval['body_md'] != 'Draft only. No publishing. Exact payload:\n\n' + canonical(payload)
                        or approval['fields_json'] != canonical({'operation_id': operation})):
                    raise ValueError('Approval denied')
                conn.execute("UPDATE postiz_draft_operations SET state='in_flight',updated_at=? WHERE operation_id=?",
                    (deliverables.now(), operation))
                self._audit(operation, 'approval_consumed')
            # In-flight is COMMITTED before any transport; outer transaction is forbidden.
            before = self._remote(READBACK, WINDOW)['posts']
            baseline = [p['id'] for p in before]
            with deliverables._atomic(conn):
                conn.execute('UPDATE postiz_draft_operations SET baseline_json=? WHERE operation_id=?',
                    (canonical(baseline), operation))
                # Revalidate after preflight and immediately before dispatch.
                if self._exact(json.loads(row['request_json'])) != (operation, payload):
                    raise ValueError('Stale revision')
            result = self._remote(WRITE, payload)
            post_id = result['post_id']
            late = False
            try:
                late = self._exact(json.loads(row['request_json'])) != (operation, payload)
            except (ValueError, LookupError):
                late = True
            outcome = 'succeeded_stale' if late else 'succeeded'
            with deliverables._atomic(conn):
                conn.execute('UPDATE postiz_draft_operations SET state=?,post_id=?,updated_at=? WHERE operation_id=?',
                    (outcome, post_id, deliverables.now(), operation))
                self._audit(operation, outcome)
            return {'ok': not late, 'state': outcome, 'post_id': post_id}
        except Exception:
            # Never echo SDK/server bodies, credentials, campaign content, or arbitrary IDs.
            with deliverables._atomic(conn):
                conn.execute("UPDATE postiz_draft_operations SET state='ambiguous',updated_at=? WHERE operation_id=? AND project_id=? AND state='in_flight'",
                    (deliverables.now(), operation, self.project_id))
                self._audit(operation, 'denied_or_uncertain')
            return {'ok': False, 'state': 'denied_or_uncertain'}

    def reconcile(self, operation):
        """Read-only candidate reconciliation; NEVER resets/authorizes another write.

        Postiz has no client idempotency field. Even one exact match needs a human
        check; identical drafts from another actor cannot be distinguished safely.
        """
        if self.conn.in_transaction:
            raise ValueError('Committed dedicated connection required')
        row = self._operation(operation)
        if not self.enabled or row['state'] not in {'in_flight', 'ambiguous'}:
            raise ValueError('No uncertain operation to reconcile')
        payload = json.loads(row['payload_json'])
        content = payload['socialPost'][0]['postsAndComments'][0]['content']
        baseline = json.loads(row['baseline_json']) if row['baseline_json'] is not None else None
        candidates = []
        for post in self._remote(READBACK, WINDOW)['posts']:
            if (baseline is not None and post.get('id') not in baseline
                    and post.get('state') == 'DRAFT' and post.get('integrationId') == self.transport.channel
                    and post.get('content') == content
                    and post.get('publishDate', '').removesuffix('Z') == DATE.removesuffix('Z')
                    and isinstance(post.get('id'), str)
                    and re.fullmatch('[A-Za-z0-9_-]{1,100}', post['id'])):
                candidates.append(post['id'])
        with deliverables._atomic(self.conn):
            self._audit(operation, 'reconciliation_read')
        return {'state': 'manual_reconciliation_required', 'candidate_ids': candidates,
            'baseline_available': baseline is not None, 'retry_allowed': False}
