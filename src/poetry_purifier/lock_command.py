# SPDX-License-Identifier: MIT
#
# `poetry purifier lock`: writes poetry-purifier.lock.json.
#
# The command copies the project to a temporary directory and runs Poetry
# there in record mode, with an empty cache and the network on: `poetry
# install` with every group and extra, then `poetry build`. That run shows
# which files Poetry downloads and which build requirements Poetry installs
# when it builds a package from source.
#
# The command then locks every platform rather than the platform it runs
# on. It asks Poetry's repositories for the URL of every file that
# poetry.lock names. A registry package that some platform may build from
# its sdist has its build requirements read from the sdist. Every set of
# build requirements is resolved for all platforms with `poetry lock` in a
# throwaway project. A git dependency is hashed with nix-prefetch-git, so
# that Nix fetches the same checkout.
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile

from pathlib import Path
from typing import Any

from cleo.helpers import option
from poetry.__version__ import __version__ as poetry_version
from poetry.console.commands.command import Command
from poetry.core.packages.package import Package
from poetry.core.utils.helpers import canonicalize_name
from poetry.factory import Factory

from poetry_purifier import lockfile


IGNORED = shutil.ignore_patterns(
    ".git", ".venv", "dist", "result", "__pycache__", ".poetry-purifier"
)

# The requirements of a project whose pyproject.toml declares no build
# system, as the `build` package that Poetry uses spells them.
DEFAULT_BUILD_REQUIREMENTS = ["setuptools >= 40.8.0"]


