# SPDX-License-Identifier: MIT
{
  pkgs,
  overlay,
  # The shared purifier library.
  purifier,
  # This flake's library namespace, with buildPoetryProject.
  poetryPurifier,
}:
let
  inherit (pkgs) lib runCommand;
  purified = pkgs.extend overlay;
  inherit (purified.poetry-purifier) poetry;
  build = poetryPurifier.buildPoetryProject;

  app = build {
    inherit pkgs;
    src = ./fixtures/app;
  };
  environment = app.packages.app;
  sitePackages = "${environment}/${environment.python.sitePackages}";
  lockText = builtins.readFile ./fixtures/app/poetry-purifier.lock.json;
  lock = builtins.fromJSON lockText;

  selectFor =
    arch:
    map (file: baseNameOf file.path) (
      import ../nix/lib/select.nix {
        packages = import ../nix/lib/entries.nix {
          inherit lock;
          poetryLock = ./fixtures/app/poetry.lock;
        };
        installs = purifier.wheels {
          inherit arch;
          pythonVersion = "3.14";
          glibcVersion = "2.40";
        };
      }
    );
  charsetWheels = arch: lib.filter (lib.hasPrefix "charset_normalizer-") (selectFor arch);

  # Textualize's rich at v15.0.0, pinned by commit, as its authors publish it.
  richUpstream = builtins.fetchTarball {
    url = "https://github.com/Textualize/rich/archive/6ac483cbea39cab124dfd3483bba70ffafb71050.tar.gz";
    sha256 = "1qs8n1hcjxcpwbfjjlzfmywfczxzcpvjkk3ay04kdbi1lvlynkaj";
  };

  # Whether readLock reports a problem for this lockfile and this project,
  # and evaluating a build of the project with them stops the evaluation.
  refused =
    {
      lockFile,
      src ? ./fixtures/app,
    }:
    let
      files = { inherit lockFile src; };
    in
    (poetryPurifier.readLock files).problems != [ ]
    && !(builtins.tryEval (build (files // { inherit pkgs; })).release.drvPath).success;
  # The fixture's lockfile with its text changed by `change`.
  changedLock = change: builtins.toFile "poetry-purifier.lock.json" (change lockText);
  gitHash = builtins.head (builtins.attrValues lock.git);

  # Checks that the plugin refuses a lockfile that `damage` changed, with a
  # message that names the command to run.
  refusedByPlugin =
    name: damage:
    runCommand name { nativeBuildInputs = [ poetry ]; } ''
      ${withFixture}
      ${damage}
      if poetry purifier lock --check > "$TMPDIR/message" 2>&1; then
        echo "the plugin accepted the lockfile" >&2
        exit 1
      fi
      cat "$TMPDIR/message"
      grep -q 'Run `poetry purifier lock`' "$TMPDIR/message"
      touch "$out"
    '';

  # Poetry's own Python with pytest and the modules Poetry needs, for the
  # plugin's unit tests. nixpkgs builds Poetry as an application, so its own
  # modules come from its site-packages directory.
  unitPython = pkgs.poetry.python.withPackages (
    ps: [ ps.pytest ] ++ pkgs.poetry.requiredPythonModules
  );
  poetryModules = "${pkgs.poetry}/${pkgs.poetry.python.sitePackages}";

  # A copy of the fixture that Poetry can write to.
  withFixture = ''
    export HOME="$TMPDIR"
    export POETRY_CACHE_DIR="$TMPDIR/poetry-cache"
    cp -r ${./fixtures/app} app
    chmod -R u+w app
    cd app
  '';
in
{
  # The contract: a lockfile older than the project fails, at evaluation and
  # in the command, naming the command to run. Here the fixture's lockfile
  # meets another project's files.
  conformance-stale =
    assert refused {
      lockFile = ./fixtures/app/poetry-purifier.lock.json;
      src = richUpstream;
    };
    refusedByPlugin "conformance-stale" "echo >> pyproject.toml";

  # The contract: a lockfile with a changed hash fails. Here the hash of the
  # git checkout changes.
  conformance-tampered =
    assert refused {
      lockFile = changedLock (
        builtins.replaceStrings [ gitHash ] [ "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" ]
      );
    };
    refusedByPlugin "conformance-tampered" ''
      sed -i 's|${gitHash}|sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=|' poetry-purifier.lock.json
    '';

  # The contract: a lockfile without a needed entry fails, naming the
  # command to run. Here the URL of docopt's sdist goes missing.
  conformance-missing =
    assert refused {
      lockFile = changedLock (
        builtins.replaceStrings [ ''"docopt-0.6.2.tar.gz"'' ] [ ''"docopt-0.6.3.tar.gz"'' ]
      );
    };
    refusedByPlugin "conformance-missing" ''
      sed -i 's|"docopt-0.6.2.tar.gz"|"docopt-0.6.3.tar.gz"|' poetry-purifier.lock.json
    '';

  # The contract: a lockfile that is not valid fails, naming the command to
  # run. A truncated file is not JSON, and the evaluation stops with the
  # purifier's message, before anything parses it.
  conformance-malformed =
    assert refused {
      lockFile = changedLock (builtins.substring 0 500);
    };
    refusedByPlugin "conformance-malformed" ''
      head -c 500 poetry-purifier.lock.json > truncated
      mv truncated poetry-purifier.lock.json
    '';

  # The contract: a fixture whose source holds no purifier lockfile builds
  # from a lockfile kept outside it.
  conformance-external =
    let
      external = build {
        inherit pkgs;
        src = builtins.path {
          path = ./fixtures/app;
          name = "app-without-lockfile";
          filter = path: _: baseNameOf path != "poetry-purifier.lock.json";
        };
        lockFile = ./fixtures/app/poetry-purifier.lock.json;
      };
    in
    runCommand "conformance-external" { } ''
      ${lib.getExe external.packages.hello} --name=external | tee "$out"
      grep -q 'hello, external' "$out"
    '';

  # The project's script runs from the environment Poetry installed
  # offline. It imports one dependency of each kind: registry wheels, an
  # sdist Poetry built with the setuptools the lock resolved, a git
  # dependency, a URL sdist and a path dependency.
  app-runs = runCommand "app-runs" { } ''
    ${lib.getExe app.packages.hello} --name=nix | tee "$out"
    grep -q 'hello, nix (requests 2\..*six 1\.17\.0, git-package-example.*attrs 25\.4\.0)' "$out"
  '';

  # `tests` runs the fixture's pytest suite in the sandbox, with the `dev`
  # group installed offline beside the built wheel.
  app-tests = app.tests;

  # The lock in memory: the shape that the purifier library checks, the
  # charset-normalizer wheels of each CPU marked with their system, the git
  # checkout on every system, and the outputs by name.
  read-lock =
    let
      memory = poetryPurifier.readLock { src = ./fixtures/app; };
      named = prefix: lib.filter (entry: lib.hasPrefix prefix (baseNameOf entry.path)) memory.entries;
      charset = named "charset_normalizer-";
      checkouts = lib.filter (entry: entry ? git) memory.entries;
    in
    assert purifier.checkLock memory == memory;
    assert memory.purifier == "poetry-purifier";
    assert lib.any (entry: entry.systems or null == [ "aarch64-linux" ]) charset;
    assert lib.any (entry: entry.systems or null == [ "x86_64-linux" ]) charset;
    assert checkouts != [ ] && lib.all (entry: !(entry ? systems)) checkouts;
    assert
      memory.outputs == {
        app.kind = "environment";
        hello.kind = "script";
      };
    assert
      app.fetches == (poetryPurifier.readLock {
        src = ./fixtures/app;
        pythonVersion = "3.14";
        glibcVersion = pkgs.stdenv.cc.libc.version;
      }).entries;
    runCommand "read-lock" { } "touch $out";

  # The fixture's environment and its release, which the conformance-rebuild
  # job builds a second time with `nix build --rebuild`.
  app-environment = environment;
  app-release = app.release;

  # Nix knows the project's outputs when it evaluates the build: the
  # environment and the one script from pyproject.toml, and the wheel and
  # sdist of `poetry build` by the file names the lock run recorded.
  outputs-by-name =
    assert
      lib.attrNames app.packages == [
        "app"
        "hello"
      ];
    assert
      lib.attrNames app.release.files == [
        "app-1.0.0-py3-none-any.whl"
        "app-1.0.0.tar.gz"
      ];
    assert lib.all (entry: entry ? path) app.fetches;
    runCommand "outputs-by-name" { } ''
      test -f ${app.release.files."app-1.0.0-py3-none-any.whl"}
      test -f ${app.release.files."app-1.0.0.tar.gz"}
      touch "$out"
    '';

  # The plugin's unit tests: the lockfile functions, the lock command's
  # reading of sdists, and the versions of source trees.
  unit =
    runCommand "unit"
      {
        nativeBuildInputs = [ unitPython ];
      }
      ''
        export HOME="$TMPDIR"
        cp -r ${../src} src
        cp -r ${./unit} unit
        PYTHONPATH=src:${poetryModules} python -m pytest -p no:cacheprovider unit
        touch "$out"
      '';

  # A compiled wheel the lock run built on another system gets the tags of
  # this one, from the shared library's wheelName: its CPython tags, and the
  # platform tag of setuptools-style backends and of poetry-core.
  wheel-name =
    let
      name = purifier.wheelName {
        pythonVersion = "3.13";
        arch = "aarch64";
        glibcVersion = "2.40";
      };
    in
    assert name "app-1.0.0-py3-none-any.whl" == "app-1.0.0-py3-none-any.whl";
    assert name "fast-1.0-cp314-cp314-linux_x86_64.whl" == "fast-1.0-cp313-cp313-linux_aarch64.whl";
    assert
      name "fast-1.0-cp314-cp314-manylinux_2_44_x86_64.whl"
      == "fast-1.0-cp313-cp313-manylinux_2_40_aarch64.whl";
    assert name "fast-1.0-cp311-abi3-linux_x86_64.whl" == "fast-1.0-cp311-abi3-linux_aarch64.whl";
    pkgs.emptyFile;

  # The path dependency is in develop mode, and the Nix build installs it as
  # a regular package rather than as a link into the build directory.
  path-dependency-installed = runCommand "path-dependency-installed" { } ''
    test -f ${sitePackages}/greeting.py
    ! grep -rq /build ${sitePackages} --include='*.pth'
    touch "$out"
  '';

  # The git dependency is fetched with its submodules, as Poetry clones it.
  git-submodules =
    let
      checkout = purifier.fetchEntry { inherit pkgs; } (
        lib.findFirst (
          entry: entry ? git && lib.hasInfix "test-fixture-vcs-repository" entry.git.url
        ) null app.fetches
      );
    in
    runCommand "git-submodules" { } ''
      test -n "$(ls -A ${checkout}/submodules/sample-namespace-packages)"
      touch "$out"
    '';

  # One lock serves every system: for each CPU, the build fetches only the
  # wheels of that CPU.
  select-per-cpu =
    assert charsetWheels "x86_64" != [ ];
    assert charsetWheels "aarch64" != [ ];
    assert lib.all (lib.hasInfix "aarch64") (
      lib.filter (name: !lib.hasSuffix "py3-none-any.whl" name) (charsetWheels "aarch64")
    );
    assert lib.elem "docopt-0.6.2.tar.gz" (selectFor "aarch64");
    pkgs.emptyFile;

  # The committed lock matches the fixture's pyproject.toml and poetry.lock.
  lock-up-to-date = runCommand "lock-up-to-date" { nativeBuildInputs = [ poetry ]; } ''
    ${withFixture}
    poetry purifier lock --check
    touch "$out"
  '';

  # `poetry purifier publish` uploads the `release` that Nix built to a
  # package index.
  publish =
    runCommand "publish"
      {
        nativeBuildInputs = [
          poetry
          # pypiserver imports cgi, which Python 3.14 removed, unless
          # legacy-cgi provides it.
          (pkgs.python3.withPackages (ps: [
            ps.pypiserver
            ps.legacy-cgi
          ]))
        ];
      }
      ''
        ${withFixture}
        mkdir "$TMPDIR/index"
        python3 -m pypiserver run --port 8765 --authenticate . --passwords . "$TMPDIR/index" &
        server=$!
        for _ in $(seq 50); do
          python3 -c 'import socket; socket.create_connection(("127.0.0.1", 8765))' 2>/dev/null && break
          sleep 0.2
        done

        export POETRY_REPOSITORIES_LOCAL_URL=http://127.0.0.1:8765/
        # The index accepts any credentials.
        export POETRY_HTTP_BASIC_LOCAL_USERNAME=purifier
        export POETRY_HTTP_BASIC_LOCAL_PASSWORD="$POETRY_HTTP_BASIC_LOCAL_USERNAME"
        export POETRY_PURIFIER_DIST=${app.release}
        poetry purifier publish --repository local

        kill "$server"
        test -f "$TMPDIR/index/app-1.0.0-py3-none-any.whl"
        test -f "$TMPDIR/index/app-1.0.0.tar.gz"
        cmp "$TMPDIR/index/app-1.0.0-py3-none-any.whl" ${app.release.files."app-1.0.0-py3-none-any.whl"}
        touch "$out"
      '';
}
