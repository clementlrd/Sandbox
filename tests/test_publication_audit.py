"""A publication audit permits real Git identities/remotes, but scans history."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from sandboxlib import cli, preflight


class PublicationAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.repo = root / 'project'
        self.repo.mkdir()
        self.deny = root / 'denylist.txt'
        self.deny.write_text('SECRET_PUBLICATION_MARKER_DO_NOT_SHARE\n', encoding='utf-8')
        self.deny.chmod(0o600)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Real Developer')
        self.git('config', 'user.email', 'developer@example.invalid')
        self.git('remote', 'add', 'origin', 'https://example.invalid/repo.git')
        (self.repo / 'README.md').write_text('hello\n')
        self.git('add', 'README.md')
        self.git('commit', '-qm', 'first commit')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args],
                              check=True, capture_output=True)

    def test_publication_audit_allows_real_authors_and_remotes(self):
        args = cli.parser().parse_args(['audit', str(self.repo), '--denylist', str(self.deny)])
        output = StringIO()
        with redirect_stdout(output):
            cli.command_audit(args, {})
        self.assertIn('Audit passed:', output.getvalue())
        # The normal sandbox preflight still rejects an ordinary source repository.
        with self.assertRaisesRegex(ValueError, 'remotes'):
            preflight.scan(self.repo, self.deny)

    def test_audit_finds_secret_in_deleted_git_history(self):
        marker = 'SECRET_PUBLICATION_MARKER_DO_NOT_SHARE'
        sensitive = self.repo / 'deleted.txt'
        sensitive.write_text('previous value: ' + marker)
        self.git('add', 'deleted.txt')
        self.git('commit', '-qm', 'sensitive historical content')
        sensitive.unlink()
        self.git('add', '-u')
        self.git('commit', '-qm', 'remove sensitive file')

        stderr = StringIO()
        with redirect_stderr(stderr):
            with self.assertRaisesRegex(ValueError, 'denylist match'):
                preflight.scan(self.repo, self.deny, enforce_identity=False,
                               require_no_remotes=False)
        self.assertIn('denylist term #1', stderr.getvalue())
        self.assertNotIn(marker, stderr.getvalue())

    def test_audit_checks_remote_url_without_forbidding_remotes(self):
        self.git('remote', 'set-url', 'origin',
                 'https://example.invalid/SECRET_PUBLICATION_MARKER_DO_NOT_SHARE.git')
        with self.assertRaisesRegex(ValueError, 'denylist match'):
            preflight.scan(self.repo, self.deny, enforce_identity=False,
                           require_no_remotes=False)

    def test_parser_accepts_default_current_directory(self):
        args = cli.parser().parse_args(['audit'])
        self.assertEqual(args.repository, '.')
        self.assertIs(args.handler, cli.command_audit)


if __name__ == '__main__':
    unittest.main()
