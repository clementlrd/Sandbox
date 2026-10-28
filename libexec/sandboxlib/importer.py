"""Import Git DAGs, restoring historical ancestry and rewriting new identities."""
from __future__ import annotations
from pathlib import Path
import os
import re
import subprocess
from .common import git, git_text


def host_env():
    result = os.environ.copy()
    for k in tuple(result):
        if k.startswith(('GIT_AUTHOR_', 'GIT_COMMITTER_', 'GIT_CONFIG_', 'GIT_DIR',
                         'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_NAMESPACE')):
            del result[k]
    return result


def identities(source, field, env):
    raw = git_text(source, 'var', f'GIT_{field}_IDENT', env=env)
    match = re.fullmatch(r'(.+) <([^<>]+)> \d+ [+-]\d{4}', raw)
    if not match:
        raise ValueError(f'host Git {field.lower()} identity is not configured')
    return match.group(1), match.group(2)


def load_map(path):
    inverse = {}
    for i, row in enumerate(path.read_text(encoding='ascii').splitlines()):
        if i == 0 and row.split() == ['old', 'new']:
            continue
        fields = row.split()
        if len(fields) != 2 or any(not re.fullmatch('[a-f0-9]{40,64}', x) for x in fields):
            raise ValueError('invalid Git commit-map')
        if int(fields[1], 16):
            inverse[fields[1]] = fields[0]
    if not inverse:
        raise ValueError('empty commit-map')
    return inverse


def refs(repo, prefix, env):
    raw = git(repo, 'for-each-ref', '--format=%(refname)%00%(objectname)', prefix, env=env)
    result = {}
    for line in raw.splitlines():
        if line:
            name, sha = line.decode('ascii').split('\0')
            result[name] = sha
    return result


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
            match = re.search(rb' (\d+) ([+-]\d{4})$', line)
            if match:
                dates[key] = (match.group(1) + b' ' + match.group(2)).decode()
    if not tree or set(dates) != {'author', 'committer'}:
        raise ValueError('invalid commit headers')
    return tree, parents, dates, message


def import_branches(session: Path, meta: dict, *, force=False):
    source = Path(meta['source']).resolve(strict=True)
    repo = session / 'repo'
    env = host_env()
    if Path(git_text(source, 'rev-parse', '--show-toplevel', env=env)).resolve() != source:
        raise ValueError('original Git worktree moved or is not accessible')
    sandbox_refs = refs(repo, 'refs/heads/', env)
    if not sandbox_refs:
        raise ValueError('no sandbox branches to import')
    prefix = f'refs/heads/sandbox/{meta["name"]}/'
    incoming = f'refs/sandbox/incoming/{meta["id"]}/'
    existing = refs(source, prefix, env)
    if existing and not force:
        raise ValueError('review refs already exist; use --force to replace them')
    an, ae = identities(source, 'AUTHOR', env)
    cn, ce = identities(source, 'COMMITTER', env)
    inverse = load_map(session / 'commit-map')
    # Copy objects via a local fetch. This NEVER modifies an original branch.
    try:
        git(source, 'fetch', '--no-tags', '--no-write-fetch-head', '--no-recurse-submodules',
            str(repo), f'+refs/heads/*:{incoming}*', env=env)
        tips = refs(source, incoming, env)
        if len(tips) != len(sandbox_refs):
            raise ValueError('some sandbox branch refs were not transferred')
        revs = git_text(source, 'rev-list', '--reverse', '--topo-order',
                        *[tips[x] for x in sorted(tips)], env=env).splitlines()
        rewritten = {}
        for oid in revs:
            if oid in inverse:
                original = inverse[oid]
                git(source, 'cat-file', '-e', original + '^{commit}', env=env)
                rewritten[oid] = original
                continue
            tree, parents, dates, message = parts(source, oid, env)
            translated = []
            for p in parents:
                if p not in rewritten:
                    raise ValueError('missing historical parent mapping for ' + oid)
                translated.extend(['-p', rewritten[p]])
            new_env = env.copy()
            new_env.update(GIT_AUTHOR_NAME=an, GIT_AUTHOR_EMAIL=ae,
                           GIT_COMMITTER_NAME=cn, GIT_COMMITTER_EMAIL=ce,
                           GIT_AUTHOR_DATE=dates['author'], GIT_COMMITTER_DATE=dates['committer'])
            rewritten[oid] = git(source, 'commit-tree', tree, *translated,
                                 env=new_env, input_bytes=message).decode().strip()
        updates = []
        for ref, tip in sorted(tips.items()):
            branch = ref.removeprefix(incoming)
            destination = prefix + branch
            new_oid = rewritten[tip]
            if (git_text(source, 'rev-parse', new_oid + '^{tree}', env=env) !=
                    git_text(source, 'rev-parse', tip + '^{tree}', env=env)):
                raise ValueError('tree mismatch for branch ' + branch)
            updates.append((destination, new_oid, existing.get(destination, '0' * len(new_oid))))
        tx = b'start\0'
        for ref, new_oid, old_oid in updates:
            tx += f'update {ref}\0{new_oid}\0{old_oid}\0'.encode('ascii')
        tx += b'prepare\0commit\0'
        git(source, 'update-ref', '--stdin', '-z', env=env, input_bytes=tx)
        return [ref.removeprefix('refs/heads/') for ref, _, _ in updates]
    finally:
        for ref in refs(source, incoming, env):
            git(source, 'update-ref', '-d', ref, env=env)
