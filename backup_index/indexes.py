"""Opérations explicites sur les seuls index déclarés et validés."""
from opensearchpy import OpenSearch
from opensearchpy.exceptions import RequestError, NotFoundError

from backup_index.mappings import (
    MappingError, validate_mapping, compare_mappings, flatten_settings, count_fields,
)


def guard(document: dict, prefix: str) -> str:
    validate_mapping(document, prefix)
    return document["index"]


def ensure_concrete(client: OpenSearch, name: str) -> None:
    # A name can resolve to an alias or data stream. Never write through either.
    resolved = client.indices.get(index=name)
    if set(resolved) != {name} or resolved[name].get("data_stream"):
        raise MappingError(f"{name} : index concret attendu (alias/flux refusé)")


def alias_state(client: OpenSearch, document: dict, prefix: str) -> dict:
    """Inspect the exact logical name, without changing an existing alias."""
    name = guard(document, prefix)
    alias = document['alias']
    try:
        resolved = client.indices.get_alias(name=alias)
    except NotFoundError:
        if client.indices.exists(index=alias):
            return {'name': alias, 'status': 'CONFLICT',
                    'reason': 'Le nom logique est déjà un index ou un flux, pas un alias'}
        return {'name': alias, 'status': 'MISSING', 'targets': []}
    if set(resolved) != {name}:
        return {'name': alias, 'status': 'CONFLICT', 'targets': sorted(resolved),
                'reason': 'Alias associé à un autre index ou à plusieurs index'}
    options = resolved[name].get('aliases', {}).get(alias)
    if (not isinstance(options, dict) or options.get('is_write_index') is False
            or set(options) - {'is_write_index'}):
        return {'name': alias, 'status': 'CONFLICT', 'targets': [name],
                'reason': 'Alias avec filtre, routage ou configuration incompatible'}
    # A single unfiltered target is writable even when is_write_index is omitted.
    return {'name': alias, 'status': 'OK', 'targets': [name]}


def require_alias(state: dict) -> None:
    if state['status'] != 'OK':
        raise MappingError(f"{state['name']} : alias {state['status']} ; aucune réparation automatique")


def creation_preflight(client: OpenSearch, document: dict, prefix: str) -> bool:
    """Return whether the declared pair exists; reject every known conflict."""
    name = guard(document, prefix)
    state = alias_state(client, document, prefix)
    if state['status'] == 'CONFLICT':
        require_alias(state)
    if client.indices.exists(index=name):
        ensure_concrete(client, name)
        require_alias(state)
        return True
    if state['status'] != 'MISSING':
        raise MappingError('État index/alias modifié pendant la vérification ; relancer')
    return False


def create_index(client: OpenSearch, document: dict, prefix: str) -> str:
    name = guard(document, prefix)
    if creation_preflight(client, document, prefix):
        return "EXISTS — non créé ; conformité non vérifiée"
    try:
        response = client.indices.create(index=name, body={
            "settings": document["settings"], "mappings": document["mappings"],
            'aliases': {document['alias']: {'is_write_index': True}},
        })
    except RequestError as error:
        if error.error == "resource_already_exists_exception":
            raise MappingError('Création concurrente : index déjà présent ; vérifier index et alias') from None
        raise
    if not response.get("acknowledged"):
        raise RuntimeError("Création non acquittée")
    require_alias(alias_state(client, document, prefix))
    return "CREATED"


def check_index(client: OpenSearch, document: dict, prefix: str) -> dict:
    name = guard(document, prefix)
    state = alias_state(client, document, prefix)
    if not client.indices.exists(index=name):
        return {'status': 'DIFFERENT' if state['status'] == 'CONFLICT' else 'MISSING', 'alias': state}
    ensure_concrete(client, name)
    remote = client.indices.get_mapping(index=name)[name]["mappings"]
    diff = compare_mappings(document["mappings"], remote)
    actual = flatten_settings(client.indices.get_settings(index=name)[name]["settings"])
    settings_diff = [f"{key} : {actual.get(key)!r} -> {value!r}"
                     for key, value in flatten_settings(document["settings"]).items()
                     if str(actual.get(key)) != str(value)]
    return {"status": "OK" if diff.conforms and not settings_diff and state['status'] == 'OK' else "DIFFERENT",
            'alias': state,
            "missing": diff.missing, "incompatible": diff.incompatible,
            "extra": diff.extra, "settings": settings_diff}


def apply_mapping(client: OpenSearch, document: dict, prefix: str, dry_run: bool = False) -> dict:
    name = guard(document, prefix)
    if not client.indices.exists(index=name):
        raise MappingError(f"{name} : index absent ; exécuter create")
    require_alias(alias_state(client, document, prefix))
    ensure_concrete(client, name)
    remote = client.indices.get_mapping(index=name)[name]["mappings"]
    diff = compare_mappings(document["mappings"], remote)
    if diff.incompatible:
        raise MappingError(f"{name} : incompatibilités : " + "; ".join(diff.incompatible)
                           + ' ; nouvel index versionné nécessaire, migration manuelle')
    result = {"status": "DRY-RUN" if dry_run else "UNCHANGED",
              "missing": diff.missing, "patch": diff.additions, "extra": diff.extra}
    if dry_run or not diff.missing:
        return result
    response = client.indices.put_mapping(index=name, body=diff.additions)
    if not response.get("acknowledged"):
        raise RuntimeError("Modification non acquittée")
    verified = compare_mappings(document["mappings"], client.indices.get_mapping(index=name)[name]["mappings"])
    if not verified.conforms:
        raise RuntimeError("Mapping distant non conforme après modification")
    result["status"] = "APPLIED"
    return result


