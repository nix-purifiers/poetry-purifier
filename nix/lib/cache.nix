# SPDX-License-Identifier: MIT
#
# A directory laid out like Poetry's cache directory, holding the given
# download entries. A file entry's `path` is where Poetry's artifact cache
# keeps that file. A git entry's `path` is the checkout that the plugin
# copies when Poetry would clone the repository.
{
  fetchurl,
  fetchgit,
  linkFarm,
}:
name: entries:
let
  fetch =
    entry:
    if entry ? git then
      fetchgit {
        inherit (entry.git) url rev;
        inherit (entry) hash;
        fetchSubmodules = entry.git.submodules;
      }
    else
      fetchurl {
        inherit (entry) url hash;
        name = baseNameOf entry.path;
      };
in
linkFarm name (
  map (entry: {
    name = entry.path;
    path = fetch entry;
  }) entries
)
