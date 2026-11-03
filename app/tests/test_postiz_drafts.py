"""Synthetic OFFLINE acceptance. No Postiz instance or real account is contacted."""
import asyncio
import copy
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.database.sqlite import connect
from app.services import campaign_deliverables as editorial, state
from app.services.credentials import vault
from app.tests.test_postiz_readonly import MemoryBackend
from prototypes.marketing_mcp.demo import PROJECT, CAMPAIGN, COPY, seed, approve
from prototypes.postiz_readonly.client import Binding
from prototypes.postiz_drafts.contract import WRITE, READBACK, DATE, WINDOW, canonical, payload_for, validate_payload
from prototypes.postiz_drafts.host import ControlledDraftHost, LOCAL_TOOL, SERVER
from prototypes.postiz_drafts.transport import DraftTransport, DraftHTTP


@pytest.fixture
def setup(tmp_path):
    conn = connect(tmp_path / 'synthetic.sqlite')
    row = seed(conn)
    backend = MemoryBackend()
    ref = vault.store('project', 'postiz', 'synthetic-secret-sentinel', project_id=PROJECT, conn=conn, backend=backend)
    binding = Binding(PROJECT, 'synthetic-org', 'http://127.0.0.1:5199/api/mcp', ref)
    calls, posts = [], []
    knobs = {}

    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'organization': {'id': knobs.get('org', binding.organization)}})
        body = json.loads(request.content)
        method = body['method']
        if method == 'notifications/initialized':
            return httpx.Response(202)
        if method == 'initialize':
            result = {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'synthetic-offline', 'version': '1'}}
        elif method == 'tools/list':
            result = {'tools': [{'name': tool, 'inputSchema': {'type': 'object',
                'properties': props, 'required': required}} for tool, props, required in [
                    (WRITE, {'socialPost': {'type': 'array'}}, ['socialPost']),
                    (READBACK, {k: {'type': 'string'} for k in WINDOW}, list(WINDOW)),
                    ('integrationList', {}, [])]]}
        else:
            name = body['params']['name']
            args = body['params']['arguments']
            calls.append((name, copy.deepcopy(args)))
            if name == 'integrationList':
                value = {'output': [{'id': knobs.get('channel', 'synthetic-channel'), 'name': 'Inert',
                    'picture': '', 'platform': 'linkedin'}]}
            elif name == READBACK:
                value = {'output': {'posts': copy.deepcopy(posts)}}
            elif name == WRITE:
                validate_payload(args, 'synthetic-channel')
                posts.append({'id': 'synthetic-post-1', 'state': 'DRAFT', 'integrationId': 'synthetic-channel',
                    'publishDate': DATE.removesuffix('Z'),
                    'content': args['socialPost'][0]['postsAndComments'][0]['content']})
                if knobs.get('during_write'):
                    knobs['during_write']()
                if knobs.get('timeout'):
                    raise httpx.ReadTimeout('synthetic-secret-sentinel private-evidence')
                value = {'output': [{'postId': 'synthetic-post-1', 'integration': 'synthetic-channel',
                    'previewUrl': 'https://untrusted.invalid/private'}]}
                if knobs.get('malformed'):
                    value['output'] = []
            else:
                raise AssertionError('Unexpected tool')
            result = {'content': [{'type': 'text', 'text': json.dumps(value)}], 'structuredContent': value}
        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': result})

    transport = DraftTransport(binding, conn, backend, channel='synthetic-channel', http_transport=httpx.MockTransport(handler))
    host = ControlledDraftHost(conn, transport)
    yield host, row, calls, posts, knobs
    conn.close()


def ready(setup, enable=True, decision=True):
    host, row, *_ = setup
    row = approve(host.conn, row)
    operation = host.prepare(CAMPAIGN, row['id'], row['current_version'])
    if decision:
        state.decide_approval(host.conn, operation['approval_id'], 'approved', project_id=PROJECT)
    if enable:
        host.enable()
    return host, row, operation


