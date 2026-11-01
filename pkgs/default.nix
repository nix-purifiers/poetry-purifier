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
  # locked files through Nix, offline. The default package overlay gives it
  # the source of the shared purifier library.
  poetry = callPackage ./poetry { inherit plugin; };
}
