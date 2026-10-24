from copy import deepcopy
from unittest.mock import MagicMock

import pytest
from opensearchpy.exceptions import NotFoundError
from typer.testing import CliRunner

from backup_index.cli import app
from backup_index.scripts import load_scripts, script_plan, install_scripts
from backup_index.mappings import MappingError


def test_local_scripts():
    sources = load_scripts()
    assert set(sources) == {'backup_jobs_upsert', 'backup_images_upsert', 'backup_configuration_upsert'}
    assert all("ctx.op == 'create'" in source for source in sources.values())


def test_missing_scripts():
    client = MagicMock()
    client.get_script.side_effect = NotFoundError(404, 'missing', {})
    assert all(item['status'] == 'MISSING' for item in script_plan(client, load_scripts()))


def test_different_needs_explicit_replace():
    client = MagicMock()
    client.get_script.return_value = {'script': {'lang': 'painless', 'source': 'old'}}
    plan = script_plan(client, load_scripts())
    assert all(item['diff'] for item in plan)
    with pytest.raises(MappingError, match='--replace'):
        install_scripts(client, load_scripts(), plan)
    client.put_script.assert_not_called()


def test_install_and_verify():
    client = MagicMock()
    sources = load_scripts()
    storage = {}
    def get_script(id):
        if id not in storage:
            raise NotFoundError(404, 'missing', {})
        return {'script': storage[id]}
    def put_script(id, body):
        storage[id] = deepcopy(body['script'])
        return {'acknowledged': True}
    client.get_script.side_effect = get_script
    client.put_script.side_effect = put_script
    plan = script_plan(client, sources)
    assert len(install_scripts(client, sources, plan)) == 3
    assert all(item['status'] == 'OK' for item in script_plan(client, sources))
    client.put_script.reset_mock()
    assert all(item['status'] == 'UNCHANGED' for item in install_scripts(client, sources, script_plan(client, sources)))
    client.put_script.assert_not_called()


def test_concurrent_script_change_refused():
    client = MagicMock()
    sources = load_scripts()
    client.get_script.return_value = {'script': {'lang': 'painless', 'source': 'a'}}
    plan = script_plan(client, sources)
    client.get_script.return_value = {'script': {'lang': 'painless', 'source': 'b'}}
    with pytest.raises(MappingError, match='modifiés'):
        install_scripts(client, sources, plan, replace=True)
    client.put_script.assert_not_called()


def test_install_dry_run(monkeypatch):
    client = MagicMock()
    client.get_script.return_value = {'script': {'lang': 'painless', 'source': 'old'}}
    monkeypatch.setattr('backup_index.cli.create_client', lambda _: client)
    result = CliRunner().invoke(app, ['scripts', 'install', '--all', '--dry-run'])
    assert result.exit_code == 0, result.output
    assert 'DIFFERENT' in result.output and 'DRY-RUN' in result.output
    client.put_script.assert_not_called()


def test_catalog_provenance_and_separation():
    import json
    from pathlib import Path
    from backup_index.config import PROJECT_ROOT
    from backup_index.mappings import load_mappings
    documents = load_mappings(PROJECT_ROOT / 'mappings', 'backup')
    total = 0
    for kind in ('jobs', 'images', 'policies', 'slps'):
        catalog = json.loads((PROJECT_ROOT / f'catalog/netbackup/{kind}.json').read_text())
        assert catalog['entity'] == kind
        for attribute in catalog['attributes']:
            total += 1
            if attribute['source_type'] == 'python':
                assert Path(attribute['source_reference'].rsplit(':', 1)[0]).is_absolute()
                assert attribute['python_type'] is not None
            else:
                assert attribute['source_reference'].startswith('https://')
            assert attribute['confidence'] in {'documented', 'official_example', 'source_verified'}
            assert attribute['availability']['cib'] == 'unknown'
            if attribute['source_type'] == 'cli':
                assert attribute['path'] is None or attribute['path'].startswith('field')
                assert attribute['location']
            if attribute['selected']:
                assert attribute['mapping_field'] in documents[kind]['mappings']['properties']
        assert 'mappings' not in catalog and 'index' not in catalog
    assert total == 350


def test_ingestion_script_fields_match_current_mappings():
    import ast
    import re
    from backup_index.config import PROJECT_ROOT
    from backup_index.mappings import load_mappings
    documents = load_mappings(PROJECT_ROOT / 'mappings', 'backup')
    sources = load_scripts()
    for kind in ('jobs', 'images'):
        source = sources[f'backup_{kind}_upsert']
        allowed = ast.literal_eval(re.search(r'def allowed = (\[[^;]+\]);', source)[1])
        assert set(allowed) == set(documents[kind]['mappings']['properties']) - {'created_at'}
    source = sources['backup_configuration_upsert']
    base = set(ast.literal_eval(re.search(r'def allowed = (\[[^;]+\]);', source)[1]))
    branches = re.findall(r'allowed.addAll\((\[[^;]+\])\)', source)
    for kind, branch in zip(('policies', 'slps'), branches):
        assert base | set(ast.literal_eval(branch)) == set(documents[kind]['mappings']['properties']) - {'history', 'created_at'}
