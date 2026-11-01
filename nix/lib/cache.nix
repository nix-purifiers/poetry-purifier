# SPDX-License-Identifier: MIT
#
# A directory laid out like Poetry's cache directory, holding the given
# download entries. Each entry's `path` is where Poetry's artifact cache
# keeps that file.
{ fetchurl, linkFarm }:
name: entries:
linkFarm name (
  map (entry: {
    name = entry.path;
    path = fetchurl {
      inherit (entry) url hash;
      name = baseNameOf entry.path;
    };
  }) entries
)
