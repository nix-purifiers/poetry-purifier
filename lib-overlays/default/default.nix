# SPDX-License-Identifier: MIT
#
# The `poetry-purifier` library namespace of the purifier contract.
# `readLock` reads poetry.lock and the purifier lockfile into the lock in
# memory (lib-overlays/default/read-lock.nix). It uses the shared purifier library,
# `lib.purifier`. The builder needs a package set, so the default package
# overlay adds it as the package `buildPoetryProject`.
{ ... }:
{

  overlay = final: prev: {
    poetry-purifier = (prev.poetry-purifier or { }) // {
      readLock = import ./read-lock.nix {
        lib = final;
        inherit (final) purifier;
      };
    };
  };

}