def _is_universal_wheel(file_name: str) -> bool:
    return file_name.endswith(("-py3-none-any.whl", "-py2.py3-none-any.whl"))


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
        option(
            "check",
            None,
            f"Checks that {lockfile.FILE_NAME} is up to date with pyproject.toml"
            " and poetry.lock, without locking.",
        ),
    ]

    def handle(self) -> int:
        root = self.poetry.pyproject_path.parent
        if self.option("check"):
            return self._check(root)
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
            self._workdir = Path(tmp)
            self._purifier_env = self._subprocess_env()
            records = self._record(root, python)
            lock = self._compose(root, python, records)

        target = root / lockfile.FILE_NAME
        lockfile.write(target, lock)
        self.line(f"Wrote {target}")
        return 0

    def _check(self, root: Path) -> int:
        lock_path = root / lockfile.FILE_NAME
        if not lock_path.exists():
            self.line_error(f"<error>{lock_path} is missing.</error>")
            return 1
        if lockfile.read(lock_path)["inputsHash"] != lockfile.inputs_hash(root):
            self.line_error(
                f"<error>{lock_path} is out of date. Run `poetry purifier lock`.</error>"
            )
            return 1
        self.line(f"{lock_path} is up to date.")
        return 0

    def _subprocess_env(self) -> dict[str, str]:
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
            "POETRY_CACHE_DIR": str(self._workdir / "cache"),
            "POETRY_VIRTUALENVS_IN_PROJECT": "true",
            "POETRY_NO_INTERACTION": "1",
        }
        return env

    def _run_poetry(self, cwd: Path, *arguments: str, **env: str) -> None:
        self.line(f"Running poetry {' '.join(arguments)}")
        subprocess.run(
            [sys.executable, "-m", "poetry", *arguments],
            cwd=cwd,
            env=self._purifier_env | env,
            check=True,
        )

    def _record(self, root: Path, python: str) -> list[dict[str, Any]]:
        project = self._workdir / "project"
        shutil.copytree(root, project, ignore=IGNORED)
        record_path = self._workdir / "record.jsonl"
        record_path.touch()
        record = {
            "POETRY_PURIFIER_MODE": "record",
            "POETRY_PURIFIER_RECORD": str(record_path),
        }
        self._run_poetry(project, "env", "use", python, **record)
        self._run_poetry(project, "install", "--all-groups", "--all-extras", **record)
        self._run_poetry(project, "build", **record)
        built = sorted(path.name for path in (project / "dist").iterdir())
        wheels = [name for name in built if name.endswith(".whl")]
        sdists = [name for name in built if name.endswith(".tar.gz")]
        if len(wheels) != 1 or len(sdists) != 1:
            raise RuntimeError(
                f"`poetry build` made {built}, rather than one wheel and one sdist."
            )
        self._dist = {"wheel": wheels[0], "sdist": sdists[0]}
        return [json.loads(line) for line in record_path.read_text().splitlines()]

    def _compose(
        self, root: Path, python: str, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        downloads = {r["url"]: r for r in records if r["kind"] == "download"}
        packages: dict[str, list[dict[str, Any]]] = {}
        # Registry packages whose sdist some platform may build.
        buildable: list[list[dict[str, Any]]] = []

        for package in self.poetry.locker.locked_repository().packages:
            key = lockfile.package_key(
                canonicalize_name(package.name), package.version.text
            )
            if package.source_type in ("directory", "file"):
                continue
            if package.source_type == "git":
                packages[key] = [self._git_entry(package)]
            elif package.source_type == "url":
                download = downloads.get(package.source_url or "")
                if download is None:
                    raise RuntimeError(
                        f"The lock run did not download {package.source_url}."
                    )
                packages[key] = [
                    {k: download[k] for k in ("path", "url", "hash")} | {"mirrors": []}
                ]
            else:
                packages[key] = self._locked_files(package, self.poetry.pool)
                buildable.append(packages[key])

        requirement_sets = {
            tuple(sorted(r["requirements"])) for r in records if r["kind"] == "build"
        }
        requirement_sets |= {self._sdist_requirements(f) for f in buildable}
        builds = self._resolve_builds(requirement_sets, packages)

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
            # The file names of the project's wheel and sdist, as `poetry
            # build` made them on the system of the lock run.
            "dist": self._dist,
            "packages": packages,
            "builds": builds,
        }

    def _locked_files(self, package: Package, pool: Any) -> list[dict[str, Any]]:
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
                    # Package indexes serve each file at one URL.
                    "mirrors": [],
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

    def _git_entry(self, package: Package) -> dict[str, Any]:
        url = package.source_url
        rev = package.source_resolved_reference
        assert url is not None and rev is not None
        self.line(f"Hashing {url} at {rev}")
        # Poetry clones git dependencies with their submodules.
        prefetch = subprocess.run(
            [
                "nix-prefetch-git",
                "--quiet",
                "--url",
                url,
                "--rev",
                rev,
                "--fetch-submodules",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(prefetch.stdout)
        url_digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        return {
            "path": f"purifier-git/{url_digest}-{rev}",
            # Builds of the checkout get their version from the plugin, so
            # they need no .git directory.
            "git": {
                "url": url,
                "rev": rev,
                "submodules": True,
                "leaveDotGit": False,
                "commitObject": None,
            },
            "hash": result["hash"],
        }

    def _sdist_requirements(self, files: list[dict[str, Any]]) -> tuple[str, ...]:
        """The static build requirements of a package that some platform
        builds from its sdist, or no requirements when every platform
        installs a wheel."""
        names = [f["url"].rsplit("/", 1)[-1] for f in files]
        sdists = [f for f, n in zip(files, names, strict=True) if not n.endswith(".whl")]
        if not sdists or any(_is_universal_wheel(n) for n in names):
            return ()
        with urllib.request.urlopen(sdists[0]["url"]) as response:
            data = response.read()
        pyproject = self._read_pyproject(sdists[0]["url"], data)
        if pyproject is None or "build-system" not in pyproject:
            return tuple(DEFAULT_BUILD_REQUIREMENTS)
        return tuple(sorted(pyproject["build-system"].get("requires", [])))

    @staticmethod
    def _read_pyproject(url: str, data: bytes) -> dict[str, Any] | None:
        if url.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = [n for n in archive.namelist() if n.count("/") == 1]
                name = next((n for n in names if n.endswith("/pyproject.toml")), None)
                return None if name is None else tomllib.loads(archive.read(name).decode())
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for member in archive.getmembers():
                parts = member.name.split("/")
                if len(parts) == 2 and parts[1] == "pyproject.toml":
                    extracted = archive.extractfile(member)
                    assert extracted is not None
                    return tomllib.loads(extracted.read().decode())
        return None

    def _resolve_builds(
        self,
        requirement_sets: set[tuple[str, ...]],
        packages: dict[str, list[dict[str, Any]]],
    ) -> dict[str, list[dict[str, str]]]:
        """Resolves every set of build requirements for all platforms. A
        build requirement that some platform builds from its sdist adds its
        own build requirements, until no set is new."""
        builds: dict[str, list[dict[str, str]]] = {}
        pending = {s for s in requirement_sets if s}
        while pending:
            requirements = pending.pop()
            key = "\n".join(requirements)
            if key in builds:
                continue
            builds[key] = []
            for package, marker in self._lock_requirements(requirements):
                package_key = lockfile.package_key(
                    canonicalize_name(package.name), package.version.text
                )
                builds[key].append(
                    {
                        "name": canonicalize_name(package.name),
                        "version": package.version.text,
                        "markers": marker,
                    }
                )
                if package_key not in packages:
                    packages[package_key] = self._locked_files(package, self.poetry.pool)
                    nested = self._sdist_requirements(packages[package_key])
                    if nested and "\n".join(nested) not in builds:
                        pending.add(nested)
        return builds

    def _lock_requirements(
        self, requirements: tuple[str, ...]
    ) -> list[tuple[Package, str]]:
        project = Path(tempfile.mkdtemp(dir=self._workdir, prefix="build-"))
        python = self.poetry.package.python_versions
        (project / "pyproject.toml").write_text(
            "[tool.poetry]\n"
            "package-mode = false\n"
            "[tool.poetry.dependencies]\n"
            f"python = {json.dumps(python)}\n"
            "[project]\n"
            'name = "purifier-build-requirements"\n'
            'version = "0"\n'
            f"dependencies = {json.dumps(list(requirements))}\n"
        )
        self._run_poetry(project, "lock")
        locked = tomllib.loads((project / "poetry.lock").read_text())
        markers: dict[str, str] = {}
        for info in locked["package"]:
            marker = info.get("markers", "*")
            if isinstance(marker, dict):
                marker = marker.get("main", "*")
            markers[canonicalize_name(info["name"])] = "" if marker == "*" else marker
        poetry = Factory().create_poetry(project)
        return [
            (package, markers[canonicalize_name(package.name)])
            for package in poetry.locker.locked_repository().packages
        ]
