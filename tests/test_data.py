"""Artifact transfer and per-sandbox mounts, without a Docker daemon."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from sandboxlib import cli, data, settings


class DataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.instance = self.root / 'instances' / 'demo'
        self.repo = self.instance / 'repo'
        (self.repo / '.git').mkdir(parents=True)
        cli.save_instance(self.instance, {'name': 'demo', 'id': 'abc'})
        self.source = self.root / 'artifact.txt'
        self.source.write_text('artifact')
        patch = mock.patch.object(cli, 'instances_dir', return_value=self.instance.parent)
        patch.start()
        self.addCleanup(patch.stop)
        self.cfg = dict(settings.DEFAULT, mounts=[], runtimes={
            'base': dict(image='example:base', shell='/bin/sh', venv_volume=False,
                         devices=[], gpus=None)}, shared_tools=False)

    def command(self, *argv):
        args = cli.parser().parse_args(argv)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            args.handler(args, self.cfg)
        return output.getvalue()

    def meta(self):
        return json.loads((self.instance / 'metadata.json').read_text())

    def runtime(self):
        with mock.patch.object(cli, 'pinned_image', return_value='sha256:' + 'a'*64), \
             mock.patch.object(cli, 'remember_volume'):
            return cli.runtime_args(self.instance, self.meta(), self.cfg, 'base', ['true'])

    def test_send_file_default_and_directory_nested(self):
        self.command('send', 'demo', str(self.source))
        self.assertEqual((self.repo / self.source.name).read_text(), 'artifact')
        self.source.write_text('changed')
        self.assertEqual((self.repo / self.source.name).read_text(), 'artifact')
        folder = self.root / 'dataset'
        (folder / 'nested').mkdir(parents=True)
        (folder / 'nested' / 'run').write_text('script')
        (folder / 'nested' / 'run').chmod(0o755)
        self.command('send', 'demo', str(folder), '/workspace/data')
        result = self.repo / 'data/dataset/nested/run'
        self.assertEqual(result.read_text(), 'script')
        self.assertEqual(result.stat().st_mode & 0o777, 0o755)

    def test_send_rejects_collisions_including_dangling_links_and_directories(self):
        target = self.repo / self.source.name
        for kind in ('file', 'link', 'directory'):
            with self.subTest(kind=kind):
                if kind == 'file':
                    target.write_text('keep')
                elif kind == 'link':
                    target.symlink_to(self.root / 'missing')
                else:
                    target.mkdir()
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    data.send(self.instance, self.source, '/workspace')
                if kind == 'directory':
                    target.rmdir()
                else:
                    target.unlink()

    def test_send_rejects_bad_destinations(self):
        for dest in ('relative', '/etc', '/workspace/../outside',
                     '/workspace/.git', '/workspace/data/.git/hooks'):
            with self.subTest(dest=dest), self.assertRaises(ValueError):
                data.send(self.instance, self.source, dest)
        (self.repo / 'escape').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            data.send(self.instance, self.source, '/workspace/escape')
        self.assertEqual(list(self.instance.glob('.send-*')), [])

    def test_send_checks_destination_after_staging(self):
        original_copy = data._copy

        def replace_destination(source, staged):
            original_copy(source, staged)
            (self.repo / 'data').symlink_to(self.root, target_is_directory=True)

        with mock.patch.object(data, '_copy', side_effect=replace_destination):
            with self.assertRaises(OSError):
                data.send(self.instance, self.source, '/workspace/data')
        self.assertEqual(self.source.read_text(), 'artifact')

    def test_send_rejects_source_symlinks_and_special_files_without_partial_copy(self):
        folder = self.root / 'dataset'
        folder.mkdir()
        (folder / 'first').write_text('ok')
        (folder / 'link').symlink_to(self.source)
        with self.assertRaises(OSError):
            data.send(self.instance, folder, '/workspace')
        self.assertFalse((self.repo / 'dataset').exists())
        with self.assertRaises(ValueError):
            data.send(self.instance, folder / 'link', '/workspace')
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            data.send(self.instance, fifo, '/workspace')
        self.assertEqual(list(self.instance.glob('.send-*')), [])

    def test_send_rejects_recursive_source(self):
        with self.assertRaises(ValueError):
            data.send(self.instance, self.root, '/workspace')

    def test_mount_lifecycle_and_runtime_modes(self):
        self.command('mount', 'demo', str(self.source), '/mnt/reference')
        entry = self.meta()['mounts'][0]
        self.assertTrue(entry['read_only'])
        self.assertIn(f'type=bind,src={self.source},dst=/mnt/reference,readonly', self.runtime())
        self.command('mount', 'demo', str(self.root), '/mnt/output', '--read-write')
        self.assertIn(f'type=bind,src={self.root},dst=/mnt/output', self.runtime())
        self.assertIn('/mnt/reference\tread-only', self.command('mounts', 'demo'))
        self.command('unmount', 'demo', '/mnt/reference')
        self.assertTrue(self.source.exists())
        self.assertEqual(len(self.meta()['mounts']), 1)
        with self.assertRaises(ValueError):
            self.command('unmount', 'demo', '/mnt/reference')

    def test_mount_rejects_invalid_and_overlapping_targets(self):
        for target in ('/mnt', '/workspace/data', '/mnt/../etc', 'relative',
                       '/mnt/a,b', '/mnt/a\nb'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.command('mount', 'demo', str(self.source), target)
        self.command('mount', 'demo', str(self.source), '/mnt/data/nested')
        for target in ('/mnt/data', '/mnt/data/nested', '/mnt/data/nested/child'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.command('mount', 'demo', str(self.source), target)
        self.cfg['mounts'] = [(str(self.root), '/mnt/global', False)]
        with self.assertRaises(ValueError):
            self.command('mount', 'demo', str(self.source), '/mnt/global/child')

    def test_destroy_leaves_mount_sources_untouched(self):
        self.command('mount', 'demo', str(self.source), '/mnt/data', '--read-write')
        with mock.patch.object(cli, 'docker_rootless'), \
             mock.patch.object(cli, 'call', return_value=b''):
            self.command('destroy', 'demo', '--yes')
        self.assertFalse(self.instance.exists())
        self.assertEqual(self.source.read_text(), 'artifact')

    def test_send_has_no_link_option(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parser().parse_args(['send', 'demo', str(self.source), '--link'])

    def test_runtime_revalidates_mounts(self):
        self.command('mount', 'demo', str(self.source), '/mnt/data')
        self.cfg['mounts'] = [(str(self.root), '/mnt', False)]
        with self.assertRaises(ValueError):
            self.runtime()
        self.cfg['mounts'] = []
        self.source.unlink()
        with self.assertRaises(OSError):
            self.runtime()
        self.source.symlink_to(self.root)
        with self.assertRaises(ValueError):
            self.runtime()


if __name__ == '__main__':
    unittest.main()
