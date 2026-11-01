# SPDX-License-Identifier: MIT
#
# lib.poetry-purifier.buildPoetryProject: builds a Poetry project from its
# poetry-purifier.lock.json, offline, and meets the purifier contract.
#
# Nix fetches the files the lockfile names for this system into a copy of
# Poetry's cache, and Poetry, with the purifier plugin in offline mode, runs
# `poetry build` and `poetry install` on it. Nix reads the project's outputs
# from pyproject.toml and the lockfile when it evaluates the build, and the
# result has these attributes:
#
# - `packages.<name>`: `packages.<project>` is a virtual environment with
#   the project and the dependencies of its `groups`, with the project's
#   scripts in `bin/`. `packages.<script>` has one script in `bin/`, for each
#   script of `[project.scripts]`, `[project.gui-scripts]` and
#   `[tool.poetry.scripts]`.
# - `tests`: the environment with the project's tests run in the sandbox.
# - `release`: the wheel and the sdist that `poetry build` made, which
#   `poetry purifier publish` uploads. `release.files."<file name>"` is the
#   path of each. The lock run recorded their names, and the library's
#   wheelName gives a compiled wheel the tags of this system. The build
#   fails if `poetry build` makes other files.
# - `fetches`: the entries of the lock in memory (read-lock.nix), the
#   download entries of every supported system. This system's build
#   fetches the entries that `lib.purifier.entriesFor` gives for it.
{
  lib,
  # The shared purifier library, `lib.purifier`.
  purifier,
  # Its source, for the Nix files that the Poetry wrapper writes.
  purifierSource,
}:
{
  pkgs,
  src,
  # The purifier lockfile. By default, the one in `src`.
  lockFile ? src + "/poetry-purifier.lock.json",
  # The project's poetry.lock, for a source that does not hold the one the
  # lock was made from. It replaces any poetry.lock in `src`.
  poetryLock ? null,
  # Fetchers for the purifier library's fetchEntry and tree, such as a
  # `fetchurl` that sends credentials or uses a mirror.
  fetchers ? { },
  # The interpreter. By default, nixpkgs' interpreter of the Python version
  # the lock was made with.
  python ? null,
  # The dependency groups to install beside the project.
  groups ? [ "main" ],
  # The groups that the tests need besides `main`. The tests run in a
  # separate environment with those groups and the built wheel, and the
  # default check phase runs `pytest`.
  checkGroups ? [ "dev" ],
  ...
}@args:
let
  inherit (pkgs) stdenv runCommand;
  plugin = pkgs.callPackage ../../pkgs/plugin { };
  poetry = pkgs.callPackage ../../pkgs/poetry { inherit plugin purifierSource; };

  lockFiles = {
    inherit lockFile src;
  }
  // lib.optionalAttrs (poetryLock != null) { inherit poetryLock; };
  # The parsed lockfile, which the build reads only after the lock check
  # passed, so that a bad lockfile fails with its problem rather than with a
  # JSON parse error.
  # The interpreter comes from the lockfile, and the lock in memory needs
  # the interpreter, so this reads the lockfile before checkLock does.
  read = import ./lockfile.nix lockFiles;
  lock = if read.problems == [ ] then read.lock else throw (lib.concatStringsSep "\n" read.problems);
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
  distFiles = [
    (purifier.wheelName system lock.dist.wheel)
    lock.dist.sdist
  ];
  # The lock in memory of the purifier contract, checked.
  checked = purifier.checkLock (
    import ./read-lock.nix { inherit lib purifier; } (
      lockFiles
      // {
        inherit (system) pythonVersion glibcVersion;
      }
    )
  );
  fetches = checked.entries;
  # A directory laid out like Poetry's cache directory: each locked file at
  # the path where Poetry's artifact cache keeps it, and each git checkout
  # at the path the plugin copies it from.
  cache = purifier.tree (
    {
      inherit pkgs;
      name = "${projectName}-poetry-cache";
      entries = purifier.entriesFor stdenv.hostPlatform.system checked;
    }
    // fetchers
  );

  environment = stdenv.mkDerivation (
    removeAttrs args [
      "pkgs"
      "src"
      "lockFile"
      "poetryLock"
      "fetchers"
      "python"
      "groups"
      "checkGroups"
    ]
    // {
      name = args.name or projectName;
      inherit src;
      outputs = [
        "out"
        "dist"
      ];

      nativeBuildInputs = [
        poetry
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
        # A module imported during the build would otherwise leave bytecode
        # that records the build time, and two builds would differ.
        export PYTHONDONTWRITEBYTECODE=1
        ${lib.optionalString (poetryLock != null) ''
          cp --no-preserve=mode ${poetryLock} poetry.lock
        ''}

        # Poetry writes the wheels it builds from sdists next to them, so the
        # directories of the cache are writable and its files are links.
        mkdir -p "$POETRY_CACHE_DIR"
        cp -rs ${cache}/. "$POETRY_CACHE_DIR/"
        chmod -R u+w "$POETRY_CACHE_DIR"

        poetry build --output "$TMPDIR/dist"

        ${interpreter.interpreter} -m venv "$out"
        VIRTUAL_ENV="$out" poetry install --no-root --only ${lib.concatStringsSep "," groups}
        "$out/bin/python" -m pip install --no-deps --no-index --no-cache-dir "$TMPDIR"/dist/*.whl
        # stdenv sets doCheck to 1 when the checks run, which `tests` does.
        if [ "''${doCheck:-}" = 1 ]; then
          ${interpreter.interpreter} -m venv "$TMPDIR/check-env"
          VIRTUAL_ENV="$TMPDIR/check-env" poetry install --no-root \
            --only ${lib.concatStringsSep "," ([ "main" ] ++ checkGroups)}
          "$TMPDIR/check-env/bin/python" -m pip install --no-deps --no-index --no-cache-dir "$TMPDIR"/dist/*.whl
        fi
        runHook postBuild
      '';

      checkPhase =
        args.checkPhase or ''
          runHook preCheck
          pytest
          runHook postCheck
        '';
      preCheck = ''
        export PATH="$TMPDIR/check-env/bin:$PATH"
      ''
      + args.preCheck or "";

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
        python = interpreter;
      }
      // args.passthru or { };
    }
  );

  release = runCommand "${projectName}-release" {
    passthru.files = lib.genAttrs distFiles (file: "${release}/${file}");
  } ''cp -r ${environment.dist} "$out"'';

  scriptPackages = lib.genAttrs scripts (
    script:
    runCommand "${projectName}-${script}" { meta.mainProgram = script; } ''
      mkdir -p "$out/bin"
      ln -s ${environment}/bin/${script} "$out/bin/${script}"
    ''
  );
in
{
  packages = scriptPackages // {
    ${projectName} = environment;
  };
  tests = environment.overrideAttrs { doCheck = true; };
  inherit release fetches;
}
