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
echo "conformance-update: ok"
