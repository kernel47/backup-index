from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backup_index.indexes import create_index, check_index, apply_mapping, index_info
from backup_index.mappings import MappingError


@pytest.fixture
def document():
    return json.loads((Path(__file__).resolve().parents[1] / 'mappings/jobs.json').read_text())


@pytest.fixture
def client(document):
    mock = MagicMock()
    name = document['index']
    mock.indices.exists.return_value = True
    mock.indices.get_alias.return_value = {name: {'aliases': {document['alias']: {'is_write_index': True}}}}
    mock.indices.get.return_value = {name: {}}
    mock.indices.get_mapping.return_value = {name: {'mappings': deepcopy(document['mappings'])}}
    mock.indices.get_settings.return_value = {name: {'settings': {'index': {'number_of_shards': '1', 'number_of_replicas': '1'}}}}
    mock.indices.create.return_value = {'acknowledged': True}
    mock.indices.put_mapping.return_value = {'acknowledged': True}
    return mock


def test_create_absent(client, document):
    from opensearchpy.exceptions import NotFoundError
    client.indices.exists.return_value = False
    client.indices.get_alias.side_effect = [NotFoundError(404, 'missing', {}), {'backup-jobs-v1': {'aliases': {'backup-jobs': {'is_write_index': True}}}}]
    assert create_index(client, document, 'backup') == 'CREATED'
    client.indices.create.assert_called_once_with(index='backup-jobs-v1', body={**{k: document[k] for k in ('settings', 'mappings')}, 'aliases': {document['alias']: {'is_write_index': True}}})


def test_existing_repeated(client, document):
    for _ in range(2):
        assert create_index(client, document, 'backup').startswith('EXISTS')
    client.indices.create.assert_not_called()
    for _ in range(2):
        assert apply_mapping(client, document, 'backup')['status'] == 'UNCHANGED'
    client.indices.put_mapping.assert_not_called()


def test_missing_check(client, document):
    client.indices.exists.return_value = False
    assert check_index(client, document, 'backup')['status'] == 'MISSING'


def test_check_settings(client, document):
    assert check_index(client, document, 'backup')['status'] == 'OK'
    client.indices.get_settings.return_value['backup-jobs-v1']['settings']['index']['number_of_replicas'] = '0'
    assert check_index(client, document, 'backup')['status'] == 'DIFFERENT'


def test_apply_new_field_and_verify(client, document):
    old = deepcopy(document['mappings'])
    del old['properties']['client']
    client.indices.get_mapping.side_effect = [{'backup-jobs-v1': {'mappings': old}}, {'backup-jobs-v1': {'mappings': document['mappings']}}]
    assert apply_mapping(client, document, 'backup')['status'] == 'APPLIED'
    client.indices.put_mapping.assert_called_once_with(index='backup-jobs-v1', body={'properties': {'client': {'type': 'keyword'}}})
    assert client.indices.get_mapping.call_count == 2


def test_dry_run(client, document):
    del client.indices.get_mapping.return_value['backup-jobs-v1']['mappings']['properties']['client']
    result = apply_mapping(client, document, 'backup', dry_run=True)
    assert result['missing'] == ['client']
    assert result['patch'] == {'properties': {'client': {'type': 'keyword'}}}
    client.indices.put_mapping.assert_not_called()


def test_no_partial_update(client, document):
    remote = client.indices.get_mapping.return_value['backup-jobs-v1']['mappings']['properties']
    del remote['client']
    remote['job_id']['type'] = 'long'
    with pytest.raises(MappingError, match='incompatibilités'):
        apply_mapping(client, document, 'backup')
    client.indices.put_mapping.assert_not_called()


@pytest.mark.parametrize('operation', [create_index, check_index, apply_mapping, index_info])
def test_prefix_rejected(client, document, operation):
    document['index'] = 'other-jobs'
    with pytest.raises(MappingError):
        operation(client, document, 'backup')
    assert not client.mock_calls


def test_alias_rejected(client, document):
    client.indices.get.return_value = {'actual-index': {}}
    with pytest.raises(MappingError, match='alias'):
        apply_mapping(client, document, 'backup')
    client.indices.put_mapping.assert_not_called()


def test_info(client, document):
    client.indices.stats.return_value = {'indices': {'backup-jobs-v1': {'primaries': {'docs': {'count': 42}, 'store': {'size_in_bytes': 100}}, 'total': {'store': {'size_in_bytes': 200}}}}}
    result = index_info(client, document, 'backup')
    assert result['documents'] == 42 and result['storage_bytes'] == 200
    assert result['fields'] == len(document['mappings']['properties'])
    client.search.assert_not_called()


