from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner
from opensearchpy.exceptions import AuthenticationException, ConnectionError, AuthorizationException, NotFoundError

from app.cli import app
from app.config import load_settings

runner = CliRunner()


def test_validate_all_without_client(monkeypatch):
    def forbidden(*args):
        raise AssertionError('network forbidden')
    monkeypatch.setattr('app.commands.runtime.create_client', forbidden)
    result = runner.invoke(app, ['validate', '--all'])
    assert result.exit_code == 0, result.output
    assert result.output.count('"status": "OK"') == 5


def test_module_and_list():
    result = runner.invoke(app, ['list'])
    assert result.exit_code == 0 and 'backup-slps' in result.output and 'nas-files' not in result.output


def test_validate_target():
    result = runner.invoke(app, ['validate', 'jobs'])
    assert result.exit_code == 0, result.output
    assert 'backup-jobs' in result.output and 'backup-images' not in result.output


@pytest.mark.parametrize('target', ['images-contents', 'backup-images-contents', 'backup-images-contents-v1'])
def test_validate_images_contents(target):
    result = runner.invoke(app, ['validate', target])
    assert result.exit_code == 0, result.output
    assert 'backup-images-contents-v1' in result.output
    assert result.output.count('"status": "OK"') == 1


@pytest.mark.parametrize('args', [['validate'], ['validate', 'unknown'], ['check', 'jobs', '--all']])
def test_invalid_selection(args):
    assert runner.invoke(app, args).exit_code == 2


@pytest.mark.parametrize('error, message', [
    (ConnectionError('N/A', 'secret-password', None), 'Connexion'),
    (AuthenticationException(401, 'secret-password', {}), 'Authentification'),
    (AuthorizationException(403, 'secret-password', {}), 'Permissions'),
])
def test_safe_errors(monkeypatch, error, message):
    client = MagicMock()
    client.indices.get_alias.return_value = {'backup-jobs-v1': {'aliases': {'backup-jobs': {'is_write_index': True}}}}
    client.info.side_effect = error
    monkeypatch.setattr('app.commands.runtime.create_client', lambda _: client)
    result = runner.invoke(app, ['ping'])
    assert result.exit_code == 1
    assert message in result.output and 'secret-password' not in result.output
    client.close.assert_called_once()


def test_check_nonzero(monkeypatch):
    client = MagicMock()
    client.indices.get_alias.return_value = {'backup-jobs-v1': {'aliases': {'backup-jobs': {'is_write_index': True}}}}
    client.indices.exists.return_value = False
    client.indices.get_alias.side_effect = NotFoundError(404, 'missing', {})
    monkeypatch.setattr('app.commands.runtime.create_client', lambda _: client)
    result = runner.invoke(app, ['check', '--all'])
    assert result.exit_code == 2 and result.output.count('"targets": []') == 5


