# SPDX-License-Identifier: MIT
#
# The two ways the plugin changes Poetry.
#
# In record mode, which `poetry purifier lock` sets for the Poetry it runs,
# Poetry works as usual and the plugin appends what Poetry downloads and
# which build requirements it installs to a JSON Lines file.
#
# In offline mode, Poetry answers every question about files from the
# purifier lockfile and finds every file in its cache, which Nix filled
# beforehand. A git dependency comes from a checkout Nix fetched. A download
# that Poetry would otherwise make is an error.
#
# Both modes patch Poetry's classes rather than one repository pool, because
# Poetry creates a separate pool for each isolated build.
from __future__ import annotations

import json
import os
import shutil
import threading
import tomllib

from collections.abc import Collection
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dulwich.repo import Repo
from poetry.config.config import Config
from poetry.console.commands import build as build_command
from poetry.inspection import info as inspection_info
from poetry.installation import chef as chef_module
from poetry.core.packages.package import Package
from poetry.core.packages.utils.link import Link
from poetry.core.utils.helpers import canonicalize_name
from poetry.core.version.markers import parse_marker
from poetry.installation.chef import Chef
from poetry.installation.chooser import Chooser
from poetry.installation.executor import Executor
from poetry.installation.wheel_installer import WheelInstaller
from poetry.repositories.installed_repository import InstalledRepository
from poetry.repositories.legacy_repository import LegacyRepository
from poetry.repositories.pypi_repository import PyPiRepository
from poetry.utils import isolated_build
from poetry.utils.cache import ArtifactCache
from poetry.vcs.git import Git

from poetry_purifier import lockfile


REPOSITORY_CLASSES = (PyPiRepository, LegacyRepository)


class PurifierError(RuntimeError):
    pass


def _requirements_key(requirements: Collection[str]) -> str:
    return "\n".join(sorted(requirements))


def _cache_dir() -> Path:
    return Path(Config.create().get("cache-dir")).expanduser()


def enable_record(record_path: Path) -> None:
    def record(entry: dict[str, Any]) -> None:
        with record_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    original_download = Executor._download_archive

    def download_archive(self: Executor, operation: Any, url: str, dest: Path) -> None:
        original_download(self, operation, url, dest)
        record(
            {
                "kind": "download",
                "url": url,
                "path": str(dest.relative_to(_cache_dir())),
                "hash": lockfile.file_sri(dest),
            }
        )

    Executor._download_archive = download_archive  # type: ignore[method-assign]

    original_install = isolated_build.IsolatedEnv.install

    def install(
        self: isolated_build.IsolatedEnv,
        requirements: Collection[str],
        **kwargs: Any,
    ) -> None:
        original_install(self, requirements, **kwargs)
        record({"kind": "build", "requirements": sorted(requirements)})

    isolated_build.IsolatedEnv.install = install  # type: ignore[method-assign]


class _SourceVersions:
    """The versions of the source trees Poetry builds offline.

    A git checkout from Nix and a project copied into the Nix sandbox have no
    .git directory, so a build backend that reads its version from git
    (setuptools-scm, hatch-vcs) cannot find one. Each isolated build of such
    a tree runs with SETUPTOOLS_SCM_PRETEND_VERSION set to the version the
    lock records. The variable has no distribution name in it, because
    hatch-vcs does not tell setuptools-scm the name, so it is set for one
    build at a time: Poetry builds packages on parallel threads, and each
    thread holds the version of the build it runs.
    """

    def __init__(self) -> None:
        self._by_path: dict[Path, str] = {}
        self._current = threading.local()

    def add(self, path: Path, version: str) -> None:
        self._by_path[path.resolve()] = version

    def lookup(self, source: Path) -> str | None:
        """The version of the deepest tree that holds `source`. A git
        dependency can be a subdirectory of its checkout, and the project
        can hold a checkout, in its virtual environment."""
        source = source.resolve()
        holders = [
            path
            for path in self._by_path
            if source == path or path in source.parents
        ]
        if not holders:
            return None
        return self._by_path[max(holders, key=lambda path: len(path.parts))]

    def install(self) -> None:
        original_builder = isolated_build.isolated_builder

        @contextmanager
        def builder(source: Path, *args: Any, **kwargs: Any) -> Generator[Any]:
            previous = getattr(self._current, "version", None)
            self._current.version = self.lookup(Path(source))
            try:
                with original_builder(source, *args, **kwargs) as result:
                    yield result
            finally:
                self._current.version = previous

        for module in (isolated_build, chef_module, build_command, inspection_info):
            module.isolated_builder = builder  # type: ignore[attr-defined]

        original_environ = isolated_build.IsolatedEnv.make_extra_environ
        current = self._current

        def make_extra_environ(env: isolated_build.IsolatedEnv) -> dict[str, str]:
            environ = original_environ(env)
            version = getattr(current, "version", None)
            if version is not None:
                environ["SETUPTOOLS_SCM_PRETEND_VERSION"] = version
            return environ

        isolated_build.IsolatedEnv.make_extra_environ = make_extra_environ  # type: ignore[method-assign]


def _sdist_version(sdist: str) -> str:
    """The version in an sdist file name, `<name>-<version>.tar.gz`, whose
    name part has no hyphen."""
    return sdist.removesuffix(".tar.gz").rsplit("-", 1)[1]


