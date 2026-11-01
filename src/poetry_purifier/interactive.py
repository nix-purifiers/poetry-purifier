# SPDX-License-Identifier: MIT
#
# Interactive use: Nix fetches the files the lockfile names for this system,
# and the plugin links each one into Poetry's own artifact cache at the path
# Poetry looks for it. The Nix result is a garbage-collector root in the
# project's .poetry-purifier directory, which keeps the linked files alive.
from __future__ import annotations

import platform
import subprocess

from pathlib import Path
from typing import TYPE_CHECKING

from poetry.config.config import Config

from poetry_purifier import lockfile


if TYPE_CHECKING:
    from cleo.io.io import IO


def use_locked_cache(root: Path, lock_path: Path, realize: Path, io: IO) -> None:
    lock = lockfile.read(lock_path)
    gc_root = root / ".poetry-purifier" / "files"
    gc_root.parent.mkdir(exist_ok=True)
    (gc_root.parent / ".gitignore").write_text("*\n")
    build = subprocess.run(
        [
            "nix-build",
            str(realize),
            "--arg",
            "lockFile",
            str(lock_path),
            "--argstr",
            "pythonVersion",
            lock["python"],
            "--argstr",
            "arch",
            platform.machine(),
            "--argstr",
            "glibcVersion",
            platform.libc_ver()[1],
            "--out-link",
            str(gc_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise RuntimeError(
            f"Nix could not fetch the files of {lock_path}:\n{build.stderr}"
        )
    tree = Path(build.stdout.strip())

    cache_dir = Path(Config.create().get("cache-dir")).expanduser()
    linked = 0
    for file in tree.rglob("*"):
        if file.is_dir():
            continue
        target = cache_dir / file.relative_to(tree)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        target.symlink_to(file.resolve())
        linked += 1
    if linked:
        io.write_error_line(
            f"Linked {linked} locked files into Poetry's cache at {cache_dir}."
        )
