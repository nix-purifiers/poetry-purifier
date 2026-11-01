# SPDX-License-Identifier: MIT
#
# The purifier lockfile, poetry-purifier.lock.json, next to pyproject.toml.
#
# `packages` maps "name==version" to the files of that release: each file is
# a download entry `{path, url, mirrors, hash}`, where `path` is the file's
# place in Poetry's cache directory, `mirrors` lists other URLs of the same
# file, and `hash` is an SRI hash. It holds every locked
# file of every package in poetry.lock, for every platform, and the same for
# the packages that Poetry installed to build sdists. `builds` maps each set
# of build requirements to the packages Poetry installed for it.
from __future__ import annotations

import base64
import hashlib
import json

from pathlib import Path
from typing import Any

from poetry.core.packages.utils.link import Link
from poetry.utils.cache import ArtifactCache


FILE_NAME = "poetry-purifier.lock.json"
FORMAT = 4


def sri_from_hex(hex_digest: str) -> str:
    return "sha256-" + base64.b64encode(bytes.fromhex(hex_digest)).decode("ascii")


def hex_from_sri(sri: str) -> str:
    algorithm, _, digest = sri.partition("-")
    if algorithm != "sha256":
        raise ValueError(f"unsupported hash {sri}")
    return base64.b64decode(digest).hex()


def file_sri(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256-" + base64.b64encode(digest.digest()).decode("ascii")


def link_with_hash(url: str, sha256_hex: str | None) -> Link:
    if sha256_hex is None:
        return Link(url)
    return Link(f"{url}#sha256={sha256_hex}")


def cache_path(link: Link) -> str:
    """Where Poetry's artifact cache keeps the file of a link, relative to
    Poetry's cache directory."""
    directory = ArtifactCache(cache_dir=Path("artifacts")).get_cache_directory_for_link(
        link
    )
    return str(directory / link.filename)


def inputs_hash(root: Path) -> str:
    """The hash of the files a lock is made from. A lock with another hash is
    out of date."""
    digest = hashlib.sha256()
    for name in ("pyproject.toml", "poetry.lock"):
        digest.update(name.encode("ascii") + b"\0")
        digest.update((root / name).read_bytes())
        digest.update(b"\0")
    return "sha256-" + base64.b64encode(digest.digest()).decode("ascii")


def package_key(name: str, version: str) -> str:
    return f"{name}=={version}"


def read(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    if data.get("format") != FORMAT:
        raise RuntimeError(
            f"{path} has format {data.get('format')}, and this plugin reads format"
            f" {FORMAT}. Run `poetry purifier lock` to write it again."
        )
    return data


def write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
