"""Installer la commande backup-index dans l’environnement virtuel actif."""
from pathlib import Path
import shlex
import sys
import sysconfig


def install_command(directory: Path) -> Path:
    launcher = directory / 'backup-index'
    entrypoint = Path(__file__).resolve().parent / 'main.py'
    launcher.write_text(
        '#!/bin/sh\n'
        f'exec {shlex.quote(sys.executable)} {shlex.quote(str(entrypoint))} "$@"\n',
        encoding='utf-8',
    )
    launcher.chmod(0o755)
    return launcher


if __name__ == '__main__':
    if sys.prefix == sys.base_prefix:
        sys.exit('Activer un environnement virtuel avant : python install.py')
    print(f'Commande installée : {install_command(Path(sysconfig.get_path("scripts")))}')
