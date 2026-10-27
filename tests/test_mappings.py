from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.mappings import (
    MappingError, load_mappings, compare_mappings, validate_mapping,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def document():
    return json.loads((ROOT / 'mappings/jobs.json').read_text())


def test_valid_files():
    assert len(load_mappings(ROOT / 'mappings', 'backup')) == 5


def test_images_contents_minimal_contract():
    document = load_mappings(ROOT / 'mappings', 'backup')['images-contents']
    assert document['mappings']['_source'] == {'enabled': True}
    assert document['mappings']['properties'] == {
        'region': {'type': 'keyword'},
        'master_server': {'type': 'keyword'},
        'image_id': {'type': 'keyword'},
        'path': {'type': 'keyword'},
        'backup_time': {'type': 'date'},
        'created_at': {'type': 'date'},
    }


def test_shared_attribute_contract():
    documents = load_mappings(ROOT / 'mappings', 'backup')
    common = {
        'master_server': {'type': 'keyword'},
        'region': {'type': 'keyword'},
        'technology': {'type': 'keyword'},
        'created_at': {'type': 'date'},
        'updated_at': {'type': 'date'},
        'collected_at': {'type': 'date'},
        'data': {'type': 'object', 'enabled': False},
    }
    definitions = {}
    for kind, document in documents.items():
        properties = document['mappings']['properties']
        if kind != 'images-contents':
            assert {key: properties[key] for key in common} == common
        assert 'observed_at' not in properties
        assert 'last_seen_at' not in properties
        for name, definition in properties.items():
            assert definition == definitions.setdefault(name, definition), name


def test_invalid_json(tmp_path):
    (tmp_path / 'broken.json').write_text('{')
    with pytest.raises(MappingError, match='broken.json.*JSON invalide'):
        load_mappings(tmp_path, 'backup')


@pytest.mark.parametrize('change', ['missing_name', 'bad_type', 'prefix', 'wildcard', 'settings', 'properties', 'bool'])
def test_invalid_structure(document, change):
    if change == 'missing_name':
        del document['index']
    elif change == 'bad_type':
        document['mappings']['properties']['job_id']['type'] = 'wrong'
    elif change == 'prefix':
        document['index'] = 'other-jobs'
    elif change == 'wildcard':
        document['index'] = 'backup-*'
    elif change == 'settings':
        document['settings']['number_of_shards'] = 0
    elif change == 'properties':
        del document['mappings']['properties']
    else:
        document['mappings']['properties']['data']['enabled'] = 'false'
    with pytest.raises(MappingError):
        validate_mapping(document, 'backup')


def test_duplicate(tmp_path, document):
    for name in ('a', 'b'):
        (tmp_path / f'{name}.json').write_text(json.dumps(document))
    with pytest.raises(MappingError, match='plusieurs fichiers'):
        load_mappings(tmp_path, 'backup')


def test_missing_and_incompatible(document):
    remote = deepcopy(document['mappings'])
    del remote['properties']['client']
    remote['properties']['job_id']['type'] = 'long'
    diff = compare_mappings(document['mappings'], remote)
    assert diff.missing == ['client']
    assert 'job_id.type' in diff.incompatible[0]


def test_remote_extra_nonblocking(document):
    remote = deepcopy(document['mappings'])
    remote['properties']['extra'] = {'type': 'keyword'}
    diff = compare_mappings(document['mappings'], remote)
    assert diff.conforms and diff.extra == ['extra']


def test_nested_and_multifield_patch():
    local = {'properties': {'obj': {'type': 'object', 'properties': {'a': {'type': 'keyword'}}},
                            'title': {'type': 'text', 'fields': {'raw': {'type': 'keyword'}}}}}
    remote = {'properties': {'obj': {'properties': {}}, 'title': {'type': 'text'}}}
    diff = compare_mappings(local, remote)
    assert diff.missing == ['obj.a', 'title.raw']
    assert not diff.incompatible
    assert diff.additions['properties']['obj'] == {'properties': {'a': {'type': 'keyword'}}}


def test_significant_remote_options():
    local = {'properties': {'x': {'type': 'keyword'}}}
    remote = {'properties': {'x': {'type': 'keyword', 'index': False}}}
    assert compare_mappings(local, remote).incompatible


def test_server_defaults():
    assert compare_mappings({'dynamic': False, 'properties': {}},
                            {'dynamic': 'false', 'properties': {}}).conforms


def test_v2_contract():
    documents = load_mappings(ROOT / 'mappings', 'backup')
    assert set(documents) == {'jobs', 'images', 'policies', 'slps', 'images-contents'}
    for stem, document in documents.items():
        assert document['index'] == f'backup-{stem}-v1'
        assert document['alias'] == f'backup-{stem}'
        assert document['mappings']['dynamic'] is False
        props = document['mappings']['properties']
        if stem != 'images-contents':
            assert props['data'] == {'type': 'object', 'enabled': False}
        assert ('history' in props) == (stem in {'policies', 'slps'})
        if 'history' in props:
            assert props['history'] == {'type': 'object', 'enabled': False}
    jobs = documents['jobs']['mappings']['properties']
    assert jobs['parent_job_id']['type'] == 'keyword'
    assert jobs['kilobytes']['type'] == 'long'
    assert documents['slps']['mappings']['properties']['status']['type'] == 'keyword'



@pytest.mark.parametrize('mutation', ['missing_alias', 'same_name', 'wrong_version', 'disabled_source'])
def test_invalid_alias_and_source_contract(document, mutation):
    if mutation == 'missing_alias':
        del document['alias']
    elif mutation == 'same_name':
        document['alias'] = document['index']
    elif mutation == 'wrong_version':
        document['index'] = document['alias'] + '-v0'
    else:
        document['mappings']['_source']['enabled'] = False
    with pytest.raises(MappingError):
        validate_mapping(document, 'backup')


def test_source_default_is_normalized():
    local = {'_source': {'enabled': True}, 'properties': {}}
    assert compare_mappings(local, {'properties': {}}).conforms
    assert compare_mappings(local, {'_source': {}, 'properties': {}}).conforms
    assert not compare_mappings(local, {'_source': {'enabled': False}, 'properties': {}}).conforms


def test_audited_python_field_types():
    documents = load_mappings(ROOT / 'mappings', 'backup')
    jobs = documents['jobs']['mappings']['properties']
    assert jobs['job_id']['type'] == jobs['parent_job_id']['type'] == 'keyword'
    assert jobs['status']['type'] == 'keyword'
    assert jobs['percent_complete']['type'] == 'integer'
    assert jobs['elapsed_seconds']['type'] == 'long'
    assert jobs['type']['type'] == 'keyword'
    assert not {'id', 'job_type', 'elapsed_time', 'policy_type', 'schedule_type'} & jobs.keys()
    images = documents['images']['mappings']['properties']
    assert images['retention_level']['type'] == 'keyword'
    assert images['storage_location']['type'] == 'keyword'
    assert 'num_files' not in images
    policies = documents['policies']['mappings']['properties']
    assert policies['clients'] == {'type': 'keyword'}
    assert not {'retention', 'schedules', 'backup_selections'} & policies.keys()
    slps = documents['slps']['mappings']['properties']
    assert slps['active']['type'] == 'boolean'
    assert not {'operations', 'retention', 'state', 'slp_version'} & slps.keys()
