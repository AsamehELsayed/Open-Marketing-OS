"""Narrow official-SDK composition over existing OMOS controls."""
import asyncio
import json
from dataclasses import dataclass
from functools import partial
from importlib.metadata import version
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.services.credentials import vault
from app.services.mcp import McpGateway
from app.services.tools.registry import Registry, ToolRecord, ensure_tool_runs

ALLOW = frozenset({'groupList', 'integrationList', 'integrationSchema'})
LIMIT = 2 * 1024 * 1024


def validate_args(name, args):
    if name not in ALLOW or not isinstance(args, dict):
        raise ValueError('Read-only tool denied')
    expected = {'platform': 'linkedin', 'isPremium': False} if name == 'integrationSchema' else {}
    if args != expected or (name == 'integrationSchema' and args.get('isPremium') is not False):
        raise ValueError('Arguments denied')


@dataclass(frozen=True)
class Binding:
    project: str
    organization: str
    endpoint: str
    secret_ref: str

    def __post_init__(self):
        url = urlsplit(self.endpoint)
        if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
                or url.username or url.password or url.path != '/api/mcp'
                or url.query or url.fragment or not self.project or not self.organization
                or not vault.REF_RE.fullmatch(self.secret_ref)
                or not self.secret_ref.startswith('vault://project/')):
            raise ValueError('Invalid trusted local binding')


class BoundedStream(httpx.AsyncByteStream):
    def __init__(self, stream): self.stream = stream
    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > LIMIT: raise ValueError('Response exceeds limit')
            yield chunk
    async def aclose(self): await self.stream.aclose()


class ReadOnlyHTTP(httpx.AsyncBaseTransport):
    """Enforce before sockets; never let SDK redirects or methods widen access."""
    def __init__(self, endpoint, inner, identity_endpoint=None, record=None):
        self.endpoint, self.inner, self.identity_endpoint = endpoint, inner, identity_endpoint
        self.record = record
    async def handle_async_request(self, request):
        identity = self.identity_endpoint is not None and str(request.url) == self.identity_endpoint and request.method == 'GET'
        event = 'identity' if identity else 'stream'
        if not identity and (str(request.url) != self.endpoint or request.method not in {'POST', 'GET'}):
            raise ValueError('HTTP destination or method denied')
        if request.method == 'POST':
            data = await request.aread()
            if len(data) > 65536: raise ValueError('Request exceeds limit')
            body = json.loads(data)
            if not isinstance(body, dict) or body.get('jsonrpc') != '2.0':
                raise ValueError('Protocol message denied')
            method = body.get('method')
            event = method
            if method == 'tools/call':
                params = body.get('params', {})
                if set(params) != {'name', 'arguments'}: raise ValueError('Call parameters denied')
                validate_args(params['name'], params['arguments'])
                event = params['name']
            elif method not in {'initialize', 'notifications/initialized', 'tools/list'}:
                raise ValueError('Protocol method denied')
        if self.record: self.record(event)
        response = await self.inner.handle_async_request(request)
        if 300 <= response.status_code < 400 or response.headers.get('content-encoding', 'identity') != 'identity':
            await response.aclose()
            raise ValueError('Redirect or compressed response denied')
        response.stream = BoundedStream(response.stream)
        return response
    async def aclose(self): await self.inner.aclose()


def normalize_discovery(tools):
    names = [tool.name for tool in tools]
    if len(names) != len(set(names)) or not ALLOW.issubset(names):
        raise ValueError('Required tool inventory changed')
    normalized = []
    for tool in tools:
        if tool.name not in ALLOW: continue
        schema = tool.inputSchema
        properties = schema.get('properties', {})
        required = set(schema.get('required', []))
        allowed_props = {'group'} if tool.name == 'integrationList' else {'platform', 'isPremium'} if tool.name == 'integrationSchema' else set()
        expected_required = {'platform', 'isPremium'} if tool.name == 'integrationSchema' else set()
        if (schema.get('type') != 'object' or set(properties) != allowed_props
                or required != expected_required
                or any(key in schema for key in ('oneOf', 'anyOf', 'allOf', '$ref'))):
            raise ValueError('Allowed input schema changed')
        for key, spec in properties.items():
            if spec.get('type') != ('boolean' if key == 'isPremium' else 'string'):
                raise ValueError('Allowed input type changed')
        normalized.append({'name':tool.name, 'description':'Local Postiz read-only operation', 'parameters':schema})
    return normalized


