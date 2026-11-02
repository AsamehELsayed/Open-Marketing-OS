"""Offline contract tests; never evidence of a live Postiz instance."""
import asyncio
import json
import sqlite3
import httpx
import pytest

from prototypes.postiz_readonly.client import ALLOW, Binding, PostizHost, PostizTransport, ReadOnlyHTTP, LIMIT
from app.services.credentials import vault


class MemoryBackend:
    name = 'test-memory'
    def __init__(self): self.values = {}
    def put(self, key, value): self.values[key] = value
    def get(self, key): return self.values[key]
    def delete(self, key): self.values.pop(key, None)


class TrackingTransport(httpx.MockTransport):
    closed = False
    async def aclose(self):
        self.closed = True
        await super().aclose()


def fixture(transform=None):
    conn = sqlite3.connect(':memory:')
    backend = MemoryBackend()
    ref = vault.store('project', 'postiz', 'synthetic-local-key', project_id='A', conn=conn, backend=backend)
    binding = Binding('A', 'org-A', 'http://127.0.0.1:5199/api/mcp', ref)
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == 'GET':
            assert request.url.path == '/api/public/v1/me'
            assert request.headers['authorization'] == 'synthetic-local-key'
            return httpx.Response(200,json={'organization':{'id':'org-A','name':'Synthetic A'},'user':None})
        assert request.headers['authorization'] == 'Bearer synthetic-local-key'
        body = json.loads(request.content)
        method = body['method']
        if method == 'notifications/initialized':
            return httpx.Response(202)
        if method == 'initialize':
            result = {'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'OFFLINE fixture','version':'1'}}
        elif method == 'tools/list':
            result = {'tools':[{'name':name,'inputSchema':{'type':'object','properties':{'group':{'type':'string'}} if name == 'integrationList' else {} if name != 'integrationSchema' else {'platform':{'type':'string'},'isPremium':{'type':'boolean'}},'required':[] if name != 'integrationSchema' else ['platform','isPremium']}} for name in ALLOW] + [{'name':'triggerTool','inputSchema':{'type':'object'}}]}
        elif method == 'tools/call':
            name = body['params']['name']
            payload = {'output': []} if name != 'integrationSchema' else {'output':{'rules':'offline rules','maxLength':3000,'settings':[],'tools':[]}}
            result = {'content':[{'type':'text','text':json.dumps(payload)}], 'structuredContent':payload}
        if transform:
            return transform(body, result)
        return httpx.Response(200,json={'jsonrpc':'2.0','id':body['id'],'result':result})
    transport = PostizTransport(binding, conn, backend, http_transport=TrackingTransport(handler))
    return conn, transport, requests


def test_disabled_then_three_read_calls_and_sanitized_audit():
    conn, transport, requests = fixture()
    host = PostizHost(conn, transport)
    assert not host.call('A', 'groupList', {})['ok']
    with pytest.raises(ValueError): transport.call_tool(transport.binding.endpoint,'groupList',{})
    assert requests == []
    assert set(host.discover()) == ALLOW
    assert not host.call('A', 'groupList', {})['ok']
    host.enable()
    for name in ALLOW:
        args = {'platform':'linkedin','isPremium':False} if name == 'integrationSchema' else {}
        assert host.call('A', name, args)['ok']
    receipt = json.dumps(host.audit())
    assert 'synthetic-local-key' not in receipt
    assert 'Authorization' not in receipt
    assert all(r.method == 'POST' or r.url.path == '/api/public/v1/me' for r in requests)
    assert host.gateway._approvals == set()
    assert transport.http_transport.closed
    assert set(transport.request_counts) <= ALLOW | {'identity','initialize','notifications/initialized','tools/list'}
    assert transport.protocol == '2025-11-25'
    assert transport.inventory_count == 4
    assert conn.execute('SELECT COUNT(*) FROM tool_runs').fetchone()[0] >= 5


@pytest.mark.parametrize('name', ['triggerTool','schedulePostTool','integrationSchedulePostTool','uploadWidgetTool','deletePostTool','generateImageTool','generateVideoTool','ask_postiz','unknown','GroupList','groupList '])
def test_mutating_and_unknown_denied_at_all_boundaries(name):
    conn, transport, requests = fixture()
    host = PostizHost(conn, transport)
    host.discover(); host.enable()
    before = len(requests)
    assert not host.call('A', name, {})['ok']
    assert not host.gateway.execute_mcp_tool(host.server_id,'A',name,{})['ok']
    with pytest.raises(ValueError): transport.call_tool(transport.binding.endpoint,name,{})
    assert len(requests) == before


def test_project_and_argument_override_denied_without_dispatch():
    conn, transport, requests = fixture()
    host = PostizHost(conn, transport)
    host.discover(); host.enable()
    before = len(requests)
    assert not host.call('B','groupList',{})['ok']
    assert not host.call('A','groupList',{'organization':'B'})['ok']
    assert not host.call('A','integrationSchema',{'platform':'linkedin','isPremium':0})['ok']
    assert not host.registry.execute(conn, project_id='B',root=None,name='groupList',args={},approval_id='forged')['ok']
    assert len(requests) == before


@pytest.mark.parametrize('endpoint',['https://postiz.com/api/mcp','http://localhost:5199/api/mcp','http://127.0.0.1:5199/api/mcp?key=x','http://user:pass@127.0.0.1:5199/api/mcp'])
def test_only_fixed_literal_loopback_endpoint(endpoint):
    with pytest.raises(ValueError): Binding('A','org-A',endpoint,'vault://project/' + 'a'*32)


@pytest.mark.parametrize('method', ['DELETE','PUT','PATCH'])
def test_http_methods_rejected_before_inner_dispatch(method):
    called = []
    async def run():
        inner = httpx.MockTransport(lambda req: called.append(req) or httpx.Response(200))
        async with httpx.AsyncClient(transport=ReadOnlyHTTP('http://127.0.0.1:5199/api/mcp',inner)) as client:
            with pytest.raises(ValueError): await client.request(method,'http://127.0.0.1:5199/api/mcp')
    asyncio.run(run())
    assert not called


@pytest.mark.parametrize('body', [[], {'jsonrpc':'2.0','id':1,'method':'resources/read'}, {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'triggerTool','arguments':{}}}])
def test_raw_protocol_bypass_denied(body):
    called = []
    async def run():
        inner = httpx.MockTransport(lambda req: called.append(req) or httpx.Response(200))
        async with httpx.AsyncClient(transport=ReadOnlyHTTP('http://127.0.0.1:5199/api/mcp',inner)) as client:
            with pytest.raises(ValueError): await client.post('http://127.0.0.1:5199/api/mcp',json=body)
    asyncio.run(run())
    assert not called


