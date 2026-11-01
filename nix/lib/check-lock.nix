# SPDX-License-Identifier: MIT
#
# Reads a purifier lockfile and checks it against the project when Nix
# evaluates the build, so that a mistake stops the evaluation with a message
# that says what is wrong and which command fixes it:
#
# - a lockfile of another format, written by another version of the plugin;
# - a lockfile changed after `poetry purifier lock` wrote it, such as an
#   edited hash or a removed entry, which its `contentHash` no longer covers;
# - a lockfile out of date with pyproject.toml or poetry.lock, such as after
#   `poetry add` without a relock, which its `inputsHash` no longer covers.
#
# The plugin computes both hashes the same way, in
# src/poetry_purifier/lockfile.py.
{
  lockFile,
  src,
  # The project's poetry.lock, which is in `src` unless a build keeps it
  # elsewhere.
  poetryLockFile ? src + "/poetry.lock",
}:
let
  format = 6;
  relock = "Run `poetry purifier lock` to write it again.";
  lock = builtins.fromJSON (builtins.readFile lockFile);

  contentHash = builtins.hashString "sha256" (builtins.toJSON (removeAttrs lock [ "contentHash" ]));
  fileLine = name: file: "${name} ${builtins.hashFile "sha256" file}\n";
  inputsHash = builtins.hashString "sha256" (
    fileLine "pyproject.toml" (src + "/pyproject.toml") + fileLine "poetry.lock" poetryLockFile
  );
in
if (lock.format or null) != format then
  throw "poetry-purifier: ${toString lockFile} has format ${
    toString (lock.format or "none")
  }, and this poetry-purifier reads format ${toString format}. ${relock}"
else if (lock.contentHash or null) != contentHash then
  throw "poetry-purifier: ${toString lockFile} was changed after `poetry purifier lock` wrote it. ${relock}"
else if lock.inputsHash != inputsHash then
  throw "poetry-purifier: ${toString lockFile} is out of date with pyproject.toml or poetry.lock. Run `poetry purifier lock`."
else
  lock
