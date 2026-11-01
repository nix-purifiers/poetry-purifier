# SPDX-License-Identifier: MIT
#
# `poetry purifier publish`: publishes the wheel and sdist that Nix built.
#
# The command has Nix build the project's `dist` output from its
# poetry-purifier.lock.json, offline, as `buildPoetryProject` does, and then
# runs Poetry's own publish on that directory. Repositories, credentials and
# the other options of `poetry publish` work as they do there, and nothing
# is built outside the Nix sandbox. The command sets `--dist-dir` itself
# and turns `--build` off.
#
# POETRY_PURIFIER_DIST names a directory to publish instead of building
# one, for a build that already has the `dist` output.
from __future__ import annotations

import os
import subprocess

from pathlib import Path

from poetry.console.commands.publish import PublishCommand

from poetry_purifier import lockfile


class PurifierPublishCommand(PublishCommand):
    name = "purifier publish"
    description = (
        "Publishes the wheel and sdist that Nix builds from"
        f" {lockfile.FILE_NAME}."
    )
    def handle(self) -> int:
        root = self.poetry.pyproject_path.parent
        lock_path = root / lockfile.FILE_NAME
        if not lock_path.exists():
            self.line_error(
                f"<error>{lockfile.FILE_NAME} is missing. Run `poetry purifier"
                " lock` first.</error>"
            )
            return 1
        if lockfile.read(lock_path)["inputsHash"] != lockfile.inputs_hash(root):
            self.line_error(
                f"<error>{lockfile.FILE_NAME} is out of date. Run `poetry purifier"
                " lock` to renew it.</error>"
            )
            return 1

        dist = os.environ.get("POETRY_PURIFIER_DIST") or self._build(root)
        self.line(f"Publishing the files Nix built in {dist}")
        self.io.input.set_option("dist-dir", dist)
        self.io.input.set_option("build", False)
        return super().handle()

    def _build(self, root: Path) -> str:
        build_nix = os.environ.get("POETRY_PURIFIER_BUILD")
        if build_nix is None:
            raise RuntimeError(
                "POETRY_PURIFIER_BUILD is not set. Run `poetry purifier publish`"
                " with the Poetry from the poetry-purifier flake."
            )
        self.line("Building the project with Nix")
        build = subprocess.run(
            [
                "nix-build",
                build_nix,
                "--arg",
                "src",
                str(root),
                "--attr",
                "dist",
                "--no-out-link",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if build.returncode != 0:
            raise RuntimeError(f"Nix could not build {root}:\n{build.stderr}")
        return build.stdout.strip()
