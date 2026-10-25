"""Executable sidecar. stdout is a private framed control channel, never a log."""
import asyncio
import os
import json
import platform
from pathlib import Path
import re
import signal
import socket
import sys
import threading
from urllib.parse import urlsplit
from app.desktop.session import DesktopSession, DesktopBoundary, read_frame, write_frame


def verify_spa(root):
    """Require real production entry and every referenced executable/style asset."""
    root = Path(root).resolve()
    index = root / 'index.html'
    text = index.read_text(encoding='utf-8')
    refs = re.findall(r'(?:src|href)=["\']([^"\']+)["\']', text)
    scripts = [ref for ref in refs if urlsplit(ref).path.endswith(('.js', '.css'))]
    if not scripts or not any(urlsplit(ref).path.endswith('.js') for ref in scripts):
        raise ValueError('production SPA assets missing')
    for ref in scripts:
        parsed = urlsplit(ref)
        if parsed.scheme or parsed.netloc:
            raise ValueError('remote production asset')
        target = (root / parsed.path.lstrip('/')).resolve()
        target.relative_to(root)
        if not target.is_file():
            raise ValueError('production SPA asset missing')
    return root


def verify_manifest(root, session):
    from app.paths import APP_VERSION
    value = json.loads((Path(root)/'build-manifest.json').read_text(encoding='utf-8'))
    expected = {'schema_version':1, 'version':APP_VERSION, 'build_id':session.build_id,
                'edition':session.edition, 'platform':sys.platform, 'arch':'x64'}
    if (not isinstance(value,dict) or any(value.get(k) != v for k,v in expected.items())
        or platform.machine().lower() not in ('amd64','x86_64')):
        raise ValueError('installed build manifest mismatch')
    return expected


def _own_process_tree():
    if os.name != 'nt':
        os.setsid()
        return None
    import ctypes
    from ctypes import wintypes
    class BASIC(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit',ctypes.c_int64),('PerJobUserTimeLimit',ctypes.c_int64),('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',wintypes.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name,ctypes.c_uint64) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
    class EXTENDED(ctypes.Structure):
        _fields_ = [('BasicLimitInformation',BASIC),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE,wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None,None)
    if not job:
        raise RuntimeError('process ownership unavailable')
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job,9,ctypes.byref(info),ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(job,kernel.GetCurrentProcess()):
        kernel.CloseHandle(job)
        raise RuntimeError('process ownership unavailable')
    return (kernel, job)


def terminate_posix_group():
    """Only called after this process successfully established its own session."""
    import time
    group = os.getpgrp()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.killpg(group, signal.SIGTERM)
    # SQLite/lifespan shutdown completed; bound cleanup of TERM-ignoring trees.
    time.sleep(.2)
    os.killpg(group, signal.SIGKILL)


def main():
    # Preserve binary protocol handles before redirecting all ordinary print output.
    control_in, control_out = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr
    ownership = None
    owns_group = False
    server = None
    finished = threading.Event()
    def force_exit():
        if not finished.wait(5):
            if os.name != 'nt':
                os.killpg(os.getpgrp(), signal.SIGKILL)
            os._exit(1) # OS closes Windows kill-on-close Job handle.
    def request_stop():
        if server is not None:
            server.should_exit = True
        threading.Thread(target=force_exit,daemon=True).start()
    try:
        ownership = _own_process_tree()
        owns_group = os.name != "nt"
        # Even a parent that never sends bootstrap cannot orphan a waiting child.
        bootstrap_expired = threading.Timer(15, lambda: os._exit(1))
        bootstrap_expired.daemon = True
        bootstrap_expired.start()
        bootstrap = read_frame(control_in)
        bootstrap_expired.cancel()
        listener = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        listener.bind(('127.0.0.1',0))
        listener.listen(128)
        listener.setblocking(False)
        session = DesktopSession.bootstrap(bootstrap,listener.getsockname()[1])
        from app import paths
        manifest = verify_manifest(paths.bundle_root(),session)
        root = verify_spa(paths.bundle_root() / 'frontend' / 'dist')
        # Same source router, redirected to immutable installed resources.
        from app.routes import spa
        spa.DIST_DIR, spa.INDEX_FILE, spa.ASSETS_DIR = root, root/'index.html',root/'assets'
        spa.FAVICON_FILE = root/'favicon.ico'
        from app.main import create_app
        from fastapi import Request
        from fastapi.responses import JSONResponse
        app = create_app(desktop_mode=True)
        from app.paths import APP_VERSION
        metadata = {'protocol':1,'session_id':session.session_id,'pid':os.getpid(),'version':APP_VERSION,'build_id':manifest['build_id'],'edition':manifest['edition']}
        @app.get('/desktop/ready', include_in_schema=False)
        async def ready(request: Request):
            nonce = request.query_params.get('nonce','')
            if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}',nonce):
                return JSONResponse({'detail':'invalid nonce'},status_code=400)
            return {'ready':True,**metadata,'nonce':nonce}
        @app.post('/desktop/shutdown', include_in_schema=False)
        async def shutdown():
            request_stop()
            return {'stopping':True}
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(DesktopBoundary(app,session),log_config=None,access_log=False,log_level='critical',timeout_graceful_shutdown=3))
        def monitor():
            # No additional commands are accepted; any bytes or EOF invalidate ownership.
            control_in.read(1)
            request_stop()
        threading.Thread(target=monitor,daemon=True).start()
        async def run():
            task = asyncio.create_task(server.serve(sockets=[listener]))
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError('startup failed')
                await asyncio.sleep(.02)
            write_frame(control_out,{'type':'ready',**metadata,'port':session.port})
            await task
        asyncio.run(run())
    except BaseException:
        try: write_frame(control_out,{'type':'error','code':'startup_failed'})
        except Exception: pass
        sys.stderr.write('Desktop sidecar stopped.\n')
        raise SystemExit(1) from None
    finally:
        finished.set()
        if ownership:
            ownership[0].CloseHandle(ownership[1])
        elif owns_group:
            # Own private session only. Intentional POSIX exit may be SIGKILL.
            terminate_posix_group()

if __name__ == '__main__':
    main()
