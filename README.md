# poetry-purifier

poetry-purifier keeps Poetry's development workflow on locked,
Nix-resolved dependencies. Use the normal build and test commands in your
working tree; building the project itself in the Nix sandbox is an
optional next step.

Poetry still installs, builds, tests and publishes the project.
`poetry.lock` already records the name, version and hash of every file
Poetry installs. The plugin records the rest in `poetry-purifier.lock.json`,
next to `poetry.lock`: the URL of each file, the hash of each git checkout,
and the build requirements of packages built from source. Nix fetches the
files into Poetry's cache, and Poetry runs offline from it, both in a Nix
build and on your machine.

It fetches download entries, and picks and names wheels, with the shared
[purifier](https://github.com/nix-purifiers/purifier) library.

## Quickstart

From the project root:

```zsh
nix shell 'github:nix-purifiers/poetry-purifier'
poetry purifier lock
poetry purifier prefetch
poetry install
eval "$(poetry env activate)"
pytest
```

`prefetch` creates Poetry’s virtual environment with the locked Python;
`poetry install` populates it. `poetry env activate` activates it using
Poetry’s environment settings.

Commit the lockfiles that the lock command writes, including the build
tool's own lockfile when it has one. Keep using the project's usual build,
test and editor commands with this setup. Nix provides the locked
dependencies; the development commands above run outside the Nix sandbox.
No project Nix files are needed for this workflow.

Sandboxed builds of the whole project are an optional
[next step](#building-the-project-with-nix).

The shell provides the `poetry` wrapper. Use it for the locked
development workflow. With no purifier lockfile, it warns on standard
error and runs the ordinary tool with impure resolution.

For persistent use, add these packages through your usual Nix configuration.


## Development loop

| Step | Command |
|---|---|
| Install for development | `poetry install` after the Quickstart setup |
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

The commands find both lockfiles next to `pyproject.toml`. A project can
instead keep its build external: the flake that calls the builder keeps the
lockfiles outside the repository and passes them as `lockFile` and
`poetryLock`. The commands take the same paths as `--lockfile <path>` and
`--tool-lockfile <path>`, or as the environment variables
`POETRY_PURIFIER_LOCKFILE` and `POETRY_PURIFIER_TOOL_LOCKFILE`, and an option
takes precedence over its variable. With a `--tool-lockfile`, `lock` and
`update` write `poetry.lock` there and leave the copy in the repository as it
is, and `lock` starts from the copy in the repository when the outside one
does not exist yet. A dev shell can set both variables to the paths its flake
passes to the builder.

`poetry purifier publish --repository <name>` has Nix build the `release`
from the lockfiles, then runs Poetry's own `publish` on its files.
Repositories, credentials and the other options of `poetry publish` work as
they do there.

## Published plugin

Versioned plugin wheels are available in the [package index](https://nix-purifiers.github.io/poetry-purifier/simple/poetry-purifier/)
and as [release assets](https://github.com/nix-purifiers/poetry-purifier/releases).
Install a wheel URL with `poetry self add WHEEL_URL`.
The published plugin uses the Nix runtime pinned by its release and warns
that the plugin itself was resolved impurely. The Quickstart wrapper
provides pure plugin resolution.

## Dependencies from another purified project

```zsh
nix shell 'github:nix-purifiers/poetry-purifier'
poetry purifier lock \
  --input 'github:owner/library/<rev>#library'
poetry purifier prefetch
poetry install
poetry run pytest
```

The producer exposes `projections.python`, the same wheelhouse format used
by uv, PDM and pip. Repeat `--input` for more producers. Poetry uses their
wheels without editing project dependency declarations. The purifier lock
records the Nix source pins and omits supplied downloads. Relocking retains
those pins; `poetry purifier update` refreshes them.

Prefetch rebinds native lockfile paths for this machine without rewriting
committed files. The builder exposes `projections.python` on the project
and its package, including runtime dependencies and local path packages.
Their Nix-derived versions and dependency metadata work with ordinary
installers. Composed publishing uploads that wheelhouse through Poetry's
usual repository configuration. Use an index accepting local versions,
or consume the wheels with `--find-links`.

## Building the project with Nix

Once the native development workflow is working, you can also build and
test the whole project in the Nix sandbox. For this step, keep a flake or
another Nix build definition in the project's repository, or maintain an
external packaging repository that points at its sources and lockfiles.
The builder consumes the same lockfiles as the development workflow.

`poetry purifier lock` writes `poetry-purifier.lock.json` next to
`poetry.lock`. We recommend committing both lockfiles to the repository, and
the builder looks for them there. The `overlays.default` of this flake adds
one attribute to a package set, the scope `poetry-purifier`. The scope holds
the builder `buildPoetryProject`, the Poetry with the plugin as `poetry`, and
the `plugin`.

The authors of a project can package it with a flake. The flake can live in
the repository of the project or in another one, and it calls the builder
directly:

```nix
{
  inputs.nixpkgs.url =
    "github:NixOS/nixpkgs/nixos-unstable";
  inputs.poetry-purifier.url =
    "github:nix-purifiers/poetry-purifier";

  outputs =
    { nixpkgs, poetry-purifier, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ poetry-purifier.overlays.default ];
      };
      project =
        pkgs.poetry-purifier.buildPoetryProject {
        src = ./.;
      };
    in
    {
      # The packages are the environment of the project
      # and its scripts.
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
sandbox.

A function that an overlay or a flake passes to `callPackage` takes the scope
by name, as the argument `poetry-purifier`.
`poetry-purifier.overrideScope` replaces what everything in the scope shares,
such as the `poetry` that the builder uses. `buildPoetryProject.override`
replaces what the builder takes from the package set, such as `fetchurl` or
`pythonInterpreters`.

`buildPoetryProject` takes these arguments:

- `src`: the project.
- `lockFile`: the purifier lockfile. The default is the one in `src`. A
  `lockFile` outside `src` takes the place of any purifier lockfile in `src`.
- `poetryLock`: the `poetry.lock` of the project. The default is the one in
  `src`. A copy outside `src` takes the place of the one in `src`, in the
  lock check and in the build.
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

### One-off builds without a project Nix file

For an ad hoc build, the builder can also be evaluated directly from the
project root. Replace the example package name with one from your project:

```zsh
nix build --impure --expr 'let
  purifier = builtins.getFlake "github:nix-purifiers/poetry-purifier";
  pkgs = import purifier.inputs.nixpkgs {
    system = builtins.currentSystem;
    overlays = [ purifier.overlays.default ];
  };
in
(pkgs.poetry-purifier.buildPoetryProject { src = ./.; })
.packages.my-project'
```

This uses the purifier's pinned nixpkgs, matching the lock command above.
To build with ambient `<nixpkgs>` from your Nix search path instead,
replace `import purifier.inputs.nixpkgs` with `import <nixpkgs>`.
Both forms need no Nix files in your project. The build tool and language
versions must match the lockfile; with ambient nixpkgs, lock with the
purifier from that package set or pass a compatible toolchain to the builder.

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
| `pyproject.toml` or `poetry.lock` changed without a relock | "… is out of date with pyproject.toml or poetry.lock. Run `poetry purifier lock`." `poetry install` stops and asks for a relock. |
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

Poetry reads the path of a `directory` or `file` source relative to the
`poetry.lock` it reads. A project that keeps `poetry.lock` outside the
repository needs those paths to resolve from the directory of that copy.

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
- Systems other than x86_64-linux, aarch64-linux and aarch64-darwin. On
  aarch64-darwin, a project whose own wheel has compiled code: purifier's
  `wheelName` names Linux wheels only, and a lock of such a project made
  on a Mac does not build on Linux.

The plugin changes how Poetry looks up links, downloads files, clones git
repositories and installs build requirements. These are Poetry internals,
so the plugin works with the Poetry version in this flake, 2.5.1, and may
not work with another.

## Examples

For day-to-day work on these projects, use the native-tool setup and
commands in [Quickstart](#quickstart) and [Development loop](#development-loop).
The Nix expressions below show optional sandboxed packaging of those
projects.

### rich

[rich](https://github.com/Textualize/rich) is a Python library for styled
terminal output. Clone it, write its purifier lockfile, and build it. The
lock command runs `poetry lock` first when `poetry.lock` is out of date:

```zsh
nix shell 'github:nix-purifiers/poetry-purifier'
git clone https://github.com/Textualize/rich
cd rich
poetry purifier lock
nix build --impure --expr 'let
  purifier = builtins.getFlake "github:nix-purifiers/poetry-purifier";
  pkgs = import purifier.inputs.nixpkgs {
    system = builtins.currentSystem;
    overlays = [ purifier.overlays.default ];
  };
in
(pkgs.poetry-purifier.buildPoetryProject { src = ./.; })
.packages.rich'
```

The tests of rich run `which`, which the build sandbox does not have. This
build runs the test suite of rich in the sandbox with `which` added:

```zsh
nix build --impure --expr 'let
  purifier = builtins.getFlake "github:nix-purifiers/poetry-purifier";
  pkgs = import purifier.inputs.nixpkgs {
    system = builtins.currentSystem;
    overlays = [ purifier.overlays.default ];
  };
in
(pkgs.poetry-purifier.buildPoetryProject {
  src = ./.;
  nativeCheckInputs = [ pkgs.which ];
}).tests'
```

For development, run Poetry from this flake. It installs offline from the
locked files:

```zsh
nix shell 'github:nix-purifiers/poetry-purifier'
poetry purifier prefetch
poetry install --extras jupyter
poetry run pytest
```

## Credits

The purifier idea was inspired by nixcloud's
[cargo (libnix)](https://github.com/nixcloud/cargo).
