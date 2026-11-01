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
    (tmp_path / "poetry.lock").write_text("# lock\n")
    return tmp_path


def test_inputs_hash_is_the_documented_formula(tmp_path: Path) -> None:
    root = _project(tmp_path)
    lines = "".join(
        f"{name} {hashlib.sha256((root / name).read_bytes()).hexdigest()}\n"
        for name in ("pyproject.toml", "poetry.lock")
    )
    assert lockfile.inputs_hash(root) == hashlib.sha256(lines.encode()).hexdigest()


def test_inputs_hash_changes_with_poetry_lock(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = lockfile.inputs_hash(root)
    (root / "poetry.lock").write_text("# lock, relocked\n")
    assert lockfile.inputs_hash(root) != before


def _lock(root: Path) -> dict:
    return {
        "format": lockfile.FORMAT,
        "inputsHash": lockfile.inputs_hash(root),
        "packages": {
            "demo==1.0": [
                {
                    "path": "artifacts/aa/bb/cc/rest/demo-1.0.tar.gz",
                    "url": "https://example.org/demo-1.0.tar.gz",
                    "mirrors": [],
                    "hash": lockfile.sri_from_hex(DIGEST),
                }
            ]
        },
        "builds": {},
    }


def test_written_lock_reads_back_and_is_fresh(tmp_path: Path) -> None:
    root = _project(tmp_path)
    path = root / lockfile.FILE_NAME
    lockfile.write(path, _lock(root))
    assert lockfile.read(path)["packages"]["demo==1.0"][0]["mirrors"] == []
    assert lockfile.is_fresh(path, root)


def test_content_hash_is_compact_sorted_json(tmp_path: Path) -> None:
    data = _lock(_project(tmp_path))
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    assert lockfile.content_hash(data) == hashlib.sha256(text.encode()).hexdigest()
    assert lockfile.content_hash(data | {"contentHash": "x"}) == lockfile.content_hash(data)


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
    data = json.loads(path.read_text())
    data["packages"]["demo==1.0"][0]["hash"] = lockfile.sri_from_hex("00" * 32)
    path.write_text(json.dumps(data))
    with pytest.raises(lockfile.LockfileError, match="changed after .*poetry purifier lock"):
        lockfile.read(path)


def test_missing_entry_is_an_error(tmp_path: Path) -> None:
    root = _project(tmp_path)
    path = root / lockfile.FILE_NAME
    lockfile.write(path, _lock(root))
    data = json.loads(path.read_text())
    del data["packages"]["demo==1.0"]
    path.write_text(json.dumps(data))
    with pytest.raises(lockfile.LockfileError, match="Run `poetry purifier lock`"):
        lockfile.read(path)


def test_malformed_lock_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / lockfile.FILE_NAME
    path.write_text('{"format": ')
    with pytest.raises(lockfile.LockfileError, match="not valid JSON.*poetry purifier lock"):
        lockfile.read(path)


def test_other_format_is_an_error(tmp_path: Path) -> None:
    root = _project(tmp_path)
    path = root / lockfile.FILE_NAME
    lockfile.write(path, _lock(root) | {"format": lockfile.FORMAT - 1})
    with pytest.raises(lockfile.LockfileError, match="has format"):
        lockfile.read(path)
