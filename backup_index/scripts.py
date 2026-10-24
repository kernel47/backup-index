"""Lecture, comparaison et installation explicite des trois scripts stockés."""
from difflib import unified_diff
from pathlib import Path

from opensearchpy.exceptions import NotFoundError

from backup_index.config import PROJECT_ROOT
from backup_index.mappings import MappingError

SCRIPT_NAMES = ('jobs_upsert', 'images_upsert', 'configuration_upsert')


def load_scripts(directory: Path | None = None) -> dict[str, str]:
    directory = directory or PROJECT_ROOT / 'scripts'
    result = {}
    for name in SCRIPT_NAMES:
        path = directory / f'{name}.painless'
        try:
            source = path.read_text(encoding='utf-8')
        except OSError:
            raise MappingError(f'{path.name} : lecture du script impossible') from None
        if not source.strip():
            raise MappingError(f'{path.name} : script vide')
        result[f'backup_{name}'] = source
    return result


def script_plan(client, sources: dict[str, str]) -> list[dict]:
    plan = []
    for script_id, source in sources.items():
        try:
            remote = client.get_script(id=script_id)['script']
        except NotFoundError:
            remote = None
        status = 'MISSING' if remote is None else (
            'OK' if remote.get('lang') == 'painless' and remote.get('source') == source else 'DIFFERENT')
        diff = '' if status == 'OK' else ''.join(unified_diff(
            (remote or {}).get('source', '').splitlines(keepends=True),
            source.splitlines(keepends=True), fromfile=f'{script_id}:remote', tofile=f'{script_id}:local'))
        plan.append({'id': script_id, 'status': status, 'diff': diff,
                     'remote_lang': remote.get('lang') if remote else None})
    return plan


def install_scripts(client, sources: dict[str, str], plan: list[dict], replace: bool = False) -> list[dict]:
    if any(item['status'] == 'DIFFERENT' for item in plan) and not replace:
        raise MappingError('Script distant différent : examiner le diff puis utiliser --replace explicitement')
    # Re-read all scripts before writing; avoid overwriting a change since the preview.
    # Stored script API has no optimistic compare-and-swap. Serialize administrator changes.
    fresh = script_plan(client, sources)
    if fresh != plan:
        raise MappingError('Scripts distants modifiés depuis la prévisualisation ; relancer la commande')
    results = []
    for item in plan:
        script_id = item['id']
        if item['status'] == 'OK':
            results.append({'id': script_id, 'status': 'UNCHANGED'})
            continue
        response = client.put_script(id=script_id, body={
            'script': {'lang': 'painless', 'source': sources[script_id]},
        })
        if not response.get('acknowledged'):
            raise RuntimeError('Installation de script non acquittée')
        verified = client.get_script(id=script_id)['script']
        if verified.get('lang') != 'painless' or verified.get('source') != sources[script_id]:
            raise RuntimeError('Vérification de script échouée après installation')
        results.append({'id': script_id, 'status': 'INSTALLED'})
    return results
