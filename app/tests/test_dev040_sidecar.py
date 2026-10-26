"""Desktop trust-boundary regression tests."""
import io
import pytest
from fastapi import FastAPI, WebSocket
from app.desktop.session import DesktopSession, DesktopBoundary, read_frame, write_frame

TOKEN = 'a' * 64

def session():
    return DesktopSession('s' * 32, TOKEN, 'build', 'local', 12345)

def request(path='/legacy', headers=None, scope_type='http'):
    from urllib.parse import urlsplit
    app = FastAPI()
    @app.get('/legacy')
    async def legacy(): return {'ok': True}
    @app.websocket('/ws')
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text('ok')
        await socket.close()
    sent = []
    async def send(message): sent.append(message)
    async def receive():
        return {'type':'websocket.connect'} if scope_type == 'websocket' else {'type':'http.request','body':b'', 'more_body':False}
    parsed = urlsplit(path)
    scope = {'type':scope_type,'asgi':{'version':'3.0'},'http_version':'1.1','method':'GET','scheme':'http','path':parsed.path,'raw_path':parsed.path.encode(),'query_string':parsed.query.encode(),'root_path':'','headers':[(b'host',b'127.0.0.1:12345')] + (headers or []),'server':('127.0.0.1',12345),'client':('127.0.0.1',50000)}
    coroutine = DesktopBoundary(app,session())(scope,receive,send)
    try:
        coroutine.send(None)
    except StopIteration:
        pass
    else:
        raise AssertionError("unexpected asynchronous I/O in boundary fixture")
    return sent

@pytest.mark.parametrize('path', ['/legacy', '/assets/private.js', '/health', '/desktop/ready'])
def test_auth_covers_every_path(path):
    assert request(path)[0]['status'] == 401

def test_origin_host_and_duplicate_tokens():
    token = [(b'x-omos-desktop-token',TOKEN.encode())]
    assert request(headers=token)[0]['status'] == 200
    assert request(headers=token+[(b'origin',b'http://evil.test')])[0]['status'] == 403
    assert request(headers=token+[(b'host',b'localhost:12345')])[0]['status'] == 403
    assert request(headers=token+token)[0]['status'] == 401
    assert request('/legacy?token='+TOKEN)[0]['status'] == 401
    assert request(headers=[(b'x-omos-desktop-token',b'b'*64)])[0]['status'] == 401

def test_websocket_denied_before_accept():
    assert request('/ws',scope_type='websocket')[0]['type'] == 'websocket.close'
    messages = request('/ws',[(b'x-omos-desktop-token',TOKEN.encode())],scope_type='websocket')
    assert messages[0]['type'] == 'websocket.accept'
    assert messages[1]['text'] == 'ok'

def test_security_headers():
    headers = dict(request(headers=[(b'x-omos-desktop-token',TOKEN.encode())])[0]['headers'])
    assert b"script-src 'self'" in headers[b'content-security-policy']
    assert headers[b'x-content-type-options'] == b'nosniff'

def test_frame_roundtrip_and_bounded_input():
    pipe = io.BytesIO()
    write_frame(pipe, {'ok':True})
    pipe.seek(0)
    assert read_frame(pipe) == {'ok':True}
    with pytest.raises(ValueError): read_frame(io.BytesIO(b'\xff\xff\xff\xff'))
    with pytest.raises(EOFError): read_frame(io.BytesIO(b'\x00'))

def test_invalid_bootstrap_and_session_repr():
    value = {'protocol':1, 'session_id':'s'*32,'token':TOKEN,'build_id':'build','edition':'local'}
    assert TOKEN not in repr(DesktopSession.bootstrap(value,12345))
    for change in ({'token':''},{'protocol':2},{'session_id':'short'},{'edition':'unknown'}):
        with pytest.raises(ValueError): DesktopSession.bootstrap({**value,**change},12345)

def test_spa_missing_and_remote_assets(tmp_path):
    from app.desktop.sidecar import verify_spa
    with pytest.raises(FileNotFoundError): verify_spa(tmp_path)
    (tmp_path/'index.html').write_text('<script src="/assets/main.js"></script>')
    with pytest.raises(ValueError): verify_spa(tmp_path)
    (tmp_path/'assets').mkdir()
    (tmp_path/'assets/main.js').write_text('ok')
    assert verify_spa(tmp_path) == tmp_path
    (tmp_path/'index.html').write_text('<script src="https://evil.test/main.js"></script>')
    with pytest.raises(ValueError): verify_spa(tmp_path)

