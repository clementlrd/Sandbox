"""Read and validate user-local sandbox configuration, including generic mounts."""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import re
import tomllib
from .common import config_dir

DEFAULT = {
    'memory': '8g',
    'pids_limit': 512,
    'network': 'bridge',
    'gpu_devices': [],
    'forward_env': [],
    'venv_volume': True,
    'shared_tools': True,
}
SETUP_DEFAULT = {
    'recipes_dir': '~/.config/sandbox/recipes',
    'confirm_host_execution': True,
}
# Legacy config keys are accepted so upgrading does not discard users' skills.
LEGACY = {'mount_global_skills', 'opencode_config'}
RUNTIME_FIELDS = {'image', 'shell', 'devices', 'venv_volume', 'dockerfile', 'context'}
RUNTIME_NAME = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')
IMAGES = {
    'preparer': 'sandbox/preparer:v1',
    'base': 'sandbox/base:v1',
    'python-xpu': 'sandbox/python-xpu:v1',
}


def load():
    path = config_dir() / 'config.toml'
    obj = DEFAULT.copy()
    images = IMAGES.copy()
    extra_mounts = []
    setup = SETUP_DEFAULT.copy()
    runtime_settings = {}
    if path.is_file():
        with path.open('rb') as f:
            raw = tomllib.load(f)
        unknown = set(raw) - {'sandbox', 'images', 'mounts', 'setup', 'runtimes'}
        if unknown:
            raise ValueError(f'unknown config sections: {sorted(unknown)}')
        sc = raw.get('sandbox', {})
        im = raw.get('images', {})
        extra_mounts = raw.get('mounts', [])
        setup_raw = raw.get('setup', {})
        runtime_settings = raw.get('runtimes', {})
        if not isinstance(runtime_settings, dict):
            raise ValueError('runtimes must be TOML tables ([runtimes.NAME])')
        if not isinstance(setup_raw, dict) or set(setup_raw) - set(SETUP_DEFAULT):
            raise ValueError('unknown setup configuration key')
        setup.update(setup_raw)
        if not isinstance(sc, dict) or not isinstance(im, dict):
            raise ValueError('config sections must be TOML tables')
        if set(sc) - set(DEFAULT) - LEGACY:
            raise ValueError('unknown sandbox configuration key')
        if set(im) - set(IMAGES) - {'opencode', 'opencode-xpu'}:
            raise ValueError('unknown image configuration key')
        # Ignore legacy OpenCode image tags: application images are no longer built.
        obj.update({k: v for k, v in sc.items() if k in DEFAULT})
        images.update({k: v for k, v in im.items() if k in IMAGES})
        obj['_legacy'] = {k: sc[k] for k in LEGACY if k in sc}
    else:
        obj['_legacy'] = {}

    if not isinstance(obj['memory'], str) or not re.fullmatch(r'\d+[bkmgBKMG]?', obj['memory']):
        raise ValueError('memory must look like 8g')
    if type(obj['pids_limit']) is not int or not 1 <= obj['pids_limit'] <= 100000:
        raise ValueError('pids_limit must be an integer in [1,100000]')
    if obj['network'] not in ('bridge', 'none'):
        raise ValueError('network must be bridge or none')
    if not isinstance(obj['gpu_devices'], list) or any(
        not isinstance(s, str) or not re.fullmatch(r'/dev/dri/renderD\d+', s)
        for s in obj['gpu_devices']):
        raise ValueError('gpu_devices must be /dev/dri/renderD* device paths')
    if len(set(obj['gpu_devices'])) != len(obj['gpu_devices']):
        raise ValueError('gpu_devices must be unique')
    if not isinstance(obj['forward_env'], list) or any(
        not isinstance(s, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', s)
        for s in obj['forward_env']):
        raise ValueError('forward_env must be an array of environment variable names')
    for field in ('venv_volume', 'shared_tools'):
        if type(obj[field]) is not bool:
            raise ValueError(field + ' must be boolean')
    for field, value in obj['_legacy'].items():
        if field == 'mount_global_skills' and type(value) is not bool:
            raise ValueError('mount_global_skills must be boolean')
        if field == 'opencode_config' and not isinstance(value, str):
            raise ValueError('opencode_config must be a string')
    for k, v in images.items():
        if not isinstance(v, str) or not v.strip() or '\n' in v:
            raise ValueError(f'invalid image tag: {k}')
    # Keep the original [images] tags for existing installations. Runtime
    # definitions are optional overrides or entirely new named profiles.
    runtimes = {}
    for name in ('base', 'python-xpu'):
        runtimes[name] = {
            'image': images[name], 'shell': '/bin/bash',
            'devices': list(obj['gpu_devices']) if name == 'python-xpu' else [],
            'venv_volume': obj['venv_volume'],
            'dockerfile': None, 'context': None,
        }
    for name, entry in runtime_settings.items():
        if not isinstance(name, str) or not RUNTIME_NAME.fullmatch(name) or name == 'preparer':
            raise ValueError(f'invalid runtime name: {name!r}')
        if not isinstance(entry, dict) or set(entry) - RUNTIME_FIELDS:
            raise ValueError(f'invalid fields in runtime {name!r}')
        if name not in runtimes and 'image' not in entry:
            raise ValueError(f'runtime {name!r} needs an image')
        base = runtimes.get(name, {
            'image': None, 'shell': '/bin/bash', 'devices': [],
            'venv_volume': False, 'dockerfile': None, 'context': None,
        }).copy()
        base.update(entry)
        runtimes[name] = base
    for name, rt in runtimes.items():
        if not isinstance(rt['image'], str) or not rt['image'].strip() or '\n' in rt['image']:
            raise ValueError(f'invalid image for runtime {name!r}')
        shell = rt['shell']
        shell_path = PurePosixPath(shell) if isinstance(shell, str) else None
        if (shell_path is None or not shell_path.is_absolute() or '..' in shell_path.parts or
                str(shell_path) == '/'):
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
    obj['runtimes'] = runtimes

    if not isinstance(setup['recipes_dir'], str) or not setup['recipes_dir'].strip():
        raise ValueError('setup.recipes_dir must be a nonempty path')
    if type(setup['confirm_host_execution']) is not bool:
        raise ValueError('setup.confirm_host_execution must be boolean')
    obj['setup'] = setup
    if not isinstance(extra_mounts, list):
        raise ValueError('mounts must be an array of TOML tables ([[mounts]])')
    parsed_mounts = []
    for entry in extra_mounts:
        if not isinstance(entry, dict) or set(entry) - {'source', 'target', 'optional'}:
            raise ValueError('each [[mounts]] must contain only source, target, optional')
        source, target, optional = (entry.get('source'), entry.get('target'), entry.get('optional', False))
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
    obj['images'] = images
    return obj


def read_only_mounts(cfg):
    """Resolve configured host mounts. Existing OpenCode configs remain supported."""
    specs = list(cfg.get('mounts', []))
    legacy = cfg.get('_legacy', {})
    if legacy.get('mount_global_skills', False):
        specs += [
            ('~/.config/opencode/skills', '/root/.config/opencode/skills', True),
            ('~/.agents/skills', '/root/.agents/skills', True),
            ('~/.claude/skills', '/root/.claude/skills', True),
        ]
    config = legacy.get('opencode_config', '')
    if config:
        path = Path(config).expanduser()
        if not path.is_absolute():
            path = config_dir() / path
        specs.append((str(path), '/root/.config/opencode/opencode.jsonc', True))
    result = []
    targets = {}
    for source, target, optional in specs:
        path = Path(source).expanduser()
        if not path.exists():
            if optional:
                continue
            raise ValueError(f'configured read-only mount does not exist: {path}')
        if not path.is_dir() and not path.is_file():
            raise ValueError(f'unsupported mount source: {path}')
        resolved = path.resolve(strict=True)
        if target in targets:
            if targets[target] == resolved:
                continue  # Same mount specified by both new and legacy configs.
            raise ValueError(f'conflicting read-only mount target: {target}')
        targets[target] = resolved
        result.append((resolved, target))
    return result
