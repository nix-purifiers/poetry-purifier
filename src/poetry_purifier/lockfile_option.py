# SPDX-License-Identifier: MIT
#
# The `--lockfile` option that every purifier command takes, so that the
# lockfile can live outside the project, as buildPoetryProject's `lockFile`
# can. The environment variable POETRY_PURIFIER_LOCKFILE names it too, so
# that a dev shell can set it once. The option takes precedence over the
# variable, and both take precedence over the lockfile next to
# pyproject.toml.
from __future__ import annotations

import os

from pathlib import Path
from typing import Any

from cleo.helpers import option

from poetry_purifier import lockfile


VARIABLE = "POETRY_PURIFIER_LOCKFILE"

OPTION = option(
    "lockfile",
    None,
    f"The purifier lockfile. By default, ${VARIABLE}, or {lockfile.FILE_NAME}"
    " next to pyproject.toml.",
    flag=False,
)


def path(command: Any) -> Path:
    given = command.option("lockfile") or os.environ.get(VARIABLE)
    if given:
        return Path(given).resolve()
    return command.poetry.pyproject_path.parent / lockfile.FILE_NAME
