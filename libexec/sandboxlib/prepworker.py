"""Trusted worker: runs only in sandbox/preparer, never in an agent runtime."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import subprocess
from .common import git, git_env, git_text
from .preflight import scan


def prepare(source: Path, dest: Path, anonymize: bool, denylist: Path | None):
    env = git_env()
    source, dest = source.resolve(strict=True), dest.resolve(strict=True)
    if any(dest.iterdir()):
        raise ValueError('preparation output must be an empty directory')
    (dest / 'template').mkdir()
    env['GIT_TEMPLATE_DIR'] = str(dest / 'template')
    stage, repo = dest / 'stage.git', dest / 'repo'
    # A bare independent clone avoids sharing any object store with /source.
    subprocess.run(['git', 'clone', '--bare', '--no-local', '--', str(source), str(stage)],
                   check=True, env=env)
    if anonymize:
        subprocess.run(['git', '-C', str(stage), 'filter-repo', '--force',
            '--name-callback', 'return b"Sandbox"',
            '--email-callback', 'return b"sandbox@invalid"',
            '--prune-empty', 'never', '--prune-degenerate', 'never',
            '--replace-refs', 'delete-no-add'], check=True, env=env)
        mapping = stage / 'filter-repo' / 'commit-map'
        if not mapping.is_file():
            raise ValueError('git-filter-repo produced no commit map')
        shutil.copyfile(mapping, dest / 'commit-map')
    else:
        # Identical historical commit IDs are an equally valid import mapping.
        revisions = git_text(stage, 'rev-list', '--all', env=env).splitlines()
        if not revisions:
            raise ValueError('source repository has no commits')
        (dest / 'commit-map').write_text('old                                      new\n' +
            ''.join(f'{x} {x}\n' for x in revisions), encoding='ascii')
    (dest / 'commit-map').chmod(0o600)
    # A fresh clone transfers *reachable* rewritten objects, not residual originals.
    subprocess.run(['git', 'clone', '--no-local', '--no-single-branch', '--',
                    str(stage), str(repo)], check=True, env=env)
    refs = git_text(repo, 'for-each-ref', '--format=%(refname)',
                    'refs/remotes/origin', env=env).splitlines()
    for ref in refs:
        if ref == 'refs/remotes/origin/HEAD':
            continue
        branch = ref.removeprefix('refs/remotes/origin/')
        if not branch:
            continue
        result = subprocess.run(['git', '-C', str(repo), 'show-ref', '--verify', '--quiet',
                                 'refs/heads/' + branch], env=env)
        if result.returncode:
            subprocess.run(['git', '-C', str(repo), 'branch', branch, ref],
                           check=True, env=env)
    subprocess.run(['git', '-C', str(repo), 'remote', 'remove', 'origin'],
                   check=True, env=env)
    for key, value in [('user.name', 'Sandbox'), ('user.email', 'sandbox@invalid'),
                       ('commit.gpgsign', 'false'), ('tag.gpgsign', 'false'),
                       ('core.hooksPath', '/dev/null')]:
        git(repo, 'config', '--local', key, value, env=env, capture=False)
    logs = repo / '.git/logs'
    if logs.exists():
        shutil.rmtree(logs)
    # Do not persist the source Git object store or build stages.
    shutil.rmtree(stage)
    shutil.rmtree(dest / 'template')
    if anonymize:
        if denylist is None:
            raise ValueError('anonymization requires a denylist')
        scan(repo, denylist)
    print('PREPARED: ' + ('anonymized' if anonymize else 'independent') + ' repository')


def main():
    parser = argparse.ArgumentParser(description='Trusted sandbox preparation worker')
    sub = parser.add_subparsers(dest='command', required=True)
    a = sub.add_parser('prepare')
    a.add_argument('--source', default='/source')
    a.add_argument('--output', default='/output')
    a.add_argument('--anonymize', action='store_true')
    a.add_argument('--denylist', default='/private/denylist.txt')
    c = sub.add_parser('check')
    c.add_argument('--repo', default='/workspace')
    c.add_argument('--denylist', default='/private/denylist.txt')
    c.add_argument('--extra', action='append', default=[])
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(Path(args.source), Path(args.output), args.anonymize,
                Path(args.denylist) if args.anonymize else None)
    else:
        scan(Path(args.repo), Path(args.denylist), [Path(x) for x in args.extra])

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        import sys
        print(f'sandbox-preparer: {exc}', file=sys.stderr)
        raise SystemExit(1)