def test_verification_failure(client, document):
    del client.indices.get_mapping.return_value['backup-jobs-v1']['mappings']['properties']['client']
    with pytest.raises(RuntimeError, match='après modification'):
        apply_mapping(client, document, 'backup')
    client.indices.put_mapping.assert_called_once()


def test_settings_never_written_by_apply(client, document):
    document['settings']['number_of_replicas'] = 7
    assert apply_mapping(client, document, 'backup')['status'] == 'UNCHANGED'
    client.indices.put_settings.assert_not_called()
    client.indices.put_mapping.assert_not_called()


def test_missing_apply_no_create(client, document):
    client.indices.exists.return_value = False
    with pytest.raises(MappingError, match='absent'):
        apply_mapping(client, document, 'backup')
    client.indices.create.assert_not_called()
    client.indices.put_mapping.assert_not_called()


@pytest.fixture
def removal_client(client):
    client.indices.get.return_value = {'backup-jobs-v1': {'settings': {'index': {'uuid': 'test-uuid'}}}}
    client.count.return_value = {'count': 12, '_shards': {'failed': 0}}
    client.indices.refresh.return_value = {'_shards': {'failed': 0}}
    return client


def test_delete_index_explicit_and_verified(removal_client, document):
    from backup_index.indexes import delete_index
    removal_client.indices.delete.return_value = {'acknowledged': True}
    removal_client.indices.exists.side_effect = [True, False]
    result = delete_index(removal_client, document, 'backup', 'test-uuid')
    assert result['status'] == 'DELETED'
    removal_client.indices.delete.assert_called_once_with(index='backup-jobs-v1')
    removal_client.delete_by_query.assert_not_called()


def test_clear_preserves_structure(removal_client, document):
    from backup_index.indexes import clear_index
    removal_client.count.side_effect = [{'count': 12}, {'count': 0}]
    removal_client.delete_by_query.return_value = {'total': 12, 'deleted': 12, 'version_conflicts': 0, 'failures': [], 'timed_out': False}
    result = clear_index(removal_client, document, 'backup', 'test-uuid')
    assert result['status'] == 'CLEARED' and result['deleted_documents'] == 12
    kwargs = removal_client.delete_by_query.call_args.kwargs
    assert kwargs['index'] == 'backup-jobs-v1'
    assert kwargs['body'] == {'query': {'match_all': {}}}
    assert kwargs['params']['requests_per_second'] == 1
    removal_client.indices.refresh.assert_called_once_with(index='backup-jobs-v1')
    removal_client.indices.delete.assert_not_called()
    removal_client.indices.create.assert_not_called()
    removal_client.indices.put_mapping.assert_not_called()


@pytest.mark.parametrize('response', [
    {'total': 12, 'deleted': 11, 'version_conflicts': 1, 'failures': [], 'timed_out': False},
    {'total': 12, 'deleted': 12, 'version_conflicts': 0, 'failures': [], 'timed_out': True},
    {'total': 12, 'deleted': 0, 'version_conflicts': 0, 'failures': [{'reason': 'secret'}], 'timed_out': False},
    {},
])
def test_clear_partial_failure(removal_client, document, response):
    from backup_index.indexes import clear_index
    removal_client.delete_by_query.return_value = response
    result = clear_index(removal_client, document, 'backup', 'test-uuid')
    assert result['status'] == 'PARTIAL'
    assert 'secret' not in str(result)


@pytest.mark.parametrize('operation', ['clear_index', 'delete_index'])
def test_removal_rejects_recreated_index(removal_client, document, operation):
    import backup_index.indexes as indexes
    with pytest.raises(MappingError, match='recréé'):
        getattr(indexes, operation)(removal_client, document, 'backup', 'old-uuid')
    removal_client.indices.delete.assert_not_called()
    removal_client.delete_by_query.assert_not_called()


@pytest.mark.parametrize('operation', ['clear_index', 'delete_index'])
def test_removal_rejects_prefix_and_alias(removal_client, document, operation):
    import backup_index.indexes as indexes
    document['index'] = 'other-jobs'
    with pytest.raises(MappingError):
        getattr(indexes, operation)(removal_client, document, 'backup', 'test-uuid')
    assert not removal_client.mock_calls
    document['index'] = 'backup-jobs-v1'
    removal_client.indices.get.return_value = {'another-index': {}}
    with pytest.raises(MappingError, match='alias'):
        getattr(indexes, operation)(removal_client, document, 'backup', 'test-uuid')
    removal_client.indices.delete.assert_not_called()
    removal_client.delete_by_query.assert_not_called()


