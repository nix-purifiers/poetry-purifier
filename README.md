# poetry-purifier

A Poetry plugin and Nix library that build a Poetry project in the Nix
sandbox, offline, from a lockfile. You do not commit generated Nix files.

Poetry still installs and builds the project. The plugin records the files
Poetry downloads in `poetry-purifier.lock.json`, next to `poetry.lock`. Nix
fetches those files into Poetry's cache, and Poetry runs offline from it,
both in a Nix build and on your machine.

The design follows [sbt-nix-backend](https://github.com/clhodapp/sbt-nix-backend),
which is part of the [nix-purifiers](https://nix-purifiers.github.io) family.

## Lock

Run the lock with the Poetry from this flake:

```zsh
nix run github:nix-purifiers/poetry-purifier -- purifier lock
```

`poetry.lock` has to exist and be up to date, because the purifier records
the files of the packages that `poetry.lock` already names. Commit
`poetry-purifier.lock.json` beside it.

`poetry purifier lock` copies the project to a temporary directory and runs
Poetry there with an empty cache and the network on. It runs `poetry install`
with every group and extra, then `poetry build`. That run shows which
packages Poetry installs to build sdists. The command then asks Poetry's
repositories for the URL of every file in `poetry.lock`, for every platform.
`poetry.lock` records only file names and hashes, so these URLs are what the
purifier adds.

`--python` names the interpreter of the lock run. Its major and minor
version are the default interpreter of the Nix build.

## Build

The flake's `overlays.default` adds `buildPoetryProject` to a package set:

```nix
# package.nix
{ buildPoetryProject }:
buildPoetryProject { src = ./.; }
```

The result is a virtual environment with the project and the dependencies of
its `main` group, and with the project's scripts in `bin/`. Its `dist` output
holds the wheel and the sdist that `poetry build` made.

`buildPoetryProject` takes these arguments besides the usual ones of
`stdenv.mkDerivation`:

- `src`: the project.
- `lockFile`: the purifier lockfile. The default is the one in `src`.
- `python`: the interpreter. The default is nixpkgs' `python3XY` for the
  version the lock was made with.
- `groups`: the dependency groups to install. The default is `[ "main" ]`.

For each package, the build fetches the wheels that install with that
Python on the build's system. When no wheel installs, it fetches the
package's sdist, and Poetry builds it with the build requirements the lock
run recorded. One lockfile serves every Linux system.

## Interactive use

`nix develop github:nix-purifiers/poetry-purifier` gives a shell where
`poetry` has the plugin. In a project with an up-to-date
`poetry-purifier.lock.json`, `poetry install`, `poetry sync` and
`poetry build` have Nix fetch the locked files for this machine, link them
into Poetry's cache, and run offline. The Nix result is a garbage-collector
root in `.poetry-purifier/` in the project. When `pyproject.toml` or
`poetry.lock` changed since the lock, Poetry warns and downloads as usual.
`POETRY_PURIFIER=0` turns interactive mode off.

## The lockfile

`packages` maps each `name==version` to the locked files of that release.
Each file is a download entry `{ path, url, hash }`, where `path` is where
Poetry's artifact cache keeps the file and `hash` is its SRI hash. `builds`
maps each set of build requirements to the packages Poetry installed for
it. `project` names the project and its scripts, `python` is the Python
version of the lock run, and `inputsHash` covers `pyproject.toml` and
`poetry.lock`.

## Limits

- Git dependencies are not supported yet. A lock with one fails.
- A build requirement must have a wheel for the build's system. Building a
  build requirement from its sdist is not supported.
- Build requirements are resolved on the system of the lock run. A system
  where Poetry would pick different build requirements needs its own lock.
- Wheel selection covers Linux (manylinux and `linux_*` wheels).
- The plugin patches Poetry internals and is tested with Poetry 2.5.

## Tests

```zsh
nix flake check
```

The checks build `tests/fixtures/app` offline and run its script, check its
`dist` output, and check that one lock selects the right wheels for x86_64
and aarch64.
