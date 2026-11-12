# SPDX-License-Identifier: MIT
# nixpkgs' Poetry is taken from `pkgs`: in this package scope `poetry` names
# the wrapper with this plugin.
{
  lib,
  pkgs,
  version ? import ../version.nix,
}:
let
  inherit (pkgs) poetry;
in
poetry.python.pkgs.buildPythonPackage {
  pname = "poetry-purifier";
  inherit version;
  pyproject = true;

  src = lib.fileset.toSource {
    root = ../..;
    fileset = lib.fileset.unions [
      ../../pyproject.toml
      ../../src
    ];
  };

  postPatch = ''
    substituteInPlace pyproject.toml \
      --replace-fail 'version = "0.0.0.dev0"' 'version = "${version}"'
  '';

  build-system = [ poetry.python.pkgs.setuptools ];
  # Poetry loads the plugin into its own environment, which provides Poetry.
  dontCheckRuntimeDeps = true;

  pythonImportsCheck = [ ];
}
