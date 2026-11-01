# SPDX-License-Identifier: MIT
{ callPackage, ... }:
let
  plugin = callPackage ./plugin { };
in
{
  # The Poetry plugin, built against nixpkgs' Poetry.
  inherit plugin;
  # Poetry with the plugin. In a project with an up-to-date
  # poetry-purifier.lock.json, `poetry install`, `sync` and `build` read the
  # locked files through Nix, offline.
  poetry = callPackage ./poetry { inherit plugin; };
}
