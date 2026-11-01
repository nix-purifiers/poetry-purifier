# SPDX-License-Identifier: MIT
from pathlib import Path

from poetry_purifier import modes


def test_sdist_version() -> None:
    assert modes._sdist_version("app-1.0.0.tar.gz") == "1.0.0"
    assert modes._sdist_version("my_app-2.1.0rc1.tar.gz") == "2.1.0rc1"


def test_source_versions_take_the_deepest_tree(tmp_path: Path) -> None:
    versions = modes._SourceVersions()
    project = tmp_path / "project"
    checkout = project / ".venv" / "src" / "attrs"
    versions.add(project, "1.0.0")
    versions.add(checkout, "25.4.0")
    assert versions.lookup(project) == "1.0.0"
    assert versions.lookup(project / "libs" / "greeting") == "1.0.0"
    assert versions.lookup(checkout) == "25.4.0"
    assert versions.lookup(checkout / "subdirectory") == "25.4.0"
    assert versions.lookup(tmp_path / "elsewhere") is None
