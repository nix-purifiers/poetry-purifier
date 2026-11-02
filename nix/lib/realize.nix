# SPDX-License-Identifier: MIT
#
# What the plugin builds for `poetry purifier prefetch` and for interactive
# use: `files`, the cache directory of the files that a lockfile and
# poetry.lock name for the calling system, and `python`, nixpkgs'
# interpreter of the Python version the lock was made with. The Poetry
# wrapper passes `pkgs` and the purifier library; the plugin passes the rest.
{
  pkgs,
  purifier,
  lockFile,
  poetryLock,
  pythonVersion,
  arch,
  glibcVersion,
}:
let
  # The plugin checks the lockfile before it builds this.
  lock = builtins.fromJSON (builtins.readFile lockFile);
  projectInputs = import ./python-inputs.nix {
    inherit purifier lock;
    inherit (pkgs) lib runCommand writeText;
    python = pkgs."python${builtins.replaceStrings [ "." ] [ "" ] lock.python}";
    system = pkgs.stdenv.hostPlatform.system;
  };
in
pkgs.linkFarm "poetry-purifier-prefetch" (
  [
    {
      name = "files";
      path = purifier.tree {
        inherit pkgs;
        name = "poetry-purifier-files";
        entries = import ./select.nix {
          packages = import ./entries.nix { inherit lock poetryLock; };
          installs = purifier.wheels { inherit pythonVersion arch glibcVersion; };
        };
      };
    }
    {
      name = "python";
      path = pkgs."python${builtins.replaceStrings [ "." ] [ "" ] lock.python}";
    }
  ]
  ++ pkgs.lib.optional (projectInputs.dependencies != [ ]) {
    name = "python-inputs.json";
    path = "${projectInputs.bindings}/inputs.json";
  }
)