def test_xdg_and_windows_layout(monkeypatch,tmp_path):
    from app import paths
    monkeypatch.setenv('OMOS_FROZEN','1')
    for name in ('OMOS_WORKSPACE_DIR','OMOS_LOG_DIR','OMOS_BUNDLE_DIR'):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setattr(paths.sys,'platform','linux')
    monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path/'data'))
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    assert paths.user_data_root() == tmp_path/'data'/'OpenMarketingOS'
    assert paths.logs_dir() == tmp_path/'state'/'OpenMarketingOS'/'logs'
    monkeypatch.setenv('XDG_DATA_HOME','relative')
    with pytest.raises(ValueError): paths.user_data_root()
    monkeypatch.setattr(paths.sys,'platform','win32')
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'windows'))
    assert paths.user_data_root() == tmp_path/'windows'/'OpenMarketingOS'

def test_manifest_is_independent_of_bootstrap(tmp_path, monkeypatch):
    import json
    import sys
    from app.paths import APP_VERSION
    from app.desktop.sidecar import verify_manifest
    value = {'schema_version':1,'version':APP_VERSION,'build_id':'build','edition':'local','platform':sys.platform,'arch':'x64'}
    target = tmp_path/'build-manifest.json'
    target.write_text(json.dumps(value))
    assert verify_manifest(tmp_path,session()) == value
    for field,new in [('build_id','stale'),('edition','openrouter'),('version','0.0.0'),('platform','unknown')]:
        target.write_text(json.dumps({**value,field:new}))
        with pytest.raises(ValueError): verify_manifest(tmp_path,session())

def test_desktop_environment_cannot_start_factory_without_bootstrap(monkeypatch):
    from app.main import create_app
    monkeypatch.setenv('OMOS_DESKTOP_MODE','1')
    with pytest.raises(RuntimeError,match='bootstrap'):
        create_app()

def test_posix_cleanup_escalates_only_owned_group(monkeypatch):
    import signal
    import time
    from app.desktop import sidecar
    calls = []
    monkeypatch.setattr(sidecar.os,'getpgrp',lambda:8123,raising=False)
    monkeypatch.setattr(sidecar.os,'killpg',lambda group,sig:calls.append(('kill',group,sig)),raising=False)
    monkeypatch.setattr(signal,'signal',lambda sig,handler:calls.append(('ignore',sig,handler)))
    monkeypatch.setattr(time,'sleep',lambda seconds:calls.append(('wait',seconds)))
    # SIGKILL may not exist on Windows; POSIX value is only used in this mocked helper.
    monkeypatch.setattr(signal,'SIGKILL',9,raising=False)
    sidecar.terminate_posix_group()
    assert calls == [('ignore',signal.SIGTERM,signal.SIG_IGN),('kill',8123,signal.SIGTERM),('wait',.2),('kill',8123,9)]


def test_x64_check_survives_a_cleaned_environment(monkeypatch):
    """Regression: Electron's allow-listed env has no PROCESSOR_ARCHITECTURE and
    platform.machine() can then be empty, which used to refuse sidecar startup."""
    from app.desktop import sidecar
    for name in ("PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sidecar.platform, "machine", lambda: "")
    assert sidecar._is_x64() is True
    monkeypatch.setattr(sidecar.struct, "calcsize", lambda _fmt: 4)
    assert sidecar._is_x64() is False
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "ARM64")
    assert sidecar._is_x64() is False
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    assert sidecar._is_x64() is True


def test_control_pipe_monitor_returns_on_eof_and_on_data():
    """The monitor must wake on parent EOF or any byte, without a blocking CRT read."""
    import os
    import threading
    from app.desktop import sidecar
    for action in ("close", "byte"):
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb")
        done = threading.Event()
        threading.Thread(target=lambda: (sidecar._wait_for_control_close(reader, poll=0.01), done.set()), daemon=True).start()
        assert not done.wait(0.1)
        if action == "close":
            os.close(write_fd)
        else:
            os.write(write_fd, b"x")
        assert done.wait(3)
        if action == "byte":
            os.close(write_fd)
        reader.close()
