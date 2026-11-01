#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# The contract's rebuild check: the fixture's environment, its tests and
# its release build, and then `nix build --rebuild` builds them again and
# fails if any output differs from the first build.
#
# Run it from the repository root: tests/conformance-rebuild.sh
set -euo pipefail

system="$(nix eval --raw --impure --expr builtins.currentSystem)"
attributes=(
  ".#checks.$system.app-environment"
  ".#checks.$system.app-tests"
  ".#checks.$system.app-release"
)
nix build --no-link "${attributes[@]}"
nix build --no-link --rebuild "${attributes[@]}"
echo "conformance-rebuild: ok"
