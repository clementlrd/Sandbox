"""Real local Git tests; production invokes the same worker through Docker."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from sandboxlib import cli, importer, prepworker, refresher
from sandboxlib.commitmap import CommitMap, transfer_lock
from sandboxlib.common import git_env
from sandboxlib.gitgraph import prepared_commit, verify_export


def cmd(repo, *args, data=None, extra=None):
    env = git_env(anonymized=False)
    if extra:
        env.update(extra)
    return subprocess.run(['git', '-C', str(repo), '-c', 'core.hooksPath=/dev/null', *args],
                          input=data, env=env, capture_output=True, check=True).stdout.decode().strip()


def commit(repo, message, filename='file.txt', content='content', extra=None):
    (repo / filename).parent.mkdir(parents=True, exist_ok=True)
    (repo / filename).write_text(content)
    cmd(repo, 'add', filename)
    cmd(repo, 'commit', '-m', message, extra=extra)
    return cmd(repo, 'rev-parse', 'HEAD')


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        cmd(self.source, 'init', '-b', 'main')
        cmd(self.source, 'config', 'user.name', 'Host Person')
        cmd(self.source, 'config', 'user.email', 'host@example.invalid')
        self.original = commit(self.source, 'Initial', content='initial')
        self.session = self.root / 'session'
        self.session.mkdir(mode=0o700)
        self.repo = self.session / 'repo'
        self.meta = {'source': str(self.source), 'name': 'test', 'id': '1234', 'anonymized': True}
        self.deny = self.root / 'denylist'
        self.deny.write_text('Host Person\nhost@example.invalid\nTeammate\nteam@example.invalid\n')
        self.deny.chmod(0o600)
        self.teammate = {'GIT_AUTHOR_NAME': 'Teammate', 'GIT_AUTHOR_EMAIL': 'team@example.invalid',
                         'GIT_COMMITTER_NAME': 'Teammate', 'GIT_COMMITTER_EMAIL': 'team@example.invalid'}

    def create(self, anonymize=True):
        self.meta['anonymized'] = anonymize
        if anonymize:
            # Simulate creation's filter-repo output without requiring that
            # executable on test hosts. Refresh itself uses the real worker.
            staging = self.root / 'baseline.git'
            cmd(self.root, 'clone', '--bare', '--no-local', str(self.source), str(staging))
            anonymous = prepared_commit(staging, self.original, [], True, git_env())
            self.repo.mkdir()
            cmd(self.repo, 'init', '-b', 'bootstrap')
            cmd(self.repo, 'fetch', '--no-tags', '--no-write-fetch-head', str(staging),
                f'{anonymous}:refs/heads/main')
            cmd(self.repo, 'switch', 'main')
            mapping = CommitMap()
            mapping.add(self.original, anonymous)
            mapping.save(self.session / 'commit-map')
        else:
            prepworker.prepare(self.source, self.session, False, None)
        cmd(self.repo, 'config', 'user.name', 'Sandbox')
        cmd(self.repo, 'config', 'user.email', 'sandbox@invalid')
        self.base = cmd(self.repo, 'rev-parse', 'main')

    def refresh(self, branch='main', *, skip=False, prepare=prepworker.prepare_refresh):
        return refresher.refresh_branch(self.session, self.meta, branch, prepare,
            denylist=self.deny if self.meta['anonymized'] and not skip else None,
            skip_denylist=skip)

    def mapping(self):
        return CommitMap.load(self.session / 'commit-map')

    def assert_missing(self, repo, oid):
        with self.assertRaises(subprocess.CalledProcessError):
            cmd(repo, 'cat-file', '-e', oid)

    def test_round_trips_reuse_commits_and_preserve_teammate(self):
        self.create()
        cmd(self.repo, 'switch', '-c', 'feat/test')
        sandbox = commit(self.repo, 'Feature', 'feature.txt', 'feature', extra=self.teammate)
        importer.import_branches(self.session, self.meta)
        exported = cmd(self.source, 'rev-parse', 'sandbox/test/feat/test')
        self.assertEqual(self.mapping().to_sandbox[exported], sandbox)
        cmd(self.source, 'merge', '--ff-only', 'sandbox/test/feat/test')
        host = commit(self.source, 'Upstream fix', 'fix.txt', 'fix', extra=self.teammate)
        # Runtime changes are not subject to a new full-repository preflight.
        (self.repo / 'local.txt').write_text('Host Person')
        cmd(self.repo, 'remote', 'add', 'unrelated', 'https://example.invalid/repo')
        before = cmd(self.repo, 'status', '--porcelain')
        result = self.refresh()
        incoming = result['new']
        self.assertEqual(cmd(self.repo, 'rev-parse', incoming + '^'), sandbox)
        self.assertEqual(cmd(self.repo, 'rev-parse', 'feat/test'), sandbox)
        self.assertEqual(cmd(self.repo, 'rev-parse', 'main'), self.base)
        self.assertEqual(cmd(self.repo, 'status', '--porcelain'), before)
        self.assertEqual(cmd(self.repo, 'log', '-1', '--format=%an <%ae>', incoming),
                         'Sandbox <sandbox@invalid>')
        self.assert_missing(self.repo, host)
        self.assert_missing(self.repo, exported)
        cmd(self.repo, 'merge', '--ff-only', 'host/main')
        last = commit(self.repo, 'Continue', 'continue.txt', 'more')
        importer.import_branches(self.session, self.meta, force=True)
        review = cmd(self.source, 'rev-parse', 'sandbox/test/feat/test')
        self.assertEqual(cmd(self.source, 'rev-parse', review + '^'), host)
        self.assertEqual(cmd(self.source, 'log', '-1', '--format=%an <%ae>', host),
                         'Teammate <team@example.invalid>')
        self.assertEqual(cmd(self.source, 'rev-parse', 'main'), host)
        self.assertEqual(cmd(self.source, 'rev-parse', 'sandbox/test/main'), self.original)
        self.assertNotIn('sandbox/test/host/main', cmd(self.source, 'branch', '--list'))
        cmd(self.source, 'merge', '--ff-only', 'sandbox/test/feat/test')
        self.assertEqual(self.refresh()['new'], last)
        self.assertEqual(self.refresh()['new'], last)
        self.assertEqual(cmd(self.repo, 'rev-list', '--count', 'host/main'), '4')

    def test_plain_refresh_preserves_original_id_and_ignores_dirty_host(self):
        self.create(False)
        host = commit(self.source, 'Update', content='updated', extra=self.teammate)
        (self.source / 'file.txt').write_text('uncommitted host edit')
        (self.source / 'untracked.txt').write_text('not transferred')
        source_status = cmd(self.source, 'status', '--porcelain')
        (self.repo / 'file.txt').write_text('uncommitted sandbox edit')
        sandbox_status = cmd(self.repo, 'status', '--porcelain')
        self.assertEqual(self.refresh()['new'], host)
        self.assertEqual(self.mapping().to_host[host], host)
        self.assertEqual(cmd(self.source, 'status', '--porcelain'), source_status)
        self.assertEqual(cmd(self.repo, 'status', '--porcelain'), sandbox_status)
        self.assertFalse((self.repo / 'untracked.txt').exists())
        self.assertEqual(cmd(self.repo, 'remote'), '')

    def test_merge_graph_and_local_main_are_preserved(self):
        self.create()
        local = commit(self.repo, 'Local main', 'local-main.txt', 'local')
        main = commit(self.source, 'Host main', 'host-main.txt', 'main')
        cmd(self.source, 'switch', '-c', 'topic', self.original)
        topic = commit(self.source, 'Host topic', 'topic.txt', 'topic', extra=self.teammate)
        cmd(self.source, 'switch', 'main')
        cmd(self.source, 'merge', '--no-ff', 'topic', '-m', 'Merge topic')
        host_merge = cmd(self.source, 'rev-parse', 'main')
        result = self.refresh()
        mapping = self.mapping()
        parents = cmd(self.repo, 'rev-list', '--parents', '-n', '1', result['new']).split()[1:]
        self.assertEqual(parents, [mapping.to_sandbox[main], mapping.to_sandbox[topic]])
        self.assertEqual(cmd(self.repo, 'rev-parse', 'main'), local)
        cmd(self.repo, 'merge', '--no-ff', 'host/main', '-m', 'Integrate host')
        importer.import_branches(self.session, self.meta)
        review = cmd(self.source, 'rev-parse', 'sandbox/test/main')
        parents = cmd(self.source, 'rev-list', '--parents', '-n', '1', review).split()[1:]
        self.assertEqual(parents[1], host_merge)
        self.assertEqual(cmd(self.source, 'rev-parse', 'main'), host_merge)

    def test_rewritten_sandbox_history_keeps_old_correspondences(self):
        self.create()
        cmd(self.repo, 'switch', '-c', 'feat/test')
        old = commit(self.repo, 'First feature', 'feature.txt', 'first')
        importer.import_branches(self.session, self.meta)
        old_host = cmd(self.source, 'rev-parse', 'sandbox/test/feat/test')
        cmd(self.source, 'merge', '--ff-only', 'sandbox/test/feat/test')
        (self.repo / 'feature.txt').write_text('rewritten')
        cmd(self.repo, 'add', 'feature.txt')
        cmd(self.repo, 'commit', '--amend', '-m', 'Rewritten feature')
        rewritten = cmd(self.repo, 'rev-parse', 'HEAD')
        importer.import_branches(self.session, self.meta, force=True)
        new_host = cmd(self.source, 'rev-parse', 'sandbox/test/feat/test')
        self.assertNotEqual(new_host, old_host)
        self.assertEqual(self.mapping().to_host[old], old_host)
        self.assertEqual(self.mapping().to_host[rewritten], new_host)
        # Refresh the still-old host main: do not substitute rewritten history.
        self.assertEqual(self.refresh()['new'], old)
        self.assertEqual(cmd(self.repo, 'rev-parse', 'feat/test'), rewritten)

    def test_rewritten_host_history_is_reported_without_changing_local_main(self):
        self.create()
        old_host = commit(self.source, 'First', content='first')
        old = self.refresh()['new']
        cmd(self.source, 'reset', '--hard', self.original)
        host = commit(self.source, 'Replacement', content='replacement')
        result = self.refresh()
        self.assertTrue(result['non_fast_forward'])
        self.assertEqual(self.mapping().to_sandbox[old_host], old)
        self.assertEqual(self.mapping().to_sandbox[host], result['new'])
        self.assertEqual(cmd(self.repo, 'rev-parse', 'main'), self.base)

    def test_failed_screening_does_not_transfer_raw_or_prepared_objects(self):
        self.create()
        host = commit(self.source, 'Identifying content', content='Host Person')
        mapping_before = (self.session / 'commit-map').read_bytes()
        objects_before = cmd(self.repo, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname)')
        with self.assertRaisesRegex(ValueError, 'denylist match'):
            self.refresh()
        self.assertEqual((self.session / 'commit-map').read_bytes(), mapping_before)
        self.assertEqual(cmd(self.repo, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname)'),
                         objects_before)
        self.assertEqual(cmd(self.repo, 'for-each-ref', 'refs/remotes/host'), '')
        self.assertEqual(list(self.session.glob('.refresh-*')), [])
        self.assert_missing(self.repo, host)
        # Bypass is explicit for this invocation; identity rewriting still runs.
        result = self.refresh(skip=True)
        self.assertNotEqual(result['new'], host)
        self.assert_missing(self.repo, host)

    def test_deleted_historical_content_and_nested_paths_are_screened(self):
        self.create()
        commit(self.source, 'Add', 'safe/file.txt', 'Host Person')
        commit(self.source, 'Remove identifying text', 'safe/file.txt', 'safe')
        with self.assertRaisesRegex(ValueError, 'denylist match'):
            self.refresh()
        cmd(self.source, 'reset', '--hard', self.original)
        self.deny.write_text('private/name\n')
        commit(self.source, 'Nested path', 'private/name/file.txt', 'safe')
        with self.assertRaisesRegex(ValueError, 'denylist match'):
            self.refresh()

    def test_only_selected_branch_is_exported(self):
        self.create()
        main = commit(self.source, 'Selected', 'selected.txt', 'safe')
        cmd(self.source, 'switch', '-c', 'private')
        private = commit(self.source, 'Secret branch', 'private.txt', 'Host Person')
        private_blob = cmd(self.source, 'rev-parse', 'HEAD:private.txt')
        result = self.refresh()
        self.assertEqual(self.mapping().to_sandbox[main], result['new'])
        self.assertNotIn(private, self.mapping().to_sandbox)
        self.assert_missing(self.repo, private)
        self.assert_missing(self.repo, private_blob)
        self.assertEqual(cmd(self.repo, 'for-each-ref', '--format=%(refname)', 'refs/remotes/host'),
                         'refs/remotes/host/main')

    def test_mapping_is_saved_before_refresh_reference_publication_and_retry_reuses_it(self):
        self.create()
        host = commit(self.source, 'Update', content='updated')
        real_git = refresher.git
        def fail_publication(repo, *args, **kwargs):
            if args[:2] == ('update-ref', '--no-deref') and 'refs/remotes/host/main' in args:
                raise subprocess.CalledProcessError(1, ['git', *args])
            return real_git(repo, *args, **kwargs)
        with mock.patch.object(refresher, 'git', side_effect=fail_publication):
            with self.assertRaises(subprocess.CalledProcessError):
                self.refresh()
        prepared = self.mapping().to_sandbox[host]
        self.assertEqual(cmd(self.repo, 'for-each-ref', 'refs/remotes/host'), '')
        self.assertEqual(cmd(self.repo, 'for-each-ref', 'refs/sandbox/refresh'), '')
        self.assertEqual(self.refresh()['new'], prepared)

    def test_mapping_is_saved_before_import_publication_and_retry_preserves_identity(self):
        self.create()
        sandbox = commit(self.repo, 'Update', 'new.txt', 'new')
        real_git = importer.git
        def fail_publication(repo, *args, **kwargs):
            if args[:2] == ('update-ref', '--stdin'):
                raise subprocess.CalledProcessError(1, ['git', *args])
            return real_git(repo, *args, **kwargs)
        with mock.patch.object(importer, 'git', side_effect=fail_publication):
            with self.assertRaises(subprocess.CalledProcessError):
                importer.import_branches(self.session, self.meta)
        exported = self.mapping().to_host[sandbox]
        cmd(self.source, 'config', 'user.name', 'Different host identity')
        importer.import_branches(self.session, self.meta)
        self.assertEqual(cmd(self.source, 'rev-parse', 'sandbox/test/main'), exported)

    def test_concurrent_tracking_update_is_not_overwritten(self):
        self.create()
        first = self.refresh()['new']
        other = commit(self.repo, 'Concurrent local work', 'other.txt', 'other')
        commit(self.source, 'Host update', 'host.txt', 'host')
        real_git = refresher.git
        def race(repo, *args, **kwargs):
            if args[:2] == ('update-ref', '--no-deref') and 'refs/remotes/host/main' in args:
                cmd(self.repo, 'update-ref', 'refs/remotes/host/main', other, first)
            return real_git(repo, *args, **kwargs)
        with mock.patch.object(refresher, 'git', side_effect=race):
            with self.assertRaises(subprocess.CalledProcessError):
                self.refresh()
        self.assertEqual(cmd(self.repo, 'rev-parse', 'host/main'), other)

    def test_import_ref_snapshot_race_is_rejected(self):
        self.create()
        before = (self.session / 'commit-map').read_bytes()
        real_git = importer.git
        def race(repo, *args, **kwargs):
            if args and args[0] == 'fetch':
                commit(self.repo, 'Concurrent change', 'race.txt', 'race')
            return real_git(repo, *args, **kwargs)
        with mock.patch.object(importer, 'git', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'changed during import'):
                importer.import_branches(self.session, self.meta)
        self.assertEqual((self.session / 'commit-map').read_bytes(), before)
        self.assertEqual(cmd(self.source, 'for-each-ref', 'refs/heads/sandbox'), '')

    def test_missing_mapped_objects_fail_explicitly(self):
        self.create()
        host = commit(self.source, 'Host', 'host.txt', 'host')
        mapping = self.mapping()
        mapping.add(host, 'f' * 40)
        mapping.save(self.session / 'commit-map')
        with self.assertRaisesRegex(ValueError, 'mapped sandbox commit .* is missing'):
            self.refresh()
        # Unknown sandbox commits whose recorded host object disappeared are
        # not silently reconstructed using a possibly different host identity.
        sandbox = commit(self.repo, 'Sandbox', 'sandbox.txt', 'sandbox')
        mapping.add('e' * 40, sandbox)
        mapping.save(self.session / 'commit-map')
        with self.assertRaisesRegex(ValueError, 'mapped host commit .* is missing'):
            importer.import_branches(self.session, self.meta)

    def test_lock_blocks_import_and_refresh(self):
        self.create()
        worker = mock.Mock()
        with transfer_lock(self.session):
            with self.assertRaisesRegex(ValueError, 'in progress'):
                self.refresh(prepare=worker)
            with self.assertRaisesRegex(ValueError, 'in progress'):
                importer.import_branches(self.session, self.meta)
        worker.assert_not_called()

    def test_invalid_branch_and_shadowing_local_branch_are_rejected(self):
        self.create()
        for branch in ('missing', '../main', 'main:refs/heads/overwrite'):
            with self.assertRaisesRegex(ValueError, 'host branch'):
                self.refresh(branch)
        cmd(self.repo, 'branch', 'host/main')
        with self.assertRaisesRegex(ValueError, 'shadow'):
            self.refresh()
        cmd(self.repo, 'branch', '-D', 'host/main')
        cmd(self.repo, 'symbolic-ref', 'refs/remotes/host/main', 'refs/heads/main')
        with self.assertRaisesRegex(ValueError, 'symbolic'):
            self.refresh()

    def test_refresh_does_not_change_linked_worktrees(self):
        self.create()
        linked = self.repo / 'linked'
        cmd(self.repo, 'worktree', 'add', '-b', 'parallel', str(linked))
        (linked / 'file.txt').write_text('unfinished work')
        before = cmd(linked, 'status', '--porcelain')
        commit(self.source, 'Host', 'host.txt', 'host')
        self.refresh()
        self.assertEqual(cmd(linked, 'status', '--porcelain'), before)
        self.assertEqual(cmd(linked, 'rev-parse', 'HEAD'), self.base)

    def test_refresh_uses_captured_tip_even_if_host_advances(self):
        self.create()
        captured = commit(self.source, 'Captured', 'captured.txt', 'captured')
        later = []
        def advance_then_prepare(*args, **kwargs):
            later.append(commit(self.source, 'Later', 'later.txt', 'later'))
            prepworker.prepare_refresh(*args, **kwargs)
        result = self.refresh(prepare=advance_then_prepare)
        self.assertEqual(result['host_tip'], captured)
        self.assertNotIn(later[0], self.mapping().to_sandbox)
        self.assertEqual(self.refresh()['host_tip'], later[0])

    def test_signed_non_utf8_commit_is_cleanly_anonymized_but_restored_on_import(self):
        self.create()
        tree = cmd(self.source, 'rev-parse', 'HEAD^{tree}')
        original = (f'tree {tree}\nparent {self.original}\n'
            'author Teammate <team@example.invalid> 1700000000 +0130\n'
            'committer Host Person <host@example.invalid> 1700000010 -0230\n'
            'encoding ISO-8859-1\n'
            'gpgsig -----BEGIN PGP SIGNATURE-----\n Teammate signature\n'
            ' -----END PGP SIGNATURE-----\n\n').encode() + b'Message caf\xe9\n'
        host = cmd(self.source, 'hash-object', '-t', 'commit', '-w', '--stdin', data=original)
        cmd(self.source, 'update-ref', 'refs/heads/main', host)
        # Exercise packed raw and prepared histories, not just loose objects.
        cmd(self.source, 'gc')
        cmd(self.repo, 'gc')
        incoming = self.refresh()['new']
        raw = subprocess.run(['git', '-C', str(self.repo), 'cat-file', 'commit', incoming],
                             env=git_env(), capture_output=True, check=True).stdout
        self.assertIn(b'author Sandbox <sandbox@invalid> 1700000000 +0130\n', raw)
        self.assertIn(b'committer Sandbox <sandbox@invalid> 1700000010 -0230\n', raw)
        self.assertIn(b'encoding ISO-8859-1\n', raw)
        self.assertTrue(raw.endswith(b'Message caf\xe9\n'))
        self.assertNotIn(b'gpgsig', raw)
        self.assertNotIn(b'Teammate', raw)
        self.assert_missing(self.repo, host)
        verify_export(self.repo, git_env())
        cmd(self.repo, 'merge', '--ff-only', 'host/main')
        commit(self.repo, 'Continue', 'continue.txt', 'continue')
        importer.import_branches(self.session, self.meta)
        self.assertEqual(cmd(self.source, 'rev-parse', 'sandbox/test/main^'), host)
        restored = subprocess.run(['git', '-C', str(self.source), 'cat-file', 'commit', host],
                                  capture_output=True, check=True).stdout
        self.assertEqual(restored, original)

    def test_plain_refresh_preserves_signed_commit_exactly_when_parents_unchanged(self):
        self.create(False)
        tree = cmd(self.source, 'rev-parse', 'HEAD^{tree}')
        raw = (f'tree {tree}\nparent {self.original}\n'
            'author Teammate <team@example.invalid> 1700000000 +0000\n'
            'committer Teammate <team@example.invalid> 1700000000 +0000\n'
            'gpgsig -----BEGIN PGP SIGNATURE-----\n signature\n'
            ' -----END PGP SIGNATURE-----\n\nSigned\n').encode()
        host = cmd(self.source, 'hash-object', '-t', 'commit', '-w', '--stdin', data=raw)
        cmd(self.source, 'update-ref', 'refs/heads/main', host)
        self.assertEqual(self.refresh()['new'], host)

    def test_unrelated_host_history_with_no_mapped_boundary(self):
        self.create()
        cmd(self.source, 'switch', '--orphan', 'new-history')
        host = commit(self.source, 'New root', 'root.txt', 'root')
        incoming = self.refresh('new-history')['new']
        self.assertNotEqual(incoming, host)
        self.assertEqual(cmd(self.repo, 'rev-list', '--count', incoming), '1')
        self.assert_missing(self.repo, host)

    def test_unicode_branch_names_and_tracking_ref_screening(self):
        self.create()
        cmd(self.source, 'branch', 'feat/café')
        result = self.refresh('feat/café')
        self.assertEqual(result['ref'], 'refs/remotes/host/feat/café')
        cmd(self.source, 'branch', 'private/Teammate')
        with self.assertRaisesRegex(ValueError, 'denylist match'):
            self.refresh('private/Teammate')

    def test_export_validation_rejects_unreachable_objects(self):
        self.create()
        cmd(self.repo, 'hash-object', '-w', '--stdin', data=b'residual original metadata')
        with self.assertRaisesRegex(ValueError, 'outside its reachable history'):
            verify_export(self.repo, git_env())

    def test_sha256_plain_history(self):
        # Creation and refresh must use the source's object format, not assume SHA-1.
        self.source = self.root / 'sha256'
        self.source.mkdir()
        cmd(self.source, 'init', '-b', 'main', '--object-format=sha256')
        cmd(self.source, 'config', 'user.name', 'Host Person')
        cmd(self.source, 'config', 'user.email', 'host@example.invalid')
        self.original = commit(self.source, 'Initial')
        self.meta['source'] = str(self.source)
        self.create(False)
        host = commit(self.source, 'Host', 'host.txt', 'host')
        self.assertEqual(len(self.refresh()['new']), 64)
        self.assertEqual(cmd(self.repo, 'rev-parse', 'host/main'), host)
        cmd(self.repo, 'merge', '--ff-only', 'host/main')
        commit(self.repo, 'Sandbox', 'sandbox.txt', 'sandbox')
        importer.import_branches(self.session, self.meta)
        self.assertEqual(cmd(self.source, 'rev-parse', 'sandbox/test/main^'), host)

    def test_cli_routes_to_docker_and_does_not_inherit_creation_bypass(self):
        self.create()
        self.meta['denylist_checked'] = False
        args = cli.parser().parse_args(['refresh', 'test', '--branch', 'main',
                                       '--denylist', str(self.deny)])
        with mock.patch.object(cli, 'load_instance', return_value=(self.session, self.meta)), \
             mock.patch.object(cli, 'docker_rootless'), \
             mock.patch.object(cli, 'inspect_image', return_value='sha256:image'), \
             mock.patch.object(cli, 'prepare_refresh_container', side_effect=lambda image, *a, **kw:
                               prepworker.prepare_refresh(*a, **kw)) as worker:
            cli.command_refresh(args, {'preparer': {'image': 'sandbox/preparer:v1'}})
        self.assertFalse(worker.call_args.kwargs['skip_denylist'])
        self.assertEqual(worker.call_args.args[0], 'sha256:image')
        with mock.patch.object(cli, 'call') as call:
            cli.prepare_refresh_container('sha256:image', self.source, self.repo,
                self.session / 'commit-map', self.root, self.original, True, self.deny,
                branch='main')
        command = call.call_args.args[0]
        self.assertEqual(command[command.index('--network') + 1], 'none')
        self.assertIn('type=bind,src=' + str(self.source) + ',dst=/source,readonly', command)
        self.assertIn('type=bind,src=' + str(self.repo) + ',dst=/sandbox,readonly', command)
        self.assertEqual(command[command.index('sha256:image') + 1], 'refresh')
        with self.assertRaises(SystemExit):
            cli.parser().parse_args(['refresh', 'test'])
        self.meta['anonymized'] = False
        with mock.patch.object(cli, 'load_instance', return_value=(self.session, self.meta)):
            with self.assertRaisesRegex(ValueError, 'anonymized'):
                cli.command_refresh(args, {})


class MappingTests(unittest.TestCase):
    def test_legacy_format_and_atomic_growth(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'commit-map'
            path.write_text('old new\n' + 'a' * 40 + ' ' + 'b' * 40 + '\n')
            mapping = CommitMap.load(path)
            mapping.add('c' * 40, 'd' * 40)
            mapping.save(path)
            self.assertEqual(CommitMap.load(path).to_sandbox, mapping.to_sandbox)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with mock.patch('sandboxlib.commitmap.os.replace', side_effect=OSError('failure')):
                mapping.add('e' * 40, 'f' * 40)
                with self.assertRaises(OSError):
                    mapping.save(path)
            self.assertNotIn('e' * 40, CommitMap.load(path).to_sandbox)
            self.assertEqual(list(path.parent.glob('.commit-map-*')), [])

    def test_ambiguous_and_malformed_pairs_fail_without_overwriting(self):
        mapping = CommitMap()
        mapping.add('a' * 40, 'b' * 40)
        for host, sandbox in (('a' * 40, 'c' * 40), ('c' * 40, 'b' * 40)):
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                mapping.add(host, sandbox)
        with self.assertRaisesRegex(ValueError, 'invalid'):
            mapping.add('x' * 40, 'y' * 40)


if __name__ == '__main__':
    unittest.main()
