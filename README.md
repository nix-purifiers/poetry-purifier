# poetry-purifier

A Poetry plugin and Nix library that build a Poetry project in the Nix
sandbox, offline, from a lockfile. You do not commit generated Nix files.

Poetry still installs, builds and publishes the project. The plugin records
the files Poetry downloads in `poetry-purifier.lock.json`, next to
`poetry.lock`. Nix fetches those files into Poetry's cache, and Poetry runs
offline from it, both in a Nix build and on your machine.

poetry-purifier carries the design of
[sbt-nix-backend](https://github.com/clhodapp/sbt-nix-backend) over to
Poetry. sbt-nix-backend made Nix the dependency and build backend of sbt,
with a lockfile of every download, a sandboxed build from it, an offline
interactive mode, and a publish step that publishes what Nix built.
poetry-purifier is part of the [nix-purifiers](https://nix-purifiers.github.io)
family.

## Lock

Run the lock with the Poetry from this flake:

```zsh
nix run github:nix-purifiers/poetry-purifier -- purifier lock
```

`poetry.lock` has to exist and be up to date, because the purifier records
the files of the packages that `poetry.lock` already names. Commit
`poetry-purifier.lock.json` beside it. `poetry purifier lock --check` checks
that the lockfile is up to date with `pyproject.toml` and `poetry.lock`,
offline.

`poetry purifier lock` copies the project to a temporary directory and runs
Poetry there with an empty cache and the network on. It runs `poetry install`
with every group and extra, then `poetry build`, and records what Poetry
downloads and which build requirements it installs to build packages from
source. Then it locks every platform:

- It asks Poetry's repositories for the URL of every file that `poetry.lock`
  names, for every platform. `poetry.lock` records only file names and
  hashes, so these URLs are what the purifier adds.
- A registry package that some platform installs from its sdist, because it
  has no wheel that installs everywhere, has its build requirements read
  from the `[build-system]` table of its sdist.
- Each set of build requirements is resolved for all platforms by `poetry
  lock` in a throwaway project, and so are the build requirements of build
  requirements.
- A git dependency is hashed with `nix-prefetch-git`, with its submodules,
  because Poetry clones git dependencies with their submodules.

`--python` names the interpreter of the lock run. Its major and minor
version are the default interpreter of the Nix build.

## Dependency sources

Every source `poetry.lock` can name works, in the Nix build and in
interactive use. The test fixture has one of each.

| Source in `poetry.lock` | Locked as | Offline, Poetry gets it from |
|---|---|---|
| PyPI, or another index (`legacy`) | The URL and hash of every locked file | Its artifact cache, filled by Nix |
| `url` (a wheel or an sdist) | The file the lock run downloaded | Its artifact cache, filled by Nix |
| `git`, with submodules | The checkout, as `fetchgit` fetches it | A copy of that checkout in place of a clone |
| `directory` or `file` (a path) | Nothing: the files are in the project | The project |

A checkout from Nix has no `.git` directory. For packages from git and from
paths, the plugin sets `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<name>` to the
version `poetry.lock` records, so build backends that read their version
from git (setuptools-scm, hatch-vcs) still build.

## Build

The flake's `overlays.default` adds `buildPoetryProject` to a package set:

```nix
# package.nix
{ buildPoetryProject }:
buildPoetryProject { src = ./.; }
```

The result is a virtual environment with the project and the dependencies of
its `main` group, and with the project's scripts in `bin/`. Path
dependencies in develop mode are installed as regular packages, because the
build directory does not outlive the build.

Nix knows every output of the project by name when it evaluates the build,
with no generated Nix files and no import-from-derivation, and the result
has one attribute per output:

- `bins.<script>` is a package with one script in `bin/`, for each script
  of `[project.scripts]`, `[project.gui-scripts]` and
  `[tool.poetry.scripts]` in `pyproject.toml`. `nix run` runs it.
- `artifacts."<file name>"` is the path of the wheel or the sdist that
  `poetry build` made. The lock run records their file names. A compiled
  wheel gets the CPython tags of the build's Python and the platform tag of
  the build's system: `linux_<cpu>` from setuptools-style backends, or
  `manylinux_2_<glibc minor>_<cpu>` from poetry-core. The build fails if
  `poetry build` makes other files.

```nix
let
  app = buildPoetryProject { src = ./.; };
in
{
  packages.default = app;
  packages.hello = app.bins.hello;
}
```

`buildPoetryProject` takes these arguments besides the usual ones of
`stdenv.mkDerivation`:

- `src`: the project.
- `lockFile`: the purifier lockfile. The default is the one in `src`.
- `python`: the interpreter. The default is nixpkgs' `python3XY` for the
  version the lock was made with.
- `groups`: the dependency groups to install. The default is `[ "main" ]`.
- `fetchurl`: the fetcher of locked files, for an index that needs
  credentials or a mirror.

For each package, the build fetches the wheels that install with that
Python on the build's system. When no wheel installs, it fetches the
package's sdist, and Poetry builds it with the build requirements the lock
resolved. One lockfile serves x86_64-linux and aarch64-linux.

## Interactive use

`nix develop github:nix-purifiers/poetry-purifier` gives a shell where
`poetry` has the plugin. In a project with an up-to-date
`poetry-purifier.lock.json`, `poetry install`, `poetry sync` and
`poetry build` have Nix fetch the locked files for this machine, link them
into Poetry's cache, and run offline. Path dependencies in develop mode stay
editable. The Nix result is a garbage-collector root in `.poetry-purifier/`
in the project. When `pyproject.toml` or `poetry.lock` changed since the
lock, Poetry warns and downloads as usual. `POETRY_PURIFIER=0` turns
interactive mode off.

## Publish

```zsh
poetry purifier publish --repository <name>
```

`poetry purifier publish` has Nix build the project's wheel and sdist from
the lockfile, then runs Poetry's own `publish` on it. Repositories, credentials
and the other options of `poetry publish` work as they do there, and nothing
is built outside the Nix sandbox.

## The lockfile

`packages` maps each `name==version` to download entries. A file entry is
`{ path, url, mirrors, hash }`, where `path` is where Poetry's artifact cache
keeps the file, `mirrors` lists other URLs that serve the same file, and
`hash` is its SRI hash. Package indexes serve each file at one URL, so
`mirrors` is empty in the entries the lock run writes. A git entry is
`{ path, git = { url, rev, submodules }, hash }`, where `hash` is what
`nix-prefetch-git` reports. `builds` maps each set of build requirements to
the packages it resolves to, with their environment markers. `dist` holds the
file names of the wheel and sdist that the lock run built. `python` is the
Python version of the lock run, and `inputsHash` covers `pyproject.toml` and
`poetry.lock`.

## Limitations

- A build backend can add build requirements at build time, through the
  `get_requires_for_build_wheel` hook. Only running the backend shows them,
  so the lockfile records them only for the builds the lock run performs on
  its own platform. For an sdist that only another platform builds, the
  lockfile records the `[build-system]` requirements of the sdist. If the
  backend asks for more on that platform, the Nix build stops with an error
  that names the missing set.
- The plugin changes how Poetry looks up links, downloads files, clones git
  repositories and installs build requirements. These are Poetry internals,
  so the plugin is tested with one Poetry version, 2.5.1, the one in the
  flake.
- The Nix builds are Linux only: x86_64-linux and aarch64-linux, the systems
  of the nix-purifiers family. Wheel selection reads manylinux and `linux_*`
  platform tags. The lockfile itself holds the files of every platform.

## Tests

```zsh
nix flake check
tests/interactive.sh
tests/lock-run.sh
```

The flake checks build `tests/fixtures/app` offline and run its script,
check its outputs by name, check that the path dependency is installed
without links into the build directory, check that the git checkout has its
submodule, check that one lock selects the right wheels for x86_64 and
aarch64, check that the committed lock is up to date, and publish the
Nix-built files to a local package index. `tests/interactive.sh` installs
and builds the fixture offline outside the sandbox. `tests/lock-run.sh`
locks the fixture against the real package index and GitHub, and builds the
result with `poetry purifier publish --dry-run`. CI runs all three.
