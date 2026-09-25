"""Discover the validated email runtime without importing COM in this daemon."""
import os
from pathlib import Path
import re
import shutil
import sys

from .hermes_paths import hermes_home


def isolated_scripts(root: Path) -> Path | None:
    try:
        generation = (root / 'email-runtime/current.txt').read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        return None
    if not re.fullmatch(r'env-[0-9a-fA-F]{32}', generation):
        return None
    scripts = root / 'email-runtime' / generation / 'Scripts'
    if all((scripts / name).is_file() for name in ('python.exe', 'catfish-email.exe')):
        return scripts
    return None


def find_email_cli() -> str | None:
    explicit = os.environ.get('CATFISH_EMAIL_BIN')
    if explicit and Path(explicit).is_file():
        return explicit
    if sys.platform == 'win32':
        scripts = isolated_scripts(hermes_home())
        if scripts:
            return str(scripts / 'catfish-email.exe')
        legacy = hermes_home() / 'hermes-agent/venv/Scripts/catfish-email.exe'
        if legacy.is_file():
            return str(legacy)
    path = shutil.which('catfish-email')
    if path:
        return path
    local = Path.home() / '.local/bin/catfish-email'
    return str(local) if local.is_file() and os.access(local, os.X_OK) else None
