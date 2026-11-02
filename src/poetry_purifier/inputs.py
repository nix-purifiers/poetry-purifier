# SPDX-License-Identifier: MIT
"""Bind native Poetry dependencies to immutable Nix-built wheels."""
from contextlib import contextmanager
import json
import os
from pathlib import Path


_modules = {}
_roots = set()
_old_roots = []

def _bind(dependency):
    from poetry.core.packages.file_dependency import FileDependency
    module = _modules.get(dependency.name)
    if module:
        replacement = FileDependency(dependency.pretty_name, Path(module["wheel"]),
            groups=dependency.groups, optional=dependency.is_optional(), extras=dependency.extras)
        replacement.marker = dependency.marker
        replacement._in_extras = dependency.in_extras
        return replacement
    return dependency

def bind(poetry):
    _roots.add(poetry.package.name)
    for group in poetry.package._dependency_groups.values():
        group._dependencies = [_bind(dependency) for dependency in group._dependencies]
        group._poetry_dependencies = [_bind(dependency) for dependency in group._poetry_dependencies]

def command(event, event_name, dispatcher):
    if event.command.name not in ("lock", "update", "install", "sync") or not _modules:
        return
    bind(event.command.poetry)

def enable():
    configuration = os.environ.get('POETRY_PURIFIER_PROJECT_INPUTS')
    if not configuration:
        return
    global _modules, _old_roots
    data = json.loads(Path(configuration).read_text())
    _modules = data['modules']
    _old_roots = data['oldRepositories']
    from poetry.puzzle.provider import Provider
    original = Provider._get_dependencies_with_overrides
    if getattr(original, '_purifier_inputs', False):
        return

    def dependencies(self, requirements, package):
        result = original(self, requirements, package)
        return [_bind(dependency) for dependency in result] if self._package.name in _roots else result

    dependencies._purifier_inputs = True
    Provider._get_dependencies_with_overrides = dependencies

    from poetry.packages.locker import Locker
    from poetry.packages.direct_origin import DirectOrigin
    original_locked = Locker._get_locked_package

    def locked(self, info, with_dependencies=True):
        source = info.get('source', {})
        module = _modules.get(info['name'])
        location = (self.lock.parent / source.get('url', '')).resolve()
        provided = module and source.get('type') == 'file' and any(
            location.is_relative_to(root) for root in _old_roots)
        if not provided:
            return original_locked(self, info, with_dependencies)
        wheel = Path(module['wheel'])
        native = DirectOrigin.get_package_from_file(wheel)
        rebound = dict(info, version=module['version'], files=native.files,
                       source=dict(source, url=str(wheel)))
        rebound.pop('dependencies', None)
        package = original_locked(self, rebound, with_dependencies=False)
        if with_dependencies:
            for dependency in native.requires:
                package.add_dependency(_bind(dependency))
            package.extras = native.extras
        return package

    Locker._get_locked_package = locked


@contextmanager
def locking(command, refresh=False):
    import sys
    from poetry_purifier import lockfile, lockfile_option
    root = command.poetry.pyproject_path.parent
    target = lockfile_option.path(command)
    try:
        previous = lockfile.read(target).get('projectInputs', [])
    except (OSError, lockfile.LockfileError):
        previous = []
    references = command.option('input') or [entry['reference'] for entry in previous]
    command._project_inputs = []
    if not references:
        yield
        return
    runtime = Path(os.environ['POETRY_PURIFIER_POETRY']) / 'share/poetry-purifier'
    tools = os.environ.get('POETRY_PURIFIER_PROJECT_INPUTS_TOOLS', str(runtime / 'project-input-tools'))
    sys.path.insert(0, tools)
    import project_inputs
    sys.path.insert(0, os.environ.get('POETRY_PURIFIER_PYTHON_BINDINGS', str(runtime / 'python-bindings')))
    import python_bindings
    records = project_inputs.pin(references, previous, refresh)
    state = target.parent / '.poetry-purifier'
    state.mkdir(exist_ok=True)
    (state / '.gitignore').write_text('*\n')
    views = project_inputs.python_views(records, state / 'project-inputs')
    configuration = python_bindings.write(views, [entry['nativeRepository'] for entry in previous], state / 'bindings')
    command._project_inputs = [entry | {'nativeRepository': str(view['repository'])}
                               for entry, view in zip(records, views, strict=True)]
    before = os.environ.get('POETRY_PURIFIER_PROJECT_INPUTS')
    os.environ['POETRY_PURIFIER_PROJECT_INPUTS'] = str(configuration)
    enable()
    try:
        yield
    finally:
        if before is None:
            os.environ.pop('POETRY_PURIFIER_PROJECT_INPUTS', None)
        else:
            os.environ['POETRY_PURIFIER_PROJECT_INPUTS'] = before
        _modules.clear()