@pytest.mark.parametrize('status', [301,307,401,403,500])
def test_redirect_auth_and_server_failures_are_sanitized(status):
    conn, transport, requests = fixture(lambda body,result: httpx.Response(status,headers={'Location':'https://provider.invalid/secret'},text='private-server-body'))
    with pytest.raises(RuntimeError, match='Local Postiz protocol operation failed'):
        transport.list_tools(transport.binding.endpoint)
    assert len(requests) == 2  # trusted identity preflight, then refused MCP initialize
    assert transport.http_transport.closed


def test_deadline_closes_client_without_delete():
    conn, transport, _ = fixture()
    calls = []
    async def slow(req):
        calls.append(req.method)
        await asyncio.sleep(10)
        return httpx.Response(200)
    transport.http_transport = TrackingTransport(slow)
    transport.timeout = .1
    with pytest.raises(RuntimeError): transport.list_tools(transport.binding.endpoint)
    assert calls == ['GET']
    assert transport.http_transport.closed


@pytest.mark.parametrize('failure', ['schema','isError','conflict','oversize','bad-row','unsupported-protocol'])
def test_malformed_upstream_fails_closed(failure):
    def transform(body, result):
        if failure == 'schema' and body['method'] == 'tools/list': result['tools'][0]['inputSchema']['required'] = ['unknown']
        if failure == 'unsupported-protocol' and body['method'] == 'initialize': result['protocolVersion'] = '1900-01-01'
        if body['method'] == 'tools/call':
            if failure == 'isError': result['isError'] = True
            if failure == 'conflict': result['structuredContent'] = {'output':['different']}
            if failure == 'oversize': result['content'][0]['text'] = 'x'*(LIMIT+1)
            if failure == 'bad-row':
                value = {'output':[{'id':'A','name':'A','token':'private'}]}
                result['structuredContent'] = value
                result['content'][0]['text'] = json.dumps(value)
        return httpx.Response(200,json={'jsonrpc':'2.0','id':body['id'],'result':result})
    conn, transport, requests = fixture(transform)
    if failure in {'schema','unsupported-protocol'}:
        with pytest.raises(RuntimeError): transport.list_tools(transport.binding.endpoint)
    else:
        host = PostizHost(conn,transport)
        host.discover(); host.enable()
        assert not host.call('A','groupList',{})['ok']
        assert not host.gateway._approvals


def test_credential_ref_ownership_and_revocation():
    conn, transport, requests = fixture()
    other = Binding('B','org-B',transport.binding.endpoint,transport.binding.secret_ref)
    wrong = PostizTransport(other,conn,transport.backend)
    with pytest.raises(ValueError): wrong.list_tools(other.endpoint)
    vault.revoke(transport.binding.secret_ref,conn=conn,backend=transport.backend)
    with pytest.raises(ValueError): transport.list_tools(transport.binding.endpoint)
    assert not requests


def test_authenticated_organization_must_match_trusted_binding():
    conn, transport, requests = fixture()
    wrong = Binding('A','org-B',transport.binding.endpoint,transport.binding.secret_ref)
    transport.binding = wrong
    with pytest.raises(RuntimeError): transport.list_tools(wrong.endpoint)
    assert len(requests) == 1
