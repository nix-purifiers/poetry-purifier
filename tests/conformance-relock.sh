#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# The contract's relock check, against the real package index and GitHub,
# in copies of the fixture:
#
# 1. `poetry purifier lock --force` relocks the committed lockfile and must
#    reproduce it byte for byte.
# 2. `poetry purifier lock` with no lockfile writes a new one. It must hold
#    the git checkout's hash, the URLs of both CPUs' wheels, and the build
#    requirements, and it must leave out the URL dependency, whose URL and
#    hash poetry.lock has. `poetry purifier publish --dry-run` then builds
#    the project with Nix from it, which exercises the publish step's own
#    Nix build.
#
# Run it from the repository root: tests/conformance-relock.sh
set -euo pipefail

poetry="$(nix build --no-link --print-out-paths '.#poetry')/bin/poetry"
jq="$(nix build --no-link --print-out-paths 'nixpkgs#jq.bin')/bin/jq"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export POETRY_CACHE_DIR="$work/cache"
lock="poetry-purifier.lock.json"

# -R copies a symbolic link in the fixture as a link; macOS's cp -r
# follows it.
cp -R tests/fixtures/app "$work/relock"
(
  cd "$work/relock"
  "$poetry" purifier lock --force
  cmp "$lock" "$OLDPWD/tests/fixtures/app/$lock"
)
echo "conformance-relock: the relock is byte for byte the committed lockfile"

# Both lockfiles kept outside the project: `lock` starts from the poetry.lock
# in the project, writes both files outside, and leaves the copy in the
# project as it is. The outside files are the committed ones, byte for byte.
repo="$PWD"
cp -R tests/fixtures/app "$work/outside"
mkdir "$work/kept"
(
  cd "$work/outside"
  cp poetry.lock "$work/tree-poetry.lock"
  rm "$lock"
  "$poetry" purifier lock --lockfile "$work/kept/$lock" --tool-lockfile "$work/kept/poetry.lock"
  cmp poetry.lock "$work/tree-poetry.lock"
  cmp "$work/kept/poetry.lock" poetry.lock
  cmp "$work/kept/$lock" "$repo/tests/fixtures/app/$lock"
  test ! -e "$lock"
  "$poetry" purifier lock --check --lockfile "$work/kept/$lock" --tool-lockfile "$work/kept/poetry.lock"
)
echo "conformance-relock: the lockfiles kept outside the project are the committed ones"

cp -R tests/fixtures/app "$work/app"
rm "$work/app/$lock"
cd "$work/app"
"$poetry" purifier lock
"$jq" -e '.git | keys | any(test("test-fixture-vcs-repository"))' "$lock"
"$jq" -e '.urls | keys | any(test("charset_normalizer-.*aarch64"))' "$lock"
"$jq" -e '.urls | keys | any(test("charset_normalizer-.*x86_64"))' "$lock"
"$jq" -e '.urls | has("six-1.17.0.tar.gz") | not' "$lock"
"$jq" -e '.builds["setuptools >= 40.8.0"] | length > 0' "$lock"
"$poetry" purifier lock --check

# A dry run builds with Nix and uploads nothing.
POETRY_PYPI_TOKEN_PYPI=dry-run "$poetry" purifier publish --dry-run
echo "conformance-relock: ok"