def test_exact_approved_revision_single_use_persistence_and_audit(setup):
    host, row, op = ready(setup)
    assert op['payload'] == payload_for(COPY, 'synthetic-channel')
    assert host.prepare(CAMPAIGN, row['id'], row['current_version'])['operation_id'] == op['operation_id']
    result = host.execute(op['operation_id'], op['approval_id'])
    assert result == {'ok': True, 'state': 'succeeded', 'post_id': 'synthetic-post-1'}
    assert host.execute(op['operation_id'], op['approval_id']) == result
    restarted = ControlledDraftHost(host.conn, host.transport)
    restarted.enable()
    assert restarted.execute(op['operation_id'], op['approval_id']) == result
    assert len(setup[3]) == 1
    assert [x[0] for x in setup[2]].count(WRITE) == 1
    assert not host.gateway._approvals and host.transport._authorized is None
    audit = [dict(r) for r in host.conn.execute('SELECT * FROM postiz_draft_audit')]
    assert {'prepared', 'approval_consumed', 'succeeded', 'replay_blocked'} <= {r['event'] for r in audit}
    telemetry = json.dumps(audit + host.gateway.audit_rows() + [dict(r) for r in host.conn.execute('SELECT * FROM tool_runs')])
    assert COPY not in telemetry and 'synthetic-secret-sentinel' not in telemetry


def test_default_disabled_and_no_ambient_network(setup):
    host, _, op = ready(setup, enable=False)
    assert not host.execute(op['operation_id'], op['approval_id'])['ok']
    assert setup[2] == []
    t = host.transport
    t.http_transport = None
    with pytest.raises(ValueError, match='disabled'):
        t.list_tools(t.binding.endpoint)


@pytest.mark.parametrize('status', ['DRAFT', 'IN_REVIEW'])
def test_unapproved_rejected(setup, status):
    host, row, *_ = setup
    if status == 'IN_REVIEW':
        row = editorial.transition(host.conn, PROJECT, CAMPAIGN, row['id'], row['current_version'], status)
    with pytest.raises(ValueError):
        host.prepare(CAMPAIGN, row['id'], row['current_version'])
    assert setup[2] == []


@pytest.mark.parametrize('mode', ['stale', 'edited', 'tampered_snapshot', 'wrong_project', 'boolean_version'])
def test_revision_scope_and_history(setup, mode):
    host, row, op = ready(setup)
    if mode == 'wrong_project':
        assert not host.execute(op['operation_id'], op['approval_id'], project_id='fixture-beta')['ok']
    elif mode == 'boolean_version':
        with pytest.raises(ValueError):
            host.prepare(CAMPAIGN, row['id'], True)
    elif mode == 'stale':
        with pytest.raises(ValueError):
            host.prepare(CAMPAIGN, row['id'], row['current_version'] - 1)
    else:
        if mode == 'edited':
            editorial.revise(host.conn, PROJECT, CAMPAIGN, row['id'], row['current_version'], {'content_md': 'Unapproved edit'})
        else:
            host.conn.execute('UPDATE campaign_deliverables SET title=? WHERE id=?', ('Tampered', row['id']))
            host.conn.commit()
        assert not host.execute(op['operation_id'], op['approval_id'])['ok']
    assert not setup[3]


@pytest.mark.parametrize('mode', ['pending', 'rejected', 'forged', 'missing', 'payload', 'approval_binding', 'approval_preview'])
def test_separate_persisted_exact_approval_required(setup, mode):
    host, row, op = ready(setup, decision=mode not in {'pending', 'rejected'})
    approval = op['approval_id']
    if mode == 'rejected':
        state.decide_approval(host.conn, approval, 'rejected', project_id=PROJECT)
    elif mode == 'forged':
        approval = 'forged-nonempty'
    elif mode == 'missing':
        approval = None
    elif mode == 'payload':
        host.conn.execute("UPDATE postiz_draft_operations SET payload_json='{}'")
        host.conn.commit()
    elif mode.startswith('approval_'):
        column = 'fields_json' if mode == 'approval_binding' else 'body_md'
        host.conn.execute(f'UPDATE approvals SET {column}=? WHERE id=?', ('changed', approval))
        host.conn.commit()
    result = host.registry.execute(host.conn, project_id=PROJECT, root=None, name=LOCAL_TOOL,
        args={'operation_id': op['operation_id'], 'approval_id': approval}, approval_id='forged-registry-approval')
    assert not result['ok']
    assert not setup[2] and not setup[3]


@pytest.mark.parametrize('mode', ['org', 'channel'])
def test_server_identity_and_channel_owner_checked_before_write(setup, mode):
    host, _, op = ready(setup)
    setup[4][mode] = 'foreign'
    assert not host.execute(op['operation_id'], op['approval_id'])['ok']
    assert not setup[3]
    assert not any(x[0] == WRITE for x in setup[2])


