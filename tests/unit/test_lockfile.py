# SPDX-License-Identifier: MIT
import hashlib
import json

from pathlib import Path

import pytest

from poetry.core.packages.utils.link import Link
from poetry.utils.cache import ArtifactCache

from poetry_purifier import lockfile


DIGEST = hashlib.sha256(b"wheel").hexdigest()
URL = "https://files.pythonhosted.org/packages/ab/cd/demo-1.0-py3-none-any.whl"
GIT_URL = "https://example.org/repo.git"
REV = "0" * 40


def test_sri_and_hex_round_trip() -> None:
    sri = lockfile.sri_from_hex(DIGEST)
    assert sri.startswith("sha256-")
    assert lockfile.hex_from_sri(sri) == DIGEST


def test_hex_from_sri_refuses_other_algorithms() -> None:
    with pytest.raises(ValueError, match="unsupported hash"):
        lockfile.hex_from_sri("sha512-AAAA")


def test_file_sri(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"wheel")
    assert lockfile.file_sri(path) == lockfile.sri_from_hex(DIGEST)


def test_cache_path_is_where_poetry_looks(tmp_path: Path) -> None:
    link = lockfile.link_with_hash(URL, DIGEST)
    cache = ArtifactCache(cache_dir=tmp_path / "artifacts")
    expected = cache.get_cache_directory_for_link(link) / link.filename
    assert tmp_path / lockfile.cache_path(link) == expected


def test_cache_path_depends_on_url_and_hash() -> None:
    with_hash = lockfile.cache_path(lockfile.link_with_hash(URL, DIGEST))
    without_hash = lockfile.cache_path(Link(URL))
    assert with_hash != without_hash
    assert with_hash.startswith("artifacts/")
    assert with_hash.endswith("/demo-1.0-py3-none-any.whl")


def _project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    (tmp_path / "poetry.lock").write_text(
        "[[package]]\n"
        'name = "demo"\n'
        'version = "1.0"\n'
        f'files = [{{file = "demo-1.0-py3-none-any.whl", hash = "sha256:{DIGEST}"}}]\n'
        "\n"
        "[[package]]\n"
        'name = "six"\n'
        'version = "1.17.0"\n'
        f'files = [{{file = "six-1.17.0.tar.gz", hash = "sha256:{DIGEST}"}}]\n'
        "[package.source]\n"
        'type = "url"\n'
        'url = "https://example.org/six-1.17.0.tar.gz"\n'
        "\n"
        "[[package]]\n"
        'name = "repo"\n'
        'version = "0.1"\n'
        "files = []\n"
        "[package.source]\n"
        'type = "git"\n'
        f'url = "{GIT_URL}"\n'
        f'resolved_reference = "{REV}"\n'
    )
    return tmp_path


def _lock(root: Path) -> dict:
    return {
        "version": lockfile.VERSION,
        "inputsHash": lockfile.inputs_hash(root),
        "python": "3.14",
        "dist": {"wheel": "app-1.0-py3-none-any.whl", "sdist": "app-1.0.tar.gz"},
        "urls": {"demo-1.0-py3-none-any.whl": URL},
        "git": {lockfile.git_key(GIT_URL, REV): lockfile.sri_from_hex(DIGEST)},
        "builds": {"setuptools": [{"name": "setuptools", "version": "80.0", "markers": ""}]},
        "buildPackages": {
            "setuptools==80.0": [
                {
                    "url": "https://example.org/setuptools-80.0-py3-none-any.whl",
                    "hash": lockfile.sri_from_hex(DIGEST),
                }
            ]
        },
    }


def test_inputs_hash_is_the_documented_formula(tmp_path: Path) -> None:
    root = _project(tmp_path)
    lines = "".join(
        f"{name} {hashlib.sha256((root / name).read_bytes()).hexdigest()}\n"
        for name in ("pyproject.toml", "poetry.lock")
    )
    assert lockfile.inputs_hash(root) == hashlib.sha256(lines.encode()).hexdigest()


