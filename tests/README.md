# Tests

For maintainers. The README addresses users and leaves these out.

```zsh
nix flake check
tests/conformance-relock.sh
tests/conformance-update.sh
tests/conformance-rebuild.sh
tests/conformance-offline.sh
tests/real-world-rich.sh
```

`nix flake check` runs the plugin's unit tests, the conformance checks that
need no network (`conformance-stale`, `conformance-tampered`,
`conformance-missing`, `conformance-malformed` and `conformance-external`),
the build of `fixtures/app` with its tests, `pythonpath-unset`, the wheel
selection checks, `read-lock`, and `publish` against a local package index.

The scripts are the checks that need the network. CI runs each as a job of
the same name on x86_64-linux and aarch64-linux.

- `conformance-relock.sh` relocks `fixtures/app` and compares the result
  byte for byte with the committed lockfile. It does the same with both
  lockfiles kept outside the project through `--lockfile` and
  `--tool-lockfile`, and checks that the `poetry.lock` in the project stays
  as it is. Then it locks a copy from scratch.
- `conformance-update.sh` runs `poetry purifier update` on `fixtures/update`,
  whose `poetry.lock` holds an old idna, and checks both lockfiles. It then
  updates with both lockfiles kept outside the project and checks that the
  copy in the project stays at the old version.
- `conformance-rebuild.sh` builds `fixtures/app` twice with `nix build
  --rebuild`.
- `conformance-offline.sh` runs `poetry purifier prefetch` on `fixtures/app`,
  cuts the network, and runs the everyday loop and pyright. It then moves
  both lockfiles out of the project, sets `POETRY_PURIFIER_LOCKFILE` and
  `POETRY_PURIFIER_TOOL_LOCKFILE`, and runs the loop again.

`conformance-external` builds `fixtures/app` from a lockfile kept outside its
source, and from a source that also has no `poetry.lock`, with the
`poetryLock` argument naming the copy outside it.
- `real-world-rich.sh` builds rich at its pinned commit and runs its test
  suite. Rich's own `poetry.lock` was made by Poetry 2.1.3, so the source
  tree is rich with a `poetry.lock` relocked by Poetry 2.5.1 (see
  `real-world/rich/`). That tree is a derivation, so the build runs from a
  script and not from a flake check.
