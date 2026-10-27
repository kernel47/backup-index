"""Real Painless tests, opt-in, only the dedicated loopback test cluster.

Never reads .env or uses a CIB URL. Test indexes are retained; stop/discard the
isolated cluster directory after the run, rather than calling any deletion API.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import uuid

import pytest
from opensearchpy import OpenSearch

from app.config import PROJECT_ROOT
from app.mappings import load_mappings
from app.scripts import load_scripts, script_plan, install_scripts
from app.purge import purge_options, plan_purge, execute_purge

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(os.environ.get('BACKUP_INDEX_RUN_INTEGRATION') != '1',
                                 reason='Explicit isolated OpenSearch test opt-in required')]


@pytest.fixture(scope='module')
def cluster():
    client = OpenSearch(hosts=['http://127.0.0.1:19280'], timeout=30, max_retries=0)
    assert client.info()['cluster_name'] == 'backup-index-local-test', 'Refusing non-test cluster'
    sources = load_scripts()
    install_scripts(client, sources, script_plan(client, sources), replace=True)
    yield client
    client.close()


@pytest.fixture
def index_factory(cluster):
    def make(kind):
        name = f'backup-test-{uuid.uuid4().hex}-{kind}-v1'
        document = load_mappings(PROJECT_ROOT / 'mappings', 'backup')[kind]
        cluster.indices.create(index=name, body={
            'settings': {'number_of_shards': 1, 'number_of_replicas': 0},
            'mappings': document['mappings'],
        })
        return name
    return make


def stamp(second):
    return f'2026-01-01T00:00:{second:02d}Z'


def update(cluster, index, script, document, id='one', **params):
    return cluster.update(index=index, id=id, retry_on_conflict=50, body={
        'scripted_upsert': True, 'upsert': {},
        'script': {'id': f'backup_{script}_upsert', 'params': {'document': document, **params}},
    })


def configuration(second, value):
    data = {'active': value}
    return {'master_server': 'master-test', 'name': 'policy-test', 'updated_at': stamp(second),
            'active': value, 'hash': hashlib.sha256(json.dumps(data).encode()).hexdigest(), 'data': data}


def job(second, state):
    return {'master_server': 'master-test', 'job_id': '123', 'start_time': stamp(0),
            'updated_at': stamp(second), 'state': state}


@pytest.mark.parametrize('kind', ['jobs', 'images', 'policies', 'slps'])
def test_creation_and_confirmation_dates(cluster, index_factory, kind):
    index = index_factory(kind)
    if kind == 'jobs':
        document = job(1, 'ACTIVE')
    elif kind == 'images':
        document = {'master_server': 'master-test', 'image_id': 'image-test', 'updated_at': stamp(1)}
    else:
        document = configuration(1, True)
    script = kind if kind in {'jobs', 'images'} else 'configuration'
    params = {} if script != 'configuration' else {'kind': kind}
    update(cluster, index, script, document, **params)
    initial = cluster.get(index=index, id='one')['_source']
    assert initial['created_at'] == initial['updated_at'] == stamp(1)
    assert update(cluster, index, script, {**document, 'updated_at': stamp(3)}, **params)['result'] == 'updated'
    current = cluster.get(index=index, id='one')['_source']
    assert current['created_at'] == stamp(1)
    assert current['updated_at'] == stamp(3)
    assert update(cluster, index, script, {**document, 'updated_at': stamp(2)}, **params)['result'] == 'noop'
    if script == 'configuration':
        assert current['history'] == []
        changed = configuration(4, False)
        update(cluster, index, script, changed, **params)
        current = cluster.get(index=index, id='one')['_source']
        assert current['created_at'] == current['updated_at'] == stamp(4)
        assert current['history'][0]['created_at'] == stamp(1)
        assert current['history'][0]['updated_at'] == stamp(3)


def test_bulk_initial_scripted_upsert(cluster, index_factory):
    index = index_factory('jobs')
    document = job(1, 'QUEUED')
    response = cluster.bulk(body=[
        {'update': {'_index': index, '_id': 'bulk-one', 'retry_on_conflict': 5}},
        {'scripted_upsert': True, 'upsert': {},
         'script': {'id': 'backup_jobs_upsert', 'params': {'document': document}}},
    ])
    assert not response['errors'], response
    assert response['items'][0]['update']['result'] == 'created'
    assert cluster.get(index=index, id='bulk-one')['_source']['state'] == 'QUEUED'


def test_job_transitions_and_final_corrections(cluster, index_factory):
    index = index_factory('jobs')
    assert update(cluster, index, 'jobs', job(1, 'QUEUED'))['result'] == 'created'
    assert update(cluster, index, 'jobs', job(2, 'ACTIVE'))['result'] == 'updated'
    assert update(cluster, index, 'jobs', job(1, 'QUEUED'))['result'] == 'noop'
    assert update(cluster, index, 'jobs', job(3, 'DONE'))['result'] == 'updated'
    assert update(cluster, index, 'jobs', job(4, 'ACTIVE'))['result'] == 'noop'
    final = {**job(4, 'DONE'), 'status': 0, 'kilobytes': 200}
    assert update(cluster, index, 'jobs', final)['result'] == 'updated'
    assert update(cluster, index, 'jobs', final)['result'] == 'noop'
    correction = {**final, 'updated_at': stamp(5), 'kilobytes': 300}
    assert update(cluster, index, 'jobs', correction)['result'] == 'noop'
    assert update(cluster, index, 'jobs', correction, allow_final_corrections=True)['result'] == 'updated'
    assert cluster.get(index=index, id='one')['_source']['kilobytes'] == 300


def test_job_fixed_attributes_protected(cluster, index_factory):
    index = index_factory('jobs')
    update(cluster, index, 'jobs', {**job(1, 'ACTIVE'), 'client': 'a'})
    assert update(cluster, index, 'jobs', {**job(2, 'DONE'), 'client': 'b'})['result'] == 'noop'


def test_images_enrichment_and_stale(cluster, index_factory):
    index = index_factory('images')
    original = {'master_server': 'master-test', 'image_id': 'image-test', 'updated_at': stamp(1),
                'expiration_time': '2026-02-01T00:00:00Z', 'data': {'copies': 1}}
    assert update(cluster, index, 'images', original)['result'] == 'created'
    newer = {**original, 'updated_at': stamp(3), 'expiration_time': '2026-03-01T00:00:00Z', 'data': {'copies': 2}}
    assert update(cluster, index, 'images', newer)['result'] == 'noop'
    assert update(cluster, index, 'images', newer, allow_corrections=True)['result'] == 'updated'
    assert update(cluster, index, 'images', {**original, 'updated_at': stamp(2)}, allow_corrections=True)['result'] == 'noop'
    assert cluster.get(index=index, id='one')['_source']['data']['copies'] == 2


def test_configuration_initial_same_changed_a_b_a(cluster, index_factory):
    index = index_factory('policies')
    assert update(cluster, index, 'configuration', configuration(1, True), kind='policies')['result'] == 'created'
    assert cluster.get(index=index, id='one')['_source']['history'] == []
    assert update(cluster, index, 'configuration', configuration(1, True), kind='policies')['result'] == 'noop'
    update(cluster, index, 'configuration', configuration(3, True), kind='policies')
    assert update(cluster, index, 'configuration', configuration(2, False), kind='policies')['result'] == 'noop'
    update(cluster, index, 'configuration', configuration(4, False), kind='policies')
    update(cluster, index, 'configuration', configuration(5, True), kind='policies')
    result = cluster.get(index=index, id='one')['_source']
    assert [v['data']['active'] for v in result['history']] == [True, False]
    assert result['active'] is True and result['created_at'] == stamp(5)


def test_configuration_history_limit(cluster, index_factory):
    index = index_factory('policies')
    for second in range(1, 16):
        update(cluster, index, 'configuration', configuration(second, second % 2 == 0), kind='policies')
    result = cluster.get(index=index, id='one')['_source']
    assert len(result['history']) == 10
    assert result['history'][0]['created_at'] == stamp(5)
    assert all('history' not in v for v in result['history'])
    update(cluster, index, 'configuration', configuration(16, True), kind='policies', max_history_versions=0)
    assert cluster.get(index=index, id='one')['_source']['history'] == []


def test_slp_normalized_fields(cluster, index_factory):
    index = index_factory('slps')
    doc = configuration(1, True)
    doc.update(status='active', target_storage='storage-test')
    update(cluster, index, 'configuration', doc, kind='slps')
    actual = cluster.get(index=index, id='one')['_source']
    assert actual['active'] is True and actual['target_storage'] == 'storage-test'
    assert 'slp_version' not in actual


def test_concurrent_upserts_monotonic(cluster, index_factory):
    index = index_factory('policies')
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(update, cluster, index, 'configuration', configuration(second, second % 2 == 0), kind='policies')
                   for second in [5, 1, 8, 2, 10, 3, 12, 4, 15, 6, 14, 7, 13, 9, 11]]
        for future in futures:
            future.result()
    result = cluster.get(index=index, id='one')['_source']
    assert result['updated_at'] == stamp(15)
    assert result['active'] is False
    dates = [item['created_at'] for item in result['history']]
    assert dates == sorted(set(dates)) and len(dates) <= 10


@pytest.mark.parametrize('mode', ['keep', 'older-than', 'all'])
def test_real_purge_preserves_current_and_replays(cluster, index_factory, mode):
    index = index_factory('policies')
    current = configuration(1, True)
    history = [{'created_at': f'2025-12-{day:02d}T00:00:00Z', 'hash': str(day), 'data': {'old': day}} for day in range(1, 13)]
    cluster.index(index=index, id='p1', body={**current, 'history': history}, refresh=True)
    options = {'mode': mode}
    if mode == 'keep': options['keep'] = 5
    if mode == 'older-than': options['cutoff'] = '2025-12-08T00:00:00Z'
    plan = plan_purge(cluster, [index], options)
    assert plan[0]['before'] == 12
    result = execute_purge(cluster, [index], plan, options)
    assert result['status'] == 'OK', result
    actual = cluster.get(index=index, id='p1')['_source']
    assert {key: value for key, value in actual.items() if key != 'history'} == current
    assert len(actual['history']) == (0 if mode == 'all' else 5)
    assert plan_purge(cluster, [index], options) == []


def test_purge_concurrent_change_is_skipped(cluster, index_factory):
    index = index_factory('policies')
    cluster.index(index=index, id='p1', body={**configuration(1, True), 'history': [{'created_at': stamp(0), 'data': {'old': 1}}]}, refresh=True)
    plan = plan_purge(cluster, [index], {'mode': 'all'})
    cluster.update(index=index, id='p1', body={'doc': {'updated_at': stamp(2)}}, refresh=True)
    result = execute_purge(cluster, [index], plan, {'mode': 'all'})
    assert result['status'] == 'PARTIAL' and result['skipped_documents'] == 1
    assert len(cluster.get(index=index, id='p1')['_source']['history']) == 1


@pytest.mark.parametrize('limit', [-1, 1.5, 2147483648])
def test_invalid_history_limit_no_creation(cluster, index_factory, limit):
    from opensearchpy.exceptions import RequestError
    index = index_factory('policies')
    with pytest.raises(RequestError):
        update(cluster, index, 'configuration', configuration(1, True), kind='policies', max_history_versions=limit)
    assert not cluster.exists(index=index, id='one')


def test_hash_format_checked(cluster, index_factory):
    from opensearchpy.exceptions import RequestError
    index = index_factory('policies')
    document = {**configuration(1, True), 'hash': 'z' * 64}
    with pytest.raises(RequestError):
        update(cluster, index, 'configuration', document, kind='policies')
    assert not cluster.exists(index=index, id='one')


def test_real_versioned_creation_and_alias(cluster):
    from app.indexes import create_index, check_index
    document = deepcopy(load_mappings(PROJECT_ROOT / 'mappings', 'backup')['jobs'])
    prefix = 'backup-test-' + uuid.uuid4().hex
    document.update(index=prefix + '-jobs-v1', alias=prefix + '-jobs')
    document['settings']['number_of_replicas'] = 0
    assert create_index(cluster, document, prefix) == 'CREATED'
    assert check_index(cluster, document, prefix)['status'] == 'OK'
    assert set(cluster.indices.get_alias(name=document['alias'])) == {document['index']}
    assert create_index(cluster, document, prefix).startswith('EXISTS')
