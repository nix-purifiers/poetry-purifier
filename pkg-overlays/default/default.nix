# SPDX-License-Identifier: MIT
#
# The default package overlay. It adds one attribute to the package set, the
# scope `pkgs.poetry-purifier`, made with `lib.makeScope`. The scope holds
# the builder `buildPoetryProject` (nix/lib/build-poetry-project.nix), a
# package for callPackage, beside the Poetry wrapper `poetry` and the
# `plugin` (pkgs/). A package written for callPackage takes the scope by
# name, as `{ poetry-purifier }:`. `poetry-purifier.overrideScope` replaces
# what everything in the scope shares, and `buildPoetryProject.override`
# replaces what the builder takes. The wrapper gets the source of the shared
# purifier library, for the Nix files that the plugin evaluates on its own.
# A consumer that lists this flake in `projects` holds the overlay as
# `poetry-purifier/default`, which its package sets apply by default.
{ closure-inputs, ... }:
{
  overlay = final: prev: {
    poetry-purifier = final.lib.makeScope final.newScope (self: {
      plugin = self.callPackage ../../pkgs/plugin { };
      poetry = self.callPackage ../../pkgs/poetry { purifierSource = closure-inputs.purifier; };
      buildPoetryProject = self.callPackage ../../nix/lib/build-poetry-project.nix {
        purifier = import (closure-inputs.purifier + "/nix/lib") { inherit (final) lib; };
      };
    });
  };
}