def index_info(client: OpenSearch, document: dict, prefix: str) -> dict:
    name = guard(document, prefix)
    state = alias_state(client, document, prefix)
    if not client.indices.exists(index=name):
        return {"index": name, "exists": False, 'alias': state}
    ensure_concrete(client, name)
    mapping = client.indices.get_mapping(index=name)[name]["mappings"]
    settings = flatten_settings(client.indices.get_settings(index=name)[name]["settings"])
    stats = client.indices.stats(index=name, metric="docs,store")["indices"][name]
    return {"index": name, "exists": True, 'alias': state,
            "documents": stats["primaries"]["docs"]["count"],
            "storage_bytes": stats["total"]["store"]["size_in_bytes"],
            "primary_storage_bytes": stats["primaries"]["store"]["size_in_bytes"],
            "shards": settings.get("number_of_shards"),
            "replicas": settings.get("number_of_replicas"), "fields": count_fields(mapping)}


def removal_plan(client: OpenSearch, document: dict, prefix: str) -> dict:
    """Read-only preview for a single declared, concrete index."""
    name = guard(document, prefix)
    state = alias_state(client, document, prefix)
    if state['status'] == 'CONFLICT':
        require_alias(state)
    if not client.indices.exists(index=name):
        return {'index': name, 'status': 'MISSING', 'exists': False}
    require_alias(state)
    ensure_concrete(client, name)
    resolved = client.indices.get(index=name)
    if set(resolved) != {name} or resolved[name].get('data_stream'):
        raise MappingError(f'{name} : index concret attendu (alias/flux refusé)')
    uuid = resolved[name].get('settings', {}).get('index', {}).get('uuid')
    if not isinstance(uuid, str) or not uuid:
        raise MappingError(f'{name} : UUID de l’index introuvable')
    count = client.count(index=name)
    if count.get('_shards', {}).get('failed', 0):
        raise RuntimeError('Comptage incomplet avant suppression')
    return {'index': name, 'status': 'PREVIEW', 'exists': True,
            'alias': document['alias'], 'uuid': uuid, 'visible_documents': count['count']}


def verify_removal_target(client: OpenSearch, document: dict, prefix: str, expected_uuid: str) -> str:
    current = removal_plan(client, document, prefix)
    if not current['exists'] or current['uuid'] != expected_uuid:
        raise MappingError('Index absent ou recréé depuis la prévisualisation ; relancer la commande')
    return current['index']


def delete_index(client: OpenSearch, document: dict, prefix: str, expected_uuid: str) -> dict:
    """Explicit deletion of one index, after CLI preview and confirmation."""
    name = verify_removal_target(client, document, prefix, expected_uuid)
    response = client.indices.delete(index=name)
    acknowledged = response.get('acknowledged') is True
    absent = not client.indices.exists(index=name)
    return {'index': name, 'status': 'DELETED' if acknowledged and absent else 'PARTIAL',
            'acknowledged': acknowledged, 'exists_after': not absent}


def clear_index(client: OpenSearch, document: dict, prefix: str, expected_uuid: str) -> dict:
    """Delete documents while keeping index, mappings, settings and stored scripts."""
    name = verify_removal_target(client, document, prefix, expected_uuid)
    # Make documents from recent test writes visible to the deletion snapshot.
    refreshed = client.indices.refresh(index=name)
    if refreshed.get('_shards', {}).get('failed', 0):
        raise RuntimeError('Refresh incomplet avant suppression des données')
    response = client.delete_by_query(index=name, body={'query': {'match_all': {}}}, params={
        'conflicts': 'proceed', 'refresh': 'true', 'wait_for_completion': 'true',
        'requests_per_second': 1, 'scroll_size': 100,
    })
    required = {'total', 'deleted', 'version_conflicts', 'failures', 'timed_out'}
    failed = (not required.issubset(response) or response.get('timed_out', False)
              or bool(response.get('failures')) or response.get('version_conflicts', 0) != 0
              or response.get('deleted') != response.get('total'))
    after = removal_plan(client, document, prefix)
    remaining = after.get('visible_documents')
    failed = failed or after.get('uuid') != expected_uuid or remaining != 0
    return {'index': name, 'status': 'PARTIAL' if failed else 'CLEARED',
            'deleted_documents': response.get('deleted'), 'remaining_documents': remaining,
            'version_conflicts': response.get('version_conflicts', 0),
            'failures_count': len(response.get('failures', [])),
            'timed_out': response.get('timed_out', False)}
