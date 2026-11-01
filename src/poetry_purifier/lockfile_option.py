# SPDX-License-Identifier: MIT
#
# The options that every purifier command takes, so that the lockfiles can
# live outside the project, as the `lockFile` and `poetryLock` arguments of
# buildPoetryProject can:
#
# - `--lockfile` names the purifier lockfile, and POETRY_PURIFIER_LOCKFILE
#   does too.
# - `--tool-lockfile` names poetry.lock, and POETRY_PURIFIER_TOOL_LOCKFILE
#   does too.
#
# The option takes precedence over the variable, and both take precedence
# over the file next to pyproject.toml. A dev shell can set the variables
# once.
from __future__ import annotations

import os

from pathlib import Path
from typing import Any

from cleo.helpers import option

from poetry_purifier import lockfile


VARIABLE = "POETRY_PURIFIER_LOCKFILE"
TOOL_VARIABLE = "POETRY_PURIFIER_TOOL_LOCKFILE"

OPTION = option(
    "lockfile",
    None,
    f"The purifier lockfile. By default, ${VARIABLE}, or {lockfile.FILE_NAME}"
    " next to pyproject.toml.",
    flag=False,
)

TOOL_OPTION = option(
    "tool-lockfile",
    None,
    f"The poetry.lock of the project. By default, ${TOOL_VARIABLE}, or"
    " poetry.lock next to pyproject.toml. With a path outside the project,"
    " the commands that write poetry.lock write it there and leave the copy"
    " in the project as it is.",
    flag=False,
)

OPTIONS = [OPTION, TOOL_OPTION]


def path(command: Any) -> Path:
    given = command.option("lockfile") or os.environ.get(VARIABLE)
    if given:
        return Path(given).resolve()
    return command.poetry.pyproject_path.parent / lockfile.FILE_NAME


def tool_path(command: Any, root: Path) -> Path:
    """The poetry.lock of the project at `root` that the command uses."""
    given = command.option("tool-lockfile") or os.environ.get(TOOL_VARIABLE)
    if given:
        return Path(given).resolve()
    return root / "poetry.lock"


def tool_path_from_environment(root: Path) -> Path:
    """The poetry.lock of the project at `root` for code that runs outside a
    command, which has no options to read."""
    given = os.environ.get(TOOL_VARIABLE)
    if given:
        return Path(given).resolve()
    return root / "poetry.lock"
