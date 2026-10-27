"""Commandes explicites de suppression de données ou d’index."""
from typing import Annotated
import typer

from app.mappings import MappingError, load_mappings
from app.commands.runtime import app, administrative, emit
def destructive_command(ctx: typer.Context, target: str, action: str, dry_run: bool, yes: bool) -> None:
    from app.indexes import removal_plan, delete_index, clear_index
    def operation(settings, client):
        documents = load_mappings(settings.mappings_dir, settings.index_prefix)
        matches = [doc for stem, doc in documents.items()
                   if target in {stem, doc['index'], doc['alias']}]
        if len(matches) != 1:
            raise MappingError('Mapping inconnu ou ambigu : utiliser la commande list')
        document = matches[0]
        plan = removal_plan(client, document, settings.index_prefix)
        emit({'action': action, **plan})
        if dry_run:
            emit({'status': 'DRY-RUN', 'writes': 0})
            return 0
        if not plan['exists']:
            return 0
        if not yes:
            verb = 'Supprimer définitivement l’index et ses données' if action == 'delete' else 'Supprimer tous les documents en conservant le mapping'
            consequence = f" (alias {plan['alias']} également retiré avec l’index)" if action == 'delete' else ''
            typer.confirm(f"{verb} : {plan['index']}{consequence} ?", abort=True)
        perform = delete_index if action == 'delete' else clear_index
        result = perform(client, document, settings.index_prefix, plan['uuid'])
        emit(result)
        return 1 if result['status'] == 'PARTIAL' else 0
    administrative(ctx, operation)


@app.command('delete')
def delete_command(ctx: typer.Context, target: Annotated[str, typer.Argument()],
                   dry_run: bool = typer.Option(False, '--dry-run'),
                   yes: bool = typer.Option(False, '--yes', help='Confirmer explicitement sans interaction')):
    """Supprimer un index déclaré et tous ses documents (irréversible)."""
    destructive_command(ctx, target, 'delete', dry_run, yes)


@app.command('clear')
def clear_command(ctx: typer.Context, target: Annotated[str, typer.Argument()],
                  dry_run: bool = typer.Option(False, '--dry-run'),
                  yes: bool = typer.Option(False, '--yes', help='Confirmer explicitement sans interaction')):
    """Vider les documents d’un index déclaré, en conservant sa structure."""
    destructive_command(ctx, target, 'clear', dry_run, yes)
