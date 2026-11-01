# SPDX-License-Identifier: MIT
#
# The Poetry application plugin. It adds `poetry purifier lock`, `update`,
# `prefetch` and `publish`, and it puts Poetry in the mode that POETRY_PURIFIER_MODE
# names before a command runs:
#
# - `record`: the Poetry that `poetry purifier lock` runs.
# - `offline`: a Nix build. POETRY_PURIFIER_LOCK names the lockfile, and
#   POETRY_CACHE_DIR is a cache Nix filled from it.
# - unset: interactive use. The wrapper from this flake sets
#   POETRY_PURIFIER_REALIZE; when a project has an up-to-date lockfile, the
#   plugin has Nix fetch the locked files into Poetry's cache, and `poetry
#   install`, `sync` and `build` run offline from it.
from __future__ import annotations

import os

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from cleo.events.console_events import COMMAND
from poetry.plugins.application_plugin import ApplicationPlugin

from poetry_purifier import interactive
from poetry_purifier import lockfile
from poetry_purifier import modes
from poetry_purifier.lock_command import PurifierLockCommand
from poetry_purifier.lock_command import PurifierUpdateCommand
from poetry_purifier.prefetch_command import PurifierPrefetchCommand
from poetry_purifier.publish_command import PurifierPublishCommand


if TYPE_CHECKING:
    from cleo.events.console_command_event import ConsoleCommandEvent
    from poetry.console.application import Application


OFFLINE_COMMANDS = {"install", "sync", "build"}


class PurifierPlugin(ApplicationPlugin):
    @property
    def commands(self) -> list[Any]:
        return [
            PurifierLockCommand,
            PurifierUpdateCommand,
            PurifierPrefetchCommand,
            PurifierPublishCommand,
        ]

    def activate(self, application: Application) -> None:
        super().activate(application)
        mode = os.environ.get("POETRY_PURIFIER_MODE")
        if mode == "record":
            modes.enable_record(Path(os.environ["POETRY_PURIFIER_RECORD"]))
        elif mode == "offline":
            # The project is the working directory of the Nix build.
            modes.enable_offline(
                Path(os.environ["POETRY_PURIFIER_LOCK"]), Path.cwd(), sandbox=True
            )
        elif mode is None and os.environ.get("POETRY_PURIFIER") != "0":
            dispatcher = application.event_dispatcher
            assert dispatcher is not None
            dispatcher.add_listener(COMMAND, self._interactive)

    def _interactive(
        self, event: ConsoleCommandEvent, event_name: str, dispatcher: Any
    ) -> None:
        realize = os.environ.get("POETRY_PURIFIER_REALIZE")
        command = event.command
        if realize is None or command.name not in OFFLINE_COMMANDS:
            return
        root = command.poetry.pyproject_path.parent  # type: ignore[attr-defined]
        # POETRY_PURIFIER_LOCKFILE names a lockfile kept outside the project,
        # like the --lockfile option of the purifier commands.
        lock_path = Path(
            os.environ.get("POETRY_PURIFIER_LOCKFILE") or root / lockfile.FILE_NAME
        )
        if not lock_path.exists():
            return
        if lockfile.read(lock_path)["inputsHash"] != lockfile.inputs_hash(root):
            event.io.write_error_line(
                f"<warning>{lockfile.FILE_NAME} is out of date, so Poetry downloads"
                " as usual. Run `poetry purifier lock` to renew it.</warning>"
            )
            return
        interactive.use_locked_cache(root, lock_path, Path(realize), event.io)
        modes.enable_offline(lock_path, root)
