# SPDX-License-Identifier: MIT
#
# `poetry purifier lock`: writes poetry-purifier.lock.json, with what
# poetry.lock cannot record.
#
# The command copies the project to a temporary directory and runs Poetry
# there in record mode, with an empty cache and the network on: `poetry
# install` with every group and extra, then `poetry build`. That run shows
# which build requirements Poetry installs when it builds a package from
# source, and the file names of the project's wheel and sdist.
#
# The command then locks every platform rather than the platform it runs
# on. It asks Poetry's repositories for the URL of every file that
# poetry.lock names, which poetry.lock does not record. A registry package
# that some platform may build from its sdist has its build requirements
# read from the sdist. Every set of build requirements is resolved for all
# platforms with `poetry lock` in a throwaway project, with each package
# that poetry.lock also locks held at the version poetry.lock has. A git
# dependency is hashed with nix-prefetch-git, so that Nix fetches the same
# checkout.
from __future__ import annotations

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

from cleo.helpers import argument
from cleo.helpers import option
from poetry.console.commands.command import Command
from poetry.core.packages.package import Package
from poetry.core.utils.helpers import canonicalize_name
from poetry.factory import Factory

from poetry_purifier import lockfile
from poetry_purifier import lockfile_option


IGNORED = shutil.ignore_patterns(
    ".git", ".venv", "dist", "result", "__pycache__", ".poetry-purifier"
)

# The requirements of a project whose pyproject.toml declares no build
# system, as the `build` package that Poetry uses spells them.
DEFAULT_BUILD_REQUIREMENTS = ["setuptools >= 40.8.0"]


def _is_universal_wheel(file_name: str) -> bool:
    return file_name.endswith(("-py3-none-any.whl", "-py2.py3-none-any.whl"))


def _sdk_pythonpath() -> str:
    # Discovery subprocesses may run a different Python; expose SDK packages
    # without replacing that interpreter's standard library.
    return os.pathsep.join(path for path in sys.path if path and (
        any(part in ('site-packages','dist-packages') for part in Path(path).parts)
        or (Path(path) / 'poetry').is_dir()
        or (Path(path) / 'poetry_purifier').is_dir()))


