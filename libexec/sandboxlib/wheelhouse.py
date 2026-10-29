"""Host-owned immutable wheel publication; containers only receive read access."""
from contextlib import contextmanager
from email.parser import Parser
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from .common import resource_dir


def store_dir():
    return resource_dir() / 'python-wheels'


def profile_dir(image):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
        raise ValueError('wheel profiles require an immutable Docker image ID')
    return store_dir() / 'profiles' / image.split(':', 1)[1]


def wheel_name(path):
    """Validate metadata without extracting or executing an imported wheel."""
    if not re.fullmatch(r'[A-Za-z0-9_.+!\-]+\.whl', path.name):
        raise ValueError('expected a wheel filename: ' + path.name)
    with zipfile.ZipFile(path) as archive:
        metadata = [i for i in archive.infolist() if i.filename.endswith('.dist-info/METADATA')]
        if len(metadata) != 1 or metadata[0].file_size > 4 * 1024 * 1024:
            raise ValueError('wheel must contain one reasonably sized METADATA file')
        message = Parser().parsestr(archive.read(metadata[0]).decode('utf-8'))
        name, version = message.get('Name', ''), message.get('Version', '')
        canonical = lambda value: re.sub(r'[-_.]+', '_', value).lower()
        pieces = path.name[:-4].split('-')
        if (len(pieces) not in (5, 6) or canonical(pieces[0]) != canonical(name) or
                pieces[1] != version or not name or not version):
            raise ValueError('wheel filename does not match package metadata')
    return path.name


@contextmanager
def store_lock():
    root = store_dir()
    if root.is_symlink():
        raise ValueError('wheel store must not be a symlink')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / '.lock').open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield root


def publish(image, sources):
    """Explicit trust decision on the host. Never called automatically on exit."""
    profile = profile_dir(image)
    with store_lock() as root:
        objects = root / 'objects'
        objects.mkdir(exist_ok=True)
        profile.mkdir(parents=True, exist_ok=True)
        for source in sources:
            source = Path(source).expanduser().resolve(strict=True)
            # First snapshot the input, so validation, hashing, and publication
            # all refer to the same bytes even if the source is being rebuilt.
            with tempfile.TemporaryDirectory(prefix='publish-', dir=root) as directory:
                snapshot = Path(directory) / source.name
                shutil.copyfile(source, snapshot)
                try:
                    name = wheel_name(snapshot)
                except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
                    raise ValueError('invalid wheel: ' + source.name) from exc
                with snapshot.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                target = profile / name
                if target.exists():
                    with target.open('rb') as stream:
                        existing = hashlib.file_digest(stream, 'sha256').hexdigest()
                    if existing != digest:
                        raise ValueError('refusing to overwrite an immutable wheel: ' + name)
                    continue
                artifact = objects / digest
                if not artifact.exists():
                    snapshot.chmod(0o444)
                    os.replace(snapshot, artifact)
                os.link(artifact, target)
    return profile
