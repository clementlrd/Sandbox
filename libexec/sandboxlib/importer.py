"""Import Git DAGs, restoring historical ancestry and rewriting new identities."""
from __future__ import annotations
from pathlib import Path
import os
import re
import subprocess
import uuid
from .common import git, git_text
from .commitmap import CommitMap, transfer_lock
from .gitgraph import parts, refs, require_commit


def host_env():
    result = os.environ.copy()
    for k in tuple(result):
        if k.startswith(('GIT_AUTHOR_', 'GIT_COMMITTER_', 'GIT_CONFIG_', 'GIT_DIR',
                         'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_NAMESPACE')):
            del result[k]
    result['GIT_NO_REPLACE_OBJECTS'] = '1'
    result['GIT_ALLOW_PROTOCOL'] = 'file'
    return result


def identities(source, field, env):
    raw = git_text(source, 'var', f'GIT_{field}_IDENT', env=env)
    match = re.fullmatch(r'(.+) <([^<>]+)> \d+ [+-]\d{4}', raw)
    if not match:
        raise ValueError(f'host Git {field.lower()} identity is not configured')
    return match.group(1), match.group(2)


def load_map(path):
    return CommitMap.load(path).to_host


def import_branches(session: Path, meta: dict, *, force=False):
    with transfer_lock(session):
        return _import_branches(session, meta, force=force)


def _import_branches(session: Path, meta: dict, *, force=False):
    source = Path(meta['source']).resolve(strict=True)
    repo = session / 'repo'
    env = host_env()
    if Path(git_text(source, 'rev-parse', '--show-toplevel', env=env)).resolve() != source:
        raise ValueError('original Git worktree moved or is not accessible')
    sandbox_refs = refs(repo, 'refs/heads/', env)
    if not sandbox_refs:
        raise ValueError('no sandbox branches to import')
    prefix = f'refs/heads/sandbox/{meta["name"]}/'
    incoming = f'refs/sandbox/incoming/{meta["id"]}/{uuid.uuid4().hex}/'
    existing = refs(source, prefix, env)
    if existing and not force:
        raise ValueError('review refs already exist; use --force to replace them')
    an, ae = identities(source, 'AUTHOR', env)
    cn, ce = identities(source, 'COMMITTER', env)
    mapping = CommitMap.load(session / 'commit-map')
    # Copy objects via a local fetch. This NEVER modifies an original branch.
    try:
        git(source, 'fetch', '--no-tags', '--no-write-fetch-head', '--no-recurse-submodules',
            '--no-auto-maintenance', str(repo), f'+refs/heads/*:{incoming}*', env=env)
        tips = refs(source, incoming, env)
        if {ref.removeprefix(incoming): tip for ref, tip in tips.items()} != {
                ref.removeprefix('refs/heads/'): tip for ref, tip in sandbox_refs.items()}:
            raise ValueError('sandbox branches changed during import; retry the operation')
        revs = git_text(source, 'rev-list', '--reverse', '--topo-order',
                        *[tips[x] for x in sorted(tips)], env=env).splitlines()
        rewritten = {}
        for oid in revs:
            if oid in mapping.to_host:
                original = mapping.to_host[oid]
                require_commit(source, original, env, 'host')
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
            mapping.add(rewritten[oid], oid)
        updates = []
        for ref, tip in sorted(tips.items()):
            branch = ref.removeprefix(incoming)
            destination = prefix + branch
            new_oid = rewritten[tip]
            if (git_text(source, 'rev-parse', new_oid + '^{tree}', env=env) !=
                    git_text(source, 'rev-parse', tip + '^{tree}', env=env)):
                raise ValueError('tree mismatch for branch ' + branch)
            updates.append((destination, new_oid, existing.get(destination, '0' * len(new_oid))))
        mapping.save(session / 'commit-map')
        tx = b'start\0option no-deref\0'
        for ref, new_oid, old_oid in updates:
            tx += f'update {ref}\0{new_oid}\0{old_oid}\0'.encode('utf-8', errors='surrogateescape')
        tx += b'prepare\0commit\0'
        git(source, 'update-ref', '--stdin', '-z', env=env, input_bytes=tx)
        return [ref.removeprefix('refs/heads/') for ref, _, _ in updates]
    finally:
        for ref in refs(source, incoming, env):
            git(source, 'update-ref', '-d', ref, env=env)
