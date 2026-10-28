"""Host-side setup recipes are explicit, private and independent of agent files."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from sandboxlib import cli, settings


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.recipes = self.root / 'recipes'
        self.recipes.mkdir(mode=0o700)
        self.cfg = {'default_runtime': 'base',
                    'runtimes': {'python-xpu': {'shell': '/bin/bash'}},
                    'setup': {'recipes_dir': str(self.recipes),
                              'confirm_host_execution': True}}
        self.meta = {'name': 'agent', 'id': 'abc123', 'setup_recipes': {}}
        self.script = self.recipes / 'example.sh'
        self.script.write_text('#!/bin/sh\nexit 0\n')
        self.script.chmod(0o700)

    def test_parser_supports_listing_setup_and_run_shortcut(self):
        p = cli.parser()
        self.assertEqual(p.parse_args(['setup', 'list']).name, 'list')
        self.assertEqual(p.parse_args(['setup', 'agent', 'opencode-openai',
                                      '--runtime', 'python-xpu']).runtime, 'python-xpu')
        args = p.parse_args(['run', 'agent', '--runtime', 'python-xpu',
                             '--setup', 'opencode-openai', '--yes'])
        self.assertEqual(args.setup, ['opencode-openai'])
        self.assertTrue(args.yes)

    def test_config_setup_section(self):
        config = self.root / 'config'
        config.mkdir()
        (config / 'config.toml').write_text('[sandbox]\ndefault_runtime = "base"\n'
                                            '[preparer]\nimage = "sandbox/preparer:v1"\n'
                                            '[runtimes.base]\nimage = "sandbox/base:v1"\n'
                                            '[setup]\nrecipes_dir = "~/recipes"\n'
                                            'confirm_host_execution = false\n')
        with mock.patch.object(settings, 'config_dir', return_value=config):
            obj = settings.load()
        self.assertFalse(obj['setup']['confirm_host_execution'])
        self.assertEqual(obj['setup']['recipes_dir'], '~/recipes')

    def test_no_untrusted_paths_or_permissions(self):
        self.assertEqual(cli.recipe_script(self.cfg, 'example'), self.script)
        for name in ('../example', '.', 'foo/bar', '--help', 'a.b'):
            with self.assertRaises(ValueError):
                cli.recipe_script(self.cfg, name)
        link = self.recipes / 'malicious.sh'
        link.symlink_to(self.script)
        with self.assertRaises(ValueError):
            cli.recipe_script(self.cfg, 'malicious')
        self.script.chmod(0o722)
        with self.assertRaises(ValueError):
            cli.recipe_script(self.cfg, 'example')

    def test_confirmation_required_noninteractively(self):
        with mock.patch.object(cli, 'load_instance', return_value=(self.root, self.meta)), \
             mock.patch.object(cli.sys.stdin, 'isatty', return_value=False):
            with self.assertRaisesRegex(ValueError, 'confirmation'):
                cli.apply_setup('agent', 'example', self.cfg)

    def test_records_success_only_and_skips_without_force(self):
        self.cfg['setup']['confirm_host_execution'] = False
        def load(_): return self.root, self.meta
        def save(_, meta): self.meta = meta
        with mock.patch.object(cli, 'load_instance', side_effect=load), \
             mock.patch.object(cli, 'save_instance', side_effect=save), \
             mock.patch.object(cli, 'resource_dir', return_value=self.root), \
             mock.patch.object(cli.subprocess, 'run') as runner:
            cli.apply_setup('agent', 'example', self.cfg, runtime='python-xpu')
            self.assertIn('example', self.meta['setup_recipes'])
            self.assertEqual(runner.call_args.kwargs['env']['SANDBOX_RUNTIME'], 'python-xpu')
            cli.apply_setup('agent', 'example', self.cfg)
            self.assertEqual(runner.call_count, 1)
            cli.apply_setup('agent', 'example', self.cfg, force=True)
            self.assertEqual(runner.call_count, 2)

    def test_failed_recipe_not_marked_successful(self):
        self.cfg['setup']['confirm_host_execution'] = False
        with mock.patch.object(cli, 'load_instance', return_value=(self.root, self.meta)), \
             mock.patch.object(cli.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'script')):
            with self.assertRaises(subprocess.CalledProcessError):
                cli.apply_setup('agent', 'example', self.cfg)
        self.assertNotIn('example', self.meta['setup_recipes'])

    def test_install_preserves_existing_recipes(self):
        project = Path(__file__).resolve().parents[1]
        home = self.root / 'home'
        home.mkdir()
        env = os.environ.copy()
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
                   XDG_DATA_HOME=str(home / '.local/share'))
        subprocess.run(['bash', str(project / 'install.sh'), '--no-tools'],
                       check=True, capture_output=True, env=env)
        recipe = home / '.config/sandbox/recipes/opencode-openai.sh'
        self.assertTrue(recipe.is_file())
        self.assertTrue(os.access(recipe, os.X_OK))
        recipe.write_text('customized\n')
        subprocess.run(['bash', str(project / 'install.sh'), '--no-tools'],
                       check=True, capture_output=True, env=env)
        self.assertEqual(recipe.read_text(), 'customized\n')

    def test_list_recipes_does_not_need_docker(self):
        args = cli.parser().parse_args(['setup', 'list'])
        with mock.patch.object(cli, 'docker_rootless', side_effect=AssertionError('unexpected docker')):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                cli.command_setup(args, self.cfg)
        self.assertEqual(out.getvalue().strip(), 'example')

    def test_run_applies_requested_recipe_before_shell(self):
        args = cli.parser().parse_args(['run', 'agent', '--runtime', 'python-xpu',
                                        '--setup', 'example', '--yes'])
        cfg = dict(self.cfg, shared_tools=False)
        with mock.patch.object(cli, 'load_instance', return_value=(self.root, self.meta)), \
             mock.patch.object(cli, 'docker_rootless'), \
             mock.patch.object(cli, 'apply_setup') as apply, \
             mock.patch.object(cli, 'runtime_args', return_value=['echo', 'inside']), \
             mock.patch.object(cli.subprocess, 'run') as runner:
            cli.command_run(args, cfg)
        apply.assert_called_once_with('agent', 'example', cfg, force=False, yes=True, runtime='python-xpu')
        runner.assert_called_once_with(['echo', 'inside'], check=True)

    def test_builtin_opencode_recipe_pipes_auth_and_removes_tempfile(self):
        project = Path(__file__).resolve().parents[1]
        bin_dir = self.root / 'tools/bin'
        bin_dir.mkdir(parents=True)
        exported = b'{"provider":"openai","secret":"a-private-test-value"}\n'
        opencode = bin_dir / 'opencode'
        opencode.write_text('''#!/bin/sh
if [ "$1 $2 $3" = "auth export openai" ]; then
  printf '%s\n' '{"provider":"openai","secret":"a-private-test-value"}'
elif [ "$1 $2" = "auth import" ]; then
  cp "$3" "$CAPTURED_BYTES"
  printf '%s\n' "$3" > "$TEMP_PATH"
else
  exit 5
fi
''')
        opencode.chmod(0o700)
        receiver = self.root / 'sandbox'
        receiver.write_text('''#!/bin/sh
[ "$1" = exec ] || exit 9
shift  # exec
[ "$1" = agent ] || exit 9
shift  # agent
[ "$1" = --runtime ] || exit 9
shift  # --runtime
[ "$1" = python-xpu ] || exit 9
shift  # python-xpu
[ "$1" = -- ] || exit 9
shift  # --
exec "$@"
''')
        receiver.chmod(0o700)
        dest = self.root / 'imported'
        temp_path = self.root / 'temp-path'
        env = os.environ.copy()
        env.update(PATH=str(bin_dir) + ':' + env['PATH'],
                   SANDBOX_CLI=str(receiver), SANDBOX_TOOLS_DIR=str(self.root / 'tools'),
                   SANDBOX_RUNTIME='python-xpu', CAPTURED_BYTES=str(dest),
                   TEMP_PATH=str(temp_path))
        proc = subprocess.run(['bash', str(project / 'recipes/opencode-openai.sh'), 'agent'],
                              capture_output=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(dest.read_bytes(), exported)
        self.assertFalse(Path(temp_path.read_text().strip()).exists())
        self.assertNotIn(b'a-private-test-value', proc.stdout + proc.stderr)



if __name__ == '__main__':
    unittest.main()