@pytest.mark.parametrize('failure', ['timeout', 'malformed'])
def test_ambiguous_commit_never_retries_even_after_restart(setup, failure):
    host, _, op = ready(setup)
    setup[4][failure] = True
    assert not host.execute(op['operation_id'], op['approval_id'])['ok']
    assert host.status(op['operation_id'])['state'] == 'ambiguous'
    restarted = ControlledDraftHost(host.conn, host.transport)
    restarted.enable()
    assert not restarted.execute(op['operation_id'], op['approval_id'])['ok']
    assert len(setup[3]) == 1
    receipt = restarted.reconcile(op['operation_id'])
    assert receipt['candidate_ids'] == ['synthetic-post-1'] and receipt['retry_allowed'] is False
    assert host.status(op['operation_id'])['state'] == 'ambiguous'


def test_late_edit_preserves_remote_identity_and_blocks_retry(setup):
    host, row, op = ready(setup)
    setup[4]['during_write'] = lambda: editorial.revise(host.conn, PROJECT, CAMPAIGN, row['id'],
        row['current_version'], {'content_md': 'Edited during transport'})
    result = host.execute(op['operation_id'], op['approval_id'])
    assert result == {'ok': False, 'state': 'succeeded_stale', 'post_id': 'synthetic-post-1'}
    assert not host.execute(op['operation_id'], op['approval_id'])['ok']
    assert len(setup[3]) == 1


@pytest.mark.parametrize('field,value', [('type', 'now'), ('type', 'schedule'), ('shortLink', True),
    ('date', '2030-01-01'), ('integrationId', 'foreign'), ('settings', [{'key': 'x', 'value': 1}]),
    ('extra', 'injected')])
def test_payload_modes_and_extra_fields_denied(field, value):
    payload = payload_for('Synthetic copy', 'synthetic-channel')
    payload['socialPost'][0][field] = value
    with pytest.raises(ValueError):
        validate_payload(payload, 'synthetic-channel')


@pytest.mark.parametrize('name', ['publishPostTool', 'deletePostTool', 'triggerTool', 'uploadWidgetTool',
    'integrationSchedulePostTool', 'schedulePostTool ', 'ask_postiz'])
def test_no_general_transport_authority(setup, name):
    host = setup[0]
    host.enable()
    with pytest.raises(ValueError):
        host.transport.call_tool(host.binding.endpoint, name, {})
    assert not host.gateway.execute_mcp_tool(SERVER, PROJECT, name, {})['ok']
    assert setup[2] == []


def test_wire_guard_single_use_and_exact_payload():
    called = []
    payload = payload_for('Synthetic <escaped> & copy', 'synthetic-channel')
    async def run():
        endpoint = 'http://127.0.0.1:5199/api/mcp'
        guard = DraftHTTP(endpoint, httpx.MockTransport(lambda req: called.append(req) or httpx.Response(200)),
            channel='synthetic-channel', authorized=(WRITE, canonical(payload)))
        async with httpx.AsyncClient(transport=guard) as client:
            body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': WRITE, 'arguments': payload}}
            changed = copy.deepcopy(body)
            changed['params']['arguments']['socialPost'][0]['type'] = 'schedule'
            with pytest.raises(ValueError):
                await client.post(endpoint, json=changed)
            await client.post(endpoint, json=body)
            with pytest.raises(ValueError):
                await client.post(endpoint, json=body)
    asyncio.run(run())
    assert len(called) == 1


@pytest.mark.parametrize('mode', ['media', 'comment', 'batch', 'html', 'tenant', 'true_premium'])
def test_no_media_comments_html_or_tenant_overrides(mode):
    payload = payload_for('Synthetic copy', 'synthetic-channel')
    post = payload['socialPost'][0]
    if mode == 'media':
        post['postsAndComments'][0]['attachments'] = ['https://provider.invalid/private.png']
    elif mode == 'comment':
        post['postsAndComments'].append({'content': '<p>Comment</p>', 'attachments': []})
    elif mode == 'batch':
        payload['socialPost'].append(copy.deepcopy(post))
    elif mode == 'html':
        post['postsAndComments'][0]['content'] = '<img src="https://provider.invalid/private">'
    elif mode == 'tenant':
        payload['organization'] = 'foreign'
    else:
        post['isPremium'] = True
    with pytest.raises(ValueError):
        validate_payload(payload, 'synthetic-channel')


