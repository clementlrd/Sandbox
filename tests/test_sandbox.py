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
        cfg['images'] = settings.IMAGES.copy()
        state = self.root / 'state' / 'instances'
        args = type('Args', (), {'name': 'test-01', 'source': str(self.original),
                                  'anonymize': False, 'denylist': None})()
        def fake_prepare(image, source, dest, anonymize, deny):
            self.assertFalse(anonymize)
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

    def test_gpu_volume_shared_between_shell_and_opencode(self):
        from sandboxlib import settings
        cfg = settings.DEFAULT.copy()
        cfg['images'] = settings.IMAGES.copy()
        cfg['gpu_devices'] = []
        with mock.patch.object(cli, 'pinned_image', side_effect=lambda meta, path, profile, cfg:
                               'sha256:' + ('b' * 64 if profile == 'python-xpu' else 'a' * 64)), \
             mock.patch.object(cli, 'remember_volume') as volumes, \
             mock.patch.object(cli, 'app_mounts', return_value=[]):
            meta = {'id': 'abcd', 'name': 'test', 'anonymized': False, 'runtime_images': {}, 'volumes': []}
            first = cli.runtime_args(self.root, meta, cfg, 'python-xpu', 'shell', ['python'])
            second = cli.runtime_args(self.root, meta, cfg, 'opencode-xpu', 'opencode', ['opencode'])
        self.assertIn('sandbox-venvs-bbbbbbbbbbbb-abcd', ' '.join(first))
        self.assertIn('sandbox-venvs-bbbbbbbbbbbb-abcd', ' '.join(second))

    def test_mount_options_and_profile(self):
        self.assertEqual(cli.image_profile('opencode', 'python-xpu'), 'opencode-xpu')
        self.assertEqual(cli.image_profile('shell', 'base'), 'base')
        with self.assertRaises(ValueError):
            cli.image_profile('shell', 'bogus')
        self.assertEqual(cli.parser().parse_args(['create', 'test', '/a']).anonymize, False)
        self.assertTrue(cli.parser().parse_args(['create', 'test', '/a', '--anonymize']).anonymize)
        self.assertEqual(cli.main(['exec', 'abc', 'python', '-V']), 2)

    def test_build_command_does_not_autobuild_parent(self):
        from sandboxlib import settings
        cfg = settings.DEFAULT.copy()
        cfg['images'] = settings.IMAGES.copy()
        fake = self.root / 'resources'
        (fake / 'images/opencode').mkdir(parents=True)
        (fake / 'images/opencode/Dockerfile').write_text('FROM scratch')
        args = type('A', (), {'image': 'opencode-xpu', 'no_cache': False, 'pull': False})()
        with mock.patch.object(cli, 'docker_rootless'), \
             mock.patch.object(cli, 'inspect_image', return_value='sha256:abc'), \
             mock.patch.object(cli, 'resource_dir', return_value=fake), \
             mock.patch.object(cli, 'call') as c:
            cli.command_build(args, cfg)
        command = c.call_args.args[0]
        self.assertEqual(command.count('build'), 1)
        self.assertIn('BASE_IMAGE=sandbox/python-xpu:v1', command)

if __name__ == '__main__':
    unittest.main()
