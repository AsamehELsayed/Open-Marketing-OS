"""Real process exit/stdout/stderr and persisted state; all writes are mocks."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.services import campaign_deliverables as editorial, state
from app.tests.test_postiz_drafts import setup  # noqa: F401 (shared fixture)
from prototypes.marketing_mcp.demo import PROJECT, CAMPAIGN, approve

ROOT = Path(__file__).resolve().parents[2]
AUTHORIZED = '--founder-authorized-local-acceptance'


@pytest.fixture
def cli(setup, tmp_path):
    host, row, *_ = setup
    row = approve(host.conn, row)
    config = tmp_path / 'operator.json'
    config.write_text(json.dumps({'database': host.conn.execute('PRAGMA database_list').fetchone()[2],
        'project': PROJECT, 'organization': host.binding.organization,
        'endpoint': host.binding.endpoint, 'secret_ref': host.binding.secret_ref,
        'channel': host.transport.channel}))
    remote = tmp_path / 'synthetic-remote.json'

    def invoke(*args, mode='success'):
        script = "import sys;sys.path.insert(0,sys.argv.pop(1));from app.tests.postiz_cli_runner import run;raise SystemExit(run())"
        result = subprocess.run([sys.executable, '-I', '-c', script, str(ROOT),
            str(remote), mode, '--config', str(config), *args], cwd=tmp_path,
            capture_output=True, text=True, timeout=60)
        assert result.stderr == '', result.stderr
        assert 'synthetic-secret-sentinel' not in result.stdout + result.stderr
        assert 'untrusted.invalid' not in result.stdout + result.stderr
        return result.returncode, json.loads(result.stdout)

    return host, row, invoke, remote


def prepared(cli, *, in_subprocess=False):
    host, row, invoke, _ = cli
    if not in_subprocess:
        return host.prepare(CAMPAIGN, row['id'], row['current_version'])
    code, operation = invoke('prepare', CAMPAIGN, row['id'], str(row['current_version']))
    assert code == 0
    assert operation['state'] == 'prepared'
    return operation


def decide(cli, operation, decision='approved'):
    host = cli[0]
    state.decide_approval(host.conn, operation['approval_id'], decision, project_id=PROJECT)


def test_preparation_and_approved_status_are_successful_reads(cli):
    operation = prepared(cli, in_subprocess=True)
    assert operation['payload']['socialPost'][0]['type'] == 'draft'
    decide(cli, operation)
    code, result = cli[2]('status', operation['operation_id'])
    assert code == 0 and result['approval_status'] == 'approved'
    assert result['state'] == 'prepared' and result['post_id'] is None
    assert json.loads(cli[3].read_text()) == []


def test_missing_authorization_is_nonzero_and_preserves_ledger(cli):
    operation = prepared(cli)
    decide(cli, operation)
    code, result = cli[2]('execute', operation['operation_id'])
    assert code == 1 and result['ok'] is False
    assert cli[0].status(operation['operation_id'])['state'] == 'prepared'
    assert json.loads(cli[3].read_text()) == []


@pytest.mark.parametrize('decision', ['pending', 'rejected', 'stale'])
def test_denied_or_stale_approval_is_nonzero_without_dispatch(cli, decision):
    operation = prepared(cli)
    if decision != 'pending':
        decide(cli, operation, 'rejected' if decision == 'rejected' else 'approved')
    if decision == 'stale':
        host, row, *_ = cli
        editorial.revise(host.conn, PROJECT, CAMPAIGN, row['id'], row['current_version'],
            {'content_md': 'Unapproved replacement'})
    code, result = cli[2]('execute', operation['operation_id'], AUTHORIZED)
    assert code == 1 and result['ok'] is False
    assert cli[0].status(operation['operation_id'])['state'] == 'prepared'
    assert json.loads(cli[3].read_text()) == []


def test_success_and_duplicate_execution_return_zero_once(cli):
    operation = prepared(cli)
    decide(cli, operation)
    first = cli[2]('execute', operation['operation_id'], AUTHORIZED)
    second = cli[2]('execute', operation['operation_id'], AUTHORIZED)
    assert first == second == (0, {'ok': True, 'state': 'succeeded', 'post_id': 'synthetic-post-1'})
    assert len(json.loads(cli[3].read_text())) == 1
    assert cli[0].status(operation['operation_id'])['state'] == 'succeeded'


def test_ambiguous_write_retry_and_manual_reconciliation_are_nonzero(cli):
    operation = prepared(cli)
    decide(cli, operation)
    code, result = cli[2]('execute', operation['operation_id'], AUTHORIZED, mode='ambiguous')
    assert code == 2 and result['ok'] is False
    assert cli[0].status(operation['operation_id'])['state'] == 'ambiguous'
    assert len(json.loads(cli[3].read_text())) == 1
    code, result = cli[2]('execute', operation['operation_id'], AUTHORIZED)
    assert code == 2 and result['state'] == 'ambiguous'
    assert len(json.loads(cli[3].read_text())) == 1
    code, result = cli[2]('reconcile', operation['operation_id'], AUTHORIZED)
    assert code == 2 and result['state'] == 'manual_reconciliation_required'
    assert result['retry_allowed'] is False
    assert result['candidate_ids'] == ['synthetic-post-1']
    assert len(json.loads(cli[3].read_text())) == 1
    code, result = cli[2]('status', operation['operation_id'])
    assert code == 0 and result['state'] == 'ambiguous'


def test_late_edit_preserves_remote_id_with_nonzero_exit_and_no_retry(cli):
    operation = prepared(cli)
    decide(cli, operation)
    code, result = cli[2]('execute', operation['operation_id'], AUTHORIZED, mode='late-edit')
    assert code == 2
    assert result == {'ok': False, 'state': 'succeeded_stale', 'post_id': 'synthetic-post-1'}
    assert cli[0].status(operation['operation_id'])['state'] == 'succeeded_stale'
    assert cli[2]('execute', operation['operation_id'], AUTHORIZED) == (code, result)
    assert len(json.loads(cli[3].read_text())) == 1
