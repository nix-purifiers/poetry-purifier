# SPDX-License-Identifier: MIT
#
# `poetry purifier publish`: publishes the wheel and sdist that Nix built.
#
# The command has Nix build the project's `release`, as buildPoetryProject
# does, offline, and then runs Poetry's own publish on that directory.
# Repositories, credentials and the other options of `poetry publish` work
# as they do there, and nothing is built outside the Nix sandbox. The
# command sets `--dist-dir` itself and turns `--build` off.
#
# POETRY_PURIFIER_DIST names a directory to publish instead of building
# one, for a build that already has the release.
from __future__ import annotations

import os
import subprocess

from pathlib import Path

from poetry.console.commands.publish import PublishCommand

from poetry_purifier import lockfile
from poetry_purifier import lockfile_option


class PurifierPublishCommand(PublishCommand):
    name = "purifier publish"
    description = "Publishes the wheel and sdist that Nix builds from the lockfile."
    options = [*PublishCommand.options, *lockfile_option.OPTIONS]  # noqa: RUF012

    def handle(self) -> int:
        root = self.poetry.pyproject_path.parent
        lock_path = lockfile_option.path(self)
        tool_lock = lockfile_option.tool_path(self, root)
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

        dist = os.environ.get("POETRY_PURIFIER_DIST") or self._build(
            root, lock_path, tool_lock
        )
        self.line(f"Publishing the files Nix built in {dist}")
        self.io.input.set_option("dist-dir", dist)
        self.io.input.set_option("build", False)
        return super().handle()

    def _build(self, root: Path, lock_path: Path, tool_lock: Path) -> str:
        build_nix = os.environ.get("POETRY_PURIFIER_BUILD")
        if build_nix is None:
            raise RuntimeError(
                "POETRY_PURIFIER_BUILD is not set. Run `poetry purifier publish`"
                " with the Poetry from the poetry-purifier flake."
            )
        self.line("Building the release with Nix")
        build = subprocess.run(
            [
                "nix-build",
                build_nix,
                "--arg",
                "src",
                str(root),
                "--arg",
                "lockFile",
                str(lock_path),
                *(
                    ["--arg", "poetryLock", str(tool_lock)]
                    if tool_lock != root / "poetry.lock"
                    else []
                ),
                "--attr",
                "release",
                "--no-out-link",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if build.returncode != 0:
            raise RuntimeError(f"Nix could not build {root}:\n{build.stderr}")
        return build.stdout.strip()
