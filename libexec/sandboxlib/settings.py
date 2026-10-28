"""Read and validate the user-local configuration."""
from __future__ import annotations
from pathlib import Path
import re
import tomllib
from .common import config_dir

DEFAULT = {
  'memory': '8g',
  'pids_limit': 512,
  'network': 'bridge',
  'gpu_devices': [],
  'forward_env': [],
  'mount_global_skills': True,
  'opencode_config': 'apps/opencode.jsonc',
  'venv_volume': True,
}
IMAGES = {
  'preparer': 'sandbox/preparer:v1',
  'base': 'sandbox/base:v1',
  'python-xpu': 'sandbox/python-xpu:v1',
  'opencode': 'sandbox/opencode:v1',
  'opencode-xpu': 'sandbox/opencode-xpu:v1',
}

def load():
    path = config_dir() / 'config.toml'
    obj = DEFAULT.copy()
    images = IMAGES.copy()
    if path.is_file():
        with path.open('rb') as f:
            raw = tomllib.load(f)
        unknown = set(raw) - {'sandbox', 'images'}
        if unknown:
            raise ValueError(f'unknown config sections: {sorted(unknown)}')
        sc = raw.get('sandbox', {})
        im = raw.get('images', {})
        if not isinstance(sc, dict) or not isinstance(im, dict):
            raise ValueError('config sections must be TOML tables')
        if set(sc) - set(DEFAULT) or set(im) - set(IMAGES):
            raise ValueError('unknown sandbox or image configuration key')
        obj.update(sc)
        images.update(im)
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
    for field in ('mount_global_skills', 'venv_volume'):
        if type(obj[field]) is not bool:
            raise ValueError(field + ' must be boolean')
    if not isinstance(obj['opencode_config'], str):
        raise ValueError('opencode_config must be a string')
    for k, v in images.items():
        if not isinstance(v, str) or not v.strip() or '\n' in v:
            raise ValueError(f'invalid image tag: {k}')
    obj['images'] = images
    return obj


def app_mounts(cfg, app):
    if app != 'opencode':
        return []
    result = []
    if cfg['mount_global_skills']:
        for sub, dst in (
            ('.config/opencode/skills', '/root/.config/opencode/skills'),
            ('.agents/skills', '/root/.agents/skills'),
            ('.claude/skills', '/root/.claude/skills'),
        ):
            src = Path.home() / sub
            if src.is_dir():
                result.append((src.resolve(), dst))
    value = cfg['opencode_config']
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = config_dir() / path
        if path.exists():
            if not path.is_file():
                raise ValueError('opencode_config must be a file')
            result.append((path.resolve(), '/root/.config/opencode/opencode.jsonc'))
    return result
