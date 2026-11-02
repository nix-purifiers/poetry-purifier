#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Native Poetry inputs, relocatable locks, sandbox builds and real publishing."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

root = Path(__file__).resolve().parent.parent
system = subprocess.check_output(['nix', 'eval', '--impure', '--raw', '--expr', 'builtins.currentSystem'], text=True).strip()
snapshot = json.loads(subprocess.check_output(['nix', 'flake', 'metadata', '--json',
    '--no-write-lock-file', 'path:' + str(root)], text=True))['path']
with tempfile.TemporaryDirectory(prefix='poetry-native-composition-') as temporary:
    temporary = Path(temporary)
    subprocess.run(['nix-store', '--add-root', str(temporary / 'source'), '--realise', snapshot], check=True)
    poetry = Path(subprocess.check_output(['nix', 'build', str(root) + '#default', '--print-out-paths',
        '--out-link', str(temporary / 'poetry')], text=True).strip()) / 'bin/poetry'
    expression = '''let
      f = builtins.getFlake ("path:" + builtins.getEnv "POETRY_COMPOSITION_FLAKE");
      p = f.inputs.nixpkgs.legacyPackages.${builtins.currentSystem};
      api = p.poetry.python.withPackages (ps: [ ps.poetry-core ps.poetry ]);
    in p.runCommand "poetry-composition-tools" {} ''
      mkdir -p "$out/bin"
      ln -s ${p.uv}/bin/uv "$out/bin/uv"
      ln -s ${p.python3.interpreter} "$out/bin/python"
      ln -s ${api}/bin/python "$out/bin/api-python"
    '' '''
    env = dict(os.environ, POETRY_COMPOSITION_FLAKE=snapshot,
               POETRY_VIRTUALENVS_IN_PROJECT='true', POETRY_KEYRING_ENABLED='false',
               UV_PYTHON_DOWNLOADS='never')
    tools = Path(subprocess.check_output(['nix', 'build', '--impure', '--print-out-paths',
        '--out-link', str(temporary / 'tools'), '--expr', expression], text=True, env=env).strip())
    project = temporary / 'project'
    source = project / 'src/poetry_composed/__init__.py'
    source.parent.mkdir(parents=True)
    source.write_text('from app.cli import main\n')
    definition = project / 'pyproject.toml'
    definition.write_text('''[project]
name = "poetry-composed"
version = "1.0"
requires-python = ">=3.14,<3.15"
dependencies = ["app>=99"]
[project.scripts]
poetry-composed = "poetry_composed:main"
[tool.poetry]
packages = [{include = "poetry_composed", from = "src"}]
[build-system]
requires = ["poetry-core>=2.0"]
build-backend = "poetry.core.masonry.api"
''')
    original = definition.read_bytes()
    def run(*args, **kwargs):
        return subprocess.run([str(value) for value in args], cwd=project, env=env, check=True, **kwargs)
    reference = 'path:' + snapshot + '#checks.' + system + '.composition-producer'
    run(poetry, 'purifier', 'lock', '--python', tools / 'bin/python', '--input', reference)
    lockfile, native = project / 'poetry-purifier.lock.json', project / 'poetry.lock'
    lock = json.loads(lockfile.read_text())
    assert len(lock['projectInputs']) == 1 and not lock['urls'] and not lock['git']
    before = lockfile.read_bytes(), native.read_bytes()
    run(poetry, 'purifier', 'lock', '--python', tools / 'bin/python')
    assert before == (lockfile.read_bytes(), native.read_bytes()), 'relocking changed pinned inputs'
    missing = '/nix/store/' + '0' * 32 + '-unavailable-python-projection'
    old = lock['projectInputs'][0]['nativeRepository']
    native.write_text(native.read_text().replace(old, missing))
    lock['projectInputs'][0]['nativeRepository'] = missing
    api = '''import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('native_lockfile', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
import json
project = Path(sys.argv[2])
path = project / 'poetry-purifier.lock.json'
lock = json.loads(sys.argv[3])
lock['inputsHash'] = module.inputs_hash(project, project / 'poetry.lock')
module.write(path, lock)
'''
    run(tools / 'bin/api-python', '-c', api, root / 'src/poetry_purifier/lockfile.py', project, json.dumps(lock))
    bound = lockfile.read_bytes(), native.read_bytes()
    run(poetry, 'purifier', 'prefetch')
    run(poetry, 'sync')
    output = subprocess.check_output([str(poetry), 'run', 'poetry-composed'], cwd=project, env=env, text=True)
    assert output.startswith('hello, world (requests '), output
    assert bound == (lockfile.read_bytes(), native.read_bytes()), 'development rewrote committed locks'
    assert definition.read_bytes() == original
    expression = '''let
      f = builtins.getFlake ("path:" + builtins.getEnv "POETRY_COMPOSITION_FLAKE");
      p = import f.inputs.nixpkgs { overlays = [ f.overlays.default ]; };
      source = builtins.path {
        path = /. + builtins.getEnv "POETRY_COMPOSITION_PROJECT";
        name = "source";
        filter = path: type: !(builtins.elem (baseNameOf path)
          [ ".git" ".venv" ".poetry-purifier" "__pycache__" "dist" "poetry.lock" ]);
      };
      project = p.poetry-purifier.buildPoetryProject {
        src = source;
        poetryLock = /. + builtins.getEnv "POETRY_COMPOSITION_NATIVE_LOCK";
        checkGroups = [];
        checkPhase = "runHook preCheck; poetry-composed; runHook postCheck";
      };
    in [ project.tests project.projections.python ]'''
    env.update(POETRY_COMPOSITION_PROJECT=str(project), POETRY_COMPOSITION_NATIVE_LOCK=str(native))
    built = json.loads(subprocess.check_output(['nix', 'build', '--impure', '--json',
        '--out-link', str(temporary / 'pure'), '--expr', expression], text=True, env=env))
    repository = Path(next(value['outputs']['out'] for value in built
                          if value['outputs']['out'].endswith('-python-projection')))
    closure = subprocess.check_output(['nix-store', '--query', '--requisites', str(repository)], text=True)
    assert any(path.endswith('-poetry-composed') for path in closure.splitlines())
    assert any(path.endswith('-app') for path in closure.splitlines())
    downstream = temporary / 'downstream'
    run(tools / 'bin/uv', 'venv', '--python', tools / 'bin/python', downstream)
    run(tools / 'bin/uv', 'pip', 'install', '--python', downstream / 'bin/python',
        '--no-index', '--find-links', repository, 'poetry-composed')
    assert subprocess.check_output([str(downstream / 'bin/poetry-composed')], text=True).startswith('hello, world (requests ')
    run(downstream / 'bin/python', '-c',
        'from importlib.metadata import version; assert "+nix." in version("poetry-composed"); assert "+nix." in version("app"); assert "+nix." in version("greeting")')
    uploads, portfile = temporary / 'uploads', temporary / 'port'
    server = subprocess.Popen([str(tools / 'bin/python'), str(root / 'tests/upload_server.py'),
                               '0', str(uploads), str(portfile)])
    try:
        deadline = time.monotonic() + 30
        while not portfile.exists():
            assert server.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        run(poetry, 'config', '--local', 'repositories.composition', 'http://127.0.0.1:' + portfile.read_text() + '/')
        result = run(poetry, 'purifier', 'publish', '--repository', 'composition',
                     '--username', 'test', '--password', 'test', stdout=subprocess.PIPE, text=True)
        publication = Path(next(line.removeprefix('Publishing the files Nix built in ')
            for line in result.stdout.splitlines() if line.startswith('Publishing the files Nix built in ')))
        assert {path.name for path in uploads.glob('*.whl')} == {path.name for path in publication.glob('*.whl')}
        for path in publication.glob('*.whl'):
            assert path.read_bytes() == (uploads / path.name).read_bytes()
    finally:
        server.terminate()
        server.wait(timeout=10)
    assert definition.read_bytes() == original
    print('POETRY NATIVE, RELOCATABLE, PURE AND ORDINARY DOWNSTREAM COMPOSITION PASSED', flush=True)