def test_paths_and_environment(tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text('MAPPINGS_DIR=schemas\nINDEX_PREFIX=local\n')
    monkeypatch.setenv('INDEX_PREFIX', 'system')
    monkeypatch.chdir('/')
    settings = load_settings(env)
    assert settings.index_prefix == 'system'
    assert settings.mappings_dir == tmp_path / 'schemas'
    assert load_settings().mappings_dir == Path(__file__).resolve().parents[1] / 'mappings'


def test_invalid_config_does_not_leak(tmp_path):
    env = tmp_path / '.env'
    env.write_text('OPENSEARCH_URL=https://user:secret-password@example.com\n')
    result = runner.invoke(app, ['--env-file', str(env), 'ping'])
    assert result.exit_code == 2 and 'secret-password' not in result.output


def test_tls_client_options(monkeypatch, tmp_path):
    from app.client import create_client
    from app.config import Settings
    constructor = MagicMock()
    monkeypatch.setattr('app.client.OpenSearch', constructor)
    settings = Settings(opensearch_url='https://example.com:9200',
                        opensearch_username='user', opensearch_password='secret',
                        opensearch_ca_cert='/tmp/ca.pem', opensearch_timeout=12)
    create_client(settings)
    options = constructor.call_args.kwargs
    assert options['verify_certs'] is True
    assert options['ssl_show_warn'] is True
    assert options['ca_certs'] == '/tmp/ca.pem'
    assert options['timeout'] == 12
    assert options['http_auth'] == ('user', 'secret')
    assert 'secret' not in repr(settings)
    settings.opensearch_verify_ssl = False
    create_client(settings)
    options = constructor.call_args.kwargs
    assert options['verify_certs'] is False
    assert options['ssl_show_warn'] is False


def test_explicit_ca_resolution(tmp_path):
    env = tmp_path / '.env'
    (tmp_path / 'ca.pem').write_text('test CA path')
    env.write_text('OPENSEARCH_CA_CERT=ca.pem\n')
    assert load_settings(env).opensearch_ca_cert == str(tmp_path / 'ca.pem')


@pytest.mark.parametrize('value', ['OPENSEARCH_TIMEOUT=0', 'OPENSEARCH_VERIFY_SSL=wrong',
                                   'OPENSEARCH_CA_CERT=missing.pem', 'LOG_LEVEL=wrong'])
def test_invalid_configuration(tmp_path, value):
    env = tmp_path / '.env'
    env.write_text(value + '\n')
    result = runner.invoke(app, ['--env-file', str(env), 'validate', '--all'])
    assert result.exit_code == 2


def test_cli_dry_run_no_write(monkeypatch):
    import json
    document = json.loads((Path(__file__).resolve().parents[1] / 'mappings/jobs.json').read_text())
    del document['mappings']['properties']['client']
    client = MagicMock()
    client.indices.get_alias.return_value = {'backup-jobs-v1': {'aliases': {'backup-jobs': {'is_write_index': True}}}}
    client.indices.exists.return_value = True
    client.indices.get.return_value = {'backup-jobs-v1': {}}
    client.indices.get_mapping.return_value = {'backup-jobs-v1': {'mappings': document['mappings']}}
    monkeypatch.setattr('app.commands.runtime.create_client', lambda _: client)
    result = runner.invoke(app, ['apply', 'jobs', '--dry-run'])
    assert result.exit_code == 0, result.output
    assert 'DRY-RUN' in result.output and 'client' in result.output
    client.indices.put_mapping.assert_not_called()
    client.indices.create.assert_not_called()


@pytest.mark.parametrize('command', ['clear', 'delete'])
def test_destructive_dry_run_and_confirmation(monkeypatch, command):
    client = MagicMock()
    client.indices.get_alias.return_value = {'backup-jobs-v1': {'aliases': {'backup-jobs': {'is_write_index': True}}}}
    client.indices.exists.return_value = True
    client.indices.get.return_value = {'backup-jobs-v1': {'settings': {'index': {'uuid': 'uuid'}}}}
    client.count.return_value = {'count': 4}
    monkeypatch.setattr('app.commands.runtime.create_client', lambda _: client)
    result = runner.invoke(app, [command, 'jobs', '--dry-run', '--yes'])
    assert result.exit_code == 0 and 'DRY-RUN' in result.output
    client.indices.delete.assert_not_called()
    client.delete_by_query.assert_not_called()
    client.indices.refresh.assert_not_called()
    result = runner.invoke(app, [command, 'jobs'], input='n\n')
    assert result.exit_code == 1 and 'annulée' in result.output
    client.indices.delete.assert_not_called()
    client.delete_by_query.assert_not_called()


@pytest.mark.parametrize('command', ['clear', 'delete'])
@pytest.mark.parametrize('target', ['*', 'backup-*', 'unknown'])
def test_destructive_unknown_and_wildcard_rejected(monkeypatch, command, target):
    client = MagicMock()
    client.indices.get_alias.return_value = {'backup-jobs-v1': {'aliases': {'backup-jobs': {'is_write_index': True}}}}
    monkeypatch.setattr('app.commands.runtime.create_client', lambda _: client)
    result = runner.invoke(app, [command, target, '--yes'])
    assert result.exit_code == 2
    client.indices.delete.assert_not_called()
    client.delete_by_query.assert_not_called()
    client.indices.exists.assert_not_called()


def test_delete_yes_and_missing_replay(monkeypatch):
    client = MagicMock()
    client.indices.get_alias.return_value = {'backup-jobs-v1': {'aliases': {'backup-jobs': {'is_write_index': True}}}}
    client.indices.get.return_value = {'backup-jobs-v1': {'settings': {'index': {'uuid': 'uuid'}}}}
    client.count.return_value = {'count': 4}
    client.indices.delete.return_value = {'acknowledged': True}
    client.indices.exists.side_effect = [True, True, False]
    monkeypatch.setattr('app.commands.runtime.create_client', lambda _: client)
    result = runner.invoke(app, ['delete', 'jobs', '--yes'])
    assert result.exit_code == 0 and 'DELETED' in result.output
    client.indices.delete.assert_called_once_with(index='backup-jobs-v1')
    client.indices.delete.reset_mock()
    client.indices.exists.side_effect = None
    client.indices.exists.return_value = False
    result = runner.invoke(app, ['delete', 'jobs', '--yes'])
    assert result.exit_code == 0 and 'MISSING' in result.output
    client.indices.delete.assert_not_called()


def test_clear_yes(monkeypatch):
    client = MagicMock()
    client.indices.get_alias.return_value = {'backup-jobs-v1': {'aliases': {'backup-jobs': {'is_write_index': True}}}}
    client.indices.exists.return_value = True
    client.indices.get.return_value = {'backup-jobs-v1': {'settings': {'index': {'uuid': 'uuid'}}}}
    client.count.side_effect = [{'count': 4}, {'count': 4}, {'count': 0}]
    client.indices.refresh.return_value = {'_shards': {'failed': 0}}
    client.delete_by_query.return_value = {'total': 4, 'deleted': 4, 'version_conflicts': 0, 'failures': [], 'timed_out': False}
    monkeypatch.setattr('app.commands.runtime.create_client', lambda _: client)
    result = runner.invoke(app, ['clear', 'jobs', '--yes'])
    assert result.exit_code == 0 and 'CLEARED' in result.output
    client.indices.delete.assert_not_called()


@pytest.mark.parametrize('command', ['clear', 'delete'])
def test_destructive_requires_single_target(command):
    assert runner.invoke(app, [command, '--all', '--yes']).exit_code == 2


@pytest.mark.parametrize('target', ['jobs', 'backup-jobs', 'backup-jobs-v1'])
def test_validate_alias_and_physical_without_cluster(monkeypatch, target):
    def forbidden(*args):
        raise AssertionError('No cluster during local validation')
    monkeypatch.setattr('app.commands.runtime.create_client', forbidden)
    result = runner.invoke(app, ['validate', target])
    assert result.exit_code == 0, result.output
    assert 'backup-jobs-v1' in result.output


def test_create_all_preflights_conflicts_before_any_write(monkeypatch):
    client = MagicMock()
    client.indices.get_alias.side_effect = NotFoundError(404, 'missing', {})
    client.indices.exists.side_effect = lambda index: index == 'backup-jobs'
    monkeypatch.setattr('app.commands.runtime.create_client', lambda _: client)
    result = runner.invoke(app, ['create', '--all'])
    assert result.exit_code == 2 and 'CONFLICT' in result.output
    client.indices.create.assert_not_called()
    client.indices.put_alias.assert_not_called()
