# SPDX-License-Identifier: MIT
#
# What the plugin builds in interactive use: the cache directory of the
# files that a lockfile names for the calling system. The Poetry wrapper
# passes `pkgs` and the purifier library; the plugin passes the rest.
{
  pkgs,
  purifier,
  lockFile,
  pythonVersion,
  arch,
  glibcVersion,
}:
let
  # The plugin checks the lockfile before it builds this.
  lock = builtins.fromJSON (builtins.readFile lockFile);
in
purifier.tree {
  inherit pkgs;
  name = "poetry-purifier-files";
  entries = import ./select.nix {
    inherit lock;
    installs = purifier.wheels { inherit pythonVersion arch glibcVersion; };
  };
}
