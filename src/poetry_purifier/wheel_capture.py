# SPDX-License-Identifier: MIT
"""Retain native runtime wheels without exporting isolated build environments."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile


def enable():
    destination = os.environ.get('POETRY_PURIFIER_EXPORT_WHEELS')
    environment = os.environ.get('POETRY_PURIFIER_EXPORT_ENV')
    if not destination or not environment:
        return
    from poetry.installation.wheel_installer import WheelInstaller
    original = WheelInstaller.install
    if getattr(original, '_purifier_capture', False):
        return

    def install(self, wheel):
        if self._env.path == Path(environment):
            directory = Path(destination)
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
                path = Path(temporary.name)
                with wheel.open('rb') as source:
                    shutil.copyfileobj(source, temporary)
            try:
                try:
                    os.link(path, directory / wheel.name)
                except FileExistsError:
                    with path.open('rb') as source, (directory / wheel.name).open('rb') as previous:
                        if hashlib.file_digest(source, 'sha256').digest() != hashlib.file_digest(previous, 'sha256').digest():
                            raise ValueError('conflicting native wheel bytes: ' + wheel.name)
            finally:
                path.unlink()
        return original(self, wheel)

    install._purifier_capture = True
    WheelInstaller.install = install
