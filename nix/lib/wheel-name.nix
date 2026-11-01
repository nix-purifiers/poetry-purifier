# SPDX-License-Identifier: MIT
#
# The file name of the project's wheel on the system Nix builds for. The
# lock run built the wheel on its own system and recorded its name. A pure
# wheel (platform tag `any`) has the same name everywhere. A compiled wheel
# has its CPython tags rewritten for this Python, and its platform tag for
# this CPU and glibc:
#
# - `linux_<cpu>` is the tag of setuptools and most other build backends.
# - `manylinux_2_<glibc minor>_<cpu>` is the tag poetry-core gives a wheel
#   with a build script: the first tag `packaging` reports for the system it
#   builds on.
{
  lib,
  # The Python version, such as "3.14".
  pythonVersion,
  # The CPU name in a wheel's platform tag, such as "x86_64" or "aarch64".
  arch,
  # The glibc version of the system, such as "2.40".
  glibcVersion,
}:
lockedName:
let
  parts = lib.splitString "-" (lib.removeSuffix ".whl" lockedName);
  count = builtins.length parts;
  head = lib.take (count - 3) parts;
  pythonTag = builtins.elemAt parts (count - 3);
  abiTag = builtins.elemAt parts (count - 2);
  platformTag = builtins.elemAt parts (count - 1);

  cpython = "cp${lib.replaceStrings [ "." ] [ "" ] pythonVersion}";
  retag = tag: if builtins.match "cp3[0-9]+" tag != null then cpython else tag;
  glibcMinor = lib.elemAt (lib.splitString "." glibcVersion) 1;

  platform =
    if platformTag == "any" then
      "any"
    else if builtins.match "linux_.*" platformTag != null then
      "linux_${arch}"
    else if builtins.match "manylinux_2_[0-9]+_.*" platformTag != null then
      "manylinux_2_${glibcMinor}_${arch}"
    else
      throw "poetry-purifier: the lock run built ${lockedName}, whose platform tag ${platformTag} is not one a build on Linux makes";
in
if platformTag == "any" then
  lockedName
else
  lib.concatStringsSep "-" (
    head
    ++ [
      (if abiTag == "abi3" then pythonTag else retag pythonTag)
      (retag abiTag)
      platform
    ]
  )
  + ".whl"
