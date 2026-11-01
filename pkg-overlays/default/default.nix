# SPDX-License-Identifier: MIT
#
# The default package overlay. It adds the packages of pkgs/ under
# `pkgs.poetry-purifier` (`.plugin`, `.poetry`), and the builder
# `pkgs.buildPoetryProject` (nix/lib/build-poetry-project.nix) beside them,
# where nixpkgs keeps its own builders, so a package written for
# callPackage takes it by name. A consumer that lists this flake in
# `projects` holds it as `poetry-purifier/default`, which its package sets
# apply by default.
{ closure-lib, ... }:
let
  packages = closure-lib.caisson.nixpkgs.mkPackagesOverlay ../../pkgs "poetry-purifier";
in
{
  overlay =
    final: prev:
    packages final prev
    // {
      buildPoetryProject = final.callPackage ../../nix/lib/build-poetry-project.nix { };
    };
}