def test_delete_unacknowledged_is_partial(removal_client, document):
    from backup_index.indexes import delete_index
    removal_client.indices.delete.return_value = {'acknowledged': False}
    removal_client.indices.exists.side_effect = [True, False]
    assert delete_index(removal_client, document, 'backup', 'test-uuid')['status'] == 'PARTIAL'


@pytest.mark.parametrize('aliases', [
    {'backup-jobs-v2': {'aliases': {'backup-jobs': {'is_write_index': True}}}},
    {'backup-jobs-v1': {'aliases': {'backup-jobs': {}}}, 'backup-jobs-v2': {'aliases': {'backup-jobs': {}}}},
    {'backup-jobs-v1': {'aliases': {'backup-jobs': {'filter': {'match_all': {}}}}}},
    {'backup-jobs-v1': {'aliases': {'backup-jobs': {'routing': 'somewhere'}}}},
    {'backup-jobs-v1': {'aliases': {'backup-jobs': {'is_write_index': False}}}},
])
def test_alias_conflicts_block_create_and_apply(client, document, aliases):
    client.indices.get_alias.return_value = aliases
    with pytest.raises(MappingError, match='CONFLICT'):
        create_index(client, document, 'backup')
    with pytest.raises(MappingError, match='CONFLICT'):
        apply_mapping(client, document, 'backup')
    client.indices.create.assert_not_called()
    client.indices.put_mapping.assert_not_called()
    client.indices.put_alias.assert_not_called()
    client.indices.update_aliases.assert_not_called()
    client.indices.delete_alias.assert_not_called()


def test_legacy_logical_index_is_not_replaced(client, document):
    from opensearchpy.exceptions import NotFoundError
    client.indices.get_alias.side_effect = NotFoundError(404, 'missing', {})
    client.indices.exists.side_effect = lambda index: index == 'backup-jobs'
    with pytest.raises(MappingError, match='CONFLICT'):
        create_index(client, document, 'backup')
    client.indices.create.assert_not_called()
    client.indices.delete.assert_not_called()
    client.reindex.assert_not_called()


def test_existing_physical_without_alias_requires_manual_action(client, document):
    from opensearchpy.exceptions import NotFoundError
    client.indices.get_alias.side_effect = NotFoundError(404, 'missing', {})
    client.indices.exists.side_effect = lambda index: index == document['index']
    with pytest.raises(MappingError, match='MISSING'):
        create_index(client, document, 'backup')
    assert check_index(client, document, 'backup')['status'] == 'DIFFERENT'
    client.indices.create.assert_not_called()
    client.indices.put_alias.assert_not_called()


def test_single_implicit_write_alias_is_accepted(client, document):
    client.indices.get_alias.return_value = {document['index']: {'aliases': {document['alias']: {}}}}
    assert check_index(client, document, 'backup')['status'] == 'OK'
    assert create_index(client, document, 'backup').startswith('EXISTS')
    client.indices.put_alias.assert_not_called()


def test_non_default_prefix_and_version(client, document):
    document.update(index='cib-jobs-v3', alias='cib-jobs')
    client.indices.get_alias.return_value = {'cib-jobs-v3': {'aliases': {'cib-jobs': {'is_write_index': True}}}}
    client.indices.get.return_value = {'cib-jobs-v3': {}}
    assert create_index(client, document, 'cib').startswith('EXISTS')
    assert client.indices.get_alias.call_args.kwargs == {'name': 'cib-jobs'}


def test_failed_alias_verification_after_creation(client, document):
    from opensearchpy.exceptions import NotFoundError
    client.indices.exists.return_value = False
    client.indices.get_alias.side_effect = [NotFoundError(404, 'missing', {}),
        {'backup-jobs-v2': {'aliases': {'backup-jobs': {}}}}]
    with pytest.raises(MappingError, match='CONFLICT'):
        create_index(client, document, 'backup')
    client.indices.create.assert_called_once()
    client.indices.delete.assert_not_called()
    client.indices.update_aliases.assert_not_called()


def test_clear_and_delete_refuse_legacy_logical_conflict(client, document):
    from opensearchpy.exceptions import NotFoundError
    from backup_index.indexes import removal_plan
    client.indices.get_alias.side_effect = NotFoundError(404, 'missing', {})
    client.indices.exists.side_effect = lambda index: index == document['alias']
    with pytest.raises(MappingError, match='CONFLICT'):
        removal_plan(client, document, 'backup')
    client.indices.delete.assert_not_called()
    client.delete_by_query.assert_not_called()


def test_enabling_data_requires_new_version(client, document):
    client.indices.get_mapping.return_value['backup-jobs-v1']['mappings']['properties']['data']['enabled'] = True
    with pytest.raises(MappingError, match='nouvel index versionné'):
        apply_mapping(client, document, 'backup')
    client.indices.put_mapping.assert_not_called()