def enable_offline(
    lock_path: Path,
    root: Path,
    poetry_lock: Path | None = None,
    *,
    sandbox: bool = False,
) -> None:
    """Puts Poetry offline for the project at `root`, from the lockfile at
    `lock_path`. `poetry_lock` is the project's poetry.lock when it is not
    the one next to pyproject.toml."""
    # Poetry installs in parallel threads, and two threads that build wheels
    # from sources in the same cache can raise its _DeadlockError. Offline
    # installs build from sources, so they run one at a time.
    os.environ.setdefault("POETRY_INSTALLER_PARALLEL", "false")
    lock = lockfile.read(lock_path)
    if poetry_lock is None:
        poetry_lock = root / "poetry.lock"
    packages = lockfile.expand(lock, poetry_lock)
    builds: dict[str, list[dict[str, str]]] = lock["builds"]
    checkouts = {
        (entry["git"]["url"], entry["git"]["rev"]): entry["path"]
        for files in packages.values()
        for entry in files
        if "git" in entry
    }

    versions = _SourceVersions()
    versions.add(root, _sdist_version(lock["dist"]["sdist"]))
    git_versions: dict[tuple[str, str], str] = {}
    for info in tomllib.loads(poetry_lock.read_text()).get("package", []):
        source = info.get("source", {})
        if source.get("type") == "directory":
            versions.add(root / source["url"], info["version"])
        elif source.get("type") == "git":
            git_versions[(source["url"], source["resolved_reference"])] = info[
                "version"
            ]
    versions.install()

    def locked_files(name: str, version: str) -> list[dict[str, Any]]:
        key = lockfile.package_key(canonicalize_name(name), version)
        files = packages.get(key)
        if files is None:
            raise PurifierError(
                f"{key} is not in {lock_path}. Run `poetry purifier lock` to lock"
                " it."
            )
        return files

    def find_links(self: Any, package: Package) -> list[Link]:
        cache_dir = _cache_dir()
        links = [
            lockfile.link_with_hash(f["url"], lockfile.hex_from_sri(f["hash"]))
            for f in locked_files(package.name, package.version.text)
            if "url" in f and (cache_dir / f["path"]).exists()
        ]
        if not links:
            raise PurifierError(
                f"No locked file of {package.name} {package.version.text} is in the"
                f" cache at {cache_dir}. The files Nix fetched for this system do"
                " not include one Poetry can install."
            )
        return links

    for cls in REPOSITORY_CLASSES:
        cls.find_links_for_package = find_links  # type: ignore[method-assign]

    def download_archive(self: Executor, operation: Any, url: str, dest: Path) -> None:
        raise PurifierError(
            f"Poetry tried to download {url}, which is not in {lock_path}. Run"
            " `poetry purifier lock` to lock it."
        )

    Executor._download_archive = download_archive  # type: ignore[method-assign]

    def clone(
        cls: type[Git],
        url: str,
        name: str | None = None,
        branch: str | None = None,
        tag: str | None = None,
        revision: str | None = None,
        source_root: Path | None = None,
        clean: bool = False,
    ) -> Repo:
        path = checkouts.get((url, revision or ""))
        if path is None:
            raise PurifierError(
                f"Poetry tried to clone {url} at {revision}, which is not in"
                f" {lock_path}. Run `poetry purifier lock` to lock it."
            )
        source_root = source_root or cls.get_default_source_root()
        target = source_root / (name or cls.get_name_from_source_url(url=url))
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(_cache_dir() / path, target, symlinks=False)
        for directory, _, _ in os.walk(target):
            os.chmod(directory, 0o755)
        version = git_versions.get((url, revision or ""))
        if version is not None:
            versions.add(target, version)
        return Repo.init(str(target))

    Git.clone = classmethod(clone)  # type: ignore[method-assign,assignment]

    if sandbox:
        # An editable install would point into the build directory, which
        # Nix deletes after the build.
        original_install_operation = Executor._install

        def install_operation(self: Executor, operation: Any) -> int:
            operation.package.develop = False
            return original_install_operation(self, operation)

        Executor._install = install_operation  # type: ignore[method-assign]

    def install(
        self: isolated_build.IsolatedEnv,
        requirements: Collection[str],
        **kwargs: Any,
    ) -> None:
        if not requirements:
            return
        key = _requirements_key(requirements)
        build_packages = builds.get(key)
        if build_packages is None:
            raise PurifierError(
                f"The build requirements {sorted(requirements)} are not in"
                f" {lock_path}. Run `poetry purifier lock` to lock them."
            )
        cache_dir = _cache_dir()
        marker_env = self._env.get_marker_env()
        chooser = Chooser(self._pool, self._env)
        installer = WheelInstaller(self._env)
        # A build asks for its requirements once per build hook, so the
        # environment can already hold them.
        installed = {
            lockfile.package_key(canonicalize_name(p.name), p.version.text)
            for p in InstalledRepository.load(self._env).packages
        }
        for entry in build_packages:
            key = lockfile.package_key(entry["name"], entry["version"])
            if key in installed or not parse_marker(entry["markers"]).validate(
                marker_env
            ):
                continue
            link = chooser.choose_for(Package(entry["name"], entry["version"]))
            path = next(
                f["path"]
                for f in locked_files(entry["name"], entry["version"])
                if f.get("url") == link.url_without_fragment
            )
            archive = cache_dir / path
            if not link.is_wheel:
                chef = Chef(
                    ArtifactCache(cache_dir=cache_dir / "artifacts"),
                    self._env,
                    self._pool,
                )
                archive = chef.prepare(archive, output_dir=archive.parent)
            installer.install(archive)

    isolated_build.IsolatedEnv.install = install  # type: ignore[method-assign]
