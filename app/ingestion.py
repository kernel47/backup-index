"""Validation des règles locales d’ingestion, jamais envoyées à OpenSearch."""
from app.errors import MappingError

KINDS = ('jobs', 'images', 'policies', 'slps')
OWNED = {'created_at', 'history'}
TRACKING = {'updated_at', 'collected_at'}
CORE_IDENTITY = {
    'jobs': {'master_server', 'job_id', 'start_time'},
    'images': {'master_server', 'image_id'},
    'policies': {'master_server', 'name'},
    'slps': {'master_server', 'name'},
}


def validate_ingestion(document: dict, kind: str | None = None) -> None:
    rules = document.get('ingestion')
    if rules is None:
        if kind in KINDS:
            raise MappingError('ingestion : règles obligatoires pour la génération des scripts')
        return
    if not isinstance(rules, dict) or set(rules) - {'required', 'identity', 'mutable'}:
        raise MappingError('ingestion : objet required/identity/mutable attendu')
    fields = set(document['mappings']['properties'])
    for name in ('required', 'identity', 'mutable'):
        values = rules.get(name, [] if name == 'mutable' else None)
        if (not isinstance(values, list) or any(not isinstance(v, str) for v in values)
                or len(values) != len(set(values)) or set(values) - fields):
            raise MappingError(f'ingestion.{name} : liste sans doublons de champs du mapping attendue')
        if set(values) & OWNED:
            raise MappingError(f'ingestion.{name} : created_at/history réservés aux scripts')
    required, identity, mutable = (set(rules.get(key, [])) for key in ('required', 'identity', 'mutable'))
    if not identity or not identity <= required:
        raise MappingError('ingestion.identity : champs obligatoires requis')
    if identity & TRACKING or mutable & (identity | TRACKING):
        raise MappingError('ingestion.mutable/identity : identité et dates de suivi incompatibles')
    if kind in KINDS:
        mandatory = CORE_IDENTITY[kind] | {'updated_at'}
        if kind == 'jobs':
            mandatory |= {'state'}
        if kind in {'policies', 'slps'}:
            mandatory |= {'hash', 'data'}
            if mutable:
                raise MappingError('ingestion.mutable : configuration complète pour policies/SLP')
        if not mandatory <= required or not CORE_IDENTITY[kind] <= identity:
            raise MappingError('ingestion : identité et champs métier obligatoires manquants')
        managed = {'created_at', 'history'} if kind in {'policies', 'slps'} else {'created_at'}
        if not managed <= fields:
            raise MappingError('ingestion : champs de suivi gérés par le script absents du mapping')
