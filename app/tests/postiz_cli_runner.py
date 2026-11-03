"""Subprocess-only test adapter: real CLI/ledger, synthetic HTTP and vault."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.services import campaign_deliverables as editorial
from app.tests.test_postiz_drafts import setup
from app.tests.test_postiz_readonly import MemoryBackend
from prototypes.postiz_drafts import local
from prototypes.postiz_drafts.transport import DraftTransport


def run():
    remote, mode = Path(sys.argv[1]), sys.argv[2]
    sys.argv = ['local', *sys.argv[3:]]
    with TemporaryDirectory(prefix='offline-cli-', dir=remote.parent) as scratch:
        fixture = setup.__wrapped__(Path(scratch))
        host, _, _, posts, knobs = next(fixture)
        if remote.exists():
            posts[:] = json.loads(remote.read_text())
        knobs['timeout'] = mode == 'ambiguous'

        def transport(binding, conn, **kwargs):
            # Only synthetic fixture refs are reconstructed, never the real vault.
            backend = MemoryBackend()
            for row in conn.execute('SELECT backend_key FROM credentials_refs'):
                backend.put(row['backend_key'], b'synthetic-secret-sentinel')
            if mode == 'late-edit':
                def edit_after_mock_commit():
                    row = conn.execute('SELECT * FROM campaign_deliverables WHERE project_id=?',
                        (binding.project,)).fetchone()
                    editorial.revise(conn, binding.project, row['campaign_id'], row['id'],
                        row['current_version'], {'content_md': 'Unapproved late replacement'})
                knobs['during_write'] = edit_after_mock_commit
            return DraftTransport(binding, conn, backend, channel=kwargs['channel'],
                http_transport=host.transport.http_transport)

        try:
            # A regression accidentally constructing a real network transport
            # must fail immediately instead of reaching even a local endpoint.
            with patch.object(local, 'DraftTransport', transport), patch(
                    'httpx.AsyncHTTPTransport', side_effect=AssertionError('Real HTTP forbidden')):
                return local.main()
        finally:
            remote.write_text(json.dumps(posts))
            next(fixture, None)


if __name__ == '__main__':
    raise SystemExit(run())
