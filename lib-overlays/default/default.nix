# SPDX-License-Identifier: MIT
#
# The `poetry-purifier` library namespace of the purifier contract.
# `buildPoetryProject` is the builder (nix/lib/build-poetry-project.nix),
# and it takes the package set as its `pkgs` argument. `readLock` reads
# poetry.lock and the purifier lockfile into the lock in memory
# (nix/lib/read-lock.nix). Both use the shared purifier library,
# `lib.purifier`.
{ closure-inputs, ... }:
{

  overlay = final: prev: {
    poetry-purifier = (prev.poetry-purifier or { }) // {
      readLock = import ../../nix/lib/read-lock.nix {
        lib = final;
        inherit (final) purifier;
      };
      buildPoetryProject = import ../../nix/lib/build-poetry-project.nix {
        lib = final;
        inherit (final) purifier;
        purifierSource = closure-inputs.purifier;
      };
    };
  };

}
