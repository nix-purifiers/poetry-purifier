# poetry-purifier

A Poetry plugin and Nix library that build a Poetry project in the Nix
sandbox, offline, from its lockfiles.

Poetry still installs, builds, tests and publishes the project.
`poetry.lock` already records the name, version and hash of every file
Poetry installs. The plugin records the rest in `poetry-purifier.lock.json`,
next to `poetry.lock`: the URL of each file, the hash of each git checkout,
and the build requirements of packages built from source. Nix fetches the
files into Poetry's cache, and Poetry runs offline from it, both in a Nix
build and on your machine.

poetry-purifier is part of the [nix-purifiers](https://nix-purifiers.github.io)
family and meets the family's contract. It fetches download entries, and
picks and names wheels, with the family's shared library,
[purifier](https://github.com/nix-purifiers/purifier).

## Quickstart

In a Poetry project, write the purifier lockfile with the Poetry from this
flake. Then add it to git, because a flake sees only the files git tracks:

```zsh
nix run github:nix-purifiers/poetry-purifier -- purifier lock
git add poetry-purifier.lock.json
```

Then add a `flake.nix`. The flake's `overlays.default` adds the builder
`buildPoetryProject` to a package set, where nixpkgs keeps its own builders:

```nix
{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.poetry-purifier.url = "github:nix-purifiers/poetry-purifier";

  outputs =
    { nixpkgs, poetry-purifier, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ poetry-purifier.overlays.default ];
      };
      project = pkgs.buildPoetryProject { src = ./.; };
    in
    {
      # The packages are the environment of the project and its scripts.
      packages.${system} = project.packages;
      checks.${system}.tests = project.tests;
      # The shell has a `poetry` with the plugin.
      devShells.${system}.default = pkgs.mkShell {
        packages = [ pkgs.poetry-purifier.poetry ];
      };
    };
}
```

`nix build '.#<project>'` builds the environment, `nix run '.#<script>'`
runs one of the project's scripts, `nix flake check` runs the tests in the
sandbox, and `nix develop` gives the shell.

A package written for `callPackage` takes the builder by name, like any
other builder:

```nix
# package.nix
{ buildPoetryProject }:
(buildPoetryProject { src = ./.; }).packages.<project>
```

`buildPoetryProject.override` replaces what the builder takes from the
package set, such as `fetchurl`, `pythonInterpreters` or the
`poetry-purifier` scope.

`buildPoetryProject` takes these arguments:

- `src`: the project.
- `lockFile`: the purifier lockfile. The default is the one in `src`.
- `fetchers`: fetchers for the purifier library, such as a `fetchurl` that
  sends credentials or uses a mirror.
- `python`: the interpreter. The default is the nixpkgs interpreter of the
  Python version the lock was made with.
- `groups`: the dependency groups of the environment. The default is
  `[ "main" ]`.
- `checkGroups`: the groups the tests need besides `main`. The default is
  `[ "dev" ]`. The tests run in a separate environment with those groups and
  the built wheel, and the default `checkPhase` runs `pytest`. A test that
  runs a system tool needs that tool in `nativeCheckInputs`.

Any other argument goes to `stdenv.mkDerivation`. The result has these
attributes, all known by name when Nix evaluates the build:

- `packages.<project>`: a virtual environment with the project and the
  dependencies of its `groups`, with the project's scripts in `bin/`.
- `packages.<script>`: one script in `bin/`, for each script of
  `[project.scripts]`, `[project.gui-scripts]` and `[tool.poetry.scripts]`.
- `tests`: the environment with the tests run in the sandbox.
- `release`: the wheel and the sdist that `poetry build` made.
  `release.files."<file name>"` is the path of each.
- `fetches`: the download entries of the lock, for every supported system.

`lib.poetry-purifier.readLock { src, lockFile }` reads both lockfiles into
the family's shape, `{ purifier, entries, outputs, problems }`, for tools
that read every purifier, such as an SBOM generator. An entry that only some
systems fetch, such as a wheel for one CPU, has `systems`. The builder
checks this shape with the purifier library's `checkLock`.

## Development loop

| Step | Command |
|---|---|
| Install for development | `poetry install` in `nix develop` |
| Run a script or the tests | `poetry run <script>`, `poetry run pytest` |
| Add a dependency | `poetry add <package>`, then `poetry purifier lock` |
| Update dependencies | `poetry purifier update [<package>...]` |
| Relock after editing `pyproject.toml` | `poetry purifier lock` |
| Check the lockfiles in CI | `poetry purifier lock --check` |
| Build and test in the sandbox | `nix build`, `nix flake check` |

In a project with an up-to-date purifier lockfile, `poetry install`,
`poetry sync` and `poetry build` have Nix fetch the locked files for this
machine, link them into Poetry's cache, and run offline. Path dependencies
in develop mode stay editable. `POETRY_PURIFIER=0` turns this off.

`poetry purifier lock` keeps the resolution of `poetry.lock`. When
`poetry.lock` is missing or out of date with `pyproject.toml`, it runs
`poetry lock` first, which keeps the versions `poetry.lock` already has.
Then it records what `poetry.lock` cannot. It copies the project to a
temporary directory and runs `poetry install` with every group and extra,
then `poetry build`, with an empty cache. That run shows which build
requirements Poetry installs. Then it locks every platform:

- It asks Poetry's repositories for the URL of every file that `poetry.lock`
  names.
- A registry package that some platform builds from its sdist has its build
  requirements read from the `[build-system]` table of the sdist.
- Each set of build requirements is resolved for all platforms by `poetry
  lock` in a throwaway project. A build requirement that `poetry.lock` also
  locks is held at the version `poetry.lock` has.
- A git dependency is hashed with `nix-prefetch-git`, with its submodules,
  because Poetry clones git dependencies with their submodules.

A relock of unchanged inputs writes the same bytes. It keeps the
resolutions of build requirements that the current lockfile has, unless
they disagree with `poetry.lock`. `--update-build-requirements` resolves
them again. `--force` locks even when the lockfile is up to date.

`poetry purifier update` runs `poetry update --lock`, for every package or
for the named ones, and then relocks the purifier lockfile against the
result, in one run.

The commands find the purifier lockfile next to `pyproject.toml`. For a
lockfile kept elsewhere, they take `--lockfile <path>` or the environment
variable `POETRY_PURIFIER_LOCKFILE`, and the option takes precedence. A
dev shell can set the variable to the path its flake passes as `lockFile`.

`poetry purifier publish --repository <name>` has Nix build the `release`
from the lockfiles, then runs Poetry's own `publish` on its files.
Repositories, credentials and the other options of `poetry publish` work as
they do there.

## Working offline

```zsh
poetry purifier prefetch
```

`poetry purifier prefetch` has Nix fetch every file the lockfiles name for
this machine, which covers every group and extra and the build
requirements of sdists. It also fetches the interpreter the lock was made
with. It links the files into Poetry's cache, keeps them and the
interpreter alive with a garbage-collector root at
`.poetry-purifier/prefetch`, and points the project's virtual environment
at that interpreter. After a prefetch, `poetry install`, `poetry sync`,
`poetry run` and `poetry build` work with the network cut off.

## Editor

Editors and language servers, such as pyright, read the project's virtual
environment, which the offline install fills like any other `poetry
install`. They download nothing.

setuptools installs an editable path dependency through an import hook by
default, which pyright and other type checkers do not follow. This happens
with or without the purifier. Poetry can ask setuptools for its compat mode
instead, which writes a plain path entry, in the project's `poetry.toml`:

```toml
[installer.build-config-settings.<package>]
editable_mode = "compat"
```

## Troubleshooting

Each of these fails in the command and when Nix evaluates the build, and
each message names the command that fixes it.

| Mistake | Message |
|---|---|
| No purifier lockfile | "… is missing. Run `poetry purifier lock`." |
| `pyproject.toml` or `poetry.lock` changed without a relock | "… is out of date with pyproject.toml or poetry.lock. Run `poetry purifier lock`." `poetry install` warns and downloads as usual. |
| The lockfile edited by hand, such as a changed hash or a removed entry | "… was changed after `poetry purifier lock` wrote it. Run `poetry purifier lock` to write it again." |
| A truncated file, or one `poetry purifier lock` did not write | "… is not a lockfile that `poetry purifier lock` wrote. Run `poetry purifier lock` to write it again." |
| A lockfile of another format version | "… has version N, and this poetry-purifier reads version M. Run `poetry purifier lock` to write it again." |
| A file of `poetry.lock` without a URL in the lockfile | "… has no URL for …. Run `poetry purifier lock`." |
| A download or clone that is not in the lockfiles | "Poetry tried to download … which is not in …. Run `poetry purifier lock` to lock it." |
| `poetry build` makes other files than the lockfile names | "poetry build made …, not … as poetry-purifier.lock.json records. Run `poetry purifier lock`." |

Nix reads the content hash from the first line of the lockfile and checks
it before it parses the JSON, so a malformed file fails with the message
above rather than a parse error.

## Supported language repositories

Every source `poetry.lock` can name works, in the Nix build and offline.

| Source in `poetry.lock` | `poetry.lock` has | The purifier lockfile adds |
|---|---|---|
| PyPI, or another index (`legacy`) | Each file's name and hash | Each file's URL, in `urls` |
| `url` (a wheel or an sdist) | The URL and the hash | Nothing |
| `git`, with submodules | The URL and the commit | The hash of the checkout, in `git` |
| `directory` or `file` (a path) | The path | Nothing, because the files are in the project |

The lockfile also holds what no entry of `poetry.lock` covers. `builds`
maps each set of build requirements to the packages it resolves to, with
their environment markers. `buildPackages` holds the URL and hash of the
files of each build requirement that `poetry.lock` does not lock. `dist`
holds the file names of the wheel and sdist of the lock run, so Nix knows
the `release` files by name. `python` is the Python version of the lock
run. `inputsHash` is the SHA-256 of `pyproject.toml` and `poetry.lock`, and
`contentHash` is the SHA-256 of the lockfile with that hash zeroed. Nix
computes everything else, such as where Poetry's cache keeps each file,
from these fields and `poetry.lock`.

A checkout from Nix, and a project copied into the Nix sandbox, have no
`.git` directory, so a build backend that reads its version from git, such
as setuptools-scm or hatch-vcs, finds none. The plugin runs each build of a
git checkout, a path dependency or the project itself with
`SETUPTOOLS_SCM_PRETEND_VERSION` set to the version the lock records. That
version comes from `poetry.lock` for dependencies, and from the sdist name
of the lock run for the project.

The purifier does not support these:

- A hash other than SHA-256 in `poetry.lock`.
- A build requirement that a build backend adds at build time, through the
  `get_requires_for_build_wheel` hook, for an sdist that only another
  platform builds. Only running the backend shows these requirements, so
  the lockfile records them only for the builds of the lock run's own
  platform. The Nix build stops with an error that names the missing set.
- Systems other than x86_64-linux and aarch64-linux, the systems of the
  nix-purifiers family. Wheel selection reads manylinux and `linux_*`
  platform tags.

The plugin changes how Poetry looks up links, downloads files, clones git
repositories and installs build requirements. These are Poetry internals,
so the plugin works with the Poetry version in this flake, 2.5.1, and may
not work with another.

## Examples

### rich

[rich](https://github.com/Textualize/rich) is a Python library for styled
terminal output. Clone it and write its purifier lockfile. The command runs
`poetry lock` first when `poetry.lock` is out of date, so add both files to
git:

```zsh
git clone https://github.com/Textualize/rich
cd rich
nix run github:nix-purifiers/poetry-purifier -- purifier lock
git add poetry.lock poetry-purifier.lock.json
```

Then add this `flake.nix` next to its `pyproject.toml`. The tests of rich
run `which`, which the build sandbox does not have, so the flake adds it:

```nix
{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.poetry-purifier.url = "github:nix-purifiers/poetry-purifier";

  outputs =
    { nixpkgs, poetry-purifier, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ poetry-purifier.overlays.default ];
      };
      rich = pkgs.buildPoetryProject {
        src = ./.;
        nativeCheckInputs = [ pkgs.which ];
      };
    in
    {
      packages.${system}.default = rich.packages.rich;
      checks.${system}.tests = rich.tests;
      devShells.${system}.default = pkgs.mkShell {
        packages = [ pkgs.poetry-purifier.poetry ];
      };
    };
}
```

`nix build` builds the library, and `nix flake check` runs its test suite in
the sandbox. For development, run these in `nix develop`:

```zsh
poetry purifier prefetch
poetry install --extras jupyter
poetry run pytest
```

## Credits

poetry-purifier carries the design of
[sbt-nix-backend](https://github.com/clhodapp/sbt-nix-backend) over to
Poetry. sbt-nix-backend made Nix the dependency and build backend of sbt,
with a lockfile of every download, a sandboxed build from it, an offline
interactive mode, and a publish step that publishes what Nix built. The
nix-purifiers family takes its idea from nixcloud's cargo work (libnix).
