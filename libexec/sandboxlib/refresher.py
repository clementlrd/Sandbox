"""Publish prepared host history without changing sandbox branches or files."""
from __future__ import annotations
from pathlib import Path
import subprocess
import tempfile
import uuid
from .common import git, git_env, git_text
from .commitmap import CommitMap, transfer_lock
from .gitgraph import refs


def refresh_branch(session: Path, meta: dict, branch: str, prepare, *,
                   denylist=None, skip_denylist=False):
    """The injected prepare callback is the trusted Docker worker in production."""
    anonymize = meta['anonymized']
    if skip_denylist and not anonymize:
        raise ValueError('--skip-denylist requires an anonymized sandbox')
    if skip_denylist and denylist is not None:
        raise ValueError('--skip-denylist cannot be combined with --denylist')
    if not anonymize and denylist is not None:
        raise ValueError('--denylist requires an anonymized sandbox')
    env = git_env(anonymized=False)
    source = Path(meta['source']).resolve(strict=True)
    repo = session / 'repo'
    if Path(git_text(source, 'rev-parse', '--show-toplevel', env=env)).resolve() != source:
        raise ValueError('original Git worktree moved or is not accessible')
    source_ref = 'refs/heads/' + branch
    try:
        git(source, 'check-ref-format', source_ref, env=env)
        tip = git_text(source, 'rev-parse', '--verify', source_ref + '^{commit}', env=env)
    except subprocess.CalledProcessError as exc:
        raise ValueError(f'host branch {branch!r} does not exist or has an invalid name') from exc
    tracking = 'refs/remotes/host/' + branch

    with transfer_lock(session):
        if refs(repo, 'refs/heads/host/' + branch, env).get('refs/heads/host/' + branch):
            raise ValueError(f'local branch host/{branch} would shadow the refresh reference; rename it first')
        symbolic = subprocess.run(['git', '-C', str(repo), 'symbolic-ref', '-q', tracking],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if symbolic.returncode == 0:
            raise ValueError('refresh destination must not be a symbolic reference')
        old = refs(repo, tracking, env).get(tracking)
        mapping = CommitMap.load(session / 'commit-map')
        with tempfile.TemporaryDirectory(prefix='.refresh-', dir=session) as temporary:
            work = Path(temporary)
            snapshot = work / 'commit-map'
            mapping.save(snapshot)
            output = work / 'output'
            output.mkdir(mode=0o700)
            prepare(source, repo, snapshot, output, tip, anonymize, denylist,
                    branch=branch, skip_denylist=skip_denylist)
            prepared = CommitMap.load(output / 'commit-map')
            for host, sandbox in mapping.to_sandbox.items():
                if prepared.to_sandbox.get(host) != sandbox:
                    raise ValueError('preparer changed an existing commit correspondence')
            for host, sandbox in prepared.to_sandbox.items():
                mapping.add(host, sandbox)
            new = mapping.to_sandbox[tip]
            export = output / 'repo'
            if git_text(export, 'rev-parse', '--verify', 'refs/heads/prepared', env=env) != new:
                raise ValueError('prepared tip does not match its commit mapping')
            if (git_text(export, 'rev-parse', new + '^{tree}', env=env) !=
                    git_text(source, 'rev-parse', tip + '^{tree}', env=env)):
                raise ValueError('prepared tip has a different file tree')
            incoming = 'refs/sandbox/refresh/' + uuid.uuid4().hex
            try:
                # Never fetch /source here. The worker's clean export is the only
                # repository allowed to supply incoming objects to the live repo.
                git(repo, '-c', 'core.logAllRefUpdates=false', 'fetch', '--no-tags',
                    '--no-write-fetch-head', '--no-recurse-submodules', '--no-auto-maintenance',
                    str(export), f'refs/heads/prepared:{incoming}', env=env)
                non_fast_forward = False
                if old and old != new:
                    try:
                        git(repo, 'merge-base', '--is-ancestor', old, new, env=env)
                    except subprocess.CalledProcessError as exc:
                        if exc.returncode != 1:
                            raise
                        non_fast_forward = True
                mapping.save(session / 'commit-map')
                git(repo, 'update-ref', '--no-deref', '-m', 'sandbox refresh',
                    tracking, new, old or '0' * len(new), env=env)
            finally:
                git(repo, 'update-ref', '--no-deref', '-d', incoming, env=env)
        return {'ref': tracking, 'old': old, 'new': new,
                'host_tip': tip, 'non_fast_forward': non_fast_forward}
