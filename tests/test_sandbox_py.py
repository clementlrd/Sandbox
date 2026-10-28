"""Offline tests for the persistent pip/XPU helper."""
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
loader = SourceFileLoader('sandbox_py_tool', str(PROJECT / 'tools/sandbox-py'))
spec = spec_from_loader(loader.name, loader)
sandbox_py = module_from_spec(spec)
loader.exec_module(sandbox_py)


class SandboxPyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()

    def test_reads_uv_style_project_and_group_includes(self):
        (self.project / 'pyproject.toml').write_text('''
[project]
name = "demo"
version = "0.1.0"
dependencies = ["torch>=2.0", "numpy>=2"]
[project.optional-dependencies]
viz = ["pillow>=10"]
[dependency-groups]
dev = ["pytest>=8", {include-group = "docs"}]
docs = ["sphinx>=8"]
''')
        conf, requirements = sandbox_py.get_project_requirements(self.project, ['viz'], ['dev'])
        self.assertEqual(requirements, ['torch>=2.0', 'numpy>=2', 'pillow>=10',
                                        'pytest>=8', 'sphinx>=8'])
        self.assertNotIn('build-system', conf)
        with self.assertRaisesRegex(ValueError, 'unknown dependency group'):
            sandbox_py.get_project_requirements(self.project, [], ['unknown'])

    def test_safe_env_names_and_paths(self):
        with mock.patch.object(sandbox_py, 'VENV_ROOT', self.root / 'venvs'):
            self.assertEqual(sandbox_py.venv_path('training'), self.root / 'venvs/training')
            for bad in ('../home', '/opt', '..', '.', '-name', 'a/b'):
                with self.assertRaises(ValueError):
                    sandbox_py.venv_path(bad)

    def test_installs_project_dependencies_without_build_backend(self):
        (self.project / 'pyproject.toml').write_text('''
[project]
name = "demo"
version = "0.1"
dependencies = ["torch>=2.0", "rich>=13"]
''')
        env = self.root / 'venvs' / 'project'
        env.mkdir(parents=True)
        data = {'version': 1, 'project': str(self.project), 'base_torch': '2.14.0+xpu',
                'base_python': '/opt/venv/bin/python'}
        sandbox_py.write_state(env, data)
        (env / sandbox_py.CONSTRAINT_NAME).write_text('torch==2.14.0+xpu\n')
        args = sandbox_py.parser().parse_args(['init', 'project', '--project', str(self.project)])
        with mock.patch.object(sandbox_py, 'VENV_ROOT', self.root / 'venvs'), \
             mock.patch.object(sandbox_py, 'check_base'), \
             mock.patch.object(sandbox_py, 'run') as run:
            sandbox_py.command_init(args)
        call = run.call_args.args[0]
        self.assertEqual(call[:3], [env / 'bin/python', '-m', 'pip'])
        self.assertIn('-c', call)
        self.assertIn('torch>=2.0', call)
        self.assertIn('rich>=13', call)
        self.assertNotIn('-e', call)
        self.assertNotIn('--upgrade', call)

    def test_refuses_to_install_to_unknown_environment(self):
        args = sandbox_py.parser().parse_args(['install', 'missing', 'numpy'])
        with mock.patch.object(sandbox_py, 'VENV_ROOT', self.root / 'venvs'):
            with self.assertRaisesRegex(ValueError, 'not managed'):
                sandbox_py.command_install(args)

    def test_pip_recognizes_inherited_torch_and_no_download(self):
        """Use fake torch dist-info to test actual pip/.pth behavior offline."""
        base, child = (self.root / 'base', self.root / 'child')
        subprocess.run([sys.executable, '-m', 'venv', str(base)], check=True)
        subprocess.run([sys.executable, '-m', 'venv', str(child)], check=True)
        base_info = sandbox_py.py_info(base / 'bin/python')
        child_info = sandbox_py.py_info(child / 'bin/python')
        site = Path(base_info['site'])
        (site / 'torch').mkdir()
        (site / 'torch/__init__.py').write_text('__version__ = "2.14.0+xpu"\n')
        dist = site / 'torch-2.14.0+xpu.dist-info'
        dist.mkdir()
        (dist / 'METADATA').write_text('Metadata-Version: 2.1\nName: torch\nVersion: 2.14.0+xpu\n')
        (Path(child_info['site']) / 'sandbox-base-pytorch.pth').write_text(str(site) + '\n')
        version = sandbox_py.torch_pin(sandbox_py.py_info(base / 'bin/python'),
                                       sandbox_py.py_info(child / 'bin/python'), base)
        self.assertEqual(version, '2.14.0+xpu')
        constraint = self.root / 'constraints.txt'
        constraint.write_text('torch==2.14.0+xpu\n')
        env = os.environ.copy()
        env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
        env['PIP_NO_INDEX'] = '1'
        env['PIP_CACHE_DIR'] = str(self.root / 'pip-cache')
        p = subprocess.run([str(child / 'bin/python'), '-m', 'pip', 'install',
                            '-c', str(constraint), 'torch>=2.0'], capture_output=True,
                           text=True, env=env, check=True)
        self.assertIn('Requirement already satisfied', p.stdout)
        self.assertFalse((Path(child_info['site']) / 'torch').exists())
        # Incompatible torch requirements fail, without a replacement download.
        blocked = subprocess.run([str(child / 'bin/python'), '-m', 'pip', 'install',
                                  '-c', str(constraint), 'torch>=3'], capture_output=True,
                                 text=True, env=env)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertFalse((Path(child_info['site']) / 'torch').exists())

    def test_sandbox_py_creates_inherited_env_without_runtime_helper(self):
        """Real venv creation inherits base torch without a sandbox-venv binary."""
        base = self.root / 'base'
        venvs = self.root / 'venvs'
        venvs.mkdir()
        subprocess.run([sys.executable, '-m', 'venv', str(base)], check=True)
        base_site = Path(sandbox_py.py_info(base / 'bin/python')['site'])
        (base_site / 'torch').mkdir()
        (base_site / 'torch/__init__.py').write_text('__version__ = "2.14.0+xpu"\n')
        dist = base_site / 'torch-2.14.0+xpu.dist-info'
        dist.mkdir()
        (dist / 'METADATA').write_text('Metadata-Version: 2.1\nName: torch\nVersion: 2.14.0+xpu\n')

        with mock.patch.object(sandbox_py, 'VENV_ROOT', venvs), \
             mock.patch.object(sandbox_py, 'BASE_PYTHON', base / 'bin/python'):
            env = sandbox_py.venv_path('project')
            state = sandbox_py.ensure_env(env, self.project)
            self.assertEqual(state['base_torch'], '2.14.0+xpu')
            self.assertEqual((env / sandbox_py.CONSTRAINT_NAME).read_text(), 'torch==2.14.0+xpu\n')
            self.assertEqual(sandbox_py.py_info(env / 'bin/python')['torch'], '2.14.0+xpu')
            self.assertEqual(sandbox_py.ensure_env(env, self.project), state)

        child_site = Path(sandbox_py.py_info(env / 'bin/python')['site'])
        self.assertEqual(
            (child_site / 'sandbox-base-pytorch.pth').read_text(),
            str(base_site.resolve()) + '\n'
        )
        self.assertFalse((child_site / 'torch').exists())

    def test_sandbox_py_creates_plain_env_without_base_torch(self):
        venvs = self.root / 'venvs'
        venvs.mkdir()
        with mock.patch.object(sandbox_py, 'VENV_ROOT', venvs), \
             mock.patch.object(sandbox_py, 'BASE_PYTHON', self.root / 'missing-python'):
            path = sandbox_py.venv_path('plain')
            state = sandbox_py.ensure_env(path, self.project)
        self.assertIsNone(state['base_torch'])
        self.assertTrue((path / 'bin/python').is_file())
        self.assertFalse((path / sandbox_py.CONSTRAINT_NAME).exists())

    def test_installer_always_provides_sandbox_py_without_optional_downloads(self):
        home = self.root / 'home'
        home.mkdir()
        env = dict(os.environ, HOME=str(home),
                   XDG_DATA_HOME=str(home / 'share'),
                   XDG_CONFIG_HOME=str(home / 'config'))
        subprocess.run(['bash', str(PROJECT / 'install.sh'), '--no-tools'],
                       env=env, check=True, capture_output=True)
        installed = home / 'share/sandbox/tools/bin/sandbox-py'
        self.assertTrue(installed.is_file())
        p = subprocess.run([str(installed), '--help'], text=True, capture_output=True,
                           check=True)
        self.assertIn('Persistent Python environments', p.stdout)


if __name__ == '__main__':
    unittest.main()
