#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Interactive use, outside the Nix sandbox: in a copy of the fixture, with
# an empty Poetry cache, the flake's Poetry has Nix fetch the locked files
# and installs offline. The plugin fails any download Poetry would make, so
# a successful install shows that every file came through Nix. The script
# then runs the project's script from the environment.
#
# Run it from the repository root: tests/interactive.sh
set -euo pipefail

poetry="$(nix build --no-link --print-out-paths '.#poetry')/bin/poetry"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

cp -r tests/fixtures/app "$work/app"
cd "$work/app"
export POETRY_CACHE_DIR="$work/cache"
export POETRY_VIRTUALENVS_IN_PROJECT=true

"$poetry" install
.venv/bin/hello --name=interactive | tee "$work/out"
grep -q 'hello, interactive (requests 2\..*six 1\.17\.0, git-package-example' "$work/out"

# The path dependency stays in develop mode outside Nix.
grep -rq "$work/app/libs/greeting" .venv --include='__editable__*'

# `poetry build` runs offline too.
"$poetry" build
test -f dist/app-1.0.0-py3-none-any.whl
echo "interactive: ok"
