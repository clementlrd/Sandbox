"""Build-time Python stack inventory; run with the runtime's base interpreter."""
from importlib import metadata
import json
import platform
import re
import sys
import sysconfig

packages = {}
for dist in metadata.distributions():
    name = dist.metadata.get('Name')
    if name:
        name = re.sub(r'[-_.]+', '-', name).lower()
        if name in packages:
            continue
        packages[name] = {'version': dist.version,
                          'metadata': dist.read_text('METADATA') or dist.read_text('PKG-INFO'),
                          'site': str(dist.locate_file('').resolve())}
print(json.dumps({'version': 1, 'python': sys.version,
                  'platform': sysconfig.get_platform(),
                  'implementation': platform.python_implementation(),
                  'site': sysconfig.get_paths()['purelib'], 'packages': packages}, sort_keys=True))
