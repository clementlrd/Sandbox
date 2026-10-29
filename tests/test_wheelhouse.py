"""Trusted publication is immutable, deduplicated, and never writable in a sandbox."""
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from sandboxlib import cli, wheelhouse


class Wheelhouse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.image = 'sha256:' + 'a' * 64
        patch = mock.patch.object(wheelhouse, 'resource_dir', return_value=self.root / 'resources')
        patch.start()
        self.addCleanup(patch.stop)

    def wheel(self, payload=''):
        wheel = self.root / 'demo-1.0-py3-none-any.whl'
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('demo-1.0.dist-info/METADATA', 'Name: demo\nVersion: 1.0\n')
            archive.writestr('demo/__init__.py', payload)
        return wheel

    def test_publishing_is_idempotent_and_immutable(self):
        source = self.wheel()
        profile = wheelhouse.publish(self.image, [source])
        wheelhouse.publish(self.image, [source])
        self.assertTrue((profile / source.name).is_file())
        self.wheel('different code')
        with self.assertRaisesRegex(ValueError, 'immutable'):
            wheelhouse.publish(self.image, [source])

    def test_identical_artifacts_share_storage_but_profiles_are_separate(self):
        source = self.wheel()
        first = wheelhouse.publish(self.image, [source])
        second = wheelhouse.publish('sha256:' + 'b' * 64, [source])
        self.assertNotEqual(first, second)
        self.assertEqual((first / source.name).stat().st_ino, (second / source.name).stat().st_ino)
        self.assertEqual(len(list((wheelhouse.store_dir() / 'objects').iterdir())), 1)

    def test_rejects_tags_and_metadata_mismatch(self):
        for image in ('latest', '../escape', 'sha256:abc'):
            with self.assertRaises(ValueError):
                wheelhouse.profile_dir(image)
        source = self.wheel()
        renamed = source.with_name('other-1.0-py3-none-any.whl')
        source.rename(renamed)
        with self.assertRaisesRegex(ValueError, 'metadata'):
            wheelhouse.publish(self.image, [renamed])

    def test_rejects_non_wheels_without_extracting(self):
        source = self.root / 'bad-1.0-py3-none-any.whl'
        source.write_text('not a zip file')
        with self.assertRaisesRegex(ValueError, 'invalid wheel'):
            wheelhouse.publish(self.image, [source])

    def test_matching_profile_is_mounted_read_only(self):
        profile = wheelhouse.publish(self.image, [self.wheel()])
        cfg = {'shared_tools': False, 'mounts': [], 'memory': '8g', 'pids_limit': 512,
               'network': 'none', 'forward_env': [], 'runtimes': {'custom': {
                   'image': 'custom:v1', 'venv_volume': True, 'devices': [], 'gpus': None}}}
        meta = {'id': 'test', 'name': 'test', 'volumes': []}
        with mock.patch.object(cli, 'pinned_image', return_value=self.image), \
             mock.patch.object(cli, 'remember_volume'):
            args = cli.runtime_args(self.root, meta, cfg, 'custom', ['python'])
        self.assertIn(f'type=bind,src={profile},dst=/opt/sandbox/wheels,readonly', args)
        self.assertIn('SANDBOX_PYTHON_WHEELS=/opt/sandbox/wheels', args)
        self.assertIn('UV_CACHE_DIR=/opt/venvs/.uv-cache', args)
        with mock.patch.object(cli, 'pinned_image', return_value='sha256:' + 'b' * 64), \
             mock.patch.object(cli, 'remember_volume'):
            args = cli.runtime_args(self.root, meta, cfg, 'custom', ['python'])
        self.assertNotIn('SANDBOX_PYTHON_WHEELS=/opt/sandbox/wheels', args)


if __name__ == '__main__':
    unittest.main()
