#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# The lock run, against the real package index and GitHub, in copies of the
# fixture:
#
# 1. `poetry purifier lock --force` relocks the committed lockfile and must
#    reproduce it byte for byte.
# 2. `poetry purifier lock` with no lockfile writes a new one, which must
#    hold one entry of each dependency kind. `poetry purifier publish
#    --dry-run` then builds the project with Nix from it, which exercises
#    the publish step's own Nix build.
#
# Run it from the repository root: tests/lock-run.sh
set -euo pipefail

poetry="$(nix build --no-link --print-out-paths '.#poetry')/bin/poetry"
jq="$(nix build --no-link --print-out-paths 'nixpkgs#jq.bin')/bin/jq"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export POETRY_CACHE_DIR="$work/cache"
lock=poetry-purifier.lock.json

cp -r tests/fixtures/app "$work/relock"
(
  cd "$work/relock"
  "$poetry" purifier lock --force
  cmp "$lock" "$OLDPWD/tests/fixtures/app/$lock"
)
echo "lock run: the relock is byte for byte the committed lockfile"

cp -r tests/fixtures/app "$work/app"
rm "$work/app/$lock"
cd "$work/app"
"$poetry" purifier lock
"$jq" -e '.packages["six==1.17.0"][0].url | endswith("six-1.17.0.tar.gz")' "$lock"
"$jq" -e '.packages["git-package-example==0.2.0a1"][0].git.submodules' "$lock"
"$jq" -e '.packages["charset-normalizer==3.5.2"] | map(select(.url | test("aarch64"))) | length > 0' "$lock"
"$jq" -e '.builds["setuptools >= 40.8.0"] | length > 0' "$lock"
"$poetry" purifier lock --check

# A dry run builds with Nix and uploads nothing.
POETRY_PYPI_TOKEN_PYPI=dry-run "$poetry" purifier publish --dry-run
echo "lock run: ok"
