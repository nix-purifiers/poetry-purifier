# SPDX-License-Identifier: MIT
{
  pkgs,
  overlay,
  # The shared purifier library.
  purifier,
}:
let
  inherit (pkgs) lib runCommand;
  purified = pkgs.extend overlay;
  inherit (purified.poetry-purifier) poetry;

  app = purified.buildPoetryProject { src = ./fixtures/app; };
  lock = lib.importJSON ./fixtures/app/poetry-purifier.lock.json;
  sitePackages = "${app}/${app.python.sitePackages}";

  selectFor =
    arch:
    map (file: baseNameOf file.path) (
      import ../nix/lib/select.nix {
        inherit lock;
        installs = purifier.wheels {
          inherit arch;
          pythonVersion = "3.14";
          glibcVersion = "2.40";
        };
      }
    );
  charsetWheels = arch: lib.filter (lib.hasPrefix "charset_normalizer-") (selectFor arch);

  # A lockfile changed by `change`, written to the store at evaluation time.
  # `rehash` gives it a matching contentHash, as a lock written by another
  # project or version of the plugin would have.
  changedLock =
    {
      change,
      rehash ? false,
    }:
    let
      changed = change lock;
      body = removeAttrs changed [ "contentHash" ];
      final =
        if rehash then
          changed // { contentHash = builtins.hashString "sha256" (builtins.toJSON body); }
        else
          changed;
    in
    builtins.toFile "poetry-purifier.lock.json" (builtins.toJSON final);
  # Whether reading `lockFile` against the fixture stops the evaluation.
  refused =
    lockFile:
    !(builtins.tryEval (
      builtins.deepSeq (import ../nix/lib/check-lock.nix {
        inherit lockFile;
        src = ./fixtures/app;
      }) true
    )).success;

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
  # The project's script runs from the environment Poetry installed
  # offline. It imports one dependency of each kind: registry wheels, an
  # sdist Poetry built with the setuptools the lock resolved, a git
  # dependency, a URL sdist and a path dependency.
  app-runs = runCommand "app-runs" { } ''
    ${lib.getExe app.bins.hello} --name=nix | tee "$out"
    grep -q 'hello, nix (requests 2\..*six 1\.17\.0, git-package-example.*attrs 25\.4\.0)' "$out"
  '';

  # `doCheck = true` runs the fixture's pytest suite in the sandbox, with
  # the `dev` group installed offline beside the built wheel.
  app-tests = app.overrideAttrs { doCheck = true; };

  # A real-world project: Textualize's rich at v15.0.0, pinned by commit. Its
  # own poetry.lock was made by Poetry 2.1.3, so the lock was made after
  # `poetry lock` with Poetry 2.5.1, which kept every locked version. The
  # build runs rich's whole pytest suite in the sandbox.
  real-world-rich =
    let
      rich = purified.buildPoetryProject {
        src = builtins.fetchTarball {
          url = "https://github.com/Textualize/rich/archive/6ac483cbea39cab124dfd3483bba70ffafb71050.tar.gz";
          sha256 = "1qs8n1hcjxcpwbfjjlzfmywfczxzcpvjkk3ay04kdbi1lvlynkaj";
        };
        poetryLockFile = ./real-world/rich/poetry.lock;
        lockFile = ./real-world/rich/poetry-purifier.lock.json;
        doCheck = true;
        # test_brokenpipeerror runs `which python` and `which head`.
        nativeCheckInputs = [ pkgs.which ];
      };
    in
    assert
      lib.attrNames rich.artifacts == [
        "rich-15.0.0-py3-none-any.whl"
        "rich-15.0.0.tar.gz"
      ];
    runCommand "real-world-rich" { } ''
      ${rich}/bin/python -c 'import rich; rich.print("[bold]rich[/bold] from Nix")' | tee "$out"
      grep -q 'rich from Nix' "$out"
      test -f ${rich.artifacts."rich-15.0.0-py3-none-any.whl"}
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

  # Each mistake in a lockfile stops the evaluation of the build, rather
  # than failing inside it: a lock out of date with pyproject.toml or
  # poetry.lock, an edited hash, a removed entry, and another format.
  lock-errors =
    assert refused (changedLock {
      change = l: l // { inputsHash = lib.fixedWidthString 64 "0" ""; };
      rehash = true;
    });
    assert refused (changedLock {
      change =
        l:
        l
        // {
          packages = l.packages // {
            "six==1.17.0" = map (
              f: f // { hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="; }
            ) l.packages."six==1.17.0";
          };
        };
    });
    assert refused (changedLock {
      change = l: l // { packages = removeAttrs l.packages [ "six==1.17.0" ]; };
    });
    assert refused (changedLock {
      change = l: l // { format = l.format - 1; };
      rehash = true;
    });
    assert
      !refused (changedLock {
        change = l: l;
      });
    pkgs.emptyFile;

  # Nix knows the project's outputs when it evaluates the build: its one
  # script from pyproject.toml, and the wheel and sdist of `poetry build` by
  # the file names the lock run recorded.
  outputs-by-name =
    assert lib.attrNames app.bins == [ "hello" ];
    assert
      lib.attrNames app.artifacts == [
        "app-1.0.0-py3-none-any.whl"
        "app-1.0.0.tar.gz"
      ];
    runCommand "outputs-by-name" { } ''
      test -f ${app.artifacts."app-1.0.0-py3-none-any.whl"}
      test -f ${app.artifacts."app-1.0.0.tar.gz"}
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
  git-submodules = runCommand "git-submodules" { } ''
    test -n "$(ls -A ${app.cache}/purifier-git/*/submodules/sample-namespace-packages)"
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

  # `poetry purifier publish` uploads the wheel and sdist that Nix built to
  # a package index.
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
        export POETRY_PURIFIER_DIST=${app.dist}
        poetry purifier publish --repository local

        kill "$server"
        test -f "$TMPDIR/index/app-1.0.0-py3-none-any.whl"
        test -f "$TMPDIR/index/app-1.0.0.tar.gz"
        cmp "$TMPDIR/index/app-1.0.0-py3-none-any.whl" ${app.artifacts."app-1.0.0-py3-none-any.whl"}
        touch "$out"
      '';
}
