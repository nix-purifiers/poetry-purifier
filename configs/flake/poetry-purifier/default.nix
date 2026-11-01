# SPDX-License-Identifier: MIT
{ closure-lib, ... }:
{
  config,
  inputs,
  lib,
  ...
}:
{

  imports = [
    inputs.flake-parts.flakeModules.partitions
    # The flake publishes the derivations of its scope as `packages`.
    closure-lib.caisson.modules.flake."purifier/purifier-packages"
  ];

  debug = false;
  # The scope `pkgs.poetry-purifier` is the `default` entry of this flake's
  # package overlay registry (pkg-overlays/): the package set applies it by
  # default, and the flake exports it as `pkgOverlays` and as the plain
  # `overlays.default`.
  purifier.scope = "poetry-purifier";
  caisson.libOverlays.exported = libOverlays: {
    inherit (libOverlays) default;
  };

  # `nix run github:nix-purifiers/poetry-purifier -- purifier lock` writes a
  # project's poetry-purifier.lock.json. `nix develop
  # github:nix-purifiers/poetry-purifier` gives a shell where `poetry` is
  # that wrapper.
  perSystem =
    { config, pkgs, ... }:
    {
      packages.default = config.packages.poetry;
      devShells.default = pkgs.mkShell {
        packages = [ config.packages.poetry ];
      };
    };

  partitionedAttrs.checks = "checks";
  partitionedAttrs.formatter = "formatter";

  partitions.formatter = {
    extraInputs = (lib.caisson.pins.flake-compat ../../../tests/dependencies).sources;
    module =
      { inputs, ... }:
      {
        imports = [ inputs.treefmt-nix.flakeModule ];
        perSystem.treefmt.programs.nixfmt.enable = true;
      };
  };

  partitions.checks = {
    extraInputs = (lib.caisson.pins.flake-compat ../../../tests/dependencies).sources;
    module =
      { inputs, self, ... }:
      {
        imports = [ inputs.treefmt-nix.flakeModule ];
        perSystem =
          { pkgs, ... }:
          {
            checks = import ../../../tests {
              inherit pkgs;
              overlay = self.overlays.default;
              purifier = self.inputs.purifier.lib.purifier;
              poetryPurifier = self.lib.poetry-purifier;
            };
            treefmt.programs.nixfmt.enable = true;
          };
      };
  };

}
