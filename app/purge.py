"""Purge manuelle des seuls historiques, avec plan et contrôle des versions."""
from datetime import datetime, timedelta, timezone
import re
from time import monotonic, sleep

from opensearchpy.helpers import scan

from app.client import error_message
from app.indexes import ensure_concrete, guard, alias_state, require_alias
from app.mappings import MappingError

# Deliberately separate from the ingestion scripts. No document/index deletion operation.
PURGE_SCRIPT = r"""
// Update By Query exposes _version=-1 on OpenSearch 2.19; never rely on it.
if (!params.expected_snapshots.containsKey(ctx._id)) { ctx.op = 'noop'; return; }
def expected = params.expected_snapshots[ctx._id];
for (def key : ['created_at','updated_at']) {
    if (ctx._source[key] != expected[key]) { ctx.op = 'noop'; return; }
}
if (ctx._source.history == null || ctx._source.history.isEmpty()) { ctx.op = 'noop'; return; }
if (ctx._source.history.size() != expected.history.size()) { ctx.op = 'noop'; return; }
for (int i = 0; i < expected.history.size(); i++) {
    if (ctx._source.history[i].created_at != expected.history[i].created_at ||
        ctx._source.history[i].hash != expected.history[i].hash) {
        ctx.op = 'noop'; return;
    }
}
def versions = new ArrayList(ctx._source.history);
versions.sort((a, b) -> Instant.parse(a.created_at).compareTo(Instant.parse(b.created_at)));
def retained = new ArrayList();
if (params.mode == 'keep') {
    int start = versions.size() - (int)params.keep;
    if (start < 0) { start = 0; }
    for (int i = start; i < versions.size(); i++) { retained.add(versions[i]); }
} else if (params.mode == 'older-than') {
    def cutoff = Instant.parse(params.cutoff);
    for (def version : versions) {
        if (!Instant.parse(version.created_at).isBefore(cutoff)) { retained.add(version); }
    }
} else if (params.mode != 'all') { throw new IllegalArgumentException('Unknown purge mode'); }
if (retained.size() == versions.size()) { ctx.op = 'noop'; return; }
ctx._source.history = retained;
"""


def parse_observation(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value
    ):
        raise MappingError('history.created_at : date ISO-8601 obligatoire')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError
        # Preserve comparable instants, reject sub-microsecond precision rather than
        # rounding differently from Painless's nanosecond Instant comparison.
        if re.search(r'\.\d{7,}', value):
            raise ValueError
        return result.astimezone(timezone.utc)
    except ValueError:
        raise MappingError('history.created_at : date ISO-8601 avec fuseau (précision <= microseconde) attendue') from None


def purge_options(keep: int | None, older_than: str | None, all_versions: bool,
                  now: datetime | None = None) -> dict:
    if sum((keep is not None, older_than is not None, all_versions)) != 1:
        raise MappingError('Choisir exactement une option : --keep, --older-than ou --all')
    if keep is not None:
        if keep < 0 or keep > 2147483647:
            raise MappingError('--keep doit être entre 0 et 2147483647')
        return {'mode': 'keep', 'keep': keep}
    if older_than is not None:
        match = re.fullmatch(r'([1-9][0-9]*)d', older_than)
        if not match:
            raise MappingError('--older-than : durée attendue en jours, par exemple 90d')
        try:
            cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=int(match[1]))
        except OverflowError:
            raise MappingError('--older-than : durée trop grande') from None
        return {'mode': 'older-than', 'cutoff': cutoff.isoformat().replace('+00:00', 'Z')}
    return {'mode': 'all'}


def removed_versions(history: list, options: dict) -> list:
    if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
        raise MappingError('history : tableau de versions attendu')
    ordered = sorted(history, key=lambda item: parse_observation(item.get('created_at')))
    if options['mode'] == 'all':
        return ordered
    if options['mode'] == 'keep':
        return ordered[:max(0, len(ordered) - options['keep'])]
    cutoff = parse_observation(options['cutoff'])
    return [item for item in ordered if parse_observation(item['created_at']) < cutoff]


def history_targets(documents: dict, prefix: str, kind: str | None = None) -> list[str]:
    if kind not in {None, 'policies', 'slps'}:
        raise MappingError('--type : policies ou slps attendu')
    result = []
    for stem in ([kind] if kind else ['policies', 'slps']):
        if stem not in documents:
            raise MappingError(f'Mapping {stem} absent')
        name = guard(documents[stem], prefix)
        if documents[stem]['alias'] != f'{prefix}-{stem}':
            raise MappingError('Purge limitée aux alias explicites prefix-policies et prefix-slps')
        if documents[stem]['mappings']['properties'].get('history') != {'type': 'object', 'enabled': False}:
            raise MappingError(f'{stem} : mapping history désactivé attendu')
        result.append(name)
    return result


def validate_history_aliases(client, documents: dict, prefix: str, kind: str | None = None) -> list[str]:
    targets = history_targets(documents, prefix, kind)
    for stem in ([kind] if kind else ['policies', 'slps']):
        require_alias(alias_state(client, documents[stem], prefix))
    return targets


