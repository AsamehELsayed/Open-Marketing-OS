"""Official SDK, vault and DEV-050 HTTP bounds with one-shot draft authority."""
import asyncio
import copy
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from prototypes.postiz_readonly.client import PostizTransport, ReadOnlyHTTP, decode_result
from .contract import WRITE, READBACK, WINDOW, canonical, envelope, validate_payload, write_result


class DraftHTTP(ReadOnlyHTTP):
    def __init__(self, *args, channel, authorized, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel, self.authorized = channel, authorized
        self.used = False

    async def handle_async_request(self, request):
        if request.method == 'POST':
            data = await request.aread()
            if len(data) > 65536:
                raise ValueError('Request exceeds limit')
            body = json.loads(data)
            if isinstance(body, dict) and body.get('method') == 'tools/call':
                params = body.get('params', {})
                name = params.get('name')
                if name in {WRITE, READBACK}:
                    if (str(request.url) != self.endpoint or body.get('jsonrpc') != '2.0'
                            or set(params) != {'name', 'arguments'} or self.used
                            or self.authorized != (name, canonical(params['arguments']))):
                        raise ValueError('One-shot operation denied')
                    if name == WRITE:
                        validate_payload(params['arguments'], self.channel)
                    elif params['arguments'] != WINDOW:
                        raise ValueError('Readback range denied')
                    self.used = True  # consume BEFORE possible socket activity
                    if self.record:
                        self.record(name)
                    response = await self.inner.handle_async_request(request)
                    if (300 <= response.status_code < 400
                            or response.headers.get('content-encoding', 'identity') != 'identity'):
                        await response.aclose()
                        raise ValueError('Redirect or compressed response denied')
                    from prototypes.postiz_readonly.client import BoundedStream
                    response.stream = BoundedStream(response.stream)
                    return response
        return await super().handle_async_request(request)


class DraftTransport(PostizTransport):
    def __init__(self, binding, conn, backend=None, *, channel, allow_network=False, **kwargs):
        super().__init__(binding, conn, backend, **kwargs)
        if not isinstance(channel, str) or not channel or len(channel) > 100:
            raise ValueError('Trusted channel required')
        self.channel = channel
        self.allow_network = allow_network
        self._authorized = None

    def arm(self, name, payload):
        if not self.enabled or self._authorized is not None:
            raise ValueError('Transport capability denied')
        if name == WRITE:
            validate_payload(payload, self.channel)
        elif name != READBACK or payload != WINDOW:
            raise ValueError('Transport capability denied')
        self._authorized = (name, canonical(payload))

    def disarm(self):
        self._authorized = None

    def _run(self, endpoint, name=None, args=None, credential=None):
        if endpoint != self.binding.endpoint:
            raise ValueError('Endpoint denied')
        if self.http_transport is None and not self.allow_network:
            raise ValueError('Actual Postiz access disabled')
        if name is not None and self._authorized != (name, canonical(args)):
            raise ValueError('Exact operation authority required')
        secret = self.resolve(self.binding.secret_ref)
        if credential is not None and credential != secret:
            raise ValueError('Credential mismatch')
        try:
            return asyncio.run(self._session(secret, name, copy.deepcopy(args)))
        except Exception:
            raise RuntimeError('Postiz operation outcome uncertain') from None
        finally:
            if name is not None:
                self.disarm()

    async def _session(self, secret, name, args):
        inner = self.http_transport or httpx.AsyncHTTPTransport(retries=0)
        identity_url = self.binding.endpoint.removesuffix('/mcp') + '/public/v1/me'
        guard = DraftHTTP(self.binding.endpoint, inner, identity_url, self.record,
            channel=self.channel, authorized=self._authorized)
        async with asyncio.timeout(self.timeout):
            async with httpx.AsyncClient(transport=guard, trust_env=False, follow_redirects=False,
                    headers={'Authorization': 'Bearer ' + secret, 'Accept-Encoding': 'identity'},
                    timeout=httpx.Timeout(self.timeout)) as client:
                identity = await client.get(identity_url, headers={'Authorization': secret})
                identity.raise_for_status()
                if identity.json().get('organization', {}).get('id') != self.binding.organization:
                    raise ValueError('Organization mismatch')
                async with streamable_http_client(self.binding.endpoint, http_client=client,
                        terminate_on_close=False) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        discovery = await session.list_tools()
                        names = [t.name for t in discovery.tools]
                        if discovery.nextCursor or len(names) != len(set(names)):
                            raise ValueError('Discovery drift')
                        specs = {t.name: t.inputSchema for t in discovery.tools}
                        for tool, required in ((WRITE, {'socialPost'}),
                                               (READBACK, {'startDate', 'endDate'})):
                            schema = specs.get(tool, {})
                            if (schema.get('type') != 'object'
                                    or set(schema.get('required', [])) != required
                                    or not required.issubset(schema.get('properties', {}))):
                                raise ValueError('Discovery contract drift')
                        if name is None:
                            return [{'name': t, 'description': 'Controlled Postiz draft operation',
                                'parameters': specs[t]} for t in (WRITE, READBACK)]
                        # Validate the trusted channel against this credential's organization.
                        channels = decode_result('integrationList', await session.call_tool('integrationList', {}))
                        if not any(c['id'] == self.channel and c['platform'] == 'linkedin'
                                   for c in channels['output']):
                            raise ValueError('Channel binding mismatch')
                        value = envelope(await session.call_tool(name, args))
                        if name == WRITE:
                            return {'ok': True, 'post_id': write_result(value, self.channel)}
                        if not isinstance(value, dict) or set(value) != {'posts'} or not isinstance(value['posts'], list):
                            raise ValueError('Invalid readback')
                        return {'ok': True, 'posts': value['posts']}
