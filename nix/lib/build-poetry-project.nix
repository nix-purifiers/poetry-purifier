# SPDX-License-Identifier: MIT
#
# Builds a Poetry project from its poetry-purifier.lock.json, offline.
#
# Nix fetches the files the lockfile names for this system into a copy of
# Poetry's cache, and Poetry, with the purifier plugin in offline mode, runs
# `poetry build` and `poetry install` on it. The result is a virtual
# environment that holds the project and the dependencies of its `groups`,
# with the project's scripts in `bin/`.
#
# Nix reads the project's outputs from pyproject.toml when it evaluates the
# build, and the result has one attribute per output:
#
# - `bins.<script>`: one package per script of `[project.scripts]`,
#   `[project.gui-scripts]` and `[tool.poetry.scripts]`, with that script
#   in `bin/`.
# - `artifacts."<file name>"`: the wheel and the sdist that `poetry build`
#   made. The lock run recorded their names, and wheel-name.nix rewrites a
#   compiled wheel's tags for the system Nix builds for. The build fails if
#   `poetry build` makes other files.
{
  lib,
  pkgs,
  stdenv,
  fetchgit,
  linkFarm,
  runCommand,
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
  pyproject = builtins.fromTOML (builtins.readFile (src + "/pyproject.toml"));
  poetrySection = pyproject.tool.poetry or { };
  projectName = pyproject.project.name or poetrySection.name;
  scripts = lib.unique (
    lib.attrNames (pyproject.project.scripts or { })
    ++ lib.attrNames (pyproject.project.gui-scripts or { })
    ++ lib.attrNames (poetrySection.scripts or { })
  );

  interpreter =
    if python != null then python else pkgs."python${lib.replaceStrings [ "." ] [ "" ] lock.python}";

  system = {
    pythonVersion = lib.versions.majorMinor interpreter.version;
    arch = stdenv.hostPlatform.parsed.cpu.name;
    glibcVersion = stdenv.cc.libc.version;
  };
  files = import ./select.nix ({ inherit lock; } // system);
  distFiles = [
    (import ./wheel-name.nix ({ inherit lib; } // system) lock.dist.wheel)
    lock.dist.sdist
  ];
  mkCache = import ./cache.nix { inherit fetchurl fetchgit linkFarm; };
  cache = mkCache "${projectName}-poetry-cache" files;

  environment = stdenv.mkDerivation (
    removeAttrs args [
      "lockFile"
      "python"
      "groups"
      "fetchurl"
    ]
    // {
      name = args.name or projectName;
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
        expected=${lib.escapeShellArg (lib.concatStringsSep " " (lib.sort lib.lessThan distFiles))}
        built="$(ls "$TMPDIR/dist" | sort | tr '\n' ' ' | sed 's/ $//')"
        if [ "$built" != "$expected" ]; then
          echo "poetry-purifier: poetry build made $built, not $expected as poetry-purifier.lock.json records. Run \`poetry purifier lock\`." >&2
          exit 1
        fi
        mkdir -p "$dist"
        cp "$TMPDIR"/dist/* "$dist/"
        runHook postInstall
      '';

      passthru = {
        inherit lock cache;
        python = interpreter;
        bins = lib.genAttrs scripts (
          script:
          runCommand "${projectName}-${script}" { meta.mainProgram = script; } ''
            mkdir -p "$out/bin"
            ln -s ${environment}/bin/${script} "$out/bin/${script}"
          ''
        );
        artifacts = lib.genAttrs distFiles (file: "${environment.dist}/${file}");
      }
      // args.passthru or { };
    }
  );
in
environment
