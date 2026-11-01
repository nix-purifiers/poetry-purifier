#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# The real-world check: builds Textualize's rich at its pinned commit
# offline in the Nix sandbox and runs its whole test suite. See
# tests/real-world/rich/default.nix.
#
# Run it from the repository root: tests/real-world-rich.sh
set -euo pipefail

nix build --no-link --impure --expr \
  'import ./tests/real-world/rich { flake = builtins.getFlake "git+file://${toString ./.}"; }'
echo "real-world-rich: ok"
