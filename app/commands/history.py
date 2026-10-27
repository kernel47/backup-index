"""Prévisualisation et purge des versions historiques."""
from typing import Annotated
import typer

from app.mappings import MappingError, load_mappings
from app.commands.runtime import app, administrative, emit
purge_app = typer.Typer(no_args_is_help=True, help="Purge manuelle des anciens historiques uniquement.")
app.add_typer(purge_app, name='purge')

@purge_app.command('history')
def purge_history(ctx: typer.Context,
                  keep: Annotated[int | None, typer.Option('--keep')] = None,
                  older_than: Annotated[str | None, typer.Option('--older-than')] = None,
                  all_versions: bool = typer.Option(False, '--all'),
                  kind: Annotated[str | None, typer.Option('--type')] = None,
                  dry_run: bool = typer.Option(False, '--dry-run'),
                  yes: bool = typer.Option(False, '--yes', help='Confirmer explicitement sans interaction'),
                  requests_per_second: float = typer.Option(1.0, '--requests-per-second')):
    """Prévisualiser, puis purger les anciennes versions de policies et/ou SLP."""
    from app.purge import purge_options, validate_history_aliases, plan_purge, execute_purge
    def operation(settings, client):
        options = purge_options(keep, older_than, all_versions)
        if not 0 < requests_per_second <= 10:
            raise MappingError('--requests-per-second doit être > 0 et <= 10')
        documents = load_mappings(settings.mappings_dir, settings.index_prefix)
        targets = validate_history_aliases(client, documents, settings.index_prefix, kind)
        emit({'phase': 'PLAN', 'targets': targets, **options})
        plan = plan_purge(client, targets, options, report=emit)
        emit({'status': 'DRY-RUN' if dry_run else 'PREVIEW', 'documents': len(plan),
              'versions_to_remove': sum(item['before'] - item['after'] for item in plan)})
        if dry_run or not plan:
            return 0
        if not yes:
            typer.confirm('Confirmer la suppression des anciennes versions affichées ?', abort=True)
        validate_history_aliases(client, documents, settings.index_prefix, kind)
        result = execute_purge(client, targets, plan, options, requests_per_second, report=emit)
        emit(result)
        return 1 if result['status'] != 'OK' else 0
    administrative(ctx, operation)


