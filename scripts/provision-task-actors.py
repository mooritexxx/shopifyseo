#!/usr/bin/env python3
"""Create missing task actor tokens locally. Never prints credentials or replaces tokens."""
import os
import secrets
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.services.task_identity import ACTORS, token_directory


def main():
    directory = token_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    for actor in ACTORS:
        path = directory / f'{actor}.token'
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, 'w') as stream:
            stream.write(secrets.token_urlsafe(48) + '\n')
    print(f'Actor token files ready in {directory} (existing credentials preserved).')


if __name__ == '__main__':
    main()
