#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# The contract's offline check, outside the Nix sandbox. In a copy of the
# fixture, with an empty Poetry cache, `poetry purifier prefetch` has Nix
# fetch the locked files and the interpreter. The script then cuts the
# network: every proxy variable names a closed port, and Nix has no
# substituters. The everyday loop then runs: `poetry install`, the
# project's script, the project's tests, an editor's language server on the
# environment, and `poetry build`. The plugin also fails any download
# Poetry would make, so the loop passing shows that every file came through
# the prefetch.
#
# Run it from the repository root: tests/conformance-offline.sh
set -euo pipefail

poetry="$(nix build --no-link --print-out-paths '.#poetry')/bin/poetry"
pyright="$(nix build --no-link --print-out-paths 'nixpkgs#pyright')/bin/pyright"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

cp -r tests/fixtures/app "$work/app"
cd "$work/app"
export POETRY_CACHE_DIR="$work/cache"
export POETRY_VIRTUALENVS_IN_PROJECT=true

"$poetry" purifier prefetch
test -L .poetry-purifier/prefetch

closed=http://127.0.0.1:9
export HTTP_PROXY="$closed" HTTPS_PROXY="$closed" http_proxy="$closed" https_proxy="$closed"
export NIX_CONFIG='substituters ='

"$poetry" install
"$poetry" run hello --name=offline | tee "$work/out"
grep -q 'hello, offline (requests 2\..*six 1\.17\.0, git-package-example.*attrs 25\.4\.0)' "$work/out"
"$poetry" run pytest

# The path dependency stays in develop mode outside Nix.
grep -rq "$work/app/libs/greeting" .venv --include='*.pth'

# An editor's language server resolves every import from the environment.
"$pyright" --pythonpath .venv/bin/python src tests

"$poetry" build
test -f dist/app-1.0.0-py3-none-any.whl

# Both lockfiles kept outside the project: with the two variables a dev
# shell sets, Poetry installs from the poetry.lock outside and the loop runs
# as before. Poetry resolves a path dependency relative to the poetry.lock it
# reads, so the directory of the outside copy links the project's libs.
mkdir "$work/kept"
ln -s "$PWD/libs" "$work/kept/libs"
mv poetry.lock poetry-purifier.lock.json "$work/kept/"
rm -rf .venv
export POETRY_PURIFIER_LOCKFILE="$work/kept/poetry-purifier.lock.json"
export POETRY_PURIFIER_TOOL_LOCKFILE="$work/kept/poetry.lock"
"$poetry" purifier prefetch
"$poetry" install
"$poetry" run hello --name=outside | tee "$work/out-outside"
grep -q 'hello, outside' "$work/out-outside"
test ! -e poetry.lock
echo "conformance-offline: ok"
