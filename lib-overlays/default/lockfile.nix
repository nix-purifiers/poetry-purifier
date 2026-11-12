# SPDX-License-Identifier: MIT
#
# Reads a purifier lockfile and checks it against the project when Nix
# evaluates the build. The result is `{ lock; problems; }`. `problems` lists
# each mistake, with what is wrong and which command fixes it, and `lock`
# is the parsed lockfile, which the caller reads only when `problems` is
# empty. The mistakes are these:
#
# - a missing lockfile;
# - a file that `poetry purifier lock` did not write, or that was changed
#   after it wrote it: an edited hash, a removed entry, a truncated file, or
#   text that is not JSON. The check reads the content hash from the first
#   line and hashes the text before anything parses it, because a JSON
#   parse error would stop the evaluation without a way to name the fix.
# - a lockfile of another version, written by another version of the plugin;
# - a lockfile out of date with pyproject.toml or poetry.lock, such as after
#   `poetry add` without a relock.
#
# The plugin reads the lockfile the same way, in
# src/poetry_purifier/lockfile.py.
{
  lockFile,
  src,
  # The project's poetry.lock. By default, the one in `src`.
  poetryLock ? src + "/poetry.lock",
}:
let
  version = 8;
  relock = "Run `poetry purifier lock` to write it again.";
  name = toString lockFile;

  text = builtins.readFile lockFile;
  prefix = "{\n  \"contentHash\": \"";
  start = builtins.stringLength prefix;
  zeros = builtins.concatStringsSep "" (builtins.genList (_: "0") 64);
  digest = builtins.substring start 64 text;
  unhashed = prefix + zeros + builtins.substring (start + 64) (builtins.stringLength text) text;

  fileLine = fileName: file: "${fileName} ${builtins.hashFile "sha256" file}\n";
  inputsHash = builtins.hashString "sha256" (
    fileLine "pyproject.toml" (src + "/pyproject.toml") + fileLine "poetry.lock" poetryLock
  );

  # Reached only when the content hash matched, so the text is what the
  # plugin wrote. The context names the fix for any other failure.
  lock = builtins.addErrorContext "while reading ${name}. ${relock}" (builtins.fromJSON text);

  problem =
    if !builtins.pathExists lockFile then
      "poetry-purifier: ${name} is missing. Run `poetry purifier lock`."
    else if builtins.substring 0 start text != prefix then
      "poetry-purifier: ${name} is not a lockfile that `poetry purifier lock` wrote. ${relock}"
    else if builtins.hashString "sha256" unhashed != digest then
      "poetry-purifier: ${name} was changed after `poetry purifier lock` wrote it. ${relock}"
    else if (lock.version or null) != version then
      "poetry-purifier: ${name} has version ${
        toString (lock.version or "none")
      }, and this poetry-purifier reads version ${toString version}. ${relock}"
    else if lock.inputsHash != inputsHash then
      "poetry-purifier: ${name} is out of date with pyproject.toml or poetry.lock. Run `poetry purifier lock`."
    else
      null;
in
{
  inherit lock;
  problems = if problem == null then [ ] else [ problem ];
}
