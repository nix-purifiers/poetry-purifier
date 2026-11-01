# SPDX-License-Identifier: MIT
#
# The two ways the plugin changes Poetry.
#
# In record mode, which `poetry purifier lock` sets for the Poetry it runs,
# Poetry works as usual and the plugin appends what Poetry downloads and
# installs to a JSON Lines file.
#
# In offline mode, Poetry answers every question about files from the
# purifier lockfile and finds every file in its cache, which Nix filled
# beforehand. A download that Poetry would otherwise make is an error.
#
# Both modes patch Poetry's classes rather than one repository pool, because
# Poetry creates a separate pool for each isolated build.
from __future__ import annotations

import json

from collections.abc import Collection
from pathlib import Path
from typing import Any

from poetry.config.config import Config
from poetry.core.packages.package import Package
from poetry.core.packages.utils.link import Link
from poetry.core.utils.helpers import canonicalize_name
from poetry.installation.chooser import Chooser
from poetry.installation.executor import Executor
from poetry.installation.wheel_installer import WheelInstaller
from poetry.repositories.installed_repository import InstalledRepository
from poetry.repositories.legacy_repository import LegacyRepository
from poetry.repositories.pypi_repository import PyPiRepository
from poetry.utils import isolated_build

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

    for cls in REPOSITORY_CLASSES:
        original_find_links = cls.find_links_for_package

        def find_links(
            self: Any, package: Package, _original: Any = original_find_links
        ) -> list[Link]:
            links: list[Link] = _original(self, package)
            record(
                {
                    "kind": "links",
                    "name": canonicalize_name(package.name),
                    "version": package.version.text,
                    "links": [
                        {
                            "url": link.url_without_fragment,
                            "sha256": link.hashes.get("sha256"),
                        }
                        for link in links
                    ],
                }
            )
            return links

        cls.find_links_for_package = find_links  # type: ignore[method-assign]

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
        installed = InstalledRepository.load(self._env).packages
        record(
            {
                "kind": "build",
                "requirements": sorted(requirements),
                "packages": sorted(
                    lockfile.package_key(canonicalize_name(p.name), p.version.text)
                    for p in installed
                ),
            }
        )

    isolated_build.IsolatedEnv.install = install  # type: ignore[method-assign]


def enable_offline(lock_path: Path) -> None:
    lock = lockfile.read(lock_path)
    packages: dict[str, list[dict[str, str]]] = lock["packages"]
    builds: dict[str, list[str]] = lock["builds"]

    def locked_files(name: str, version: str) -> list[dict[str, str]]:
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
            if (cache_dir / f["path"]).exists()
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

    def install(
        self: isolated_build.IsolatedEnv,
        requirements: Collection[str],
        **kwargs: Any,
    ) -> None:
        key = _requirements_key(requirements)
        build_packages = builds.get(key)
        if build_packages is None:
            raise PurifierError(
                f"The build requirements {sorted(requirements)} are not in"
                f" {lock_path}. Run `poetry purifier lock` to lock them."
            )
        cache_dir = _cache_dir()
        chooser = Chooser(self._pool, self._env)
        installer = WheelInstaller(self._env)
        # A build asks for its requirements once per build hook, so the
        # environment can already hold them.
        installed = {
            lockfile.package_key(canonicalize_name(p.name), p.version.text)
            for p in InstalledRepository.load(self._env).packages
        }
        for entry in build_packages:
            if entry in installed:
                continue
            name, _, version = entry.partition("==")
            link = chooser.choose_for(Package(name, version))
            if not link.is_wheel:
                raise PurifierError(
                    f"The build requirement {entry} has no wheel for this system,"
                    " and building a build requirement from its sdist is not"
                    " supported."
                )
            path = next(
                f["path"]
                for f in locked_files(name, version)
                if f["url"] == link.url_without_fragment
            )
            installer.install(cache_dir / path)

    isolated_build.IsolatedEnv.install = install  # type: ignore[method-assign]
