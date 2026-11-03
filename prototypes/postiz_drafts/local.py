"""Trusted operator CLI. Not a model tool, web endpoint, or startup integration."""
import argparse
import json
from pathlib import Path

from app.database.sqlite import connect
from app.services import state
from prototypes.postiz_readonly.client import Binding
from .host import ControlledDraftHost
from .transport import DraftTransport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True,
        help='Operator-owned JSON: database, project, organization, endpoint, secret_ref, channel')
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('campaign')
    prepare.add_argument('deliverable')
    prepare.add_argument('version', type=int)
    for name in ('status', 'approve', 'reject', 'execute', 'reconcile'):
        child = commands.add_parser(name)
        child.add_argument('operation')
        if name in {'execute', 'reconcile'}:
            child.add_argument('--founder-authorized-local-acceptance', action='store_true',
                help='Use only AFTER separate founder approval and independent review gates')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        if set(config) != {'database', 'project', 'organization', 'endpoint', 'secret_ref', 'channel'}:
            raise ValueError('Invalid operator configuration')
        # Config is administrative authority, never accepted from LLM arguments.
        binding = Binding(**{key: config[key] for key in ('project', 'organization', 'endpoint', 'secret_ref')})
        conn = connect(Path(config['database']))
        try:
            network = args.command in {'execute', 'reconcile'}
            if network and not args.founder_authorized_local_acceptance:
                raise ValueError('Separate founder authorization required')
            host = ControlledDraftHost(conn, DraftTransport(binding, conn,
                channel=config['channel'], allow_network=network))
            if args.command == 'prepare':
                result = host.prepare(args.campaign, args.deliverable, args.version)
            elif args.command == 'status':
                result = host.status(args.operation)
            elif args.command in {'approve', 'reject'}:
                receipt = host.status(args.operation)
                state.decide_approval(conn, receipt['approval_id'],
                    'approved' if args.command == 'approve' else 'rejected',
                    decided_by='local-founder', project_id=binding.project)
                result = host.status(args.operation)
            else:
                host.enable()
                receipt = host.status(args.operation)
                result = host.execute(args.operation, receipt['approval_id']) if args.command == 'execute' else host.reconcile(args.operation)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            conn.close()
    except Exception:
        # Errors may contain sensitive database paths, campaign text or vault errors.
        print(json.dumps({'ok': False, 'error': 'Local draft operation refused or uncertain; inspect local state.'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
