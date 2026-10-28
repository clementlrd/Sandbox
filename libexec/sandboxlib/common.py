"""Shared paths, safe Git execution, and storage helpers."""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess

NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,47}$")


def config_dir():
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'sandbox'


def state_dir():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'sandbox'


def resource_dir():
    return Path(os.environ.get('SANDBOX_RESOURCE_DIR') or
                (Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'sandbox'))


def instances_dir():
    return state_dir() / 'instances'


def check_name(name):
    if not NAME_RE.fullmatch(name) or name in ('.', '..'):
        raise ValueError('sandbox name must be 1-48 letters, numbers, underscores, dots or hyphens')
    return name


def private_dir(p):
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    p.chmod(0o700)
    return p


def git_env(anonymized=True):
    """Never let preparer processes inherit host Git/SSH configuration."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(('GIT_', 'SSH_'))}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
               GIT_CONFIG_SYSTEM='/dev/null', GIT_TERMINAL_PROMPT='0',
               GIT_LFS_SKIP_SMUDGE='1', GIT_OPTIONAL_LOCKS='0')
    if anonymized:
        env.update(GIT_AUTHOR_NAME='Sandbox', GIT_AUTHOR_EMAIL='sandbox@invalid',
                   GIT_COMMITTER_NAME='Sandbox', GIT_COMMITTER_EMAIL='sandbox@invalid')
    return env


def invoke(args, *, cwd=None, capture=False, env=None, input_bytes=None):
    p = subprocess.run([str(a) for a in args], cwd=cwd, env=env, input=input_bytes,
                       stdout=subprocess.PIPE if capture else None, check=True)
    return p.stdout if capture else b''


def git(repo, *args, capture=True, env=None, input_bytes=None):
    return invoke(['git', '-C', str(repo), *args], capture=capture, env=env,
                  input_bytes=input_bytes)


def git_text(repo, *args, env=None):
    return git(repo, *args, env=env).decode('utf-8', errors='replace').strip()


def mount_spec(src, dst, readonly=False):
    src = str(src)
    if any(c in src for c in ',\n\r') or any(c in dst for c in ',\n\r'):
        raise ValueError('mount path contains a Docker --mount delimiter or newline')
    return f'type=bind,src={src},dst={dst}' + (',readonly' if readonly else '')
