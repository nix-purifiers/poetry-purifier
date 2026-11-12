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
  # The CPU and glibc of the calling system. On a Mac the files are instead
  # the ones the build chooses, for nixpkgs' darwinMinVersion.
  arch,
  glibcVersion,
}:
let
  # The plugin checks the lockfile before it builds this.
  lock = builtins.fromJSON (builtins.readFile lockFile);
  platform = pkgs.stdenv.hostPlatform;
  target =
    if platform.isDarwin then
      {
        # macOS wheel tags name the CPU arm64.
        arch = "arm64";
        macosVersion = platform.darwinMinVersion;
      }
    else
      { inherit arch glibcVersion; };
  projectInputs = import ../build-poetry-project/python-inputs.nix {
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
        entries = import ../../lib-overlays/default/select.nix {
          packages = import ../../lib-overlays/default/entries.nix { inherit lock poetryLock; };
          installs = purifier.wheels ({ inherit pythonVersion; } // target);
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