class PurifierLockCommand(Command):
    name = "purifier lock"
    description = (
        "Records what Poetry downloads for this project, beyond poetry.lock, in"
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
            "Checks that the lockfile is up to date with pyproject.toml and"
            " poetry.lock, without locking.",
        ),
        option(
            "force",
            None,
            "Locks even when the lockfile is up to date.",
        ),
        option(
            "update-build-requirements",
            None,
            "Resolves the build requirements again, rather than keeping the"
            " versions the current lockfile has for them.",
        ),
        option("input", None, "A Nix producer exposing projections.python.", flag=False, multiple=True),
        *lockfile_option.OPTIONS,
    ]

    def handle(self) -> int:
        from poetry_purifier import inputs
        with inputs.locking(self):
            return self._handle()

    def _handle(self) -> int:
        root = self.poetry.pyproject_path.parent
        target = lockfile_option.path(self)
        self._tool_lock = lockfile_option.tool_path(self, root)
        if self.option("check"):
            return self._check(root, target, self._tool_lock)
        if self._resolve(root, self._tool_lock):
            if self._tool_lock == root / "poetry.lock":
                # Poetry reads the new poetry.lock.
                self.get_application().reset_poetry()
        elif not self.option("force") and lockfile.is_fresh(
            target, root, self._tool_lock
        ):
            self.line(f"{target} is up to date.")
            return 0
        return self._lock(root, target)

    def _resolve(self, root: Path, tool_lock: Path) -> bool:
        """Writes poetry.lock where it is missing or out of date with
        pyproject.toml, keeping the versions it has, and returns whether it
        ran. `poetry lock` keeps the versions of an existing poetry.lock."""
        if tool_lock != root / "poetry.lock":
            return self._resolve_outside(root, tool_lock)
        locker = self.poetry.locker
        if not getattr(self, "_project_inputs", []) and locker.is_locked() and locker.is_fresh():
            return False
        self._run_project_poetry(root, "lock")
        return True

    def _stage(self, root: Path, tool_lock: Path) -> Path:
        """A copy of the project whose poetry.lock is the one at `tool_lock`,
        or the project's own when `tool_lock` does not exist yet. Poetry
        reads and writes poetry.lock next to pyproject.toml, so a poetry.lock
        kept elsewhere is locked in a copy of the project."""
        self._stage_directory = tempfile.TemporaryDirectory(
            prefix="poetry-purifier-stage-"
        )
        stage = Path(self._stage_directory.name) / "project"
        shutil.copytree(root, stage, ignore=IGNORED)
        source = tool_lock if tool_lock.exists() else root / "poetry.lock"
        if source.exists():
            shutil.copyfile(source, stage / "poetry.lock")
        return stage

    def _resolve_outside(self, root: Path, tool_lock: Path) -> bool:
        """Like `_resolve`, for a poetry.lock outside the project. The copy in
        the project stays as it is."""
        stage = self._stage(root, tool_lock)
        poetry = Factory().create_poetry(stage)
        ran = not (poetry.locker.is_locked() and poetry.locker.is_fresh())
        if ran:
            self._run_project_poetry(stage, "lock")
        self._publish_stage(stage, tool_lock, force=ran)
        return ran

    def _publish_stage(self, stage: Path, tool_lock: Path, *, force: bool) -> None:
        """Writes the poetry.lock of the staged project to `tool_lock`, and
        has the rest of the command read the staged project."""
        if force or not tool_lock.exists():
            tool_lock.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stage / "poetry.lock", tool_lock)
        self.set_poetry(Factory().create_poetry(stage))

    def _run_project_poetry(self, root: Path, *arguments: str) -> None:
        """Runs Poetry on the project itself, with the user's configuration."""
        self.line(f"Running poetry {' '.join(arguments)}")
        subprocess.run(
            [sys.executable, "-m", "poetry", *arguments],
            cwd=root,
            env=os.environ | {"PYTHONPATH": _sdk_pythonpath()},
            check=True,
        )

    def _lock(self, root: Path, target: Path) -> int:
        # A relock keeps the resolutions of build requirements that the
        # current lockfile has, so that it changes only what its inputs
        # changed.
        self._previous: dict[str, Any] = {"builds": {}, "buildPackages": {}}
        if not self.option("update-build-requirements"):
            try:
                self._previous = lockfile.read(target)
            except (OSError, lockfile.LockfileError):
                pass

        python = shutil.which(self.option("python"))
        if python is None:
            self.line_error(f"<error>{self.option('python')} is not on PATH.</error>")
            return 1

        with tempfile.TemporaryDirectory(prefix="poetry-purifier-") as tmp:
            self._workdir = Path(tmp)
            self._purifier_env = self._subprocess_env()
            records = self._record(root, python)
            lock = self._compose(root, python, records)

        if self._project_inputs:
            lock["projectInputs"] = self._project_inputs
        lockfile.write(target, lock)
        self.line(f"Wrote {target}")
        return 0

    def _check(self, root: Path, lock_path: Path, tool_lock: Path) -> int:
        if not lock_path.exists():
            self.line_error(
                f"<error>{lock_path} is missing. Run `poetry purifier lock`.</error>"
            )
            return 1
        try:
            lock = lockfile.read(lock_path)
            lockfile.expand(lock, tool_lock)
        except (lockfile.LockfileError, KeyError) as e:
            message = (
                str(e)
                if isinstance(e, lockfile.LockfileError)
                else f"{lock_path} has no entry for {e}. Run `poetry purifier lock`."
            )
            self.line_error(f"<error>{message}</error>")
            return 1
        if lock["inputsHash"] != lockfile.inputs_hash(root, tool_lock):
            self.line_error(
                f"<error>{lock_path} is out of date with pyproject.toml or"
                " poetry.lock. Run `poetry purifier lock`.</error>"
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
            "PYTHONPATH": _sdk_pythonpath(),
            "POETRY_CACHE_DIR": str(self._workdir / "cache"),
            "POETRY_VIRTUALENVS_IN_PROJECT": "true",
            "POETRY_NO_INTERACTION": "1",
        }
        for key in ("POETRY_PURIFIER_PROJECT_INPUTS", "POETRY_PURIFIER_PROJECT_INPUTS_TOOLS", "POETRY_PURIFIER_PYTHON_BINDINGS"):
            if key in os.environ:
                env[key] = os.environ[key]
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
        if self._tool_lock != root / "poetry.lock":
            shutil.copyfile(self._tool_lock, project / "poetry.lock")
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
        urls: dict[str, str] = {}
        git: dict[str, str] = {}
        # The versions poetry.lock has, which build requirements keep.
        locked_versions: dict[str, str] = {}
        # The URLs of the files of registry packages that some platform may
        # build from their sdist.
        buildable: list[list[str]] = []

        for package in self.poetry.locker.locked_repository().packages:
            name = canonicalize_name(package.name)
            if package.source_type in ("directory", "file"):
                continue
            locked_versions[name] = package.version.text
            if package.source_type == "git":
                url = package.source_url
                rev = package.source_resolved_reference
                assert url is not None and rev is not None
                git[lockfile.git_key(url, rev)] = self._git_hash(url, rev)
            elif package.source_type != "url":
                files = self._file_urls(package)
                urls |= files
                buildable.append(list(files.values()))

        requirement_sets = {
            tuple(sorted(r["requirements"])) for r in records if r["kind"] == "build"
        }
        requirement_sets |= {self._sdist_requirements(f) for f in buildable}
        builds, build_packages = self._resolve_builds(requirement_sets, locked_versions)

        return {
            "version": lockfile.VERSION,
            "python": subprocess.run(
                [python, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "inputsHash": lockfile.inputs_hash(root, self._tool_lock),
            # The file names of the project's wheel and sdist, as `poetry
            # build` made them on the system of the lock run.
            "dist": self._dist,
            "urls": urls,
            "git": git,
            "builds": builds,
            "buildPackages": build_packages,
        }

    def _file_urls(self, package: Package) -> dict[str, str]:
        """The URL of each file that poetry.lock names for a package from a
        package index, by file name."""
        pool = self.poetry.pool
        if package.source_type:
            repository = pool.repository(package.source_reference)
        elif pool.has_repository("pypi"):
            repository = pool.repository("pypi")
        else:
            repository = pool.repositories[0]
        names = {f["file"] for f in package.files}
        urls = {
            link.filename: link.url_without_fragment
            for link in repository.find_links_for_package(package)
            if link.filename in names
        }
        missing = names - set(urls)
        if missing:
            raise RuntimeError(
                f"The repository of {package.name} {package.version.text} has no"
                f" link for {sorted(missing)}."
            )
        return urls

    def _build_files(self, package: Package) -> list[dict[str, str]]:
        """The URL and hash of each file of a build package that poetry.lock
        does not lock."""
        urls = self._file_urls(package)
        return [
            {"url": urls[f["file"]], "hash": lockfile.sri_from_hex(f["hash"].split(":")[1])}
            for f in sorted(package.files, key=lambda f: f["file"])
        ]

    def _git_hash(self, url: str, rev: str) -> str:
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
        return str(json.loads(prefetch.stdout)["hash"])

    def _sdist_requirements(self, urls: list[str]) -> tuple[str, ...]:
        """The static build requirements of a package that some platform
        builds from its sdist, or no requirements when every platform
        installs a wheel."""
        names = [url.rsplit("/", 1)[-1] for url in urls]
        sdists = [u for u, n in zip(urls, names, strict=True) if not n.endswith(".whl")]
        if not sdists or any(_is_universal_wheel(n) for n in names):
            return ()
        with urllib.request.urlopen(sdists[0]) as response:
            data = response.read()
        pyproject = self._read_pyproject(sdists[0], data)
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
        locked_versions: dict[str, str],
    ) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]]]:
        """Resolves every set of build requirements for all platforms. A
        build requirement that some platform builds from its sdist adds its
        own build requirements, until no set is new. Returns the sets with
        their packages, and the files of the packages poetry.lock does not
        lock."""
        builds: dict[str, list[dict[str, str]]] = {}
        build_packages: dict[str, list[dict[str, str]]] = {}
        pending = {s for s in requirement_sets if s}
        while pending:
            requirements = pending.pop()
            key = "\n".join(requirements)
            if key in builds:
                continue
            reused = self._previous["builds"].get(key)
            # A resolution that disagrees with poetry.lock, after poetry.lock
            # moved a package, is resolved again.
            if reused is not None and any(
                locked_versions.get(entry["name"], entry["version"]) != entry["version"]
                for entry in reused
            ):
                reused = None
            if reused is not None:
                builds[key] = reused
                resolved = [
                    (Package(entry["name"], entry["version"]), entry["markers"])
                    for entry in reused
                ]
            else:
                resolved = self._lock_requirements(requirements, locked_versions)
                builds[key] = [
                    {
                        "name": canonicalize_name(package.name),
                        "version": package.version.text,
                        "markers": marker,
                    }
                    for package, marker in resolved
                ]
            for package, _ in resolved:
                name = canonicalize_name(package.name)
                if locked_versions.get(name) == package.version.text:
                    continue
                package_key = lockfile.package_key(name, package.version.text)
                if package_key in build_packages:
                    continue
                build_packages[package_key] = self._previous["buildPackages"].get(
                    package_key
                ) or self._build_files(self._locked_package(package))
                nested = self._sdist_requirements(
                    [f["url"] for f in build_packages[package_key]]
                )
                if nested and "\n".join(nested) not in builds:
                    pending.add(nested)
        return builds, build_packages

    def _locked_package(self, package: Package) -> Package:
        """The package with the files its throwaway lock recorded."""
        if package.files:
            return package
        return self._build_requirement_packages[
            lockfile.package_key(canonicalize_name(package.name), package.version.text)
        ]

    def _lock_requirements(
        self, requirements: tuple[str, ...], locked_versions: dict[str, str]
    ) -> list[tuple[Package, str]]:
        """Resolves build requirements in a throwaway project. A package that
        poetry.lock also locks is held at its version there."""
        pins: dict[str, str] = {}
        while True:
            resolved = self._lock_once(requirements, pins)
            conflicts = {
                canonicalize_name(p.name): locked_versions[canonicalize_name(p.name)]
                for p, _ in resolved
                if canonicalize_name(p.name) in locked_versions
                and p.version.text != locked_versions[canonicalize_name(p.name)]
                and canonicalize_name(p.name) not in pins
            }
            if not conflicts:
                return resolved
            pins |= conflicts

    def _lock_once(
        self, requirements: tuple[str, ...], pins: dict[str, str]
    ) -> list[tuple[Package, str]]:
        project = Path(tempfile.mkdtemp(dir=self._workdir, prefix="build-"))
        python = self.poetry.package.python_versions
        dependencies = [*requirements, *(f"{n}=={v}" for n, v in sorted(pins.items()))]
        (project / "pyproject.toml").write_text(
            "[tool.poetry]\n"
            "package-mode = false\n"
            "[tool.poetry.dependencies]\n"
            f"python = {json.dumps(python)}\n"
            "[project]\n"
            'name = "purifier-build-requirements"\n'
            'version = "0"\n'
            f"dependencies = {json.dumps(dependencies)}\n"
        )
        self._run_poetry(project, "lock", POETRY_PURIFIER_PROJECT_INPUTS="")
        locked = tomllib.loads((project / "poetry.lock").read_text())
        markers: dict[str, str] = {}
        for info in locked["package"]:
            marker = info.get("markers", "*")
            if isinstance(marker, dict):
                marker = marker.get("main", "*")
            markers[canonicalize_name(info["name"])] = "" if marker == "*" else marker
        packages = Factory().create_poetry(project).locker.locked_repository().packages
        if not hasattr(self, "_build_requirement_packages"):
            self._build_requirement_packages: dict[str, Package] = {}
        for package in packages:
            self._build_requirement_packages[
                lockfile.package_key(
                    canonicalize_name(package.name), package.version.text
                )
            ] = package
        return [(p, markers[canonicalize_name(p.name)]) for p in packages]


