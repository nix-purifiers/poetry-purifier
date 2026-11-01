# SPDX-License-Identifier: MIT
import io
import tarfile
import zipfile

from pathlib import Path
from typing import Any

import pytest

from poetry_purifier import lock_command
from poetry_purifier.lock_command import PurifierLockCommand


PYPROJECT = b'[build-system]\nrequires = ["setuptools>=61", "wheel"]\n'


def _tar(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_read_pyproject_from_a_tarball() -> None:
    data = _tar({"demo-1.0/pyproject.toml": PYPROJECT, "demo-1.0/x/pyproject.toml": b""})
    pyproject = PurifierLockCommand._read_pyproject("demo-1.0.tar.gz", data)
    assert pyproject == {"build-system": {"requires": ["setuptools>=61", "wheel"]}}


def test_read_pyproject_from_a_zip() -> None:
    data = _zip({"demo-1.0/pyproject.toml": PYPROJECT})
    pyproject = PurifierLockCommand._read_pyproject("demo-1.0.zip", data)
    assert pyproject is not None
    assert pyproject["build-system"]["requires"] == ["setuptools>=61", "wheel"]


def test_read_pyproject_of_a_setup_py_sdist() -> None:
    data = _tar({"demo-1.0/setup.py": b"from setuptools import setup\n"})
    assert PurifierLockCommand._read_pyproject("demo-1.0.tar.gz", data) is None


def test_universal_wheels() -> None:
    assert lock_command._is_universal_wheel("demo-1.0-py3-none-any.whl")
    assert lock_command._is_universal_wheel("demo-1.0-py2.py3-none-any.whl")
    assert not lock_command._is_universal_wheel("demo-1.0-cp314-cp314-linux_x86_64.whl")


def _files(*names: str) -> list[str]:
    return [f"https://example.org/{name}" for name in names]


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        pass


@pytest.fixture
def sdist(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    served: dict[str, bytes] = {}
    monkeypatch.setattr(
        lock_command.urllib.request,
        "urlopen",
        lambda url: _Response(served[url.rsplit("/", 1)[-1]]),
    )
    return served


def test_no_build_requirements_when_a_wheel_installs_everywhere(
    sdist: dict[str, bytes],
) -> None:
    command = PurifierLockCommand()
    files = _files("demo-1.0-py3-none-any.whl", "demo-1.0.tar.gz")
    assert command._sdist_requirements(files) == ()


def test_build_requirements_of_an_sdist_some_platform_builds(
    sdist: dict[str, bytes],
) -> None:
    sdist["demo-1.0.tar.gz"] = _tar({"demo-1.0/pyproject.toml": PYPROJECT})
    command = PurifierLockCommand()
    files = _files("demo-1.0-cp314-cp314-manylinux_2_28_x86_64.whl", "demo-1.0.tar.gz")
    assert command._sdist_requirements(files) == ("setuptools>=61", "wheel")


def test_default_build_requirements_of_a_setup_py_sdist(
    sdist: dict[str, bytes],
) -> None:
    sdist["demo-1.0.tar.gz"] = _tar({"demo-1.0/setup.py": b""})
    command = PurifierLockCommand()
    assert command._sdist_requirements(_files("demo-1.0.tar.gz")) == tuple(
        lock_command.DEFAULT_BUILD_REQUIREMENTS
    )


def test_check_names_the_command_for_a_missing_lock(tmp_path: Path) -> None:
    command = PurifierLockCommand()
    lines: list[str] = []
    command.line_error = lines.append  # type: ignore[method-assign]
    lock_path = tmp_path / "poetry-purifier.lock.json"
    assert command._check(tmp_path, lock_path, tmp_path / "poetry.lock") == 1
    assert "poetry purifier lock" in lines[0]
