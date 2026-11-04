"""Small Git object helpers shared by host import and trusted preparation."""
from __future__ import annotations
import re
import subprocess
from .common import git


def refs(repo, prefix, env):
    raw = git(repo, 'for-each-ref', '--format=%(refname)%00%(objectname)', prefix, env=env)
    return dict(line.decode('utf-8', errors='surrogateescape').split('\0')
                for line in raw.splitlines() if line)


def require_commit(repo, oid, env, location):
    try:
        git(repo, 'cat-file', '-e', oid + '^{commit}', env=env)
    except subprocess.CalledProcessError as exc:
        raise ValueError(f'mapped {location} commit {oid} is missing; restore it before retrying') from exc


def verify_export(repo, env):
    """A clean export must not carry unreachable leftovers from rewriting."""
    reachable = set(git(repo, 'rev-list', '--objects', '--no-object-names', '--all',
                        env=env).splitlines())
    stored = git(repo, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname)', env=env)
    if any(oid not in reachable for oid in stored.splitlines()):
        raise ValueError('prepared export contains objects outside its reachable history')


def parts(repo, oid, env):
    raw = git(repo, 'cat-file', 'commit', oid, env=env)
    head, sep, message = raw.partition(b'\n\n')
    if not sep:
        raise ValueError('bad commit object')
    tree, parents, dates = None, [], {}
    for line in head.splitlines():
        if line.startswith(b'tree '):
            tree = line[5:].decode('ascii')
        elif line.startswith(b'parent '):
            parents.append(line[7:].decode('ascii'))
        elif line.startswith((b'author ', b'committer ')):
            key = line.partition(b' ')[0].decode()
            match = re.search(rb' (-?\d+ [+-]\d{4})$', line)
            if match:
                dates[key] = match.group(1).decode()
    if not tree or set(dates) != {'author', 'committer'}:
        raise ValueError('invalid commit headers')
    return tree, parents, dates, message


def prepared_commit(repo, oid, parents, anonymize, env):
    """Rewrite only identities/parents; retain message bytes, dates and encoding."""
    raw = git(repo, 'cat-file', 'commit', oid, env=env)
    head, _, message = raw.partition(b'\n\n')
    old_parents = [line[7:].decode('ascii') for line in head.splitlines()
                   if line.startswith(b'parent ')]
    if not anonymize and parents == old_parents:
        return oid
    headers = []
    drop = False
    for line in head.splitlines():
        if line.startswith(b' '):
            if not drop:
                headers.append(line)
            continue
        key = line.partition(b' ')[0]
        # These signatures no longer authenticate a rewritten commit and can
        # contain original identities (including embedded signed merge tags).
        drop = key in (b'parent', b'gpgsig', b'gpgsig-sha256', b'mergetag')
        if drop:
            continue
        if key == b'tree':
            headers.append(line)
            headers.extend(b'parent ' + p.encode('ascii') for p in parents)
        elif anonymize and key in (b'author', b'committer'):
            date = re.search(rb' (-?\d+ [+-]\d{4})$', line)
            if not date:
                raise ValueError('invalid commit identity')
            headers.append(key + b' Sandbox <sandbox@invalid>' + date.group(0))
        else:
            headers.append(line)
    rewritten = b'\n'.join(headers) + b'\n\n' + message
    return git(repo, 'hash-object', '-t', 'commit', '-w', '--stdin',
               env=env, input_bytes=rewritten).decode('ascii').strip()
