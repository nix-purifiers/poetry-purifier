# SPDX-License-Identifier: MIT
#
# The locked files to fetch for one system. For each package, these are the
# wheels that install with the given Python on this system, or the package's
# sdists when no wheel does. Poetry makes the final choice among them when it
# installs.
{
  lock,
  pythonVersion,
  arch,
  glibcVersion,
}:
let
  inherit (builtins)
    attrValues
    baseNameOf
    concatMap
    filter
    match
    ;

  installs = import ./wheels.nix { inherit pythonVersion arch glibcVersion; };
  isWheel = file: match ".*\\.whl" (baseNameOf file.path) != null;

  forPackage =
    files:
    let
      wheels = filter (file: isWheel file && installs (baseNameOf file.path)) files;
    in
    if wheels != [ ] then wheels else filter (file: !isWheel file) files;
in
concatMap forPackage (attrValues lock.packages)
