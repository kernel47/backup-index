"""Génération locale et administration des scripts stockés."""
import typer

from app.mappings import MappingError
from app.commands.runtime import app, administrative, emit
scripts_app = typer.Typer(no_args_is_help=True, help="Administration des scripts stockés.")
app.add_typer(scripts_app, name='scripts')


@scripts_app.command('list')
def scripts_list(ctx: typer.Context):
    """Lister les trois scripts locaux sans connexion."""
    from app.scripts import load_scripts
    def operation(settings, client):
        for script_id in load_scripts(mappings_dir=settings.mappings_dir, prefix=settings.index_prefix):
            emit({'id': script_id, 'history_default': settings.history_max_versions
                  if script_id == 'backup_configuration_upsert' else None})
    administrative(ctx, operation, remote=False)


@scripts_app.command('check')
def scripts_check(ctx: typer.Context, all_scripts: bool = typer.Option(False, '--all')):
    """Comparer les scripts locaux avec les scripts stockés."""
    from app.scripts import load_scripts, script_plan
    def operation(settings, client):
        if not all_scripts:
            raise MappingError('Indiquer --all')
        plan = script_plan(client, load_scripts(mappings_dir=settings.mappings_dir, prefix=settings.index_prefix))
        for item in plan:
            emit(item)
        return 2 if any(item['status'] != 'OK' for item in plan) else 0
    administrative(ctx, operation)


@scripts_app.command('install')
def scripts_install(ctx: typer.Context, all_scripts: bool = typer.Option(False, '--all'),
                    dry_run: bool = typer.Option(False, '--dry-run'),
                    replace: bool = typer.Option(False, '--replace')):
    """Installer ; un remplacement différent exige --replace après examen du diff."""
    from app.scripts import load_scripts, script_plan, install_scripts
    def operation(settings, client):
        if not all_scripts:
            raise MappingError('Indiquer --all')
        sources = load_scripts(mappings_dir=settings.mappings_dir, prefix=settings.index_prefix)
        plan = script_plan(client, sources)
        for item in plan:
            emit(item)
        if dry_run:
            emit({'status': 'DRY-RUN', 'writes': 0})
            return 0
        for result in install_scripts(client, sources, plan, replace):
            emit(result)
    administrative(ctx, operation)


