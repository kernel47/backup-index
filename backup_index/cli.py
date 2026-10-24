"""Interface administrative exécutée à la demande."""
import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from backup_index.client import create_client, error_message
from backup_index.config import load_settings
from backup_index.indexes import create_index, check_index, apply_mapping, index_info, creation_preflight
from backup_index.mappings import MappingError, load_mappings

app = typer.Typer(no_args_is_help=True, help="Administration prudente des index OpenSearch Backup.")


@app.callback()
def main(ctx: typer.Context, env_file: Annotated[Path | None, typer.Option("--env-file", help="Fichier .env explicite")] = None):
    ctx.obj = env_file


def emit(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


def execute(ctx: typer.Context, action: str, target: str | None = None,
            all_indexes: bool = False, dry_run: bool = False) -> None:
    client = None
    code = 0
    try:
        settings = load_settings(ctx.obj)
        logging.basicConfig(level=settings.log_level, format="%(levelname)s: %(message)s")
        logging.getLogger("backup_index").setLevel(settings.log_level)
        # The transport can log raw response bodies. Only expose sanitized CLI errors.
        logging.getLogger("opensearch").disabled = True
        logging.getLogger("opensearchpy.trace").disabled = True
        logging.getLogger("backup_index").debug("Commande : %s", action)
        if action == "ping":
            client = create_client(settings)
            info = client.info()
            emit({"status": "OK", "version": info.get("version", {}).get("number"),
                  "cluster": info.get("cluster_name")})
            return
        documents = load_mappings(settings.mappings_dir, settings.index_prefix)
        if action == "list":
            for stem, document in documents.items():
                emit({"file": stem + ".json", "index": document["index"], 'alias': document['alias']})
            return
        if bool(target) == all_indexes:
            raise MappingError("Choisir un mapping OU --all")
        if target:
            matches = [stem for stem, doc in documents.items()
                       if target in {stem, doc['index'], doc['alias']}]
            if len(matches) != 1:
                raise MappingError('Mapping inconnu ou ambigu : utiliser la commande list')
            target = matches[0]
        selected = documents if all_indexes else {target: documents[target]}
        # All local files have been validated before opening a connection.
        if action != "validate":
            client = create_client(settings)
        if action == 'create' and all_indexes:
            for document in selected.values():
                creation_preflight(client, document, settings.index_prefix)
        for stem, document in selected.items():
            try:
                if action == "validate":
                    result = {"status": "OK", "file": stem + ".json"}
                elif action == "create":
                    result = {"status": create_index(client, document, settings.index_prefix)}
                elif action == "check":
                    result = check_index(client, document, settings.index_prefix)
                    if result["status"] != "OK":
                        code = 1 if code == 1 else 2
                elif action == "apply":
                    result = apply_mapping(client, document, settings.index_prefix, dry_run)
                else:
                    result = index_info(client, document, settings.index_prefix)
                emit({"index": document["index"], **result})
            except MappingError as error:
                emit({"index": document["index"], "status": "ERROR", "message": str(error)})
                code = 1 if code == 1 else 2
            except Exception as error:
                emit({"index": document["index"], "status": "ERROR", "message": error_message(error)})
                code = 1
    except (ValueError, OSError) as error:
        # Pydantic's raw ValidationError includes input values, potentially secrets.
        message = str(error) if isinstance(error, MappingError) else "Configuration invalide : vérifier .env, URL, timeout et certificat CA."
        typer.echo(message, err=True)
        code = 2
    except Exception as error:
        typer.echo(error_message(error), err=True)
        code = 1
    finally:
        if client is not None:
            client.close()
    if code:
        raise typer.Exit(code)


@app.command()
def ping(ctx: typer.Context):
    """Tester connexion, version et nom du cluster."""
    execute(ctx, "ping")


@app.command("list")
def list_command(ctx: typer.Context):
    """Lister les fichiers locaux et les index déclarés."""
    execute(ctx, "list")


@app.command()
def validate(ctx: typer.Context, target: Annotated[str | None, typer.Argument()] = None, all_indexes: bool = typer.Option(False, "--all")):
    """Valider localement les mappings, sans connexion."""
    execute(ctx, "validate", target, all_indexes)


@app.command()
def create(ctx: typer.Context, target: Annotated[str | None, typer.Argument()] = None, all_indexes: bool = typer.Option(False, "--all")):
    """Créer uniquement les index absents."""
    execute(ctx, "create", target, all_indexes)


@app.command()
def check(ctx: typer.Context, target: Annotated[str | None, typer.Argument()] = None, all_indexes: bool = typer.Option(False, "--all")):
    """Comparer mappings et paramètres attendus."""
    execute(ctx, "check", target, all_indexes)


@app.command()
def apply(ctx: typer.Context, target: Annotated[str | None, typer.Argument()] = None, dry_run: bool = typer.Option(False, "--dry-run")):
    """Ajouter les nouveaux champs compatibles à un index existant."""
    execute(ctx, "apply", target, dry_run=dry_run)


@app.command()
def info(ctx: typer.Context, target: Annotated[str | None, typer.Argument()] = None, all_indexes: bool = typer.Option(False, "--all")):
    """Afficher les statistiques sans récupérer de documents."""
    execute(ctx, "info", target, all_indexes)


scripts_app = typer.Typer(no_args_is_help=True, help="Administration des scripts stockés.")
purge_app = typer.Typer(no_args_is_help=True, help="Purge manuelle des anciens historiques uniquement.")
app.add_typer(scripts_app, name='scripts')
app.add_typer(purge_app, name='purge')


def administrative(ctx: typer.Context, operation, remote: bool = True) -> None:
    """Shared sanitized error boundary for V2 administrative commands."""
    client = None
    try:
        settings = load_settings(ctx.obj)
        logging.getLogger('opensearch').disabled = True
        logging.getLogger('opensearchpy.trace').disabled = True
        if remote:
            client = create_client(settings)
        code = operation(settings, client) or 0
    except typer.Abort:
        typer.echo('Opération annulée ; aucune suppression effectuée.', err=True)
        code = 1
    except (ValueError, OSError) as error:
        typer.echo(str(error) if isinstance(error, MappingError) else 'Configuration invalide.', err=True)
        code = 2
    except Exception as error:
        typer.echo(error_message(error), err=True)
        code = 1
    finally:
        if client is not None:
            client.close()
    if code:
        raise typer.Exit(code)


@scripts_app.command('list')
def scripts_list(ctx: typer.Context):
    """Lister les trois scripts locaux sans connexion."""
    from backup_index.scripts import load_scripts
    def operation(settings, client):
        for script_id in load_scripts():
            emit({'id': script_id, 'history_default': settings.history_max_versions
                  if script_id == 'backup_configuration_upsert' else None})
    administrative(ctx, operation, remote=False)


@scripts_app.command('check')
def scripts_check(ctx: typer.Context, all_scripts: bool = typer.Option(False, '--all')):
    """Comparer les scripts locaux avec les scripts stockés."""
    from backup_index.scripts import load_scripts, script_plan
    def operation(settings, client):
        if not all_scripts:
            raise MappingError('Indiquer --all')
        plan = script_plan(client, load_scripts())
        for item in plan:
            emit(item)
        return 2 if any(item['status'] != 'OK' for item in plan) else 0
    administrative(ctx, operation)


@scripts_app.command('install')
def scripts_install(ctx: typer.Context, all_scripts: bool = typer.Option(False, '--all'),
                    dry_run: bool = typer.Option(False, '--dry-run'),
                    replace: bool = typer.Option(False, '--replace')):
    """Installer ; un remplacement différent exige --replace après examen du diff."""
    from backup_index.scripts import load_scripts, script_plan, install_scripts
    def operation(settings, client):
        if not all_scripts:
            raise MappingError('Indiquer --all')
        sources = load_scripts()
        plan = script_plan(client, sources)
        for item in plan:
            emit(item)
        if dry_run:
            emit({'status': 'DRY-RUN', 'writes': 0})
            return 0
        for result in install_scripts(client, sources, plan, replace):
            emit(result)
    administrative(ctx, operation)


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
    from backup_index.purge import purge_options, validate_history_aliases, plan_purge, execute_purge
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


def destructive_command(ctx: typer.Context, target: str, action: str, dry_run: bool, yes: bool) -> None:
    from backup_index.indexes import removal_plan, delete_index, clear_index
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
