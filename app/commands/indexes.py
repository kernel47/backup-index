"""Commandes de consultation, validation et évolution des index."""
import logging
from typing import Annotated

import typer

from app.client import error_message
from app.config import load_settings
from app.indexes import create_index, check_index, apply_mapping, index_info, creation_preflight
from app.commands import runtime
from app.mappings import MappingError, load_mappings
from app.commands.runtime import app, emit


def execute(ctx: typer.Context, action: str, target: str | None = None,
            all_indexes: bool = False, dry_run: bool = False) -> None:
    client = None
    code = 0
    try:
        settings = load_settings(ctx.obj)
        logging.basicConfig(level=settings.log_level, format="%(levelname)s: %(message)s")
        logging.getLogger("app").setLevel(settings.log_level)
        # The transport can log raw response bodies. Only expose sanitized CLI errors.
        logging.getLogger("opensearch").disabled = True
        logging.getLogger("opensearchpy.trace").disabled = True
        logging.getLogger("app").debug("Commande : %s", action)
        if action == "ping":
            client = runtime.create_client(settings)
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
            client = runtime.create_client(settings)
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


