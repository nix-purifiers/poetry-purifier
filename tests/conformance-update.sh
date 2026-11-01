#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# The contract's update check, against the real package index, in a copy
# of the update fixture. Its poetry.lock holds idna 3.7, an old version that
# its range allows. `poetry purifier update idna` must move idna to a newer
# version, and then both lockfiles must pass the stale check: `poetry check
# --lock` for poetry.lock and `poetry purifier lock --check` for the
# purifier lockfile.
#
# Run it from the repository root: tests/conformance-update.sh
set -euo pipefail

poetry="$(nix build --no-link --print-out-paths '.#poetry')/bin/poetry"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export POETRY_CACHE_DIR="$work/cache"

cp -r tests/fixtures/update "$work/update"
cd "$work/update"
idna() {
  grep -A1 '^name = "idna"$' poetry.lock | sed -n 's/^version = "\(.*\)"$/\1/p'
}
test "$(idna)" = 3.7

"$poetry" purifier update idna
echo "conformance-update: idna moved from 3.7 to $(idna)"
test "$(idna)" != 3.7

"$poetry" check --lock
"$poetry" purifier lock --check

# Both lockfiles kept outside the project: `update` moves the poetry.lock
# outside and leaves the copy in the project as it is.
cd "$OLDPWD"
cp -r tests/fixtures/update "$work/outside"
mkdir "$work/kept"
cd "$work/outside"
cp poetry.lock "$work/tree-poetry.lock"
outside=(--lockfile "$work/kept/poetry-purifier.lock.json" --tool-lockfile "$work/kept/poetry.lock")
"$poetry" purifier update idna "${outside[@]}"
cmp poetry.lock "$work/tree-poetry.lock"
test "$(grep -A1 '^name = "idna"$' "$work/kept/poetry.lock" | sed -n 's/^version = "\(.*\)"$/\1/p')" != 3.7
"$poetry" purifier lock --check "${outside[@]}"
echo "conformance-update: ok"
