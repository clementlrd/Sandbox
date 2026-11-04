"""Screen freshly prepared anonymized repositories and incremental transfers."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from .common import git_env

CHUNK = 1024 * 1024
MAX_REPORT = 25


def scan(repo: Path, denylist: Path | None, *, skip_denylist=False,
         boundaries=None, refnames=()):
    """With boundaries, screen only incoming history, never existing sandbox work."""
    repo = Path(repo).resolve(strict=True)
    if not skip_denylist:
        if denylist is None:
            raise ValueError('denylist required unless --skip-denylist is set')
        denylist = Path(denylist).resolve(strict=True)
    if not (repo / '.git').is_dir():
        raise ValueError('expected a working Git repository with a .git directory')
    if not skip_denylist:
        if repo in denylist.parents or denylist == repo:
            raise ValueError('denylist must not be mounted inside the sandbox')
        if not denylist.is_file():
            raise ValueError('missing denylist')
    if repo.joinpath('.git/objects/info/alternates').exists():
        raise ValueError('Git object alternates are prohibited')
    if not skip_denylist:
        needles = [t.strip().casefold() for t in denylist.read_text(encoding='utf-8').splitlines()
                   if t.strip() and not t.lstrip().startswith('#')]
        if not needles:
            raise ValueError('denylist has no active entries; refusing scan')
    env = git_env()
    remotes = subprocess.check_output(['git', '-C', str(repo), 'remote'], env=env)
    if remotes.strip():
        raise ValueError('sandbox repository must not have configured Git remotes')
    revisions = ['--all']
    if boundaries:
        revisions += ['--not', *boundaries]
    raw = subprocess.check_output(['git', '-C', str(repo), 'log',
          '--format=%an%x00%ae%x00%cn%x00%ce', *revisions], env=env)
    for line in raw.splitlines():
        if line.split(b'\0') != [b'Sandbox', b'sandbox@invalid', b'Sandbox', b'sandbox@invalid']:
            raise ValueError('a reachable commit has a non-synthetic author or committer')
    if skip_denylist:
        # Keep the Git-identity, remote and alternates checks above. Do not
        # inspect source/file contents when the user explicitly opts out.
        print('WARNING: denylist content scan skipped; Git identity and remote checks passed',
              file=sys.stderr)
        return
    hits = 0
    def detected(idx, kind, place):
        nonlocal hits
        hits += 1
        if hits <= MAX_REPORT:
            print(f'BLOCKED: denylist term #{idx + 1} in {kind}: {place}', file=sys.stderr)
    def check_text(v, kind, place):
        for idx, term in enumerate(needles):
            if term in v.casefold():
                detected(idx, kind, place)
    overlap = max(256, max(len(t.encode('utf-8')) for t in needles) * 8)
    def check_stream(reader, size, kind, place):
        remaining = size
        tail = b''
        counted = set()
        while remaining is None or remaining > 0:
            block = reader.read(CHUNK if remaining is None else min(remaining, CHUNK))
            if not block:
                if remaining is not None and remaining:
                    raise ValueError('truncated Git object stream')
                break
            if remaining is not None:
                remaining -= len(block)
            value = (tail + block).decode('utf-8', errors='replace').casefold()
            for idx, term in enumerate(needles):
                if idx not in counted and term in value:
                    detected(idx, kind, place)
                    counted.add(idx)
            tail = (tail + block)[-overlap:]
    for refname in refnames:
        check_text(refname, 'reference name', '<redacted>')
    # Creation checks every object, even unreachable ones. Refresh exports only
    # prepared reachable history, and excludes already-visible mapped ancestry.
    with tempfile.TemporaryFile(dir=repo.parent) as objects:
        if boundaries is not None:
            subprocess.run(['git', '-C', str(repo), 'rev-list', '--objects',
                            '--no-object-names', *revisions], stdout=objects,
                           check=True, env=env)
            objects.seek(0)
        args = ['git', '-C', str(repo), 'cat-file', '--batch']
        if boundaries is None:
            args.append('--batch-all-objects')
        proc = subprocess.Popen(args, stdin=objects if boundaries is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        try:
            assert proc.stdout is not None
            while header := proc.stdout.readline():
                oid, kind, size = header.strip().split(b' ', 2)
                check_stream(proc.stdout, int(size), f'Git {kind.decode()} object', oid.decode())
                if proc.stdout.read(1) != b'\n':
                    raise ValueError('invalid Git object framing')
        except BaseException:
            proc.terminate()
            proc.wait()
            raise
        finally:
            if proc.stdout:
                proc.stdout.close()
        status = proc.wait()
        if status != 0:
            raise ValueError('Git object scan failed')

    def walk(root: Path, label: str):
        if root.is_symlink():
            check_text(os.readlink(root), 'symlink', label)
            return
        if root.is_file():
            with root.open('rb') as f:
                check_stream(f, None, 'mounted file', label)
            return
        if not root.is_dir():
            raise ValueError('unsupported mount path type: ' + label)
        for current, dirs, files in os.walk(root, followlinks=False,
                                            onerror=lambda err: (_ for _ in ()).throw(err)):
            path = Path(current)
            if path == repo / '.git/objects':
                dirs[:] = [d for d in dirs if d == 'info']
            for name in dirs + files:
                item = path / name
                relative = str(item.relative_to(root))
                safe_label = '<redacted>' if any(t in relative.casefold() for t in needles) else relative
                check_text(relative, 'path', f'{label}/{safe_label}')
                if item.is_symlink():
                    check_text(os.readlink(item), 'symlink target', f'{label}/{safe_label}')
            for name in files:
                item = path / name
                if item.is_symlink():
                    continue
                if not item.is_file():
                    raise ValueError('special files are not allowed in mounts')
                relative = str(item.relative_to(root))
                safe_label = '<redacted>' if any(t in relative.casefold() for t in needles) else relative
                with item.open('rb') as f:
                    check_stream(f, None, 'file', f'{label}/{safe_label}')
    if boundaries is None:
        walk(repo, 'workspace')
    else:
        # No checkout is made for refresh. Inspect complete paths in all new
        # commits, including files deleted again before the incoming tip.
        commits = subprocess.check_output(['git', '-C', str(repo), 'rev-list',
                                           *revisions], env=env).splitlines()
        for commit in commits:
            paths = subprocess.check_output(['git', '-C', str(repo), 'ls-tree',
                '-r', '-z', '--name-only', commit.decode('ascii')], env=env)
            for path in paths.split(b'\0'):
                check_text(path.decode('utf-8', errors='replace'), 'tracked path', '<redacted>')
    if hits:
        raise ValueError(f'{hits} denylist match(es); repository preparation blocked')
    print(f'PASS: {len(needles)} terms checked against prepared Git objects and paths')
