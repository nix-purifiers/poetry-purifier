# SPDX-License-Identifier: MIT
#
# `poetry purifier lock`: writes poetry-purifier.lock.json.
#
# The command copies the project to a temporary directory and runs Poetry
# there in record mode, with an empty cache and the network on: `poetry
# install` with every group and extra, then `poetry build`. That run shows
# which files Poetry downloads and which packages it installs to build
# sdists. The command then asks Poetry's repositories for the URL of every
# file that poetry.lock names, for every platform, so that Nix can fetch the
# files of any system from the one lock.
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path
from typing import Any

from cleo.helpers import option
from poetry.__version__ import __version__ as poetry_version
from poetry.console.commands.command import Command
from poetry.core.packages.package import Package
from poetry.core.utils.helpers import canonicalize_name

from poetry_purifier import lockfile


IGNORED = shutil.ignore_patterns(
    ".git", ".venv", "dist", "result", "__pycache__", ".poetry-purifier"
)


class PurifierLockCommand(Command):
    name = "purifier lock"
    description = (
        "Records the files Poetry downloads for this project in"
        f" {lockfile.FILE_NAME}."
    )
    options = [  # noqa: RUF012
        option(
            "python",
            None,
            "The Python interpreter of the lock run. Its major and minor version"
            " become the version the Nix build uses.",
            flag=False,
            default="python3",
        ),
    ]

    def handle(self) -> int:
        root = self.poetry.pyproject_path.parent
        locker = self.poetry.locker
        if not locker.is_locked() or not locker.is_fresh():
            self.line_error(
                "<error>poetry.lock is missing or out of date. Run `poetry lock`"
                " first.</error>"
            )
            return 1

        python = shutil.which(self.option("python"))
        if python is None:
            self.line_error(f"<error>{self.option('python')} is not on PATH.</error>")
            return 1

        with tempfile.TemporaryDirectory(prefix="poetry-purifier-") as tmp:
            records = self._run(root, Path(tmp), python)

        lock = self._compose(root, python, records)
        target = root / lockfile.FILE_NAME
        lockfile.write(target, lock)
        self.line(f"Wrote {target}")
        return 0

    def _run(self, root: Path, tmp: Path, python: str) -> list[dict[str, Any]]:
        project = tmp / "project"
        shutil.copytree(root, project, ignore=IGNORED)
        record_path = tmp / "record.jsonl"
        record_path.touch()
        env = {
            key: value
            for key, value in os.environ.items()
            if key != "VIRTUAL_ENV" and not key.startswith("POETRY_")
        }
        env |= {
            # The Poetry that runs this command, with this plugin. A Nix
            # wrapper puts Poetry's modules on the path at start-up, so the
            # interpreter alone does not find them.
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
            "POETRY_CACHE_DIR": str(tmp / "cache"),
            "POETRY_VIRTUALENVS_IN_PROJECT": "true",
            "POETRY_NO_INTERACTION": "1",
            "POETRY_PURIFIER_MODE": "record",
            "POETRY_PURIFIER_RECORD": str(record_path),
        }
        poetry = [sys.executable, "-m", "poetry"]
        for arguments in (
            ["env", "use", python],
            ["install", "--all-groups", "--all-extras"],
            ["build"],
        ):
            self.line(f"Running poetry {' '.join(arguments)}")
            subprocess.run([*poetry, *arguments], cwd=project, env=env, check=True)
        return [json.loads(line) for line in record_path.read_text().splitlines()]

    def _compose(
        self, root: Path, python: str, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        downloads = {r["url"]: r for r in records if r["kind"] == "download"}
        packages: dict[str, list[dict[str, str]]] = {}

        for package in self.poetry.locker.locked_repository().packages:
            key = lockfile.package_key(
                canonicalize_name(package.name), package.version.text
            )
            if package.source_type in ("directory", "file"):
                continue
            if package.source_type == "git":
                raise RuntimeError(
                    f"{package.name} comes from git, and the Poetry purifier does"
                    " not lock git dependencies yet."
                )
            if package.source_type == "url":
                download = downloads.get(package.source_url or "")
                if download is None:
                    raise RuntimeError(
                        f"The lock run did not download {package.source_url}."
                    )
                packages[key] = [
                    {k: download[k] for k in ("path", "url", "hash")}
                ]
                continue
            packages[key] = self._locked_files(package)

        for record in records:
            if record["kind"] != "links":
                continue
            key = lockfile.package_key(record["name"], record["version"])
            if key in packages:
                continue
            packages[key] = [
                {
                    "path": lockfile.cache_path(
                        lockfile.link_with_hash(link["url"], link["sha256"])
                    ),
                    "url": link["url"],
                    "hash": lockfile.sri_from_hex(link["sha256"]),
                }
                for link in record["links"]
                if link["sha256"] is not None
            ]

        builds = {
            "\n".join(r["requirements"]): r["packages"]
            for r in records
            if r["kind"] == "build"
        }

        return {
            "format": lockfile.FORMAT,
            "poetry": poetry_version,
            "python": subprocess.run(
                [python, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "inputsHash": lockfile.inputs_hash(root),
            "project": self._project(),
            "packages": packages,
            "builds": builds,
        }

    def _locked_files(self, package: Package) -> list[dict[str, str]]:
        pool = self.poetry.pool
        if package.source_type:
            repository = pool.repository(package.source_reference)
        elif pool.has_repository("pypi"):
            repository = pool.repository("pypi")
        else:
            repository = pool.repositories[0]

        hashes = {f["file"]: f["hash"] for f in package.files}
        files = []
        for link in repository.find_links_for_package(package):
            locked = hashes.get(link.filename)
            if locked is None:
                continue
            algorithm, _, digest = locked.partition(":")
            if algorithm != "sha256":
                raise RuntimeError(
                    f"poetry.lock records {link.filename} with a {algorithm} hash,"
                    " and the Poetry purifier needs sha256."
                )
            files.append(
                {
                    "path": lockfile.cache_path(
                        lockfile.link_with_hash(link.url_without_fragment, digest)
                    ),
                    "url": link.url_without_fragment,
                    "hash": lockfile.sri_from_hex(digest),
                }
            )
        missing = set(hashes) - {f["url"].rsplit("/", 1)[-1] for f in files}
        if missing:
            raise RuntimeError(
                f"The repository of {package.name} {package.version.text} has no"
                f" link for {sorted(missing)}."
            )
        return files

    def _project(self) -> dict[str, Any]:
        data = self.poetry.pyproject.data
        scripts = data.get("project", {}).get("scripts") or data.get(
            "tool", {}
        ).get("poetry", {}).get("scripts", {})
        return {"name": self.poetry.package.name, "scripts": sorted(scripts)}
