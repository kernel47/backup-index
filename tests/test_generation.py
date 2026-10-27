"""Évolution des contrats et génération sans cluster ni liste dupliquée."""
import json
import re
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.config import PROJECT_ROOT
from app.mappings import MappingError, load_mappings
from app.scripts import load_scripts


@pytest.fixture
def mappings_dir(tmp_path):
    directory = tmp_path / 'mappings'
    directory.mkdir()
    for path in (PROJECT_ROOT / 'mappings').glob('*.json'):
        (directory / path.name).write_text(path.read_text())
    return directory


def change(directory, kind, mutate):
    path = directory / f'{kind}.json'
    document = json.loads(path.read_text())
    mutate(document)
    path.write_text(json.dumps(document))


def field_lists(source, variable):
    return [set(json.loads(value)) for value in re.findall(rf'{variable} = (\[[^;]*\]);', source)]


def test_mapping_addition_generates_new_field_and_defaults_to_immutable(mappings_dir):
    original = load_scripts(mappings_dir=mappings_dir)
    change(mappings_dir, 'jobs', lambda doc: doc['mappings']['properties'].update(
        application={'type': 'keyword'}))
    generated = load_scripts(mappings_dir=mappings_dir)
    assert original['backup_jobs_upsert'] != generated['backup_jobs_upsert']
    assert 'application' in field_lists(generated['backup_jobs_upsert'], 'allowed')[0]
    assert 'application' in field_lists(generated['backup_jobs_upsert'], 'immutable')[0]
    change(mappings_dir, 'jobs', lambda doc: doc['ingestion']['mutable'].append('application'))
    mutable = load_scripts(mappings_dir=mappings_dir)['backup_jobs_upsert']
    assert 'application' not in field_lists(mutable, 'immutable')[0]
    assert load_scripts(mappings_dir=mappings_dir)['backup_jobs_upsert'] == mutable


def test_required_addition_and_independent_configuration_contracts(mappings_dir):
    def add_policy_field(doc):
        doc['mappings']['properties']['owner'] = {'type': 'keyword'}
        doc['ingestion']['required'].append('owner')
    change(mappings_dir, 'policies', add_policy_field)
    source = load_scripts(mappings_dir=mappings_dir)['backup_configuration_upsert']
    allowed = field_lists(source, 'allowed')
    required = field_lists(source, 'required')
    assert 'owner' in allowed[0] and 'owner' in required[0]
    assert 'owner' not in allowed[1] and 'owner' not in required[1]
    assert "for (def key : required)" in source


def test_field_names_are_escaped_without_rewriting_content(mappings_dir):
    field = 'def owner"\\name'
    change(mappings_dir, 'policies', lambda doc: doc['mappings']['properties'].update(
        {field: {'type': 'keyword'}}))
    source = load_scripts(mappings_dir=mappings_dir)['backup_configuration_upsert']
    assert field in field_lists(source, 'allowed')[0]


def test_template_requires_exactly_one_contract_marker(tmp_path):
    for path in (PROJECT_ROOT / 'scripts').glob('*.painless'):
        (tmp_path / path.name).write_text(path.read_text())
    path = tmp_path / 'jobs_upsert.painless'
    path.write_text(path.read_text().replace('/*__CONTRACT__*/', ''))
    with pytest.raises(MappingError, match='marqueur'):
        load_scripts(tmp_path)


@pytest.mark.parametrize('key,value', [
    ('required', ['master_server', 'missing']),
    ('required', ['master_server', 'master_server']),
    ('required', 'master_server'),
    ('required', ['created_at']),
    ('identity', []),
    ('identity', ['updated_at']),
    ('mutable', ['master_server']),
    ('mutable', ['updated_at']),
    ('mutable', ['created_at']),
    ('mutable', ['missing']),
    ('mutable', [1]),
    ('typo', []),
])
def test_invalid_rules_refused_locally(mappings_dir, key, value):
    change(mappings_dir, 'jobs', lambda doc: doc['ingestion'].update({key: value}))
    with pytest.raises(MappingError, match='ingestion'):
        load_mappings(mappings_dir, 'backup')


def test_core_requirements_and_rules_cannot_be_removed(mappings_dir):
    change(mappings_dir, 'images', lambda doc: doc['ingestion']['required'].remove('updated_at'))
    with pytest.raises(MappingError, match='obligatoires'):
        load_scripts(mappings_dir=mappings_dir)
    change(mappings_dir, 'images', lambda doc: doc.pop('ingestion'))
    with pytest.raises(MappingError, match='ingestion'):
        load_scripts(mappings_dir=mappings_dir)


def test_scripts_command_uses_custom_mappings_and_prefix(mappings_dir, tmp_path, monkeypatch):
    for path in mappings_dir.glob('*.json'):
        doc = json.loads(path.read_text())
        doc['index'] = doc['index'].replace('backup-', 'custom-', 1)
        doc['alias'] = doc['alias'].replace('backup-', 'custom-', 1)
        path.write_text(json.dumps(doc))
    change(mappings_dir, 'jobs', lambda doc: doc['mappings']['properties'].update(owner={'type': 'keyword'}))
    env = tmp_path / '.env'
    env.write_text(f'MAPPINGS_DIR={mappings_dir}\nINDEX_PREFIX=custom\n')
    # Environment variables take precedence over an env file.
    monkeypatch.delenv('INDEX_PREFIX', raising=False)
    monkeypatch.delenv('MAPPINGS_DIR', raising=False)
    sources = load_scripts(mappings_dir=mappings_dir, prefix='custom')
    client = MagicMock()
    client.get_script.side_effect = lambda id: {'script': {'lang': 'painless', 'source': sources[id]}}
    monkeypatch.setattr('app.commands.runtime.create_client', lambda _: client)
    result = CliRunner().invoke(app, ['--env-file', str(env), 'scripts', 'check', '--all'])
    assert result.exit_code == 0, result.output
    assert result.output.count('"status": "OK"') == 3
    client.put_script.assert_not_called()


def test_invalid_contract_prevents_any_script_write(mappings_dir, tmp_path, monkeypatch):
    change(mappings_dir, 'jobs', lambda doc: doc['ingestion']['mutable'].append('master_server'))
    env = tmp_path / '.env'
    env.write_text(f'MAPPINGS_DIR={mappings_dir}\n')
    monkeypatch.delenv('MAPPINGS_DIR', raising=False)
    client = MagicMock()
    monkeypatch.setattr('app.commands.runtime.create_client', lambda _: client)
    result = CliRunner().invoke(app, ['--env-file', str(env), 'scripts', 'install', '--all', '--replace'])
    assert result.exit_code == 2, result.output
    client.get_script.assert_not_called()
    client.put_script.assert_not_called()
