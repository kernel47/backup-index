"""Application Typer, sortie JSON et frontière d’erreur des commandes."""
import json
import logging
from pathlib import Path
from typing import Annotated
import typer

from app.client import create_client, error_message
from app.config import load_settings
from app.mappings import MappingError

app = typer.Typer(no_args_is_help=True, help="Administration prudente des index OpenSearch Backup.")


@app.callback()
def main(ctx: typer.Context, env_file: Annotated[Path | None, typer.Option("--env-file", help="Fichier .env explicite")] = None):
    ctx.obj = env_file


def emit(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


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


