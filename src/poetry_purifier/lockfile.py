# SPDX-License-Identifier: MIT
#
# The purifier lockfile, poetry-purifier.lock.json, next to pyproject.toml.
#
# `packages` maps "name==version" to the files of that release: each file is
# a download entry `{path, url, mirrors, hash}`, where `path` is the file's
# place in Poetry's cache directory, `mirrors` lists other URLs of the same
# file, and `hash` is an SRI hash. It holds every locked file of every
# package in poetry.lock, for every platform, and the same for the packages
# that Poetry installs to build sdists. `builds` maps each set of build
# requirements to the packages it resolves to.
#
# Two hashes guard the lockfile, and Nix checks both when it evaluates a
# build. `inputsHash` covers pyproject.toml and poetry.lock, so a lock made
# from other inputs is out of date. `contentHash` covers the lockfile
# itself, so an edit by hand is an error rather than a build from changed
# entries. Both are computed the same way in nix/lib/check-lock.nix.
from __future__ import annotations

import base64
import hashlib
import json

from pathlib import Path
from typing import Any

from poetry.core.packages.utils.link import Link
from poetry.utils.cache import ArtifactCache


FILE_NAME = "poetry-purifier.lock.json"
FORMAT = 6

RELOCK = "Run `poetry purifier lock` to write it again."


class LockfileError(RuntimeError):
    pass


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
    """The hash of the files a lock is made from: the SHA-256 of a line per
    file with the file's own SHA-256. A lock with another hash is out of
    date."""
    lines = "".join(
        f"{name} {hashlib.sha256((root / name).read_bytes()).hexdigest()}\n"
        for name in ("pyproject.toml", "poetry.lock")
    )
    return hashlib.sha256(lines.encode("ascii")).hexdigest()


def content_hash(data: dict[str, Any]) -> str:
    """The SHA-256 of the lockfile without its `contentHash`, as compact JSON
    with sorted keys, which is what Nix's builtins.toJSON writes."""
    body = {key: value for key, value in data.items() if key != "contentHash"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def package_key(name: str, version: str) -> str:
    return f"{name}=={version}"


def read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise LockfileError(f"{path} is not valid JSON ({e}). {RELOCK}") from e
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        found = data.get("format") if isinstance(data, dict) else None
        raise LockfileError(
            f"{path} has format {found}, and this plugin reads format {FORMAT}."
            f" {RELOCK}"
        )
    if data.get("contentHash") != content_hash(data):
        raise LockfileError(
            f"{path} was changed after `poetry purifier lock` wrote it. {RELOCK}"
        )
    return data


def is_fresh(path: Path, root: Path) -> bool:
    """Whether a valid lockfile exists and matches pyproject.toml and
    poetry.lock."""
    try:
        return read(path)["inputsHash"] == inputs_hash(root)
    except (OSError, LockfileError):
        return False


def write(path: Path, data: dict[str, Any]) -> None:
    data = data | {"contentHash": content_hash(data)}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
