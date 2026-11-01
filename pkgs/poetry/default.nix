# SPDX-License-Identifier: MIT
#
# Poetry with the purifier plugin. The wrapper tells the plugin about Nix:
#
# - POETRY_PURIFIER_REALIZE names a Nix file that builds the cache directory
#   of a lockfile's files and the interpreter, for `poetry purifier
#   prefetch` and interactive use.
# - POETRY_PURIFIER_BUILD names a Nix file that builds the `release` of a
#   project with `buildPoetryProject`, for `poetry purifier publish`.
#   POETRY_PURIFIER_POETRY names this wrapper, which that build uses.
#
# Both use this package set's nixpkgs, and the shared purifier library from
# the source this flake pins, which the files load the way the library's
# own lib-overlay does. nix-prefetch-git is on the path for `poetry purifier
# lock`, which hashes git dependencies with it.
{
  lib,
  # nixpkgs' Poetry, taken from `pkgs`: in this package scope `poetry`
  # names this wrapper.
  pkgs,
  plugin,
  # The source of the shared purifier library, from the flake's input.
  purifierSource ? throw "poetry-purifier: the Poetry wrapper needs the purifier library; use it from the poetry-purifier overlay",
  makeWrapper,
  nix-prefetch-git,
  symlinkJoin,
  writeText,
  path,
  stdenv,
}:
let
  inherit (pkgs) poetry;
  withPlugin = poetry.withPlugins (_: [ plugin ]);
  # The Nix files of the builder.
  sources = lib.fileset.toSource {
    root = ../..;
    fileset = ../../nix;
  };
  context = ''
    pkgs = import ${path} { system = "${stdenv.hostPlatform.system}"; };
    purifier = import ${purifierSource}/nix/lib { inherit (pkgs) lib; };
  '';

  realize = writeText "poetry-purifier-realize.nix" ''
    { ... }@args:
    let
      ${context}
    in
    import ${sources}/nix/lib/realize.nix ({ inherit pkgs purifier; } // args)
  '';

  build = writeText "poetry-purifier-build.nix" ''
    {
      src,
      lockFile,
      # The project's poetry.lock when it lies outside the project.
      poetryLock ? null,
      # This wrapper, whose path the wrapper script sets.
      poetry ? builtins.storePath (builtins.getEnv "POETRY_PURIFIER_POETRY"),
    }:
    let
      ${context}
      ignored = [ ".git" ".venv" ".poetry-purifier" "dist" "__pycache__" ];
    in
    pkgs.callPackage ${sources}/nix/lib/build-poetry-project.nix {
      inherit purifier poetry;
    } ({
      inherit lockFile;
      src = builtins.path {
        path = src;
        name = "source";
        filter =
          path: type:
          !(builtins.elem (baseNameOf path) ignored)
          && builtins.match "result.*" (baseNameOf path) == null;
      };
    } // pkgs.lib.optionalAttrs (poetryLock != null) { inherit poetryLock; })
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
      --set POETRY_PURIFIER_POETRY "$out" \
      --prefix PATH : ${lib.makeBinPath [ nix-prefetch-git ]}
  '';
  meta.mainProgram = "poetry";
}
