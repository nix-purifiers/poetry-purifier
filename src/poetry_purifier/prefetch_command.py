# SPDX-License-Identifier: MIT
#
# `poetry purifier prefetch`: makes the everyday loop work with the network
# cut off.
#
# The command has Nix fetch every file the lockfile names for this system,
# which covers every group and extra and the build requirements of sdists,
# and the interpreter the lock was made with. It links the files into
# Poetry's cache, keeps them and the interpreter alive with a
# garbage-collector root in .poetry-purifier/, and points the project's
# virtual environment at that interpreter. Afterwards `poetry install`,
# `poetry sync`, `poetry build` and `poetry run` need no network. Python
# editors read the project's virtual environment and download nothing.
from __future__ import annotations

import os

from pathlib import Path

from poetry.console.commands.command import Command
from poetry.utils.env import EnvManager

from poetry_purifier import interactive
from poetry_purifier import lockfile
from poetry_purifier import lockfile_option


class PurifierPrefetchCommand(Command):
    name = "purifier prefetch"
    description = (
        "Fetches the locked files and the interpreter through Nix, so that"
        " Poetry works offline."
    )
    options = [*lockfile_option.OPTIONS]  # noqa: RUF012

    def handle(self) -> int:
        root = self.poetry.pyproject_path.parent
        lock_path = lockfile_option.path(self)
        tool_lock = lockfile_option.tool_path(self, root)
        realize = os.environ.get("POETRY_PURIFIER_REALIZE")
        if realize is None:
            self.line_error(
                "<error>POETRY_PURIFIER_REALIZE is not set. Run `poetry purifier"
                " prefetch` with the Poetry from the poetry-purifier flake.</error>"
            )
            return 1
        if not lock_path.exists():
            self.line_error(
                f"<error>{lock_path} is missing. Run `poetry purifier lock`.</error>"
            )
            return 1
        try:
            lock = lockfile.read(lock_path)
        except lockfile.LockfileError as e:
            self.line_error(f"<error>{e}</error>")
            return 1
        if lock["inputsHash"] != lockfile.inputs_hash(root, tool_lock):
            self.line_error(
                f"<error>{lock_path} is out of date with pyproject.toml or"
                " poetry.lock. Run `poetry purifier lock`.</error>"
            )
            return 1

        realized = interactive.use_locked_cache(
            root, lock_path, tool_lock, Path(realize), self.io
        )
        python = realized / "python" / "bin" / f"python{lock['python']}"
        # What `poetry env use` does.
        env = EnvManager(self.poetry, io=self.io).activate(str(python))
        self.line(f"Using virtualenv: {env.path}")
        return 0
