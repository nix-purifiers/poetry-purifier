# SPDX-License-Identifier: MIT
#
# Fetching the locked files outside the Nix sandbox, for `poetry purifier
# prefetch` and for interactive use. Nix fetches the files the lockfile
# names for this system, and the interpreter the lock was made with, and the
# plugin links each file into Poetry's own artifact cache at the path Poetry
# looks for it. The Nix result is a garbage-collector root in the project's
# .poetry-purifier directory, which keeps the files and the interpreter
# alive.
from __future__ import annotations

import platform
import subprocess

from pathlib import Path
from typing import TYPE_CHECKING

from poetry.config.config import Config

from poetry_purifier import lockfile


if TYPE_CHECKING:
    from cleo.io.io import IO


def gc_root(root: Path) -> Path:
    return root / ".poetry-purifier" / "prefetch"


def use_locked_cache(
    root: Path, lock_path: Path, tool_lock: Path, realize: Path, io: IO
) -> Path:
    """Fetches the locked files and the interpreter, links the files into
    Poetry's cache, and returns the directory with `files` and `python`.
    `tool_lock` is the poetry.lock of the project."""
    lock = lockfile.read(lock_path)
    out_link = gc_root(root)
    out_link.parent.mkdir(exist_ok=True)
    (out_link.parent / ".gitignore").write_text("*\n")
    build = subprocess.run(
        [
            "nix-build",
            str(realize),
            "--arg",
            "lockFile",
            str(lock_path),
            "--arg",
            "poetryLock",
            str(tool_lock),
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
            str(out_link),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise RuntimeError(
            f"Nix could not fetch the files of {lock_path}:\n{build.stderr}"
        )
    realized = Path(build.stdout.strip())
    tree = realized / "files"
    configuration = realized / 'python-inputs.json'
    if configuration.exists():
        import os
        from poetry_purifier import inputs
        os.environ['POETRY_PURIFIER_PROJECT_INPUTS'] = str(configuration)
        inputs.enable()

    cache_dir = Path(Config.create().get("cache-dir")).expanduser()
    linked = 0
    # Each entry of the tree is a link to a fetched file or git checkout.
    for entry in tree.rglob("*"):
        if not entry.is_symlink():
            continue
        target = cache_dir / entry.relative_to(tree)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        target.symlink_to(entry.resolve())
        linked += 1
    if linked:
        io.write_error_line(
            f"Linked {linked} locked files into Poetry's cache at {cache_dir}."
        )
    return realized
