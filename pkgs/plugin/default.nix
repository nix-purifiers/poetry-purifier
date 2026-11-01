# SPDX-License-Identifier: MIT
# nixpkgs' Poetry is taken from `pkgs`: in this package scope `poetry` names
# the wrapper with this plugin.
{ lib, pkgs }:
let
  inherit (pkgs) poetry;
in
poetry.python.pkgs.buildPythonPackage {
  pname = "poetry-purifier";
  version = lib.removeSuffix "\n" (builtins.readFile ../../version.txt);
  pyproject = true;

  src = lib.fileset.toSource {
    root = ../..;
    fileset = lib.fileset.unions [
      ../../pyproject.toml
      ../../version.txt
      ../../src
    ];
  };

  build-system = [ poetry.python.pkgs.setuptools ];
  # Poetry loads the plugin into its own environment, which provides Poetry.
  dontCheckRuntimeDeps = true;

  pythonImportsCheck = [ ];
}
