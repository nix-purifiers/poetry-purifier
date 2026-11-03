# SPDX-License-Identifier: MIT
#
# lib.poetry-purifier.readLock: reads poetry.lock and the purifier lockfile
# into the lock in memory of the purifier contract:
#
# - `purifier`: "poetry-purifier".
# - `entries`: the download entries of the purifier library that some
#   supported system fetches. An entry that only some systems fetch, such as
#   a wheel for one CPU, has `systems`, the list of those Nix systems.
# - `outputs`: the names of the builder's `packages`. The project's
#   environment has `kind = "environment"`, and each script of the project
#   has `kind = "script"`.
# - `problems`: a stale, tampered, malformed, missing or incomplete
#   lockfile, each with the command that fixes it.
#
# The builder passes `pythonVersion` and, on Linux, `glibcVersion` or, on
# macOS, `macosVersion`, and then the entries of the systems of that OS are
# the files their builds fetch. A caller that passes neither gets the files
# for the Python version of the lock and for any glibc or macOS: every
# wheel that installs with a recent one, and each package's sdists beside
# its wheels.
{
  lib,
  # The shared purifier library, `lib.purifier`.
  purifier,
}:
{
  src,
  # The purifier lockfile. By default, the one in `src`.
  lockFile ? src + "/poetry-purifier.lock.json",
  # The project's poetry.lock. By default, the one in `src`. A copy kept
  # outside the project takes the place of the one in `src`.
  poetryLock ? src + "/poetry.lock",
  # The Python version, such as "3.14". By default, the one of the lock run.
  pythonVersion ? null,
  # The glibc version of the Linux systems, such as "2.40".
  glibcVersion ? null,
  # The oldest macOS version of the darwin systems, such as "14.0".
  macosVersion ? null,
}:
let
  read = import ./lockfile.nix { inherit lockFile src poetryLock; };
  inherit (read) lock;
  lockName = toString lockFile;

  packages = import ./entries.nix { inherit lock poetryLock; };
  allEntries = lib.concatLists (lib.attrValues packages);

  incomplete =
    map (
      entry:
      "poetry-purifier: ${lockName} has no URL for ${baseNameOf entry.path}. Run `poetry purifier lock`."
    ) (lib.filter (entry: !(entry ? git) && entry.url == null) allEntries)
    ++ map (
      entry:
      "poetry-purifier: ${lockName} has no hash for ${entry.git.url} at ${entry.git.rev}. Run `poetry purifier lock`."
    ) (lib.filter (entry: entry ? git && entry.hash == null) allEntries);

  problems = if read.problems != [ ] then read.problems else incomplete;

  systems = [
    "x86_64-linux"
    "aarch64-linux"
    "aarch64-darwin"
  ];
  selectedFor =
    system:
    let
      darwin = lib.hasSuffix "-darwin" system;
    in
    import ./select.nix {
      inherit packages;
      installs = purifier.wheels (
        {
          pythonVersion = if pythonVersion != null then pythonVersion else lock.python;
        }
        // (
          if darwin then
            {
              # macOS wheel tags name the CPU arm64.
              arch = "arm64";
              # Newer than any macOS, when the caller does not name one.
              macosVersion = if macosVersion != null then macosVersion else "999.0";
            }
          else
            {
              arch = lib.head (lib.splitString "-" system);
              # Newer than any glibc, when the caller does not name one.
              glibcVersion = if glibcVersion != null then glibcVersion else "2.999";
            }
        )
      );
      withSdists = (if darwin then macosVersion else glibcVersion) == null;
    };
  pathsFor = lib.genAttrs systems (system: map (entry: entry.path) (selectedFor system));
  selected = lib.unique (lib.concatMap selectedFor systems);
  entries = map (
    entry:
    let
      on = lib.filter (system: lib.elem entry.path pathsFor.${system}) systems;
    in
    if on == systems then entry else entry // { systems = on; }
  ) selected;

  pyproject = builtins.fromTOML (builtins.readFile (src + "/pyproject.toml"));
  poetrySection = pyproject.tool.poetry or { };
  projectName = pyproject.project.name or poetrySection.name;
  scripts =
    lib.attrNames (pyproject.project.scripts or { })
    ++ lib.attrNames (pyproject.project.gui-scripts or { })
    ++ lib.attrNames (poetrySection.scripts or { });
in
{
  purifier = "poetry-purifier";
  entries = if problems == [ ] then entries else [ ];
  outputs =
    lib.genAttrs scripts (_: {
      kind = "script";
    })
    // {
      ${projectName}.kind = "environment";
    };
  inherit problems;
}
