"""Génération déterministe : contrats JSON + modèles Painless métier."""
import json
from pathlib import Path

from app.config import PROJECT_ROOT
from app.ingestion import KINDS, OWNED, TRACKING, validate_ingestion
from app.mappings import MappingError, load_mappings


def _literal(values) -> str:
    # JSON string escaping is also valid for Painless string literals.
    return json.dumps(sorted(values), ensure_ascii=True, separators=(',', ':'))


def _contract(document: dict, *, declare: bool = True) -> str:
    fields = set(document['mappings']['properties']) - OWNED
    rules = document['ingestion']
    immutable = fields - set(rules.get('mutable', [])) - TRACKING - {'data'}
    declaration = 'def ' if declare else ''
    return '\n'.join([
        f'{declaration}allowed = {_literal(fields)};',
        f'{declaration}required = {_literal(rules["required"])};',
        f'{declaration}identity = {_literal(rules["identity"])};',
        f'{declaration}immutable = {_literal(immutable)};',
    ])


def generate_scripts(directory: Path | None = None, *, mappings_dir: Path | None = None,
                     prefix: str = 'backup') -> dict[str, str]:
    documents = load_mappings(mappings_dir or PROJECT_ROOT / 'mappings', prefix)
    for kind in KINDS:
        if kind not in documents:
            raise MappingError(f'{kind} : mapping requis pour générer les scripts')
        validate_ingestion(documents[kind], kind)
    result = {}
    for name in ('jobs', 'images', 'configuration'):
        path = (directory or PROJECT_ROOT / 'scripts') / f'{name}_upsert.painless'
        try:
            template = path.read_text(encoding='utf-8')
        except OSError:
            raise MappingError(f'{path.name} : lecture du modèle impossible') from None
        if template.count('/*__CONTRACT__*/') != 1:
            raise MappingError(f'{path.name} : marqueur de contrat unique attendu')
        if name == 'configuration':
            contract = '\n'.join([
                'def allowed; def required; def identity; def immutable;',
                "if (params.kind == 'policies') {",
                _contract(documents['policies'], declare=False),
                '} else {', _contract(documents['slps'], declare=False), '}',
            ])
        else:
            contract = _contract(documents[name])
        contract += '''
for (def key : required) {
    if (incoming[key] == null || (incoming[key] instanceof String && incoming[key].isEmpty())) {
        throw new IllegalArgumentException('Missing required field: ' + key);
    }
}
'''
        result[f'backup_{name}_upsert'] = (
            '// Generated from mappings/*.json; do not edit the generated source.\n'
            + template.replace('/*__CONTRACT__*/', contract)
        )
    return result
