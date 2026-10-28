"""Strict, application-agnostic sandbox settings. No compatibility shims."""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import re
import tomllib
from .common import config_dir

DEFAULT = {
    'memory': '8g',
    'pids_limit': 512,
    'network': 'bridge',
    'forward_env': [],
    'shared_tools': True,
    'default_runtime': 'base',
}
SETUP_DEFAULT = {
    'recipes_dir': '~/.config/sandbox/recipes',
    'confirm_host_execution': True,
}
RUNTIME_FIELDS = {'image', 'shell', 'devices', 'venv_volume', 'dockerfile', 'context'}
RUNTIME_NAME = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')


def load():
    path = config_dir() / 'config.toml'
    if not path.is_file():
        raise ValueError(f'configuration not found: {path}; run install.sh')
    with path.open('rb') as f:
        raw = tomllib.load(f)
    if not isinstance(raw, dict) or set(raw) - {'sandbox', 'preparer', 'runtimes', 'mounts', 'setup'}:
        raise ValueError('config accepts only [sandbox], [preparer], [runtimes.NAME], [[mounts]], [setup]')

    obj = DEFAULT.copy()
    sc = raw.get('sandbox', {})
    if not isinstance(sc, dict) or set(sc) - set(DEFAULT):
        raise ValueError('unknown [sandbox] key; migrate config using docs/migration.md')
    obj.update(sc)

    if not isinstance(obj['memory'], str) or not re.fullmatch(r'\d+[bkmgBKMG]?', obj['memory']):
        raise ValueError('memory must look like 8g')
    if type(obj['pids_limit']) is not int or not 1 <= obj['pids_limit'] <= 100000:
        raise ValueError('pids_limit must be an integer in [1,100000]')
    if obj['network'] not in ('bridge', 'none'):
        raise ValueError('network must be bridge or none')
    if not isinstance(obj['forward_env'], list) or any(
            not isinstance(s, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', s)
            for s in obj['forward_env']):
        raise ValueError('forward_env must be an array of environment variable names')
    if type(obj['shared_tools']) is not bool:
        raise ValueError('shared_tools must be boolean')
    if not isinstance(obj['default_runtime'], str) or not RUNTIME_NAME.fullmatch(obj['default_runtime']):
        raise ValueError('default_runtime must be a valid runtime name')

    prep = raw.get('preparer')
    if not isinstance(prep, dict) or set(prep) != {'image'}:
        raise ValueError('[preparer] must contain image; see examples/config.toml')
    if not isinstance(prep['image'], str) or not prep['image'].strip() or '\n' in prep['image']:
        raise ValueError('preparer.image must be a nonempty Docker image tag')
    obj['preparer'] = prep.copy()

    runtime_settings = raw.get('runtimes')
    if not isinstance(runtime_settings, dict) or not runtime_settings:
        raise ValueError('at least one [runtimes.NAME] section is required')
    runtimes = {}
    for name, entry in runtime_settings.items():
        if not isinstance(name, str) or not RUNTIME_NAME.fullmatch(name) or name == 'preparer':
            raise ValueError(f'invalid runtime name: {name!r}')
        if not isinstance(entry, dict) or set(entry) - RUNTIME_FIELDS or 'image' not in entry:
            raise ValueError(f'runtime {name!r} needs image and only supported fields')
        rt = {
            'image': None, 'shell': '/bin/bash', 'devices': [],
            'venv_volume': False, 'dockerfile': None, 'context': None,
        }
        rt.update(entry)
        if not isinstance(rt['image'], str) or not rt['image'].strip() or '\n' in rt['image']:
            raise ValueError(f'invalid image for runtime {name!r}')
        shell = rt['shell']
        shell_path = PurePosixPath(shell) if isinstance(shell, str) else None
        if shell_path is None or not shell_path.is_absolute() or '..' in shell_path.parts or str(shell_path) == '/':
            raise ValueError(f'invalid shell for runtime {name!r}')
        if type(rt['venv_volume']) is not bool:
            raise ValueError(f'invalid venv_volume for runtime {name!r}')
        devices = rt['devices']
        if (not isinstance(devices, list) or len(devices) > 16 or
                any(not isinstance(dev, str) or not dev.startswith('/dev/') or
                    '..' in PurePosixPath(dev).parts or '\n' in dev for dev in devices) or
                len(set(devices)) != len(devices)):
            raise ValueError(f'invalid device paths for runtime {name!r}')
        for option in ('dockerfile', 'context'):
            if rt[option] is not None and (not isinstance(rt[option], str) or
                    not rt[option].strip() or '\n' in rt[option]):
                raise ValueError(f'invalid {option} for runtime {name!r}')
        if rt['context'] and not rt['dockerfile']:
            raise ValueError(f'runtime {name!r} needs dockerfile when context is set')
        runtimes[name] = rt
    if obj['default_runtime'] not in runtimes:
        raise ValueError('sandbox.default_runtime must name a configured runtime')
    obj['runtimes'] = runtimes

    setup = SETUP_DEFAULT.copy()
    setup_raw = raw.get('setup', {})
    if not isinstance(setup_raw, dict) or set(setup_raw) - set(SETUP_DEFAULT):
        raise ValueError('unknown [setup] configuration key')
    setup.update(setup_raw)
    if not isinstance(setup['recipes_dir'], str) or not setup['recipes_dir'].strip():
        raise ValueError('setup.recipes_dir must be a nonempty path')
    if type(setup['confirm_host_execution']) is not bool:
        raise ValueError('setup.confirm_host_execution must be boolean')
    obj['setup'] = setup

    extra_mounts = raw.get('mounts', [])
    if not isinstance(extra_mounts, list):
        raise ValueError('mounts must be an array of TOML tables ([[mounts]])')
    parsed_mounts = []
    for entry in extra_mounts:
        if not isinstance(entry, dict) or set(entry) - {'source', 'target', 'optional'}:
            raise ValueError('each [[mounts]] must contain only source, target, optional')
        source, target, optional = entry.get('source'), entry.get('target'), entry.get('optional', False)
        if not isinstance(source, str) or not source.strip() or not isinstance(target, str):
            raise ValueError('mount source and target must be nonempty strings')
        if type(optional) is not bool:
            raise ValueError('mount optional must be boolean')
        dest = PurePosixPath(target)
        if not dest.is_absolute() or '..' in dest.parts or str(dest) == '/':
            raise ValueError('mount target must be an absolute normalized container path')
        if target.startswith(('/workspace/', '/opt/tools/', '/opt/venvs/')) or target in ('/workspace', '/opt/tools', '/opt/venvs'):
            raise ValueError('mount target conflicts with sandbox-managed paths')
        parsed_mounts.append((source, target, optional))
    obj['mounts'] = parsed_mounts
    return obj


def read_only_mounts(cfg):
    """Resolve only declared, generic read-only mounts."""
    result = []
    targets = set()
    for source, target, optional in cfg.get('mounts', []):
        path = Path(source).expanduser()
        if not path.exists():
            if optional:
                continue
            raise ValueError(f'configured read-only mount does not exist: {path}')
        if not path.is_dir() and not path.is_file():
            raise ValueError(f'unsupported mount source: {path}')
        if target in targets:
            raise ValueError(f'duplicate read-only mount target: {target}')
        targets.add(target)
        result.append((path.resolve(strict=True), target))
    return result
