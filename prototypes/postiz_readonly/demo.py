"""Real local endpoint only. Never falls back to mocks or prints credentials."""
import argparse
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from app.services.credentials import vault
from app.services.credentials.store import DpapiFileBackend
from .client import ALLOW, Binding, PostizHost, PostizTransport
from .environment.provision import local_secrets


def demonstrate(endpoint):
    values = local_secrets()
    receipt = {'evidence_type':'LIVE_LOCAL_POSTIZ', 'success':False, 'organizations':[], 'denied':[]}
    with TemporaryDirectory(prefix='dev050-vault-') as scratch:
        backend = DpapiFileBackend(Path(scratch))
        with sqlite3.connect(':memory:') as conn:
            hosts = []
            for suffix in ['a','b']:
                project = 'dev050-project-'+suffix
                ref = vault.store('project','postiz',values['DEV050_KEY_'+suffix.upper()],project_id=project,conn=conn,backend=backend)
                binding = Binding(project,'dev050-org-'+suffix,endpoint,ref)
                host = PostizHost(conn,PostizTransport(binding,conn,backend))
                assert not host.call(project,'groupList',{})['ok']
                discovered = host.discover()
                assert set(discovered) == ALLOW
                host.enable()
                groups = host.call(project,'groupList',{})
                channels = host.call(project,'integrationList',{})
                schema = host.call(project,'integrationSchema',{'platform':'linkedin','isPremium':False})
                if not all(row.get('ok') for row in [groups,channels,schema]):
                    raise RuntimeError('Live read-only call failed')
                # Validate inert tenant fixture identity, not merely empty-array equality.
                assert [row['id'] for row in groups['payload']['output']] == ['dev050-group-'+suffix]
                channel_ids = [row['id'] for row in channels['payload']['output']]
                assert channel_ids in [[], ['dev050-inert-'+suffix]]
                receipt['organizations'].append({'fixture':suffix,'discovered':sorted(discovered),
                    'groups':len(groups['payload']['output']), 'channel_ids':channel_ids,
                    'schema_max_length':schema['payload']['output']['maxLength'], 'audit':host.audit()})
                for name in ['triggerTool','schedulePostTool','integrationSchedulePostTool','deletePostTool',
                             'uploadWidgetTool','generateImageTool','generateVideoTool','ask_postiz','unknown']:
                    assert not host.call(project,name,{})['ok']
                    receipt['denied'].append(name)
                assert not host.call('dev050-project-other','groupList',{})['ok']
                hosts.append(host)
            # Interleave the two immutable credential bindings and verify no cross-over.
            for host in [hosts[1],hosts[0],hosts[1]]:
                assert host.call(host.binding.project,'groupList',{})['ok']
            invalid_ref = vault.store('project','invalid','dev050-invalid-local-key',project_id='invalid',conn=conn,backend=backend)
            invalid = PostizTransport(Binding('invalid','dev050-org-a',endpoint,invalid_ref),conn,backend)
            try:
                invalid.list_tools(endpoint)
            except RuntimeError:
                receipt['invalid_credential_denied'] = True
            else:
                raise RuntimeError('Invalid credential accepted')
            receipt['registry_runs'] = conn.execute('SELECT COUNT(*) FROM tool_runs').fetchone()[0]
            for host, row in zip(hosts,receipt['organizations']):
                row.update(protocol=host.transport.protocol, upstream_tool_count=host.transport.inventory_count,
                           request_counts=host.transport.request_counts, audit=host.audit())
    receipt['success'] = True
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--endpoint',default='http://127.0.0.1:5199/api/mcp')
    parser.add_argument('--receipt',type=Path,required=True)
    args = parser.parse_args()
    try:
        result = demonstrate(args.endpoint)
    except Exception:
        result = {'evidence_type':'LIVE_LOCAL_POSTIZ_ATTEMPT','success':False,
                  'error':'Local protocol/acceptance failed; inspect private local logs. No fallback or mock used.'}
    args.receipt.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'evidence_type':result['evidence_type'],'success':result['success']}))
    raise SystemExit(0 if result['success'] else 1)
