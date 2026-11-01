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
  byte for byte with the committed lockfile, then locks a copy from scratch.
- `conformance-update.sh` runs `poetry purifier update` on `fixtures/update`,
  whose `poetry.lock` holds an old idna, and checks both lockfiles.
- `conformance-rebuild.sh` builds `fixtures/app` twice with `nix build
  --rebuild`.
- `conformance-offline.sh` runs `poetry purifier prefetch` on `fixtures/app`,
  cuts the network, and runs the everyday loop and pyright.
- `real-world-rich.sh` builds rich at its pinned commit and runs its test
  suite. Rich's own `poetry.lock` was made by Poetry 2.1.3, so the source
  tree is rich with a `poetry.lock` relocked by Poetry 2.5.1 (see
  `real-world/rich/`). That tree is a derivation, so the build runs from a
  script and not from a flake check.
