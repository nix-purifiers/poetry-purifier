# SPDX-License-Identifier: MIT
#
# The default package overlay. It adds the packages of pkgs/ under
# `pkgs.poetry-purifier` (`.plugin`, `.poetry`), and the builder
# `pkgs.buildPoetryProject` (nix/lib/build-poetry-project.nix) beside them,
# where nixpkgs keeps its own builders, so a package written for
# callPackage takes it by name. The builder uses the shared purifier
# library, `lib.purifier`, and the Poetry wrapper gets the library's
# source, for the Nix files that the plugin evaluates on its own. A
# consumer that lists this flake in `projects` holds it as
# `poetry-purifier/default`, which its package sets apply by default.
{ closure-lib, closure-inputs, ... }:
let
  packages = closure-lib.caisson.nixpkgs.mkPackagesOverlay ../../pkgs "poetry-purifier";
in
{
  overlay =
    final: prev:
    let
      scope = (packages final prev).poetry-purifier;
    in
    {
      poetry-purifier = scope // {
        poetry = scope.poetry.override { purifierSource = closure-inputs.purifier; };
      };
      buildPoetryProject = final.callPackage ../../nix/lib/build-poetry-project.nix {
        inherit (closure-lib) purifier;
      };
    };
}