def plan_purge(client, targets: list[str], options: dict, report=None) -> list[dict]:
    plan = []
    for name in targets:
        if not client.indices.exists(index=name):
            raise MappingError(f'{name} : index absent')
        ensure_concrete(client, name)
        # history is disabled: an exists query cannot select it. Read only history metadata.
        hits = scan(client, index=name, query={
            'query': {'match_all': {}}, '_source': ['history.created_at', 'history.hash',
                                                  'created_at', 'updated_at'],
        }, version=True, size=100, scroll='2m')
        for hit in hits:
            if hit['_index'] != name or type(hit.get('_version')) is not int or hit['_version'] <= 0:
                raise MappingError('Prévisualisation sans index concret/version exploitable')
            history = hit.get('_source', {}).get('history', [])
            removed = removed_versions(history, options)
            if not removed:
                continue
            entry = {'index': name, 'id': hit['_id'], 'version': hit['_version'],
                     'removed_versions': [{'created_at': item['created_at'],
                                           'hash': item.get('hash')} for item in removed],
                     'before': len(history), 'after': len(history) - len(removed)}
            entry['snapshot'] = {key: hit.get('_source', {}).get(key)
                                 for key in ('created_at', 'updated_at')}
            entry['snapshot']['history'] = [
                {'created_at': item['created_at'], 'hash': item.get('hash')}
                for item in history]
            plan.append(entry)
            if report:
                report({key: value for key, value in entry.items() if key != 'snapshot'})
    return plan


def execute_purge(client, targets: list[str], plan: list[dict], options: dict,
                  requests_per_second: float = 1.0, report=None) -> dict:
    if not 0 < requests_per_second <= 10:
        raise MappingError('Vitesse de purge attendue entre 0 et 10 lots/s (borne exclue : 0)')
    if len(set(targets)) != len(targets):
        raise MappingError('Cibles de purge dupliquées')
    for name in targets:
        # Defense in depth, including direct use outside the CLI.
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*-(policies|slps)-v[1-9][0-9]*', name):
            raise MappingError('Cible de purge non autorisée')
        ensure_concrete(client, name)
    if any(item['index'] not in targets for item in plan):
        raise MappingError('Plan hors des index autorisés')
    if len({(item['index'], item['id']) for item in plan}) != len(plan):
        raise MappingError('Documents dupliqués dans le plan de purge')
    summary = {'status': 'OK', 'planned_documents': len(plan),
               'planned_removed_versions': sum(item['before'] - item['after'] for item in plan),
               'updated_documents': 0, 'confirmed_removed_versions': 0,
               'version_conflicts': 0, 'skipped_documents': 0, 'failed_batches': 0}
    last_request = None
    for name in targets:
        entries = [item for item in plan if item['index'] == name]
        for offset in range(0, len(entries), 100):
            batch = entries[offset:offset + 100]
            if last_request is not None:
                sleep(max(0, 1 / requests_per_second - (monotonic() - last_request)))
            last_request = monotonic()
            try:
                response = client.update_by_query(index=name, body={
                    'query': {'ids': {'values': [item['id'] for item in batch]}},
                    'script': {'lang': 'painless', 'source': PURGE_SCRIPT,
                               'params': {**options, 'expected_snapshots': {item['id']: item['snapshot'] for item in batch}}},
                }, params={'conflicts': 'proceed', 'requests_per_second': requests_per_second,
                           'scroll_size': 100, 'wait_for_completion': 'true', 'refresh': 'true'})
            except Exception as error:
                # A timeout may happen after writes. Keep prior confirmed counts and
                # explicitly leave the outcome of the interrupted batch unknown.
                summary.update(status='PARTIAL', failed_batches=summary['failed_batches'] + 1,
                               interrupted_batch_outcome='UNKNOWN', message=error_message(error))
                return summary
            updated = response.get('updated', 0)
            conflicts = response.get('version_conflicts', 0)
            skipped = response.get('noops', 0)
            required = {'updated', 'deleted', 'version_conflicts', 'noops', 'failures', 'timed_out', 'total'}
            failed = (not required.issubset(response)
                      or bool(response.get('failures')) or response.get('timed_out', False)
                      or response.get('deleted', 0) != 0 or updated != len(batch)
                      or conflicts != 0 or skipped != 0)
            summary['updated_documents'] += updated
            summary['version_conflicts'] += conflicts
            summary['skipped_documents'] += skipped
            if failed:
                summary['status'] = 'PARTIAL'
                summary['failed_batches'] += 1
            else:
                summary['confirmed_removed_versions'] += sum(item['before'] - item['after'] for item in batch)
            if report:
                report({'index': name, 'status': 'PARTIAL' if failed else 'OK',
                        'updated_documents': updated, 'version_conflicts': conflicts,
                        'skipped_documents': skipped, 'failures_count': len(response.get('failures', []))})
            if failed:
                # No rollback is possible. Stop rather than widen a partial operation.
                return summary
    return summary
