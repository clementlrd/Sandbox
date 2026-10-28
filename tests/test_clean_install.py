"""Strict config, bundled paths, and safe uninstall upgrade behavior."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'libexec'))
from sandboxlib import cli, settings


class CleanInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home),
                        XDG_DATA_HOME=str(self.home / 'data'),
                        XDG_CONFIG_HOME=str(self.home / 'config'),
                        XDG_STATE_HOME=str(self.home / 'state'))

    def install(self):
        subprocess.run(['bash', str(PROJECT / 'install.sh'), '--no-tools'],
                       check=True, capture_output=True, text=True, env=self.env)

    def test_example_config_and_bundled_runtime_build(self):
        self.install()
        with mock.patch.dict(os.environ, self.env), mock.patch.object(settings, 'config_dir', return_value=self.home / 'config/sandbox'):
            cfg = settings.load()
            self.assertEqual(cfg['default_runtime'], 'base')
            self.assertEqual(cfg['preparer']['image'], 'sandbox/preparer:v1')
            self.assertEqual(cfg['runtimes']['python-xpu']['devices'], [])
            self.assertEqual(cli.selected_runtime(None, cfg), 'base')
            self.assertNotIn('images', cfg)
            self.assertNotIn('_legacy', cfg)
            target = cli.resolve_build_path(cfg['runtimes']['base']['dockerfile'])
            self.assertEqual(target, self.home / 'data/sandbox/images/base/Dockerfile')
            with self.assertRaises(ValueError):
                cli.resolve_build_path('bundled:../secret')

    def test_clean_install_replaces_managed_code_not_user_tools(self):
        self.install()
        data = self.home / 'data/sandbox'
        lib = self.home / '.local/libexec/sandbox'
        (data / 'images/opencode').mkdir()
        (data / 'images/opencode/Dockerfile').write_text('FROM scratch\n')
        (data / 'libexec/sandboxlib/old.py').write_text('# obsolete\n')
        (lib / 'sandboxlib/old.py').write_text('# obsolete\n')
        (data / 'tools/bin/opencode').write_text('user tool\n')
        self.install()
        self.assertFalse((data / 'images/opencode').exists())
        self.assertFalse((data / 'libexec/sandboxlib/old.py').exists())
        self.assertFalse((lib / 'sandboxlib/old.py').exists())
        self.assertEqual((data / 'tools/bin/opencode').read_text(), 'user tool\n')

    def test_uninstall_preserves_state_and_requires_empty_instances_for_purge(self):
        self.install()
        data = self.home / 'data/sandbox'
        config = self.home / 'config/sandbox'
        state = self.home / 'state/sandbox'
        instances = state / 'instances/agent'
        instances.mkdir(parents=True)
        (instances / 'metadata.json').write_text('{}')
        (data / 'tools/bin/opencode').write_text('user tool\n')
        failed = subprocess.run(['bash', str(PROJECT / 'uninstall.sh'), '--purge-data', '--yes'],
                                capture_output=True, text=True, env=self.env)
        self.assertNotEqual(failed.returncode, 0)
        self.assertTrue(instances.exists())
        subprocess.run(['bash', str(PROJECT / 'uninstall.sh')], check=True,
                       capture_output=True, text=True, env=self.env)
        self.assertFalse((self.home / '.local/bin/sandbox').exists())
        self.assertTrue((data / 'tools/bin/opencode').exists())
        self.assertTrue((config / 'config.toml').exists())
        self.assertTrue(instances.exists())
        shutil.rmtree(instances)
        subprocess.run(['bash', str(PROJECT / 'uninstall.sh'), '--purge-data', '--yes'],
                       check=True, capture_output=True, text=True, env=self.env)
        self.assertFalse(data.exists())
        self.assertFalse(config.exists())
        self.assertFalse(state.exists())

    def test_old_config_keys_rejected(self):
        self.install()
        path = self.home / 'config/sandbox/config.toml'
        text = path.read_text()
        for old in ('[images]\nbase = "old"\n',
                    'gpu_devices = []\n',
                    'mount_global_skills = true\n'):
            path.write_text(text.replace('shared_tools = true', 'shared_tools = true\n'+old)
                            if old != '[images]\nbase = "old"\n' else text + '\n'+old)
            with self.subTest(old=old), mock.patch.object(settings, 'config_dir', return_value=path.parent):
                with self.assertRaises(ValueError):
                    settings.load()

if __name__ == '__main__':
    unittest.main()
