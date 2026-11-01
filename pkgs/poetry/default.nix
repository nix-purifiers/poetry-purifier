# SPDX-License-Identifier: MIT
#
# Poetry with the purifier plugin. POETRY_PURIFIER_REALIZE names a Nix file
# that builds the cache directory of a lockfile's files, with this package
# set's nixpkgs, so that interactive use fetches through Nix.
{
  # nixpkgs' Poetry, taken from `pkgs`: in this package scope `poetry`
  # names this wrapper.
  pkgs,
  plugin,
  makeWrapper,
  symlinkJoin,
  writeText,
  path,
  system,
}:
let
  inherit (pkgs) poetry;
  withPlugin = poetry.withPlugins (_: [ plugin ]);
  realize = writeText "poetry-purifier-realize.nix" ''
    { ... }@args:
    import ${../../nix/lib}/realize.nix (
      { pkgs = import ${path} { system = "${system}"; }; } // args
    )
  '';
in
symlinkJoin {
  name = "poetry-purifier-${withPlugin.version or poetry.version}";
  paths = [ withPlugin ];
  nativeBuildInputs = [ makeWrapper ];
  postBuild = ''
    wrapProgram "$out/bin/poetry" --set POETRY_PURIFIER_REALIZE ${realize}
  '';
  meta.mainProgram = "poetry";
}
