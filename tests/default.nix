# SPDX-License-Identifier: MIT
{ pkgs, overlay }:
let
  inherit (pkgs) lib runCommand;
  purified = pkgs.extend overlay;

  app = purified.buildPoetryProject { src = ./fixtures/app; };
  lock = lib.importJSON ./fixtures/app/poetry-purifier.lock.json;

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
in
{
  # The project's script runs from the environment Poetry installed
  # offline. docopt comes from its sdist, which Poetry built with the
  # setuptools the lock run recorded.
  app-runs = runCommand "app-runs" { } ''
    ${app}/bin/hello --name=nix | tee "$out"
    grep -q 'hello, nix (requests 2\.' "$out"
  '';

  # `poetry build` made the project's wheel and sdist.
  app-dist = runCommand "app-dist" { } ''
    test -f ${app.dist}/app-1.0.0-py3-none-any.whl
    test -f ${app.dist}/app-1.0.0.tar.gz
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
}
