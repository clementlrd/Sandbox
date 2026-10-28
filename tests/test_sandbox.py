import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from sandboxlib import cli, prepworker, preflight
from sandboxlib.importer import import_branches


def cmd(repo, *args, data=None, extra=None):
    env = os.environ.copy()
    env.pop('GIT_CONFIG_GLOBAL', None)
    env.pop('GIT_CONFIG_NOSYSTEM', None)
    if extra:
        env.update(extra)
    return subprocess.run(['git', '-C', str(repo), *args], input=data,
                          capture_output=True, check=True, env=env).stdout.decode().strip()


def commit(repo, text, filename='source.txt', content='content', extra=None):
    (repo / filename).write_text(content)
    cmd(repo, 'add', filename, extra=extra)
    cmd(repo, 'commit', '-m', text, extra=extra)
    return cmd(repo, 'rev-parse', 'HEAD')


class GitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = self.root / 'original'
        self.original.mkdir()
        cmd(self.original, 'init', '-b', 'main')
        cmd(self.original, 'config', 'user.name', 'Real Person')
        cmd(self.original, 'config', 'user.email', 'real@example.invalid')
        self.denylist = self.root / 'denylist.txt'
        self.denylist.write_text('Real Person\nreal@example.invalid\n')
        self.denylist.chmod(0o600)
        self.anon = dict(GIT_AUTHOR_NAME='Sandbox', GIT_AUTHOR_EMAIL='sandbox@invalid',
                         GIT_COMMITTER_NAME='Sandbox', GIT_COMMITTER_EMAIL='sandbox@invalid')

    def test_plain_preparation_copies_history_and_branches_without_remotes(self):
        first = commit(self.original, 'A')
        cmd(self.original, 'branch', 'feature', first)
        dest = self.root / 'session'
        dest.mkdir()
        prepworker.prepare(self.original, dest, anonymize=False, denylist=None)
        repo = dest / 'repo'
        self.assertEqual(cmd(repo, 'rev-parse', 'main'), first)
        self.assertEqual(cmd(repo, 'rev-parse', 'feature'), first)
        self.assertEqual(cmd(repo, 'remote'), '')
        self.assertFalse((repo / '.git/objects/info/alternates').exists())
        self.assertIn(first, (dest / 'commit-map').read_text())
        self.assertFalse((dest / 'stage.git').exists())

    def test_preflight_rejects_deleted_historical_and_unreachable(self):
        cmd(self.original, 'config', 'user.name', 'Sandbox')
        cmd(self.original, 'config', 'user.email', 'sandbox@invalid')
        commit(self.original, 'old', content='Real Person', extra=self.anon)
        commit(self.original, 'clean', content='safe', extra=self.anon)
        with self.assertRaisesRegex(ValueError, 'denylist match'):
            preflight.scan(self.original, self.denylist)
        # Reset to a clean repo and add an unreachable object.
        # Test a separate clean repository with an intentionally unreachable object.
        other = self.root / 'other'
        other.mkdir()
        cmd(other, 'init', '-b', 'main')
        cmd(other, 'config', 'user.name', 'Sandbox')
        cmd(other, 'config', 'user.email', 'sandbox@invalid')
        commit(other, 'start', extra=self.anon)
        cmd(other, 'hash-object', '-w', '--stdin', data=b'real@example.invalid')
        with self.assertRaisesRegex(ValueError, 'denylist match'):
            preflight.scan(other, self.denylist)

    def test_preflight_denies_skill_content_and_nonsynthetic_commit(self):
        commit(self.original, 'start')
        with self.assertRaisesRegex(ValueError, 'non-synthetic'):
            preflight.scan(self.original, self.denylist)
        cmd(self.original, 'config', 'user.name', 'Sandbox')
        cmd(self.original, 'config', 'user.email', 'sandbox@invalid')
        other = self.root / 'other'
        other.mkdir()
        cmd(other, 'init', '-b', 'main')
        cmd(other, 'config', 'user.name', 'Sandbox')
        cmd(other, 'config', 'user.email', 'sandbox@invalid')
        commit(other, 'start', extra=self.anon)
        skills = self.root / 'skills'
        skills.mkdir()
        (skills / 'SKILL.md').write_text('Real Person')
        with self.assertRaisesRegex(ValueError, 'denylist match'):
            preflight.scan(other, self.denylist, [skills])

    def test_publication_audit_is_not_a_cli_command(self):
        # Contributor authorship is legitimate; denylist is a sandbox preflight.
        with self.assertRaises(SystemExit):
            cli.parser().parse_args(['audit', '.'])

    def test_denylist_bypass_does_not_bypass_git_checks(self):
        # This is a typical legitimate case: the LICENSE contains your name.
        # The source is already synthetically authored, so only the content
        # is a denylist violation.
        cmd(self.original, 'config', 'user.name', 'Sandbox')
        cmd(self.original, 'config', 'user.email', 'sandbox@invalid')
        commit(self.original, 'license', filename='LICENSE',
               content='Copyright Real Person', extra=self.anon)
        with self.assertRaisesRegex(ValueError, 'denylist match'):
            preflight.scan(self.original, self.denylist)
        preflight.scan(self.original, None, skip_denylist=True)
        cmd(self.original, 'remote', 'add', 'origin', 'https://example.org/repo.git')
        with self.assertRaisesRegex(ValueError, 'must not have configured Git remotes'):
            preflight.scan(self.original, None, skip_denylist=True)
        cmd(self.original, 'remote', 'remove', 'origin')
        commit(self.original, 'identity failure', filename='extra.txt',
               content='safe')  # Real Person? local git config is synthetic.
        # Introduce an actual real-identity commit with env overrides.
        real = dict(GIT_AUTHOR_NAME='Real Person', GIT_AUTHOR_EMAIL='real@example.invalid',
                    GIT_COMMITTER_NAME='Real Person', GIT_COMMITTER_EMAIL='real@example.invalid')
        commit(self.original, 'real identity', filename='real.txt',
               content='safe', extra=real)
        with self.assertRaisesRegex(ValueError, 'non-synthetic'):
            preflight.scan(self.original, None, skip_denylist=True)

    def test_skip_denylist_creation_and_runtime_args(self):
        from sandboxlib import settings
        commit(self.original, 'initial')
        cfg = settings.DEFAULT.copy()
        cfg['preparer'] = {'image': 'sandbox/preparer:v1'}
        cfg['runtimes'] = {'base': {'image': 'sandbox/base:v1', 'shell': '/bin/bash',
                                  'venv_volume': False, 'devices': [],
                                  'dockerfile': None, 'context': None}}
        cfg['mounts'] = []
        cfg['shared_tools'] = False
        state = self.root / 'state' / 'instances'
        args = type('Args', (), {'name': 'licensed', 'source': str(self.original),
                                  'anonymize': True, 'denylist': None,
                                  'skip_denylist': True})()
        def fake_prepare(image, source, dest, anonymize, deny, *, skip_denylist=False):
            self.assertTrue(anonymize)
            self.assertTrue(skip_denylist)
            self.assertIsNone(deny)
            # Simulated clean rewritten output, without git-filter-repo dependency.
            prepworker.prepare(source, dest, False, None)
            repo = dest / 'repo'
            # Local test commit is rewritten by forcing the identity for a
            # fresh commit in the sandbox and dropping the original ancestor.
            cmd(repo, 'checkout', '--orphan', 'anonymous')
            cmd(repo, 'add', '-A')
            cmd(repo, 'commit', '-m', 'anonymous baseline', extra=self.anon)
            cmd(repo, 'branch', '-D', 'main')
            cmd(repo, 'branch', '-m', 'main')
            cmd(repo, 'reflog', 'expire', '--expire=now', '--all')
            cmd(repo, 'gc', '--prune=now')
        with mock.patch.object(cli, 'docker_rootless'), \
             mock.patch.object(cli, 'inspect_image', return_value='sha256:' + '1' * 64), \
             mock.patch.object(cli, 'instances_dir', return_value=state), \
             mock.patch.object(cli, 'prepare_container', side_effect=fake_prepare):
            cli.command_create(args, cfg)
            path, meta = cli.load_instance('licensed')
        self.assertTrue(meta['anonymized'])
        self.assertTrue(meta['skip_denylist'])
        self.assertIsNone(meta['denylist'])
        with mock.patch.object(cli, 'pinned_image', return_value='sha256:' + 'b'*64), \
             mock.patch.object(cli, 'remember_volume'), \
             mock.patch.object(cli, 'check_container') as checker:
            cli.runtime_args(path, meta, cfg, 'base', ['sh'])
        checker.assert_not_called()
        self.assertTrue(cli.parser().parse_args(['run', 'licensed', '--skip-denylist']).skip_denylist)
        self.assertTrue(cli.parser().parse_args(['exec', 'licensed', '--skip-denylist']).skip_denylist)
        with self.assertRaisesRegex(ValueError, 'requires --anonymize'):
            cli.command_create(type('Args', (), {'name': 'wrong', 'source': str(self.original),
                'anonymize': False, 'denylist': None, 'skip_denylist': True})(), cfg)

    def test_import_preserves_agent_merge_and_branches(self):
        a = commit(self.original, 'A', content='a')
        b = commit(self.original, 'B', content='b')
        cmd(self.original, 'branch', 'feature', a)
        cmd(self.original, 'checkout', 'feature')
        f = commit(self.original, 'F', filename='feature.txt', content='f')
        cmd(self.original, 'checkout', 'main')
        session = self.root / 'testsession'
        session.mkdir()
        prepworker.prepare(self.original, session, False, None)
        sandbox = session / 'repo'
        commit(sandbox, 'Agent main', 'new.txt', 'new', extra=self.anon)
        cmd(sandbox, 'checkout', 'feature')
        commit(sandbox, 'Agent feature', 'feature2.txt', 'test', extra=self.anon)
        cmd(sandbox, 'checkout', 'main')
        cmd(sandbox, 'merge', '--no-ff', 'feature', '-m', 'Merge agent', extra=self.anon)
        meta = {'source': str(self.original), 'name': 'alpha', 'id': '1234567890'}
        imported = import_branches(session, meta)
        self.assertIn('sandbox/alpha/main', imported)
        self.assertIn('sandbox/alpha/feature', imported)
        review = 'refs/heads/sandbox/alpha/main'
        self.assertEqual(cmd(self.original, 'rev-parse', review + '^{tree}'),
                         cmd(sandbox, 'rev-parse', 'main^{tree}'))
        self.assertEqual(cmd(self.original, 'log', '-1', '--format=%an <%ae>', review),
                         'Real Person <real@example.invalid>')
        self.assertEqual(len(cmd(self.original, 'rev-list', '--parents', '-n', '1', review).split()), 3)
        with self.assertRaisesRegex(ValueError, 'already exist'):
            import_branches(session, meta)
        import_branches(session, meta, force=True)

    def test_create_command_records_mode_and_is_independent(self):
        from sandboxlib import settings
        commit(self.original, 'start')
        cfg = settings.DEFAULT.copy()
        cfg['preparer'] = {'image': 'sandbox/preparer:v1'}
        cfg['runtimes'] = {'base': {'image':'sandbox/base:v1', 'shell':'/bin/bash', 'venv_volume':True, 'devices':[], 'dockerfile':None, 'context':None}, 'python-xpu': {'image':'sandbox/python-xpu:v1', 'shell':'/bin/bash', 'venv_volume':True, 'devices':[], 'dockerfile':None, 'context':None}}
        state = self.root / 'state' / 'instances'
        args = type('Args', (), {'name': 'test-01', 'source': str(self.original),
                                  'anonymize': False, 'denylist': None, 'skip_denylist': False})()
        def fake_prepare(image, source, dest, anonymize, deny, *, skip_denylist=False):
            self.assertFalse(anonymize)
            self.assertFalse(skip_denylist)
            prepworker.prepare(source, dest, False, None)
        with mock.patch.object(cli, 'docker_rootless'), \
             mock.patch.object(cli, 'inspect_image', return_value='sha256:' + 'a' * 64), \
             mock.patch.object(cli, 'instances_dir', return_value=state), \
             mock.patch.object(cli, 'prepare_container', side_effect=fake_prepare):
            cli.command_create(args, cfg)
            path, meta = cli.load_instance('test-01')
        self.assertEqual(meta['source'], str(self.original))
        self.assertFalse(meta['anonymized'])
        self.assertIsNone(meta['denylist'])
        self.assertEqual(cmd(path / 'repo', 'remote'), '')

    def test_anonymized_commit_map_preserves_original_ancestry_on_import(self):
        a = commit(self.original, 'Initial', content='a')
        b = commit(self.original, 'Second', content='b')
        session = self.root / 'anon-session'
        session.mkdir()
        # Simulate the output of git-filter-repo by recreating commit objects
        # with synthetic identities, preserving trees and parent structure.
        repo = session / 'repo'
        cmd(self.root, 'clone', '--no-local', str(self.original), str(repo))
        original_revs = cmd(self.original, 'rev-list', '--reverse', '--topo-order',
                            '--all').splitlines()
        mapping = {}
        for original_oid in original_revs:
            raw = subprocess.run(['git', '-C', str(self.original), 'cat-file', 'commit',
                                  original_oid], capture_output=True, check=True).stdout
            head, _, message = raw.partition(b'\n\n')
            tree = next(x.split()[1].decode() for x in head.splitlines()
                        if x.startswith(b'tree '))
            parents = [x.split()[1].decode() for x in head.splitlines()
                       if x.startswith(b'parent ')]
            flags = []
            for parent in parents:
                flags += ['-p', mapping[parent]]
            mapping[original_oid] = cmd(repo, 'commit-tree', tree, *flags,
                                        data=message, extra=self.anon)
        cmd(repo, 'update-ref', 'refs/heads/main', mapping[b])
        cmd(repo, 'reset', '--hard', mapping[b])
        cmd(repo, 'remote', 'remove', 'origin')
        cmd(repo, 'config', 'user.name', 'Sandbox')
        cmd(repo, 'config', 'user.email', 'sandbox@invalid')
        commit(repo, 'Agent commit', 'new.txt', 'added', extra=self.anon)
        (session / 'commit-map').write_text('old new\n' + ''.join(
            f'{old} {new}\n' for old, new in mapping.items()))
        meta = {'source': str(self.original), 'name': 'anon', 'id': '123abc'}
        import_branches(session, meta)
        tip = 'refs/heads/sandbox/anon/main'
        self.assertEqual(cmd(self.original, 'rev-parse', tip + '^'), b)
        self.assertEqual(cmd(self.original, 'log', '-1', '--format=%an <%ae>', tip),
                         'Real Person <real@example.invalid>')

    def test_shared_tools_mount_and_python_path_preservation(self):
        from sandboxlib import settings
        cfg = settings.DEFAULT.copy()
        cfg['preparer'] = {'image': 'sandbox/preparer:v1'}
        cfg['runtimes'] = {'base': {'image':'sandbox/base:v1', 'shell':'/bin/bash', 'venv_volume':True, 'devices':[], 'dockerfile':None, 'context':None}, 'python-xpu': {'image':'sandbox/python-xpu:v1', 'shell':'/bin/bash', 'venv_volume':True, 'devices':[], 'dockerfile':None, 'context':None}}
        cfg['mounts'] = []
        tools = self.root / 'resources/tools/bin'
        tools.mkdir(parents=True)
        meta = {'id': 'abcd', 'name': 'test', 'anonymized': False,
                'runtime_images': {}, 'volumes': []}
        with mock.patch.object(cli, 'resource_dir', return_value=self.root / 'resources'), \
             mock.patch.object(cli, 'pinned_image', return_value='sha256:' + 'b' * 64), \
             mock.patch.object(cli, 'remember_volume') as volumes:
            first = cli.runtime_args(self.root, meta, cfg, 'python-xpu', ['/bin/bash'])
            second = cli.runtime_args(self.root, meta, cfg, 'python-xpu', ['python', '-V'])
        self.assertIn('sandbox-venvs-bbbbbbbbbbbb-abcd', ' '.join(first))
        self.assertIn('sandbox-venvs-bbbbbbbbbbbb-abcd', ' '.join(second))
        self.assertIn(f'type=bind,src={tools.parent},dst=/opt/tools,readonly', first)
        self.assertIn('export PATH="/opt/tools/bin:$PATH"; exec "$@"', first)
        self.assertNotIn('PATH=/opt/tools/bin:', first)
        self.assertEqual(first[-2:], ['sandbox-entry', '/bin/bash'])
        self.assertEqual(second[-3:], ['sandbox-entry', 'python', '-V'])
        self.assertIn('sandbox-app-data-abcd', ' '.join(first))
        self.assertIn('sandbox-app-config-abcd', ' '.join(first))
        self.assertIn('sandbox-app-cache-abcd', ' '.join(first))

    def test_anonymized_scans_skills_not_shared_tools(self):
        from sandboxlib import settings
        cfg = settings.DEFAULT.copy()
        cfg['preparer'] = {'image': 'sandbox/preparer:v1'}
        cfg['runtimes'] = {'base': {'image':'sandbox/base:v1', 'shell':'/bin/bash', 'venv_volume':True, 'devices':[], 'dockerfile':None, 'context':None}, 'python-xpu': {'image':'sandbox/python-xpu:v1', 'shell':'/bin/bash', 'venv_volume':True, 'devices':[], 'dockerfile':None, 'context':None}}
        cfg['mounts'] = []
        tools = self.root / 'resources/tools/bin'
        tools.mkdir(parents=True)
        skill = self.root / 'skills'
        skill.mkdir()
        cfg['mounts'] = [(str(skill), '/root/.agents/skills', False)]
        meta = {'id': 'abcd', 'name': 'test', 'anonymized': True,
                'denylist': str(self.denylist), 'preparer_image': 'sha256:'+'1'*64,
                'runtime_images': {}, 'volumes': []}
        with mock.patch.object(cli, 'resource_dir', return_value=self.root / 'resources'), \
             mock.patch.object(cli, 'pinned_image', return_value='sha256:' + 'b' * 64), \
             mock.patch.object(cli, 'remember_volume'), \
             mock.patch.object(cli, 'check_container') as checker:
            args = cli.runtime_args(self.root, meta, cfg, 'base', ['/bin/bash'])
        self.assertIn(f'type=bind,src={tools.parent.resolve()},dst=/opt/tools,readonly', args)
        extras = checker.call_args.args[-1]
        self.assertNotIn((tools.parent.resolve(), '/opt/tools'), extras)
        self.assertIn((skill.resolve(), '/root/.agents/skills'), extras)

    def test_generic_mounts_and_core_cli(self):
        from sandboxlib import settings
        self.assertEqual(cli.parser().parse_args(['create', 'test', '/a']).anonymize, False)
        self.assertTrue(cli.parser().parse_args(['create', 'test', '/a', '--anonymize']).anonymize)
        self.assertEqual(cli.main(['exec', 'abc', 'python', '-V']), 2)
        cfg = settings.DEFAULT.copy()
        cfg['mounts'] = [('~/.agents/skills', '/root/.agents/skills', True)]
        with mock.patch.dict(os.environ, {'HOME': str(self.root)}):
            expected = self.root / '.agents/skills'
            expected.mkdir(parents=True)
            mounts = settings.read_only_mounts(cfg)
        self.assertEqual(mounts, [(expected.resolve(), '/root/.agents/skills')])

    def test_duplicate_mount_targets_rejected(self):
        from sandboxlib import settings
        with mock.patch.dict(os.environ, {'HOME': str(self.root)}):
            source = self.root / '.agents/skills'
            source.mkdir(parents=True)
            cfg = {'mounts': [('~/.agents/skills', '/root/.agents/skills', True),
                              ('~/.agents/skills', '/root/.agents/skills', True)]}
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                settings.read_only_mounts(cfg)

    def test_opencode_installer_extracts_standalone_tarball(self):
        import hashlib
        import io
        import tarfile
        project = Path(__file__).resolve().parents[1]
        root = self.root / 'fake'
        root.mkdir()
        archive = self.root / 'release.tar.gz'
        with tarfile.open(archive, 'w:gz') as tf:
            data = b'#!/bin/sh\nprintf "fake opencode\\n"\n'
            info = tarfile.TarInfo('opencode')
            info.size = len(data)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(data))
        fake_bin = root / 'bin'
        fake_bin.mkdir()
        curl = fake_bin / 'curl'
        curl.write_text("""#!/usr/bin/env bash
set -euo pipefail
while (( $# )); do
  if [[ "$1" == "-o" ]]; then
    shift
    cp "$FAKE_ARCHIVE" "$1"
    exit 0
  fi
  shift
done
exit 2
""")
        curl.chmod(0o755)
        env = os.environ.copy()
        env.update(PATH=str(fake_bin) + ':' + env['PATH'],
                   FAKE_ARCHIVE=str(archive),
                   OPENCODE_SHA256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                   OPENCODE_VERSION='2.0.0')
        tools_dir = self.root / 'tools'
        subprocess.run(['bash', str(project / 'tools/install-opencode.sh'), str(tools_dir)],
                       check=True, capture_output=True, env=env)
        self.assertEqual((tools_dir / 'bin/opencode').read_bytes(), data)
        self.assertTrue(os.access(tools_dir / 'bin/opencode', os.X_OK))
        env['OPENCODE_SHA256'] = '0' * 64
        with self.assertRaises(subprocess.CalledProcessError):
            subprocess.run(['bash', str(project / 'tools/install-opencode.sh'), str(tools_dir)],
                           check=True, capture_output=True, env=env)
        self.assertEqual((tools_dir / 'bin/opencode').read_bytes(), data)

    def test_build_command_explicit_targets_only(self):
        from sandboxlib import settings
        cfg = settings.DEFAULT.copy()
        cfg['preparer'] = {'image': 'sandbox/preparer:v1'}
        cfg['runtimes'] = {'python-xpu': {'image': 'sandbox/python-xpu:v1', 'shell': '/bin/bash',
                                      'venv_volume': True, 'devices': [],
                                      'dockerfile': 'bundled:images/python-xpu/Dockerfile',
                                      'context': 'bundled:.'}}
        fake = self.root / 'resources'
        (fake / 'images/python-xpu').mkdir(parents=True)
        (fake / 'images/python-xpu/Dockerfile').write_text('FROM scratch')
        args = type('A', (), {'image': 'python-xpu', 'no_cache': False, 'pull': False})()
        with mock.patch.object(cli, 'docker_rootless'), \
             mock.patch.object(cli, 'resource_dir', return_value=fake), \
             mock.patch.object(cli, 'call') as c:
            cli.command_build(args, cfg)
        command = c.call_args.args[0]
        self.assertEqual(command.count('build'), 1)
        self.assertNotIn('BASE_IMAGE=', ' '.join(command))
        self.assertEqual(cli.parser().parse_args(['build', 'custom']).image, 'custom')

    def test_tools_install_noninteractive_and_preserve_config(self):
        # Run the actual installer without requesting network downloads.
        project = Path(__file__).resolve().parents[1]
        home = self.root / 'home'
        home.mkdir()
        env = os.environ.copy()
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
                   XDG_DATA_HOME=str(home / '.local/share'))
        subprocess.run(['bash', str(project / 'install.sh'), '--no-tools'],
                       env=env, capture_output=True, check=True)
        shared = home / '.local/share/sandbox/tools/bin'
        self.assertTrue(shared.is_dir())
        self.assertTrue((home / '.local/share/sandbox/tool-installers/install-opencode.sh').is_file())
        config = home / '.config/sandbox/config.toml'
        config.write_text('# user customized\n')
        subprocess.run(['bash', str(project / 'install.sh'), '--no-tools'],
                       env=env, capture_output=True, check=True)
        self.assertEqual(config.read_text(), '# user customized\n')

    def test_tools_install_prompts_and_selects_scripts(self):
        # Stub the optional tool installer rather than hitting external networks.
        project = Path(__file__).resolve().parents[1]
        shadow = self.root / 'shadow'
        import shutil
        shutil.copytree(project / 'tools', shadow / 'tools')
        shutil.copy(project / 'install.sh', shadow / 'install.sh')
        shutil.copytree(project / 'bin', shadow / 'bin')
        shutil.copytree(project / 'libexec', shadow / 'libexec')
        shutil.copytree(project / 'images', shadow / 'images')
        shutil.copytree(project / 'examples', shadow / 'examples')
        for tool in ('opencode',):
            file = shadow / 'tools' / f'install-{tool}.sh'
            file.write_text(f'#!/bin/bash\nset -e\nprintf "{tool}\n" >> "$1/installed.txt"\n')
        home = self.root / 'home'
        home.mkdir()
        env = os.environ.copy()
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
                   XDG_DATA_HOME=str(home / '.local/share'))
        subprocess.run(['bash', str(shadow / 'install.sh'), '--tools', 'opencode'],
                       check=True, capture_output=True, env=env)
        installed = (home / '.local/share/sandbox/tools/installed.txt').read_text().splitlines()
        self.assertEqual(installed, ['opencode'])

if __name__ == '__main__':
    unittest.main()
