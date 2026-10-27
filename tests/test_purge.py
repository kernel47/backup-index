from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.config import PROJECT_ROOT
from app.mappings import MappingError, load_mappings
from app.purge import (
    removed_versions, purge_options, history_targets, plan_purge, execute_purge,
)


def versions(count=12):
    return [{'created_at': f'2026-01-{day:02d}T00:00:00Z', 'hash': str(day)}
            for day in range(1, count + 1)]


def test_keep_chronological():
    history = list(reversed(versions()))
    original = deepcopy(history)
    assert len(removed_versions(history, {'mode': 'keep', 'keep': 5})) == 7
    assert removed_versions(history, {'mode': 'keep', 'keep': 5})[-1]['hash'] == '7'
    assert history == original


def test_age_and_all():
    options = purge_options(None, '90d', False, datetime(2026, 4, 2, tzinfo=timezone.utc))
    assert options['cutoff'] == '2026-01-02T00:00:00Z'
    assert len(removed_versions(versions(), options)) == 1
    assert len(removed_versions(versions(), {'mode': 'all'})) == 12
    assert removed_versions([], {'mode': 'all'}) == []


@pytest.mark.parametrize('args', [(None, None, False), (1, '90d', False), (-1, None, False), (None, '90', False)])
def test_invalid_options(args):
    with pytest.raises(MappingError):
        purge_options(*args)


def test_history_targets():
    documents = load_mappings(PROJECT_ROOT / 'mappings', 'backup')
    assert history_targets(documents, 'backup') == ['backup-policies-v1', 'backup-slps-v1']
    with pytest.raises(MappingError):
        history_targets(documents, 'backup', 'jobs')
    documents['policies']['index'] = 'backup-unrelated'
    with pytest.raises(MappingError):
        history_targets(documents, 'backup')


def mock_client(monkeypatch):
    client = MagicMock()
    client.indices.exists.return_value = True
    client.indices.get_alias.return_value = {'backup-policies-v1': {'aliases': {'backup-policies': {'is_write_index': True}}}}
    client.indices.get.return_value = {'backup-policies-v1': {}}
    monkeypatch.setattr('app.purge.scan', lambda *a, **kw: iter([
        {'_index': 'backup-policies-v1', '_id': 'p1', '_version': 4,
         '_source': {'history': versions()}},
    ]))
    return client


def test_plan_no_write(monkeypatch):
    client = mock_client(monkeypatch)
    plan = plan_purge(client, ['backup-policies-v1'], {'mode': 'keep', 'keep': 5})
    assert plan[0]['before'] == 12 and plan[0]['after'] == 5
    client.update_by_query.assert_not_called()


def test_update_by_query_only_history_and_checked_snapshot(monkeypatch):
    client = mock_client(monkeypatch)
    options = {'mode': 'keep', 'keep': 5}
    plan = plan_purge(client, ['backup-policies-v1'], options)
    client.update_by_query.return_value = {'updated': 1, 'deleted': 0, 'version_conflicts': 0, 'noops': 0, 'failures': [], 'timed_out': False, 'total': 1}
    result = execute_purge(client, ['backup-policies-v1'], plan, options)
    assert result['confirmed_removed_versions'] == 7
    kwargs = client.update_by_query.call_args.kwargs
    assert kwargs['index'] == 'backup-policies-v1'
    assert kwargs['body']['query'] == {'ids': {'values': ['p1']}}
    assert kwargs['body']['script']['params']['expected_snapshots']['p1']['history'] == versions()
    source = kwargs['body']['script']['source']
    assert 'ctx._source.history = retained' in source
    assert 'ctx._source.clear' not in source and "ctx.op = 'delete'" not in source
    assert kwargs['params']['requests_per_second'] == 1
    client.indices.delete.assert_not_called()
    client.delete.assert_not_called()


@pytest.mark.parametrize('response', [
    {'updated': 0, 'noops': 1}, {'updated': 0, 'version_conflicts': 1},
    {'updated': 1, 'timed_out': True}, {'updated': 0, 'failures': [{'reason': 'secret'}]},
])
def test_partial_results(monkeypatch, response):
    client = mock_client(monkeypatch)
    options = {'mode': 'all'}
    plan = plan_purge(client, ['backup-policies-v1'], options)
    client.update_by_query.return_value = response
    result = execute_purge(client, ['backup-policies-v1'], plan, options)
    assert result['status'] == 'PARTIAL' and result['confirmed_removed_versions'] == 0


def test_cli_confirmation_and_dry_run(monkeypatch):
    client = mock_client(monkeypatch)
    monkeypatch.setattr('app.cli.create_client', lambda _: client)
    runner = CliRunner()
    result = runner.invoke(app, ['purge', 'history', '--keep', '5', '--type', 'policies', '--dry-run'])
    assert result.exit_code == 0 and 'DRY-RUN' in result.output
    client.update_by_query.assert_not_called()
    result = runner.invoke(app, ['purge', 'history', '--all', '--type', 'policies'], input='n\n')
    assert result.exit_code == 1 and 'annulée' in result.output
    client.update_by_query.assert_not_called()


def test_cli_yes_and_replay(monkeypatch):
    client = mock_client(monkeypatch)
    client.update_by_query.return_value = {'updated': 1, 'deleted': 0, 'version_conflicts': 0, 'noops': 0, 'failures': [], 'timed_out': False, 'total': 1}
    monkeypatch.setattr('app.cli.create_client', lambda _: client)
    result = CliRunner().invoke(app, ['purge', 'history', '--all', '--type', 'policies', '--yes'])
    assert result.exit_code == 0, result.output
    client.update_by_query.assert_called_once()
    client.update_by_query.reset_mock()
    monkeypatch.setattr('app.purge.scan', lambda *a, **kw: iter([]))
    result = CliRunner().invoke(app, ['purge', 'history', '--all', '--type', 'policies', '--yes'])
    assert result.exit_code == 0
    client.update_by_query.assert_not_called()


def test_timeout_preserves_partial_summary(monkeypatch):
    from opensearchpy.exceptions import ConnectionTimeout
    client = mock_client(monkeypatch)
    options = {'mode': 'all'}
    plan = plan_purge(client, ['backup-policies-v1'], options)
    client.update_by_query.side_effect = ConnectionTimeout('TIMEOUT', 'secret', None)
    result = execute_purge(client, ['backup-policies-v1'], plan, options)
    assert result['status'] == 'PARTIAL'
    assert result['interrupted_batch_outcome'] == 'UNKNOWN'
    assert 'secret' not in str(result)


def test_invalid_history_date_before_write(monkeypatch):
    client = mock_client(monkeypatch)
    with pytest.raises(MappingError):
        removed_versions([{'created_at': '2026-01-01 00:00:00Z'}], {'mode': 'all'})
    with pytest.raises(MappingError):
        removed_versions([{'created_at': 'not-a-date'}], {'mode': 'keep', 'keep': 5})
    client.update_by_query.assert_not_called()


def test_alias_conflict_blocks_purge_before_scan(monkeypatch):
    client = mock_client(monkeypatch)
    client.indices.get_alias.return_value = {'backup-policies-v2': {'aliases': {'backup-policies': {}}}}
    scanned = MagicMock()
    monkeypatch.setattr('app.purge.scan', scanned)
    monkeypatch.setattr('app.cli.create_client', lambda _: client)
    result = CliRunner().invoke(app, ['purge', 'history', '--all', '--type', 'policies', '--yes'])
    assert result.exit_code == 2 and 'CONFLICT' in result.output
    scanned.assert_not_called()
    client.update_by_query.assert_not_called()
