"""Host-side artifact copies and persistent per-instance bind mounts."""
from __future__ import annotations
from contextlib import ExitStack
import ctypes
import errno
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile

from .common import mount_spec


def container_path(value, root):
    path = PurePosixPath(value)
    if (not path.is_absolute() or '..' in path.parts or
            path.parts[:2] != ('/', root)):
        raise ValueError(f'destination must be within /{root}')
    return path


def mount_target(value):
    path = container_path(value, 'mnt')
    if len(path.parts) < 3:
        raise ValueError('mount target must be beneath /mnt/, not /mnt itself')
    mount_spec('/placeholder', str(path))
    return str(path)


def overlaps(left, right):
    a, b = PurePosixPath(left), PurePosixPath(right)
    return a == b or a in b.parents or b in a.parents


def instance_mounts(meta):
    result = []
    for entry in meta.get('mounts', []):
        target = mount_target(entry['target'])
        if type(entry['read_only']) is not bool:
            raise ValueError('mount read_only must be boolean')
        if any(overlaps(target, old['target']) for old in result):
            raise ValueError('overlapping sandbox mount targets')
        result.append(dict(source=entry['source'], target=target,
                           read_only=entry['read_only']))
    return result


def mount_source(value):
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_dir() and not path.is_file():
        raise ValueError('mount source must be a regular file or directory')
    mount_spec(path, '/mnt/check')
    return path


def _copy(source, destination):
    """Do not follow source symlinks, including replacements during traversal."""
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        mode = os.fstat(fd).st_mode
        if stat.S_ISDIR(mode):
            destination.mkdir()
            for name in os.listdir(fd):
                _copy(f'/proc/self/fd/{fd}/{name}', destination / name)
        elif stat.S_ISREG(mode):
            with os.fdopen(os.dup(fd), 'rb') as src, destination.open('xb') as dst:
                shutil.copyfileobj(src, dst)
            destination.chmod(stat.S_IMODE(mode) & 0o777)
        else:
            raise ValueError('send accepts only regular files and directories, not special files')
    finally:
        os.close(fd)


def send(instance, source, destination):
    source = Path(os.path.abspath(Path(source).expanduser()))
    if source.is_symlink():
        raise ValueError('send does not accept source symlinks')
    target = container_path(destination, 'workspace')
    parts = target.parts[2:]
    if '.git' in (*parts, source.name) or not source.name:
        raise ValueError('send may not write .git metadata or copy a filesystem root')
    repo = instance / 'repo'
    if source.is_dir() and (source.resolve() == instance.resolve() or
                            source.resolve() in instance.resolve().parents):
        raise ValueError('cannot send a directory containing the sandbox instance')
    # Stage outside the container-visible tree; failures leave no partial artifact.
    with tempfile.TemporaryDirectory(prefix='.send-', dir=instance) as temp:
        staged = Path(temp) / 'artifact'
        _copy(source, staged)
        with ExitStack() as stack:
            def open_dir(name, parent=None):
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=parent)
                stack.callback(os.close, fd)
                return fd

            fd = open_dir(repo)
            for part in parts:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
                fd = open_dir(part, fd)
            # Linux renameat2 publishes atomically without replacing any target,
            # even when a running container races to create that target.
            libc = ctypes.CDLL(None, use_errno=True)
            rename = libc.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                               ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(staged), fd, os.fsencode(source.name), 1):
                code = ctypes.get_errno()
                if code == errno.EEXIST:
                    raise ValueError(f'destination already exists: {target / source.name}')
                raise OSError(code, os.strerror(code))
    return str(target / source.name)
