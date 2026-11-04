"""Host-only commit correspondences and serialization of Git transfers."""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import tempfile


OID = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})\Z')


@contextmanager
def transfer_lock(session: Path):
    # Keep the lock file: unlinking it would let another process lock a new inode.
    fd = os.open(session / 'transfer.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('another import or refresh is in progress for this sandbox') from exc
        yield
    finally:
        os.close(fd)


class CommitMap:
    def __init__(self):
        self.to_sandbox = {}
        self.to_host = {}

    @classmethod
    def load(cls, path: Path):
        result = cls()
        for i, row in enumerate(path.read_text(encoding='ascii').splitlines()):
            fields = row.split()
            if i == 0 and fields == ['old', 'new']:
                continue
            if len(fields) != 2 or any(not OID.fullmatch(x) for x in fields):
                raise ValueError('invalid Git commit-map')
            host, sandbox = fields
            if int(sandbox, 16):
                result.add(host, sandbox)
        if not result.to_host:
            raise ValueError('empty commit-map')
        return result

    def add(self, host, sandbox):
        if not OID.fullmatch(host) or not OID.fullmatch(sandbox):
            raise ValueError('invalid commit ID in Git commit-map')
        if (host in self.to_sandbox and self.to_sandbox[host] != sandbox or
                sandbox in self.to_host and self.to_host[sandbox] != host):
            raise ValueError('ambiguous Git commit-map; refusing to guess commit ancestry')
        self.to_sandbox[host] = sandbox
        self.to_host[sandbox] = host

    def save(self, path: Path):
        # A superset is safe to persist before publishing refs. Failed ref updates
        # can then be retried without generating different commit identities.
        fd, temporary = tempfile.mkstemp(prefix='.commit-map-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                stream.write('old                                      new\n')
                for host, sandbox in self.to_sandbox.items():
                    stream.write(f'{host} {sandbox}\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
