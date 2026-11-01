# SPDX-License-Identifier: MIT
#
# The locked files to fetch for one system. For each package, these are the
# wheels that install on this system, or the package's sdists when no wheel
# does. Poetry makes the final choice among them when it installs. A git
# dependency is its checkout on every system.
{
  # The download entries of every package, by "name==version", from
  # entries.nix.
  packages,
  # Whether a wheel installs on this system, from its file name: the shared
  # library's `wheels` for this Python, CPU and glibc.
  installs,
  # Whether each package's sdists come along with its wheels, for a caller
  # that does not know the system's glibc, which may be too old for every
  # wheel.
  withSdists ? false,
}:
let
  inherit (builtins)
    attrValues
    baseNameOf
    concatMap
    filter
    match
    ;

  isWheel = file: match ".*\\.whl" (baseNameOf file.path) != null;

  forPackage =
    files:
    let
      wheels = filter (file: isWheel file && installs (baseNameOf file.path)) files;
      others = filter (file: file ? git || !isWheel file) files;
    in
    if wheels == [ ] then
      others
    else if withSdists then
      wheels ++ others
    else
      wheels;
in
concatMap forPackage (attrValues packages)
