# SPDX-License-Identifier: MIT
#
# A real-world project: Textualize's rich at v15.0.0, pinned by commit. Its
# own poetry.lock was made by Poetry 2.1.3, so the lock was made after
# `poetry lock` with Poetry 2.5.1, which kept every locked version. The
# source here is rich with that relocked poetry.lock, and the purifier
# lockfile stays outside it. The build runs rich's whole pytest suite in the
# sandbox.
#
# The source is a derivation, so a flake check cannot hold this build: it
# would need a builder for every system just to be evaluated.
# tests/real-world-rich.sh builds it for the current system, and CI runs
# that script on both systems.
{
  # This flake, from `builtins.getFlake`.
  flake,
}:
let
  system = builtins.currentSystem;
  pkgs = flake.inputs.nixpkgs.legacyPackages.${system}.extend flake.overlays.default;
  inherit (pkgs) runCommand;

  upstream = builtins.fetchTarball {
    url = "https://github.com/Textualize/rich/archive/6ac483cbea39cab124dfd3483bba70ffafb71050.tar.gz";
    sha256 = "1qs8n1hcjxcpwbfjjlzfmywfczxzcpvjkk3ay04kdbi1lvlynkaj";
  };

  rich = pkgs.buildPoetryProject {
    src = runCommand "rich-source" { } ''
      cp -r ${upstream} "$out"
      chmod -R u+w "$out"
      cp ${./poetry.lock} "$out/poetry.lock"
    '';
    lockFile = ./poetry-purifier.lock.json;
    # test_brokenpipeerror runs `which python` and `which head`.
    nativeCheckInputs = [ pkgs.which ];
  };
in
assert
  pkgs.lib.attrNames rich.release.files == [
    "rich-15.0.0-py3-none-any.whl"
    "rich-15.0.0.tar.gz"
  ];
runCommand "real-world-rich" { } ''
  ${rich.tests}/bin/python -c 'import rich; rich.print("[bold]rich[/bold] from Nix")' | tee "$out"
  grep -q 'rich from Nix' "$out"
  test -f ${rich.release.files."rich-15.0.0-py3-none-any.whl"}
''
