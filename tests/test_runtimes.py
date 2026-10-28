"""External runtimes must work without name-specific code or Docker availability."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from sandboxlib import cli, settings


class RuntimeProfiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'config'
        self.config.mkdir()

    def load_text(self, content):
        (self.config / 'config.toml').write_text(content)
        with mock.patch.object(settings, 'config_dir', return_value=self.config):
            return settings.load()

    def test_custom_image_profile_without_build_file(self):
        cfg = self.load_text('''
[runtimes.node]
image = "example/node:22"
shell = "/bin/sh"
venv_volume = false
''')
        self.assertEqual(cfg['runtimes']['node']['image'], 'example/node:22')
        self.assertEqual(cfg['runtimes']['node']['shell'], '/bin/sh')
        self.assertFalse(cfg['runtimes']['node']['venv_volume'])
        self.assertEqual(cli.runtime_definition('node', cfg)['devices'], [])
        with self.assertRaisesRegex(ValueError, 'unknown runtime'):
            cli.runtime_definition('unknown', cfg)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            cli.command_runtimes(None, cfg)
        self.assertIn('node', output.getvalue())
        self.assertIn('external', output.getvalue())

    def test_builtins_legacy_and_overrides(self):
        cfg = self.load_text('''
[sandbox]
gpu_devices = ["/dev/dri/renderD128"]
[images]
base = "my/base:1"
[runtimes.python-xpu]
devices = ["/dev/null"]
venv_volume = false
''')
        self.assertEqual(cfg['runtimes']['base']['image'], 'my/base:1')
        self.assertEqual(cfg['runtimes']['python-xpu']['devices'], ['/dev/null'])
        self.assertFalse(cfg['runtimes']['python-xpu']['venv_volume'])

    def test_rejects_bad_runtime_configuration(self):
        bad_cases = [
            '[runtimes.NAME]\nimage="repo:x"\n',
            '[runtimes.node]\nshell="/bin/sh"\n',
            '[runtimes.node]\nimage="repo:x"\nshell="../bin/bash"\n',
            '[runtimes.node]\nimage="repo:x"\ndevices=["/etc/passwd"]\n',
            '[runtimes.node]\nimage="repo:x"\ncontext="/somewhere"\n',
            '[runtimes.node]\nimage="repo:x"\nunknown=1\n',
            '[runtimes.preparer]\nimage="repo:x"\n',
        ]
        for config in bad_cases:
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.load_text(config)

    def test_command_mounts_any_runtime_device_not_just_python_xpu(self):
        cfg = self.load_text('''
[sandbox]
shared_tools = false
[runtimes.node]
image = "example/node:22"
shell = "/bin/sh"
venv_volume = false
devices = ["/dev/null"]
''')
        meta = {'id': 'abcdef', 'name': 'custom', 'anonymized': False,
                'runtime_images': {}, 'volumes': []}
        with mock.patch.object(cli, 'pinned_image', return_value='sha256:'+'a'*64), \
             mock.patch.object(cli, 'remember_volume'):
            args = cli.runtime_args(self.root, meta, cfg, 'node', ['node','--version'])
        self.assertIn('/dev/null:/dev/null:rw', args)
        self.assertNotIn('sandbox-venvs-', ' '.join(args))
        self.assertIn('sandbox-app-data-abcdef', ' '.join(args))
        self.assertEqual(args[-2:], ['node', '--version'])

    def test_custom_build_context_and_cache(self):
        build_dir = self.config / 'runtimes/node'
        build_dir.mkdir(parents=True)
        (build_dir / 'Dockerfile').write_text('FROM ubuntu:24.04\n')
        cfg = self.load_text('''
[runtimes.node]
image = "example/node:22"
dockerfile = "runtimes/node/Dockerfile"
context = "runtimes/node"
''')
        args = type('Args', (), {'image': 'node', 'no_cache': False, 'pull': False})()
        with mock.patch.object(cli, 'docker_rootless'), \
             mock.patch.object(cli, 'config_dir', return_value=self.config), \
             mock.patch.object(cli, 'call') as caller:
            cli.command_build(args, cfg)
        command = caller.call_args.args[0]
        self.assertEqual(command[0:3], ['docker','build','--progress=plain'])
        self.assertEqual(command[-1], str(build_dir))
        self.assertEqual(command[command.index('-t')+1], 'example/node:22')
        self.assertNotIn('--no-cache', command)
        args.image = 'node'
        cfg['runtimes']['node']['dockerfile'] = None
        with mock.patch.object(cli, 'docker_rootless'), self.assertRaisesRegex(ValueError, 'externally'):
            cli.command_build(args, cfg)

    def test_run_uses_runtime_configured_shell(self):
        cfg = self.load_text('''
[runtimes.node]
image = "example/node:22"
shell = "/bin/sh"
''')
        args = cli.parser().parse_args(['run','agent','--runtime','node'])
        with mock.patch.object(cli, 'load_instance', return_value=(self.root, {'name':'agent'})), \
             mock.patch.object(cli, 'docker_rootless'), \
             mock.patch.object(cli, 'runtime_args', return_value=['echo','ok']) as runtime_args, \
             mock.patch.object(cli.subprocess, 'run'):
            cli.command_run(args, cfg)
        self.assertEqual(runtime_args.call_args.args[-1], ['/bin/sh'])

if __name__ == '__main__':
    unittest.main()