def decode_result(name, result):
    if result.isError or len(result.content) != 1 or result.content[0].type != 'text':
        raise ValueError('Unexpected MCP result')
    payload = json.loads(result.content[0].text)
    if result.structuredContent is not None and result.structuredContent != payload:
        raise ValueError('Conflicting MCP result')
    if not isinstance(payload, dict) or set(payload) != {'output'}:
        raise ValueError('Unexpected result envelope')
    output = payload['output']
    if name in {'groupList', 'integrationList'}:
        fields = {'id', 'name'} if name == 'groupList' else {'id', 'name', 'picture', 'platform'}
        if not isinstance(output, list) or len(output) > 10000:
            raise ValueError('Unexpected list output')
        for row in output:
            if not isinstance(row, dict) or set(row) != fields or any(not isinstance(row[key], str) for key in fields):
                raise ValueError('Unexpected list row')
    else:
        if (not isinstance(output, dict) or set(output) != {'rules', 'maxLength', 'settings', 'tools'}
                or not isinstance(output['rules'], str)
                or type(output['maxLength']) not in {int, float} or output['maxLength'] <= 0
                or not isinstance(output['tools'], list)):
            raise ValueError('Unexpected platform schema')
    if len(json.dumps(payload).encode()) > LIMIT: raise ValueError('Result exceeds limit')
    return payload


class PostizTransport:
    def __init__(self, binding, conn, backend=None, *, timeout=15, http_transport=None):
        if version('mcp') != '1.30.0': raise RuntimeError('Requires official mcp==1.30.0')
        if not 0 < timeout <= 15: raise ValueError('Invalid deadline')
        self.binding, self.conn, self.backend = binding, conn, backend
        self.timeout, self.http_transport = timeout, http_transport
        self.enabled = False
        self.request_counts = {}
        self.protocol = None
        self.inventory_count = 0

    def record(self, event):
        self.request_counts[event] = self.request_counts.get(event, 0) + 1

    def resolve(self, ref):
        if ref != self.binding.secret_ref: raise ValueError('Credential binding mismatch')
        row = vault.get_ref_row(ref, conn=self.conn)
        if not row or row['scope'] != 'project' or row['project_id'] != self.binding.project:
            raise ValueError('Credential owner mismatch')
        return vault.resolve(ref, conn=self.conn, backend=self.backend)

    def _run(self, endpoint, name=None, args=None, credential=None):
        if endpoint != self.binding.endpoint: raise ValueError('Endpoint mismatch')
        if name is not None: validate_args(name, args)
        secret = self.resolve(self.binding.secret_ref)
        if credential is not None and credential != secret: raise ValueError('Credential mismatch')
        try:
            return asyncio.run(self._session(secret, name, args))
        except Exception:
            # SDK/HTTP errors may contain server bodies or URLs; never return them.
            raise RuntimeError('Local Postiz protocol operation failed') from None

    async def _session(self, secret, name, args):
        inner = self.http_transport or httpx.AsyncHTTPTransport(retries=0)
        identity_endpoint = self.binding.endpoint.removesuffix('/mcp') + '/public/v1/me'
        async with asyncio.timeout(self.timeout):
            async with httpx.AsyncClient(transport=ReadOnlyHTTP(self.binding.endpoint, inner, identity_endpoint, self.record),
                    headers={'Authorization': 'Bearer ' + secret, 'Accept-Encoding':'identity'}, trust_env=False,
                    follow_redirects=False, timeout=httpx.Timeout(self.timeout)) as client:
                identity = await client.get(identity_endpoint, headers={'Authorization':secret})
                identity.raise_for_status()
                if identity.json().get('organization', {}).get('id') != self.binding.organization:
                    raise ValueError('Authenticated organization mismatch')
                async with streamable_http_client(self.binding.endpoint, http_client=client,
                        terminate_on_close=False) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        self.protocol = initialized.protocolVersion
                        discovery = await session.list_tools()
                        self.inventory_count = len(discovery.tools)
                        if discovery.nextCursor: raise ValueError('Paginated discovery not accepted')
                        tools = normalize_discovery(discovery.tools)
                        if name is None: return tools
                        result = await session.call_tool(name, args)
                        return {'ok':True, 'payload':decode_result(name, result)}

    def list_tools(self, endpoint): return self._run(endpoint)
    def call_tool(self, endpoint, tool, args, *, credential=None, timeout_s=15):
        if not self.enabled: raise ValueError('Transport disabled until explicit opt-in')
        return self._run(endpoint, tool, args, credential)


