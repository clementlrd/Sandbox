"""Trusted worker: runs only in sandbox/preparer, never in an agent runtime."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import subprocess
from .common import git, git_env, git_text
from .commitmap import CommitMap, OID
from .gitgraph import parts, prepared_commit, require_commit, verify_export
from .preflight import scan


def prepare(source: Path, dest: Path, anonymize: bool, denylist: Path | None,
            skip_denylist: bool = False):
    if skip_denylist and not anonymize:
        raise ValueError('--skip-denylist requires --anonymize')
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
    verify_export(repo, env)
    # Do not persist the source Git object store or build stages.
    shutil.rmtree(stage)
    shutil.rmtree(dest / 'template')
    if anonymize:
        scan(repo, denylist, skip_denylist=skip_denylist)
    print('PREPARED: ' + ('anonymized' if anonymize else 'independent') + ' repository')


def prepare_refresh(source: Path, sandbox: Path, mapping_path: Path, dest: Path,
                    tip: str, anonymize: bool, denylist: Path | None,
                    *, branch='main', skip_denylist=False):
    """Prepare one captured host tip, anchoring at existing sandbox commits.

    Raw host objects are confined to stage.git. Only history reachable from the
    translated tip is fetched into the clean output repo; never publish stage.git.
    """
    if skip_denylist and not anonymize:
        raise ValueError('--skip-denylist requires an anonymized sandbox')
    if not OID.fullmatch(tip):
        raise ValueError('invalid captured host commit')
    source, sandbox, dest = (p.resolve(strict=True) for p in (source, sandbox, dest))
    if any(dest.iterdir()):
        raise ValueError('preparation output must be an empty directory')
    env = git_env()
    template = dest / 'template'
    template.mkdir()
    env['GIT_TEMPLATE_DIR'] = str(template)
    mapping = CommitMap.load(mapping_path)
    stage, repo = dest / 'stage.git', dest / 'repo'
    fmt = git_text(source, 'rev-parse', '--show-object-format', env=env)
    git(dest, 'init', '--bare', '-b', 'bootstrap', '--object-format=' + fmt, str(stage), env=env)
    fetch_options = ['--no-tags', '--no-write-fetch-head', '--no-recurse-submodules',
                     '--no-auto-maintenance']
    git(stage, 'fetch', *fetch_options, str(source),
        f'{tip}:refs/raw/host', env=env)

    # Stop traversing at mapped commits: a previously exported commit must reuse
    # its actual sandbox counterpart, not an independently re-anonymized copy.
    pending, seen, unknown, anchors = [(tip, False)], set(), [], {}
    while pending:
        oid, expanded = pending.pop()
        if oid in seen:
            continue
        if oid in mapping.to_sandbox:
            anchors[oid] = mapping.to_sandbox[oid]
            seen.add(oid)
        elif expanded:
            unknown.append(oid)
            seen.add(oid)
        else:
            _, parents, _, _ = parts(stage, oid, env)
            pending.append((oid, True))
            pending.extend((parent, False) for parent in reversed(parents))

    for host, prepared in anchors.items():
        require_commit(sandbox, prepared, env, 'sandbox')
        git(stage, 'fetch', *fetch_options, str(sandbox),
            f'{prepared}:refs/anchors/{prepared}', env=env)
        if parts(stage, host, env)[0] != parts(stage, prepared, env)[0]:
            raise ValueError('mapped commits have different file trees')
    translated = dict(anchors)
    for oid in unknown:
        _, parents, _, _ = parts(stage, oid, env)
        prepared = prepared_commit(stage, oid, [translated[p] for p in parents], anonymize, env)
        mapping.add(oid, prepared)
        translated[oid] = prepared

    git(stage, 'update-ref', 'refs/prepared/tip', translated[tip], env=env)
    git(dest, 'init', '-b', 'bootstrap', '--object-format=' + fmt, str(repo), env=env)
    git(repo, 'fetch', *fetch_options, str(stage),
        'refs/prepared/tip:refs/heads/prepared', env=env)
    logs = repo / '.git/logs'
    if logs.exists():
        shutil.rmtree(logs)
    verify_export(repo, env)
    # Delete the only store containing original host identities before scanning
    # or returning the export. No raw ref/config/reflog is transferred downstream.
    shutil.rmtree(stage)
    shutil.rmtree(template)
    if anonymize:
        scan(repo, denylist, skip_denylist=skip_denylist,
             boundaries=tuple(anchors.values()),
             refnames=(f'refs/remotes/host/{branch}',))
    mapping.save(dest / 'commit-map')
    print('PREPARED: incremental ' + ('anonymized' if anonymize else 'independent') + ' history')


def main():
    parser = argparse.ArgumentParser(description='Trusted sandbox preparation worker')
    sub = parser.add_subparsers(dest='command', required=True)
    a = sub.add_parser('prepare')
    a.add_argument('--source', default='/source')
    a.add_argument('--output', default='/output')
    a.add_argument('--anonymize', action='store_true')
    a.add_argument('--skip-denylist', action='store_true',
                   help='skip content screening while retaining Git identity/remote checks')
    a.add_argument('--denylist', default='/private/denylist.txt')
    r = sub.add_parser('refresh', help='prepare incremental host history')
    r.add_argument('--source', default='/source')
    r.add_argument('--sandbox', default='/sandbox')
    r.add_argument('--mapping', default='/private/commit-map')
    r.add_argument('--output', default='/output')
    r.add_argument('--tip', required=True)
    r.add_argument('--branch', required=True)
    r.add_argument('--anonymize', action='store_true')
    r.add_argument('--skip-denylist', action='store_true')
    r.add_argument('--denylist', default='/private/denylist.txt')
    args = parser.parse_args()
    deny = Path(args.denylist) if args.anonymize and not args.skip_denylist else None
    if args.command == 'prepare':
        prepare(Path(args.source), Path(args.output), args.anonymize, deny,
                skip_denylist=args.skip_denylist)
    else:
        prepare_refresh(Path(args.source), Path(args.sandbox), Path(args.mapping),
                        Path(args.output), args.tip, args.anonymize, deny,
                        branch=args.branch, skip_denylist=args.skip_denylist)

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        import sys
        print(f'sandbox-preparer: {exc}', file=sys.stderr)
        raise SystemExit(1)