def test_crash_after_claim_requires_reconciliation_no_automatic_retry(setup):
    host, _, op = ready(setup)
    host.conn.execute("UPDATE postiz_draft_operations SET state='in_flight' WHERE operation_id=?", (op['operation_id'],))
    host.conn.commit()
    restarted = ControlledDraftHost(host.conn, host.transport)
    restarted.enable()
    assert not restarted.execute(op['operation_id'], op['approval_id'])['ok']
    result = restarted.reconcile(op['operation_id'])
    assert not result['baseline_available'] and not result['candidate_ids'] and not result['retry_allowed']
    assert not setup[3]


def test_reconciliation_never_guesses_existing_or_wrong_state_drafts(setup):
    host, _, op = ready(setup)
    copy = op['payload']['socialPost'][0]['postsAndComments'][0]['content']
    setup[3].append({'id': 'preexisting', 'state': 'DRAFT', 'integrationId': 'synthetic-channel',
        'publishDate': DATE, 'content': copy})
    setup[4]['timeout'] = True
    host.execute(op['operation_id'], op['approval_id'])
    setup[3][1]['state'] = 'QUEUE'
    assert not host.reconcile(op['operation_id'])['candidate_ids']
    setup[3][1]['state'] = 'DRAFT'
    setup[3][1]['integrationId'] = 'foreign'
    assert not host.reconcile(op['operation_id'])['candidate_ids']


def test_outer_transaction_cannot_rollback_consumed_approval(setup):
    host, _, op = ready(setup)
    host.conn.execute('BEGIN IMMEDIATE')
    try:
        with pytest.raises(ValueError, match='dedicated'):
            host.execute(op['operation_id'], op['approval_id'])
    finally:
        host.conn.rollback()
    assert not setup[3]


def test_private_audit_section_excluded_from_payload(setup):
    host, row, *_ = setup
    content = ('## Final copy\nPublic synthetic text.\n\n## Persisted user facts\nprivate-evidence-sentinel\n'
        '## Retrieved evidence\nprivate-source-sentinel\n## Recommendations\nnone\n## Missing information\nnone')
    row = editorial.revise(host.conn, PROJECT, CAMPAIGN, row['id'], row['current_version'], {'content_md': content})
    row = approve(host.conn, row)
    op = host.prepare(CAMPAIGN, row['id'], row['current_version'])
    rendered = canonical(op['payload'])
    assert 'Public synthetic text.' in rendered
    assert 'private-evidence-sentinel' not in rendered and 'private-source-sentinel' not in rendered


def test_two_simultaneous_executions_dispatch_only_once(setup):
    host, _, op = ready(setup)
    database = host.conn.execute('PRAGMA database_list').fetchone()[2]
    barrier = threading.Barrier(2)
    def click():
        conn = sqlite3.connect(database, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            transport = DraftTransport(host.binding, conn, host.transport.backend,
                channel=host.transport.channel, http_transport=host.transport.http_transport)
            separate = ControlledDraftHost(conn, transport)
            separate.enable()
            barrier.wait(timeout=15)
            return separate.execute(op['operation_id'], op['approval_id'])
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: click(), range(2)))
    assert any(r['ok'] for r in results)
    assert len(setup[3]) == 1
    assert sum(name == WRITE for name, _ in setup[2]) == 1


def test_operator_cli_displays_prepared_payload_and_blocks_unflagged_write(setup, tmp_path, monkeypatch, capsys):
    from prototypes.postiz_drafts.local import main
    host, row, op = ready(setup, enable=False, decision=False)
    config = tmp_path / 'trusted-config.json'
    config.write_text(json.dumps({'database': host.conn.execute('PRAGMA database_list').fetchone()[2],
        'project': PROJECT, 'organization': host.binding.organization, 'endpoint': host.binding.endpoint,
        'secret_ref': host.binding.secret_ref, 'channel': host.transport.channel}))
    monkeypatch.setattr('sys.argv', ['local', '--config', str(config), 'prepare', CAMPAIGN, row['id'], str(row['current_version'])])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)['payload'] == op['payload']
    monkeypatch.setattr('sys.argv', ['local', '--config', str(config), 'approve', op['operation_id']])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)['approval_status'] == 'approved'
    monkeypatch.setattr('sys.argv', ['local', '--config', str(config), 'execute', op['operation_id']])
    assert main() == 1
    assert json.loads(capsys.readouterr().out)['ok'] is False
    assert not setup[2]


def test_failed_reenable_revokes_previous_capability(setup):
    host, _, op = ready(setup)
    setup[4]['org'] = 'foreign'
    with pytest.raises(RuntimeError):
        host.enable()
    assert not host.enabled and not host.transport.enabled
    assert not host.execute(op['operation_id'], op['approval_id'])['ok']
    assert not setup[2]
