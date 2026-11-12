# SPDX-License-Identifier: MIT
#
# The purifier lockfile, poetry-purifier.lock.json, next to pyproject.toml
# unless a command's --lockfile names another path.
#
# The lockfile holds only what poetry.lock cannot:
#
# - `urls`: the download URL of each file that poetry.lock names for a
#   package from a package index, by file name. poetry.lock has each file's
#   name and hash, and no URL.
# - `git`: the hash of each git checkout, by "<url>@<commit>", as
#   nix-prefetch-git reports it.
# - `builds`: each set of build requirements that Poetry installs to build a
#   package from source, with the packages it resolves to. A package that
#   poetry.lock also locks has its version there.
# - `buildPackages`: the files of the packages in `builds` that poetry.lock
#   does not lock, by "name==version", each with its URL and SRI hash.
# - `dist`: the file names of the wheel and sdist of `poetry build`.
# - `python`: the Python version of the lock run.
#
# `expand` combines the lockfile with poetry.lock into download entries
# `{path, url, mirrors, hash}`, by "name==version", where `path` is the
# file's place in Poetry's cache directory. lib-overlays/default/entries.nix computes the
# same entries.
#
# Two hashes guard the lockfile, and Nix checks both when it evaluates a
# build, in lib-overlays/default/lockfile.nix. `inputsHash` covers pyproject.toml and
# poetry.lock, so a lock made from other inputs is out of date.
# `contentHash` covers the text of the file itself, so an edit by hand, a
# truncated file and a file that is not JSON all fail before anything parses
# the file. The writer puts `contentHash` on the first line, and the hash is
# the SHA-256 of the file's text with the hash itself replaced by zeros.
from __future__ import annotations

import base64
import hashlib
import json
import tomllib

from pathlib import Path
from typing import Any

from poetry.core.packages.utils.link import Link
from poetry.core.utils.helpers import canonicalize_name
from poetry.utils.cache import ArtifactCache


FILE_NAME = "poetry-purifier.lock.json"
VERSION = 8

# The start of every lockfile, up to the content hash.
PREFIX = '{\n  "contentHash": "'
ZERO_HASH = "0" * 64

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


def inputs_hash(root: Path, poetry_lock: Path | None = None) -> str:
    """The hash of the files a lock is made from: the SHA-256 of a line per
    file with the file's own SHA-256. A lock with another hash is out of
    date."""
    files = {
        "pyproject.toml": root / "pyproject.toml",
        "poetry.lock": poetry_lock or root / "poetry.lock",
    }
    lines = "".join(
        f"{name} {hashlib.sha256(path.read_bytes()).hexdigest()}\n"
        for name, path in files.items()
    )
    return hashlib.sha256(lines.encode("ascii")).hexdigest()


def package_key(name: str, version: str) -> str:
    return f"{name}=={version}"


def git_key(url: str, rev: str) -> str:
    return f"{url}@{rev}"


def git_path(url: str, rev: str) -> str:
    """Where the checkout of a git dependency lies in the cache tree."""
    return f"purifier-git/{hashlib.sha256(url.encode()).hexdigest()[:16]}-{rev}"


def file_entry(url: str, sri: str, *, with_hash: bool = True) -> dict[str, Any]:
    """A download entry of a file. Poetry keys its cache by the URL and, for
    a file from a package index, the file's SHA-256."""
    link = link_with_hash(url, hex_from_sri(sri) if with_hash else None)
    return {"path": cache_path(link), "url": url, "mirrors": [], "hash": sri}


def git_entry(url: str, rev: str, sri: str) -> dict[str, Any]:
    # Builds of the checkout get their version from the plugin, so they need
    # no .git directory.
    return {
        "path": git_path(url, rev),
        "git": {
            "url": url,
            "rev": rev,
            "submodules": True,
            "leaveDotGit": False,
            "commitObject": None,
        },
        "hash": sri,
    }


def expand(lock: dict[str, Any], poetry_lock: Path) -> dict[str, list[dict[str, Any]]]:
    """The download entries of every package, by "name==version": the
    packages of poetry.lock and the build packages of the lockfile."""
    packages: dict[str, list[dict[str, Any]]] = {}
    for info in tomllib.loads(poetry_lock.read_text()).get("package", []):
        key = package_key(canonicalize_name(info["name"]), info["version"])
        source = info.get("source", {})
        kind = source.get("type")
        files = info.get("files", [])
        if kind in ("directory", "file"):
            continue
        if kind == "git":
            url, rev = source["url"], source["resolved_reference"]
            packages[key] = [git_entry(url, rev, lock["git"][git_key(url, rev)])]
        elif kind == "url":
            packages[key] = [
                file_entry(source["url"], _sri(f["hash"]), with_hash=False)
                for f in files
            ]
        else:
            packages[key] = [
                file_entry(lock["urls"][f["file"]], _sri(f["hash"])) for f in files
            ]
    for key, files in lock["buildPackages"].items():
        packages[key] = [file_entry(f["url"], f["hash"]) for f in files]
    return packages


def _sri(poetry_hash: str) -> str:
    algorithm, _, digest = poetry_hash.partition(":")
    if algorithm != "sha256":
        raise LockfileError(
            f"poetry.lock records a {algorithm} hash, and the Poetry purifier"
            " needs sha256."
        )
    return sri_from_hex(digest)


def render(data: dict[str, Any]) -> str:
    """The text of a lockfile: indented JSON with sorted keys, after a first
    line with the content hash."""
    body = json.dumps(data, indent=2, sort_keys=True) + "\n"
    assert body.startswith("{\n")
    unhashed = f'{PREFIX}{ZERO_HASH}",\n' + body[2:]
    digest = hashlib.sha256(unhashed.encode("utf-8")).hexdigest()
    return unhashed.replace(ZERO_HASH, digest, 1)


def read(path: Path) -> dict[str, Any]:
    text = path.read_text()
    digest = text[len(PREFIX) : len(PREFIX) + 64]
    if not text.startswith(PREFIX):
        raise LockfileError(
            f"{path} is not a lockfile that `poetry purifier lock` wrote. {RELOCK}"
        )
    unhashed = text.replace(digest, ZERO_HASH, 1)
    if hashlib.sha256(unhashed.encode("utf-8")).hexdigest() != digest:
        raise LockfileError(
            f"{path} was changed after `poetry purifier lock` wrote it. {RELOCK}"
        )
    data: dict[str, Any] = json.loads(text)
    if data.get("version") != VERSION:
        raise LockfileError(
            f"{path} has version {data.get('version')}, and this plugin reads"
            f" version {VERSION}. {RELOCK}"
        )
    return data


def is_fresh(path: Path, root: Path, poetry_lock: Path | None = None) -> bool:
    """Whether a valid lockfile exists and matches pyproject.toml and
    poetry.lock."""
    try:
        return read(path)["inputsHash"] == inputs_hash(root, poetry_lock)
    except (OSError, LockfileError):
        return False


def write(path: Path, data: dict[str, Any]) -> None:
    data = {key: value for key, value in data.items() if key != "contentHash"}
    path.write_text(render(data))
