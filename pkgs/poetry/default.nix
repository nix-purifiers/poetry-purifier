# SPDX-License-Identifier: MIT
#
# Poetry with the purifier plugin. The wrapper tells the plugin about Nix:
#
# - POETRY_PURIFIER_REALIZE names a Nix file that builds the cache directory
#   of a lockfile's files, for interactive use.
# - POETRY_PURIFIER_BUILD names a Nix file that builds a project with
#   `buildPoetryProject`, for `poetry purifier publish`.
#
# Both use this package set's nixpkgs. nix-prefetch-git is on the path for
# `poetry purifier lock`, which hashes git dependencies with it.
{
  lib,
  # nixpkgs' Poetry, taken from `pkgs`: in this package scope `poetry`
  # names this wrapper.
  pkgs,
  plugin,
  makeWrapper,
  nix-prefetch-git,
  symlinkJoin,
  writeText,
  path,
  system,
}:
let
  inherit (pkgs) poetry;
  withPlugin = poetry.withPlugins (_: [ plugin ]);
  nixpkgs = ''import ${path} { system = "${system}"; }'';

  realize = writeText "poetry-purifier-realize.nix" ''
    { ... }@args:
    import ${../../nix/lib}/realize.nix ({ pkgs = ${nixpkgs}; } // args)
  '';

  build = writeText "poetry-purifier-build.nix" ''
    { src }:
    let
      pkgs = ${nixpkgs};
      ignored = [ ".git" ".venv" ".poetry-purifier" "dist" "__pycache__" ];
    in
    pkgs.callPackage ${../../nix/lib}/build-poetry-project.nix {
      poetry-purifier.poetry = builtins.storePath "${withPlugin}";
    } {
      src = builtins.path {
        path = src;
        name = "source";
        filter =
          path: type:
          !(builtins.elem (baseNameOf path) ignored)
          && builtins.match "result.*" (baseNameOf path) == null;
      };
    }
  '';
in
symlinkJoin {
  name = "poetry-purifier-${withPlugin.version or poetry.version}";
  paths = [ withPlugin ];
  nativeBuildInputs = [ makeWrapper ];
  postBuild = ''
    wrapProgram "$out/bin/poetry" \
      --set POETRY_PURIFIER_REALIZE ${realize} \
      --set POETRY_PURIFIER_BUILD ${build} \
      --prefix PATH : ${lib.makeBinPath [ nix-prefetch-git ]}
  '';
  meta.mainProgram = "poetry";
}
