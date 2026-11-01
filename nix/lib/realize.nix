# SPDX-License-Identifier: MIT
#
# What the plugin builds in interactive use: the cache directory of the
# files that a lockfile names for the calling system. The Poetry wrapper
# passes `pkgs`; the plugin passes the rest.
{
  pkgs,
  lockFile,
  pythonVersion,
  arch,
  glibcVersion,
}:
let
  lock = builtins.fromJSON (builtins.readFile lockFile);
in
import ./cache.nix { inherit (pkgs) fetchurl linkFarm; } "poetry-purifier-files" (
  import ./select.nix {
    inherit
      lock
      pythonVersion
      arch
      glibcVersion
      ;
  }
)
