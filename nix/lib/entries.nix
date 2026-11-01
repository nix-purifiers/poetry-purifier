# SPDX-License-Identifier: MIT
#
# The download entries of every package, by "name==version", from the
# purifier lockfile and poetry.lock, which Nix reads with fromTOML. The
# plugin computes the same entries in src/poetry_purifier/lockfile.py.
#
# A file from a package index has its name and hash in poetry.lock and its
# URL in the `urls` of the lockfile. A URL dependency has its URL and hash
# in poetry.lock. A git dependency has its URL and commit in poetry.lock and
# the hash of its checkout in the `git` of the lockfile. A build package
# that poetry.lock does not lock has its files in the `buildPackages` of the
# lockfile.
#
# Each file lies where Poetry's artifact cache keeps it: under the SHA-256
# of the JSON object of its URL and, for a file from a package index, its
# SHA-256, which is what Poetry hashes.
{
  lock,
  poetryLock,
}:
let
  inherit (builtins)
    elemAt
    filter
    hashString
    listToAttrs
    match
    substring
    toJSON
    ;

  locked = (builtins.fromTOML (builtins.readFile poetryLock)).package or [ ];

  hexOf =
    sri:
    builtins.convertHash {
      hash = sri;
      toHashFormat = "base16";
    };
  sriOfPoetry =
    poetryHash:
    let
      parts = match "sha256:([0-9a-f]+)" poetryHash;
    in
    if parts == null then
      throw "poetry-purifier: poetry.lock records the hash ${poetryHash}, and the Poetry purifier needs sha256"
    else
      builtins.convertHash {
        hash = elemAt parts 0;
        hashAlgo = "sha256";
        toHashFormat = "sri";
      };

  cachePath =
    keyParts: fileName:
    let
      key = hashString "sha256" (toJSON keyParts);
    in
    "artifacts/${substring 0 2 key}/${substring 2 2 key}/${substring 4 2 key}/${substring 6 58 key}/${fileName}";

  fileEntry =
    {
      url,
      fileName,
      hash,
      withHash ? true,
    }:
    {
      path = cachePath (
        { inherit url; } // (if withHash then { sha256 = hexOf hash; } else { })
      ) fileName;
      inherit url hash;
      mirrors = [ ];
    };

  gitEntry = url: rev: {
    path = "purifier-git/${substring 0 16 (hashString "sha256" url)}-${rev}";
    git = {
      inherit url rev;
      submodules = true;
      leaveDotGit = false;
      commitObject = null;
    };
    # Null when the lockfile has no hash for the checkout, which read-lock.nix
    # reports.
    hash = lock.git."${url}@${rev}" or null;
  };

  entriesOf =
    info:
    let
      source = info.source or { };
      kind = source.type or null;
      files = info.files or [ ];
    in
    if kind == "git" then
      [ (gitEntry source.url source.resolved_reference) ]
    else if kind == "url" then
      map (
        f:
        fileEntry {
          inherit (source) url;
          fileName = f.file;
          hash = sriOfPoetry f.hash;
          withHash = false;
        }
      ) files
    else
      map (
        f:
        fileEntry {
          # Null when the lockfile has no URL for the file, which
          # read-lock.nix reports.
          url = lock.urls.${f.file} or null;
          fileName = f.file;
          hash = sriOfPoetry f.hash;
        }
      ) files;

  fromPoetryLock = listToAttrs (
    map
      (info: {
        name = "${info.name}==${info.version}";
        value = entriesOf info;
      })
      (
        filter (
          info:
          !builtins.elem (info.source.type or null) [
            "directory"
            "file"
          ]
        ) locked
      )
  );

  fromBuildPackages = builtins.mapAttrs (
    _:
    map (
      f:
      fileEntry {
        inherit (f) url hash;
        fileName = baseNameOf f.url;
      }
    )
  ) lock.buildPackages;
in
fromPoetryLock // fromBuildPackages
