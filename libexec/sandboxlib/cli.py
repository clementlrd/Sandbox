"""sandbox: named isolated repositories with optional privacy rewrite."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from .common import (check_name, config_dir, git_env, git_text, instances_dir,
                     mount_spec, private_dir, resource_dir)
from .importer import import_branches
from .settings import read_only_mounts, load


def call(args, *, capture=False, env=None):
    result = subprocess.run(args, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None, env=env)
    return result.stdout if capture else None


def docker_rootless():
    p = subprocess.run(['docker', 'info', '--format', '{{json .SecurityOptions}}'],
                       capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError('cannot contact Docker: ' + p.stderr.strip())
    try:
        options = json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError('Docker did not return valid security options') from exc
    if not isinstance(options, list) or not any('rootless' in str(x) for x in options):
        raise RuntimeError('Docker is not rootless; select and verify the rootless Docker context')


def inspect_image(tag):
    p = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag],
                       text=True, capture_output=True)
    if p.returncode or not p.stdout.strip().startswith('sha256:'):
        raise RuntimeError(f'image {tag!r} is not built locally; run sandbox build first')
    return p.stdout.strip()


def instance_path(name):
    return instances_dir() / check_name(name)


def load_instance(name):
    path = instance_path(name)
    if path.is_symlink():
        raise ValueError('sandbox path may not be a symlink')
    file = path / 'metadata.json'
    if not file.is_file():
        raise ValueError(f'unknown sandbox {name!r}')
    meta = json.loads(file.read_text(encoding='utf-8'))
    if meta.get('name') != name or not (path / 'repo/.git').is_dir():
        raise ValueError('sandbox metadata or repository is inconsistent')
    return path, meta


def save_instance(path, meta):
    target = path / 'metadata.json'
    temp = path / ('.metadata-' + uuid.uuid4().hex)
    with temp.open('x', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, sort_keys=True)
        f.write('\n')
    temp.chmod(0o600)
    os.replace(temp, target)


def source_repo(value):
    path = Path(value).expanduser().resolve(strict=True)
    env = git_env(anonymized=False)
    source = Path(git_text(path, 'rev-parse', '--show-toplevel', env=env)).resolve()
    if source != path:
        print(f'Using repository root: {source}')
    if git_text(source, 'status', '--porcelain', '--untracked-files=normal', env=env):
        raise ValueError('source repository has uncommitted or untracked changes; clean it before create')
    if not git_text(source, 'rev-list', '-1', 'HEAD', env=env):
        raise ValueError('source repository needs at least one commit')
    return source


def denylist_path(path=None):
    p = Path(path).expanduser() if path else config_dir() / 'denylist.txt'
    p = p.resolve(strict=True)
    if not p.is_file() or p.stat().st_mode & 0o077:
        raise ValueError('denylist must be a private regular file (chmod 600)')
    return p


def preparer_common(image_id):
    return ['docker', 'run', '--rm', '--init', '--network', 'none',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--pids-limit', '256', '--memory', '4g', '--user', '0:0', image_id]


def prepare_container(image, source, session, anonymize, deny):
    # Arguments must precede the Docker image identifier.
    args = preparer_common(image)[:-1]
    args += ['--mount', mount_spec(source, '/source', True),
             '--mount', mount_spec(session, '/output')]
    if anonymize:
        args += ['--mount', mount_spec(deny, '/private/denylist.txt', True)]
    args += [image, 'prepare']
    if anonymize:
        args.append('--anonymize')
    call(args)


def check_container(image, repo, deny, extras):
    args = preparer_common(image)[:-1]
    args += ['--mount', mount_spec(repo, '/workspace', True),
             '--mount', mount_spec(deny, '/private/denylist.txt', True)]
    for index, (source, _) in enumerate(extras):
        args += ['--mount', mount_spec(source, f'/extras/{index}', True)]
    args += [image, 'check']
    for index in range(len(extras)):
        args += ['--extra', f'/extras/{index}']
    call(args)


def command_create(args, cfg):
    docker_rootless()
    preparer_id = inspect_image(cfg['images']['preparer'])
    source = source_repo(args.source)
    deny = denylist_path(args.denylist) if args.anonymize else None
    private_dir(instances_dir())
    path = instance_path(args.name)
    path.mkdir(mode=0o700, exist_ok=False)
    try:
        prepare_container(preparer_id, source, path, args.anonymize, deny)
        meta = {
          'name': args.name, 'id': uuid.uuid4().hex[:16], 'source': str(source),
          'created_at': datetime.now(timezone.utc).isoformat(),
          'anonymized': args.anonymize, 'denylist': str(deny) if deny else None,
          'preparer_image': preparer_id, 'runtime_images': {}, 'volumes': [],
        }
        save_instance(path, meta)
    except BaseException:
        shutil.rmtree(path)
        raise
    print(f'Created {args.name} ({"anonymized" if args.anonymize else "normal"})')
    print(f'  source: {source}\n  workspace: {path / "repo"}')


def command_list(args, cfg):
    directory = instances_dir()
    if not directory.exists():
        print('No sandboxes.')
        return
    print(f'{"NAME":<25} {"MODE":<13} SOURCE')
    for path in sorted(directory.iterdir()):
        if not path.is_dir() or path.is_symlink() or not (path / 'metadata.json').is_file():
            continue
        try:
            meta = json.loads((path / 'metadata.json').read_text())
            print(f'{meta["name"]:<25} {"anonymized" if meta["anonymized"] else "normal":<13} {meta["source"]}')
        except (OSError, ValueError, KeyError):
            print(f'{path.name:<25} INVALID')


def command_inspect(args, cfg):
    _, meta = load_instance(args.name)
    print(json.dumps(meta, indent=2))


def image_profile(runtime):
    if runtime not in ('base', 'python-xpu'):
        raise ValueError('runtime must be base or python-xpu')
    return runtime


def pinned_image(meta, path, profile, cfg):
    pins = meta['runtime_images']
    if profile not in pins:
        pins[profile] = inspect_image(cfg['images'][profile])
        save_instance(path, meta)
    return pins[profile]


def volume_name(meta, suffix):
    return f'sandbox-{suffix}-{meta["id"]}'


def remember_volume(meta, path, name):
    if name not in meta['volumes']:
        # create labelled volume explicitly, without invoking an image build
        call(['docker', 'volume', 'create', '--label', 'sandbox.managed=true',
              '--label', f'sandbox.name={meta["name"]}', name])
        meta['volumes'].append(name)
        save_instance(path, meta)


def runtime_args(path, meta, cfg, runtime, command):
    """Compose one ephemeral shell/command container, with generic shared tools.

    The image's original PATH is preserved by prepending /opt/tools/bin *inside*
    the container. In particular, the Intel XPU image needs /opt/venv/bin.
    """
    profile = image_profile(runtime)
    image = pinned_image(meta, path, profile, cfg)
    mounts = read_only_mounts(cfg)
    if meta['anonymized']:
        deny = denylist_path(meta['denylist'])
        # Scan the repository and user-supplied read-only mounts (e.g. skills).
        # Shared executable tools are host-managed and deliberately excluded.
        check_container(meta['preparer_image'], path / 'repo', deny, list(mounts))
    if cfg['shared_tools']:
        tools = resource_dir() / 'tools'
        if not (tools / 'bin').is_dir():
            raise ValueError(f'shared tools directory missing at {tools}; rerun install.sh')
        mounts.append((tools.resolve(strict=True), '/opt/tools'))
    args = ['docker', 'run', '--rm', '--init', '--interactive',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--memory', cfg['memory'], '--pids-limit', str(cfg['pids_limit']),
            '--network', cfg['network'], '--user', '0:0', '--workdir', '/workspace',
            '--label', 'sandbox.managed=true', '--label', f'sandbox.name={meta["name"]}',
            '--mount', mount_spec(path / 'repo', '/workspace'),
            '--env', 'HOME=/root', '--env', 'GIT_CONFIG_NOSYSTEM=1',
            '--env', 'GIT_CONFIG_GLOBAL=/dev/null', '--env', 'GIT_TERMINAL_PROMPT=0',
            '--env', 'GIT_AUTHOR_NAME=Sandbox', '--env', 'GIT_AUTHOR_EMAIL=sandbox@invalid',
            '--env', 'GIT_COMMITTER_NAME=Sandbox', '--env', 'GIT_COMMITTER_EMAIL=sandbox@invalid']
    if sys.stdin.isatty() and sys.stdout.isatty():
        args.append('--tty')
    if cfg['venv_volume']:
        venv_name = volume_name(meta, 'venvs-' + image.split(':', 1)[1][:12])
        remember_volume(meta, path, venv_name)
        args += ['--mount', f'type=volume,src={venv_name},dst=/opt/venvs']
    # Generic, per-sandbox application state, independent of executable/profile.
    for suffix, dest in (
        ('app-data', '/root/.local/share'),
        ('app-config', '/root/.config'),
        ('app-cache', '/root/.cache'),
    ):
        name = volume_name(meta, suffix)
        remember_volume(meta, path, name)
        args += ['--mount', f'type=volume,src={name},dst={dest}']
    # Backward compatibility: reuse OpenCode state from an older sandbox version.
    legacy_state = volume_name(meta, 'opencode')
    if legacy_state in meta.get('volumes', []):
        args += ['--mount', f'type=volume,src={legacy_state},dst=/root/.local/share/opencode']
    if profile == 'python-xpu':
        for device in cfg['gpu_devices']:
            if not Path(device).is_char_device():
                raise ValueError('GPU render device is missing: ' + device)
            args += ['--device', device + ':' + device + ':rw',
                     '--group-add', str(Path(device).stat().st_gid)]
    for source, dest in mounts:
        args += ['--mount', mount_spec(source, dest, True)]
    for key in cfg['forward_env']:
        if key in os.environ:
            args += ['--env', key]
    if cfg['shared_tools']:
        # Do NOT replace PATH via docker --env: this must preserve /opt/venv/bin.
        command = ['/bin/sh', '-c', 'export PATH="/opt/tools/bin:$PATH"; exec "$@"',
                   'sandbox-entry', *command]
    return args + [image] + command


def recipes_directory(cfg):
    """Host-owned scripts only; never read recipes from the sandbox repository."""
    raw = cfg.get('setup', {}).get('recipes_dir', '~/.config/sandbox/recipes')
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError('setup.recipes_dir must be an absolute path or start with ~')
    if path.is_symlink():
        raise ValueError('recipes directory must not be a symlink')
    if not path.is_dir():
        raise ValueError(f'recipes directory not found: {path}; rerun install.sh')
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise ValueError('recipes directory must be owned by you and not group/world writable')
    return path


def recipe_script(cfg, recipe):
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*', recipe):
        raise ValueError('invalid recipe name')
    path = recipes_directory(cfg) / (recipe + '.sh')
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'unknown setup recipe: {recipe}')
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise ValueError('recipe must be owned by you and not group/world writable')
    if not os.access(path, os.X_OK):
        raise ValueError(f'recipe is not executable: {path}')
    return path


def apply_setup(name, recipe, cfg, *, force=False, yes=False, runtime="base"):
    path, meta = load_instance(name)
    script = recipe_script(cfg, recipe)
    completed = meta.get('setup_recipes', {})
    if recipe in completed and not force:
        print(f'Setup {recipe} already applied to {name}; use --force to run again.')
        return False
    if cfg.get('setup', {}).get('confirm_host_execution', True) and not yes:
        print(f'Host-side setup script: {script}')
        print(f'Target sandbox: {name}; this script may access host credentials.')
        if not sys.stdin.isatty():
            raise ValueError('setup requires interactive confirmation or --yes')
        answer = input('Execute this trusted host script? [y/N] ').strip().lower()
        if answer not in ('y', 'yes'):
            raise ValueError('setup cancelled')
    # Explicit setup is never launched from a repository-controlled file.
    # The recipe may use the sandbox CLI, including stdin pipelines for secrets.
    env = os.environ.copy()
    env.update(SANDBOX_NAME=name,
               SANDBOX_CLI=str(Path.home() / '.local/bin/sandbox'),
               SANDBOX_RUNTIME=runtime,
               SANDBOX_TOOLS_DIR=str(resource_dir() / 'tools'))
    subprocess.run([str(script), name], check=True, env=env)
    # Only record success, so a failed import can be retried normally.
    _, latest = load_instance(name)
    latest.setdefault('setup_recipes', {})[recipe] = datetime.now(timezone.utc).isoformat()
    save_instance(path, latest)
    print(f'Setup {recipe} completed for {name}.')
    return True


def command_setup(args, cfg):
    if args.name == 'list':
        if args.recipe is not None:
            raise ValueError('usage: sandbox setup list')
        for script in sorted(recipes_directory(cfg).glob('*.sh')):
            if script.is_file() and not script.is_symlink():
                try:
                    recipe_script(cfg, script.stem)
                except ValueError:
                    continue
                print(script.stem)
        return
    if args.recipe is None:
        raise ValueError('usage: sandbox setup NAME RECIPE')
    docker_rootless()
    apply_setup(args.name, args.recipe, cfg, force=args.force, yes=args.yes, runtime=args.runtime)


def command_run(args, cfg):
    path, meta = load_instance(args.name)
    docker_rootless()
    for recipe in args.setup:
        apply_setup(args.name, recipe, cfg, force=args.force_setup, yes=args.yes, runtime=args.runtime)
    # Reload after host-side recipes complete; they may update sandbox metadata.
    path, meta = load_instance(args.name)
    try:
        subprocess.run(runtime_args(path, meta, cfg, args.runtime, ['/bin/bash']), check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode)


def command_exec(args, cfg):
    path, meta = load_instance(args.name)
    docker_rootless()
    if not args.command:
        raise ValueError('expected command after --')
    try:
        subprocess.run(runtime_args(path, meta, cfg, args.runtime, args.command), check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode)


def command_import(args, cfg):
    path, meta = load_instance(args.name)
    updates = import_branches(path, meta, force=args.force)
    for branch in updates:
        print('  ' + branch)
    print('Imported review branches only. No merge, checkout or push was performed.')


def command_destroy(args, cfg):
    path, meta = load_instance(args.name)
    if not args.yes:
        raise ValueError('pass --yes to confirm permanent removal of the sandbox and its volumes')
    docker_rootless()
    running = call(['docker', 'ps', '--quiet', '--filter', f'label=sandbox.name={args.name}'],
                   capture=True)
    if running.strip():
        raise ValueError('sandbox has running containers; stop them first')
    for name in meta.get('volumes', []):
        # An absent volume is harmless; never delete unrelated volumes.
        result = subprocess.run(['docker', 'volume', 'inspect', name],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            call(['docker', 'volume', 'rm', name])
    shutil.rmtree(path)
    print(f'Destroyed {args.name}')


def command_build(args, cfg):
    docker_rootless()
    image = args.image
    if image not in cfg['images']:
        raise ValueError('unknown build target')
    context = resource_dir()
    dockerfile = context / 'images' / image / 'Dockerfile'
    if not dockerfile.is_file():
        raise ValueError(f'missing build resources at {context}; run install.sh')
    cmd = ['docker', 'build', '--progress=plain', '-f', str(dockerfile),
           '-t', cfg['images'][image]]
    if args.no_cache:
        cmd.append('--no-cache')
    if args.pull:
        cmd.append('--pull')
    cmd.append(str(context))
    env = os.environ.copy()
    env['DOCKER_BUILDKIT'] = '1'
    call(cmd, env=env)


def parser():
    p = argparse.ArgumentParser(prog='sandbox', description='Named isolated Git workspaces (rootless Docker)')
    sub = p.add_subparsers(dest='operation', required=True)
    create = sub.add_parser('create', help='create an independent Git sandbox')
    create.add_argument('name')
    create.add_argument('source')
    create.add_argument('--anonymize', action='store_true')
    create.add_argument('--denylist', help='alternate private denylist')
    create.set_defaults(handler=command_create)
    listing = sub.add_parser('list', help='list named sandboxes')
    listing.set_defaults(handler=command_list)
    info = sub.add_parser('inspect', help='show session metadata')
    info.add_argument('name')
    info.set_defaults(handler=command_inspect)
    run = sub.add_parser('run', help='launch an interactive shell in a sandbox')
    run.add_argument('name')
    run.add_argument('--runtime', choices=['base', 'python-xpu'], default='base')
    run.add_argument('--setup', action='append', default=[], metavar='RECIPE',
                     help='apply trusted host setup recipe before starting')
    run.add_argument('--force-setup', action='store_true', help='reapply an already completed recipe')
    run.add_argument('--yes', action='store_true', help='confirm execution of host recipes')
    run.set_defaults(handler=command_run)
    setup = sub.add_parser('setup', help='run a trusted host-side recipe or list recipes')
    setup.add_argument('name', help='sandbox name, or list')
    setup.add_argument('recipe', nargs='?', help='recipe name')
    setup.add_argument('--force', action='store_true', help='reapply a completed recipe')
    setup.add_argument('--runtime', choices=['base', 'python-xpu'], default='base')
    setup.add_argument('--yes', action='store_true', help='confirm host script execution')
    setup.set_defaults(handler=command_setup)
    exe = sub.add_parser('exec', help='execute a command in an ephemeral container')
    exe.add_argument('name')
    exe.add_argument('--runtime', choices=['base', 'python-xpu'], default='base')
    exe.set_defaults(handler=command_exec)
    imp = sub.add_parser('import', help='make host review branches from sandbox commits')
    imp.add_argument('name')
    imp.add_argument('--force', action='store_true')
    imp.set_defaults(handler=command_import)
    destroy = sub.add_parser('destroy', help='delete a sandbox and its volumes')
    destroy.add_argument('name')
    destroy.add_argument('--yes', action='store_true')
    destroy.set_defaults(handler=command_destroy)
    build = sub.add_parser('build', help='explicitly build one cached image')
    build.add_argument('image', choices=['preparer', 'base', 'python-xpu'])
    build.add_argument('--no-cache', action='store_true')
    build.add_argument('--pull', action='store_true')
    build.set_defaults(handler=command_build)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # argparse.REMAINDER swallows --runtime after a positional name; explicitly
    # separate exec's command at the mandatory '--' delimiter instead.
    exec_command = None
    if argv and argv[0] == 'exec':
        if '--' not in argv:
            print('sandbox exec: expected -- before command', file=sys.stderr)
            return 2
        index = argv.index('--')
        exec_command = argv[index + 1:]
        argv = argv[:index]
    args = parser().parse_args(argv)
    if exec_command is not None:
        args.command = exec_command
    try:
        args.handler(args, load())
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.CalledProcessError,
            json.JSONDecodeError) as exc:
        print(f'sandbox: {exc}', file=sys.stderr)
        return 1
    return 0
