"""Assemblage des commandes ; point d’entrée public de la CLI."""
from app.commands.runtime import app
from app.commands import indexes, scripts, history, destructive  # noqa: F401

__all__ = ['app']