def test_inputs_hash_reads_a_poetry_lock_kept_elsewhere(tmp_path: Path) -> None:
    root = _project(tmp_path)
    elsewhere = tmp_path / "elsewhere.lock"
    elsewhere.write_text("# another lock\n")
    assert lockfile.inputs_hash(root, elsewhere) != lockfile.inputs_hash(root)


def test_written_lock_reads_back_and_is_fresh(tmp_path: Path) -> None:
    root = _project(tmp_path)
    path = root / lockfile.FILE_NAME
    lockfile.write(path, _lock(root))
    assert path.read_text().startswith(lockfile.PREFIX)
    assert lockfile.read(path)["urls"] == {"demo-1.0-py3-none-any.whl": URL}
    assert lockfile.is_fresh(path, root)


def test_content_hash_covers_the_text_with_the_hash_zeroed(tmp_path: Path) -> None:
    text = lockfile.render(_lock(_project(tmp_path)))
    digest = text[len(lockfile.PREFIX) : len(lockfile.PREFIX) + 64]
    zeroed = text.replace(digest, lockfile.ZERO_HASH, 1)
    assert hashlib.sha256(zeroed.encode()).hexdigest() == digest
    assert json.loads(text)["contentHash"] == digest


def test_expand_gives_entries_for_every_package(tmp_path: Path) -> None:
    root = _project(tmp_path)
    packages = lockfile.expand(_lock(root), root / "poetry.lock")
    (demo,) = packages["demo==1.0"]
    assert demo["url"] == URL
    assert demo["path"] == lockfile.cache_path(lockfile.link_with_hash(URL, DIGEST))
    (six,) = packages["six==1.17.0"]
    assert six["path"] == lockfile.cache_path(Link(six["url"]))
    (repo,) = packages["repo==0.1"]
    assert repo["git"]["rev"] == REV
    assert repo["path"] == lockfile.git_path(GIT_URL, REV)
    (setuptools,) = packages["setuptools==80.0"]
    assert setuptools["path"].endswith("/setuptools-80.0-py3-none-any.whl")


def test_stale_lock_is_not_fresh(tmp_path: Path) -> None:
    root = _project(tmp_path)
    path = root / lockfile.FILE_NAME
    lockfile.write(path, _lock(root))
    (root / "pyproject.toml").write_text('[project]\nname = "demo2"\n')
    assert not lockfile.is_fresh(path, root)


def test_tampered_hash_is_an_error(tmp_path: Path) -> None:
    root = _project(tmp_path)
    path = root / lockfile.FILE_NAME
    lockfile.write(path, _lock(root))
    git_hash = lockfile.sri_from_hex(DIGEST)
    path.write_text(path.read_text().replace(git_hash, lockfile.sri_from_hex("00" * 32)))
    with pytest.raises(lockfile.LockfileError, match="changed after .*poetry purifier lock"):
        lockfile.read(path)


def test_missing_entry_is_an_error(tmp_path: Path) -> None:
    root = _project(tmp_path)
    path = root / lockfile.FILE_NAME
    lockfile.write(path, _lock(root))
    path.write_text(path.read_text().replace("demo-1.0-py3", "demo-1.1-py3"))
    with pytest.raises(lockfile.LockfileError, match="Run `poetry purifier lock`"):
        lockfile.read(path)


def test_malformed_lock_is_an_error(tmp_path: Path) -> None:
    root = _project(tmp_path)
    path = root / lockfile.FILE_NAME
    lockfile.write(path, _lock(root))
    path.write_text(path.read_text()[:200])
    with pytest.raises(lockfile.LockfileError, match="Run `poetry purifier lock`"):
        lockfile.read(path)


def test_another_file_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / lockfile.FILE_NAME
    path.write_text('{"version": ')
    with pytest.raises(lockfile.LockfileError, match="not a lockfile.*poetry purifier lock"):
        lockfile.read(path)


def test_other_version_is_an_error(tmp_path: Path) -> None:
    root = _project(tmp_path)
    path = root / lockfile.FILE_NAME
    lockfile.write(path, _lock(root) | {"version": lockfile.VERSION - 1})
    with pytest.raises(lockfile.LockfileError, match="has version"):
        lockfile.read(path)