class PostizHost:
    def __init__(self, conn, transport):
        self.conn, self.transport = conn, transport
        self.binding = transport.binding
        self.server_id = 'postiz-dev050-local'
        self.gateway = McpGateway(transport=transport, vault=transport)
        self.gateway.register_mcp_server(self.server_id, self.binding.endpoint,
            self.binding.project, self.binding.secret_ref)
        ensure_tool_runs(conn)
        self.registry = self._registry(False)
        self.discovered = False
        self.enabled = False

    def _registry(self, enabled):
        registry = Registry()
        for name in sorted(ALLOW):
            registry.register_tool(ToolRecord(tool_id=name, source_type='mcp',
                description='Local Postiz read-only operation', parameters={'type':'object'},
                side_effect='read', auth_required=True, credential_scope='project',
                permission_level='green', cost_type='free', retry_policy='none',
                timeout_s=15, enabled=enabled))
            registry.bind_handler(name, partial(self._execute, name))
        return registry

    def discover(self):
        self.enabled = False
        self.transport.enabled = False
        self.registry = self._registry(False)
        self.gateway.set_mcp_tool_allowlist(self.server_id, self.binding.project, [])
        self.discovered = False
        tools = self.gateway.discover_mcp_tools(self.server_id)
        self.discovered = True
        return [tool['name'] for tool in tools]

    def enable(self):
        if not self.discovered: raise ValueError('Discover before explicit opt-in')
        self.gateway.set_mcp_tool_allowlist(self.server_id, self.binding.project, sorted(ALLOW))
        self.registry = self._registry(True)
        self.transport.enabled = True
        self.enabled = True

    def _execute(self, name, conn, *, project_id, root, args):
        if conn is not self.conn or project_id != self.binding.project or not self.enabled:
            return {'ok':False, 'status':'denied', 'error':'Binding denied'}
        try:
            validate_args(name, args)
        except ValueError:
            return {'ok':False, 'status':'denied', 'error':'Read-only policy denied'}
        self.gateway.grant_approval(self.server_id, project_id, name)
        try:
            return self.gateway.execute_mcp_tool(self.server_id, project_id, name, args)
        finally:
            self.gateway.revoke_approval(self.server_id, project_id, name)

    def call(self, project, name, args):
        return self.registry.execute(self.conn, project_id=project, root=None, name=name, args=args)

    def audit(self):
        # Only fixed enums/booleans/counts escape; no server output, URL, args or key.
        return [{'seq':row['seq'], 'action':row['action'], 'ok':row['ok'],
                 'tool':row['tool'] if row['tool'] in ALLOW else 'denied-or-none'}
                for row in self.gateway.audit_rows()]
