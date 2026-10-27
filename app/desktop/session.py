"""Private desktop sidecar session boundary; never exposes bearer material."""
from dataclasses import dataclass
import hmac
import json
import re
import struct

MAX_FRAME = 16384

def _read_exact(pipe, size):
    result = bytearray()
    while len(result) < size:
        part = pipe.read(size - len(result))
        if not part:
            raise EOFError('closed control pipe')
        result.extend(part)
    return bytes(result)

def read_frame(pipe):
    size = struct.unpack('!I', _read_exact(pipe, 4))[0]
    if not 1 <= size <= MAX_FRAME:
        raise ValueError('invalid control frame size')
    value = json.loads(_read_exact(pipe, size))
    if not isinstance(value, dict):
        raise ValueError('invalid control frame')
    return value

def write_frame(pipe, value):
    data = json.dumps(value, separators=(',', ':')).encode('utf-8')
    if not 1 <= len(data) <= MAX_FRAME:
        raise ValueError('invalid control frame size')
    pipe.write(struct.pack('!I', len(data)) + data)
    pipe.flush()

@dataclass(frozen=True, repr=False)
class DesktopSession:
    session_id: str
    token: str
    build_id: str
    edition: str
    port: int

    @classmethod
    def bootstrap(cls, value, port):
        if (value.get('protocol') != 1
            or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', str(value.get('session_id', '')))
            or not re.fullmatch(r'[0-9a-f]{64}', str(value.get('token', '')))
            or not re.fullmatch(r'[!-~]{1,128}', str(value.get('build_id', '')))
            or value.get('edition') not in ('local', 'openrouter')):
            raise ValueError('invalid desktop bootstrap')
        return cls(value['session_id'], value['token'], value['build_id'], value['edition'], port)

    @property
    def origin(self):
        return f'http://127.0.0.1:{self.port}'

class DesktopBoundary:
    """Outer ASGI wrapper covers static, SSE, errors, legacy and WebSocket."""
    def __init__(self, app, session):
        self.app, self.session = app, session

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        headers = scope.get('headers', [])
        def values(name): return [v for k, v in headers if k.lower() == name]
        tokens = values(b'x-omos-desktop-token')
        authenticated = len(tokens) == 1 and hmac.compare_digest(tokens[0], self.session.token.encode('ascii'))
        hosts, origins = values(b'host'), values(b'origin')
        origin = self.session.origin.encode('ascii')
        valid_source = hosts == [origin.removeprefix(b'http://')] and (not origins or origins == [origin])
        if not authenticated or not valid_source:
            if scope['type'] == 'websocket':
                return await send({'type':'websocket.close', 'code':1008})
            status = 401 if not authenticated else 403
            await send({'type':'http.response.start', 'status':status, 'headers':[(b'content-type', b'application/json'), (b'cache-control', b'no-store')]})
            return await send({'type':'http.response.body', 'body':b'{"detail":"Forbidden"}'})
        async def secure_send(message):
            if message['type'] == 'http.response.start':
                message = dict(message)
                security = {
                    b'content-security-policy': ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; connect-src 'self' ws://127.0.0.1:" + str(self.session.port)).encode(),
                    b'x-content-type-options': b'nosniff', b'x-frame-options': b'DENY',
                    b'referrer-policy': b'no-referrer', b'cache-control': b'no-store',
                    b'permissions-policy': b'camera=(), microphone=(), geolocation=()',
                }
                message['headers'] = [(k,v) for k,v in message.get('headers',[]) if k.lower() not in security] + list(security.items())
            await send(message)
        await self.app(scope, receive, secure_send)