class PurifierUpdateCommand(PurifierLockCommand):
    name = "purifier update"
    description = (
        "Updates poetry.lock with `poetry update --lock`, for every package or"
        f" the named ones, and then relocks {lockfile.FILE_NAME}."
    )
    arguments = [  # noqa: RUF012
        argument("packages", "The packages to update.", optional=True, multiple=True)
    ]
    options = [  # noqa: RUF012
        o
        for o in PurifierLockCommand.options
        if o.name not in ("check", "force")
    ]

    def handle(self) -> int:
        from poetry_purifier import inputs
        with inputs.locking(self, refresh=True):
            return self._handle()

    def _handle(self) -> int:
        root = self.poetry.pyproject_path.parent
        self._tool_lock = lockfile_option.tool_path(self, root)
        packages = self.argument("packages")
        if self._tool_lock == root / "poetry.lock":
            self._run_project_poetry(root, "update", "--lock", *packages)
            self.get_application().reset_poetry()
        else:
            # The copy in the project stays as it is, and the update runs on
            # a copy of the project that holds the poetry.lock to update.
            stage = self._stage(root, self._tool_lock)
            self._run_project_poetry(stage, "update", "--lock", *packages)
            self._publish_stage(stage, self._tool_lock, force=True)
        return self._lock(root, lockfile_option.path(self))
