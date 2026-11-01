# SPDX-License-Identifier: MIT
#
# Builds a Poetry project from its poetry-purifier.lock.json, offline.
#
# Nix fetches the files the lockfile names for this system into a copy of
# Poetry's cache, and Poetry, with the purifier plugin in offline mode, runs
# `poetry build` and `poetry install` on it. The result is a virtual
# environment that holds the project and the dependencies of its `groups`,
# with the project's scripts in `bin/`. The `dist` output holds the wheel
# and sdist that `poetry build` made.
{
  lib,
  pkgs,
  stdenv,
  fetchgit,
  linkFarm,
  poetry-purifier,
}:
{
  src,
  # The lockfile. By default, the one in `src`.
  lockFile ? src + "/poetry-purifier.lock.json",
  # The interpreter. By default, nixpkgs' interpreter of the Python version
  # the lock was made with.
  python ? null,
  # The dependency groups to install beside the project.
  groups ? [ "main" ],
  # The fetcher of locked files, for a package index that needs credentials
  # or a mirror.
  fetchurl ? pkgs.fetchurl,
  ...
}@args:
let
  lock = builtins.fromJSON (builtins.readFile lockFile);
  interpreter =
    if python != null then python else pkgs."python${lib.replaceStrings [ "." ] [ "" ] lock.python}";

  files = import ./select.nix {
    inherit lock;
    pythonVersion = lib.versions.majorMinor interpreter.version;
    arch = stdenv.hostPlatform.parsed.cpu.name;
    glibcVersion = stdenv.cc.libc.version;
  };
  mkCache = import ./cache.nix { inherit fetchurl fetchgit linkFarm; };
  cache = mkCache "${lock.project.name}-poetry-cache" files;
in
stdenv.mkDerivation (
  removeAttrs args [
    "lockFile"
    "python"
    "groups"
    "fetchurl"
  ]
  // {
    name = args.name or lock.project.name;
    inherit src;
    outputs = [
      "out"
      "dist"
    ];

    nativeBuildInputs = [
      poetry-purifier.poetry
      interpreter
    ]
    ++ args.nativeBuildInputs or [ ];

    dontConfigure = true;

    buildPhase = ''
      runHook preBuild

      export HOME="$TMPDIR"
      export POETRY_CACHE_DIR="$TMPDIR/poetry-cache"
      export POETRY_NO_INTERACTION=1
      export POETRY_PURIFIER_MODE=offline
      export POETRY_PURIFIER_LOCK=${lockFile}

      # Poetry writes the wheels it builds from sdists next to them, so the
      # directories of the cache are writable and its files are links.
      mkdir -p "$POETRY_CACHE_DIR"
      cp -rs ${cache}/. "$POETRY_CACHE_DIR/"
      chmod -R u+w "$POETRY_CACHE_DIR"

      poetry build --output "$TMPDIR/dist"

      ${interpreter.interpreter} -m venv "$out"
      VIRTUAL_ENV="$out" poetry install --no-root --only ${lib.concatStringsSep "," groups}
      "$out/bin/python" -m pip install --no-deps --no-index --no-cache-dir "$TMPDIR"/dist/*.whl

      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall
      mkdir -p "$dist"
      cp "$TMPDIR"/dist/* "$dist/"
      runHook postInstall
    '';

    passthru = {
      inherit lock cache;
      python = interpreter;
    }
    // args.passthru or { };
  }
)
