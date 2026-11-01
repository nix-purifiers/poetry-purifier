# SPDX-License-Identifier: MIT
{ pkgs, overlay }:
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
        inherit lock arch;
        pythonVersion = "3.14";
        glibcVersion = "2.40";
      }
    );
  charsetWheels = arch: lib.filter (lib.hasPrefix "charset_normalizer-") (selectFor arch);

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
    grep -q 'hello, nix (requests 2\..*six 1\.17\.0, git-package-example' "$out"
  '';

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
  # this one: its CPython tags, and the platform tag of setuptools-style
  # backends and of poetry-core.
  wheel-name =
    let
      name = import ../nix/lib/wheel-name.nix {
        inherit lib;
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
