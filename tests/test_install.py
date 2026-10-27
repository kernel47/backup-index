"""La commande installée retrouve le projet sans dépendre du dossier courant."""
import subprocess

from install import install_command


def test_installed_command_from_another_directory(tmp_path):
    destination = tmp_path / 'bin avec espaces'
    destination.mkdir()
    launcher = install_command(destination)
    result = subprocess.run([str(launcher), 'validate', '--all'], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count('"status": "OK"') == 5
    result = subprocess.run([str(launcher), '--help'], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'backup-index' in result.stdout
