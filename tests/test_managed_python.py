"""Offline uv integration tests: no Docker, GPU, or package downloads."""
import json
import hashlib
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

from test_sandbox_py import sandbox_py


@unittest.skipUnless(shutil.which('uv'), 'uv is required for managed environment integration tests')
class ManagedPython(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.venvs = self.root / 'venvs'
        self.venvs.mkdir()
        self.wheels = self.root / 'wheels'
        self.wheels.mkdir()
        self.base = self.root / 'base'
        subprocess.run(['uv', 'venv', '--python', sys.executable, str(self.base)], check=True,
                       capture_output=True)
        self.site = Path(sandbox_py.py_info(self.base / 'bin/python')['site'])
        self.base_package('torch', '2.1+cu126', requires=['runtime-lib==1.0'])
        self.base_package('runtime-lib', '1.0')
        self.wheel('demo', '1.0')
        self.write_project(['torch>=2', 'demo==1.0'])
        patches = [mock.patch.object(sandbox_py, 'VENV_ROOT', self.venvs),
                   mock.patch.object(sandbox_py, 'BASE_PYTHON', self.base / 'bin/python'),
                   mock.patch.object(sandbox_py, 'RUNTIME_MANIFEST', self.root / 'manifest.json'),
                   mock.patch.dict(os.environ, {'UV_FIND_LINKS': str(self.wheels),
                                               'UV_OFFLINE': '1',
                                               'SANDBOX_RUNTIME_IMAGE': 'sha256:' + 'a' * 64})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def base_package(self, name, version, requires=()):
        stem = name.replace('-', '_')
        (self.site / stem).mkdir(exist_ok=True)
        (self.site / stem / '__init__.py').write_text(f'__version__ = {version!r}\n')
        dist = self.site / f'{stem}-{version}.dist-info'
        dist.mkdir()
        (dist / 'METADATA').write_text(self.metadata(name, version, requires))

    def metadata(self, name, version, requires=()):
        return (f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n' +
                ''.join('Requires-Dist: ' + req + '\n' for req in requires))

    def wheel(self, name, version, requires=()):
        stem = name.replace('-', '_')
        path = self.wheels / f'{stem}-{version}-py3-none-any.whl'
        dist = f'{stem}-{version}.dist-info'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(stem + '/__init__.py', f'__version__ = {version!r}\n')
            archive.writestr(dist + '/METADATA', self.metadata(name, version, requires))
            archive.writestr(dist + '/WHEEL',
                             'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            archive.writestr(dist + '/RECORD', '')
        return path

    def write_project(self, requirements):
        (self.project / 'pyproject.toml').write_text(
            '[project]\nname = "example"\nversion = "0.1"\nrequires-python = ">=3.11"\n'
            'dependencies = ' + json.dumps(requirements) + '\n')

    def invoke(self, *args):
        return sandbox_py.main([args[0], '--project', str(self.project), *args[1:]])

    def current(self):
        return sandbox_py.current_generation(sandbox_py.managed_root(self.project))

    def test_sync_reuses_runtime_and_cache_without_a_torch_copy(self):
        self.assertEqual(self.invoke('sync'), 0)
        current = self.current()
        info = sandbox_py.py_info(current / 'bin/python')
        self.assertEqual(info['torch_location'], str(self.site / 'torch'))
        self.assertFalse((Path(info['site']) / 'torch').exists())
        self.assertNotIn('torch', (current / 'install.txt').read_text())
        self.assertNotIn('runtime-lib', (current / 'install.txt').read_text())
        self.assertTrue((self.venvs / '.uv-cache').is_dir())
        self.assertGreater((Path(info['site']) / 'demo/__init__.py').stat().st_nlink, 1)
        self.assertEqual(self.invoke('sync'), 0)
        self.assertEqual(self.current(), current)

    def test_conflict_keeps_previous_environment(self):
        self.assertEqual(self.invoke('sync'), 0)
        current = self.current()
        self.write_project(['torch>=3'])
        self.assertEqual(self.invoke('sync'), 1)
        self.assertEqual(self.current(), current)
        self.assertEqual(len(list(current.parent.glob('env-*'))), 1)

    def test_same_version_direct_wheel_cannot_shadow_runtime(self):
        wheel = self.wheel('torch', '2.1+cu126')
        self.write_project(['torch @ ' + wheel.as_uri()])
        self.assertEqual(self.invoke('sync'), 1)
        self.assertIsNone(self.current())

    def test_runtime_dependency_cannot_be_shadowed(self):
        self.wheel('runtime-lib', '2.0')
        self.write_project(['runtime-lib>=2'])
        self.assertEqual(self.invoke('sync'), 1)
        self.assertIsNone(self.current())

    def test_detects_unmanaged_changes_and_can_rebuild(self):
        self.assertEqual(self.invoke('sync'), 0)
        current = self.current()
        site = Path(sandbox_py.py_info(current / 'bin/python')['site'])
        dist = site / 'surprise-1.0.dist-info'
        dist.mkdir()
        (dist / 'METADATA').write_text(self.metadata('surprise', '1.0'))
        self.assertEqual(self.invoke('run', 'python', '-c', 'raise AssertionError()'), 1)
        self.assertEqual(self.invoke('sync', '--rebuild'), 0)
        self.assertNotEqual(self.current(), current)

    def test_detects_same_version_shadow_of_inherited_distribution(self):
        self.assertEqual(self.invoke('sync'), 0)
        site = Path(sandbox_py.py_info(self.current() / 'bin/python')['site'])
        shadow = site / 'torch-2.1+cu126.dist-info'
        shadow.mkdir()
        (shadow / 'METADATA').write_text(self.metadata('torch', '2.1+cu126'))
        self.assertEqual(self.invoke('run', 'python', '-c', 'raise AssertionError("must not run")'), 1)

    def test_managed_run_uses_environment_and_preserves_exit_code(self):
        self.assertEqual(self.invoke('run', 'python', '-c',
                                    'import demo, torch; assert demo.__version__ == "1.0"; '
                                    'assert torch.__version__ == "2.1+cu126"; raise SystemExit(7)'), 7)

    def test_add_rolls_back_conflicting_project_change(self):
        self.assertEqual(self.invoke('sync'), 0)
        before = (self.project / 'pyproject.toml').read_bytes()
        current = self.current()
        self.wheel('torch', '3.0')
        self.assertEqual(self.invoke('add', 'torch>=3'), 1)
        self.assertEqual((self.project / 'pyproject.toml').read_bytes(), before)
        self.assertFalse((self.project / 'uv.lock').exists())
        self.assertEqual(self.current(), current)

    def test_add_without_lock_keeps_runtime_resolution_private(self):
        self.wheel('extra', '1.0')
        self.assertEqual(self.invoke('add', 'extra==1.0'), 0)
        self.assertFalse((self.project / 'uv.lock').exists())
        self.assertEqual(self.invoke('run', 'python', '-c', 'import extra'), 0)
        self.assertFalse((self.project / '.venv').exists())

    def test_adding_package_does_not_upgrade_unrelated_dependencies(self):
        self.write_project(['demo>=1'])
        self.assertEqual(self.invoke('sync'), 0)
        self.wheel('demo', '2.0')
        self.wheel('extra', '1.0')
        self.assertEqual(self.invoke('add', 'extra==1.0'), 0)
        self.assertEqual(self.invoke('run', 'python', '-c',
                                    'import demo; assert demo.__version__ == "1.0"'), 0)

    def test_locked_install_checks_hashes_and_preserves_current_on_mismatch(self):
        wheel = self.wheels / 'demo-1.0-py3-none-any.whl'
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        (self.project / 'uv.lock').write_text('# synthetic export fixture\n')
        original = sandbox_py.uv_command

        def export(*args, **kwargs):
            if args[0] == 'export':
                output = Path(args[args.index('--output-file') + 1])
                output.write_text(f'demo @ {wheel.as_uri()} --hash=sha256:{digest}\n')
            else:
                original(*args, **kwargs)

        with mock.patch.object(sandbox_py, 'uv_command', side_effect=export):
            self.assertEqual(self.invoke('sync'), 0)
            current = self.current()
            self.assertIn('--hash=', (current / 'install.txt').read_text())
            digest = '0' * 64
            self.assertEqual(self.invoke('sync', '--rebuild'), 1)
            self.assertEqual(self.current(), current)

    def test_stale_lock_is_not_silently_updated(self):
        self.write_project(['demo==1.0'])
        self.wheel('extra', '1.0')
        subprocess.run(['uv', '--quiet', 'lock'], cwd=self.project, check=True,
                       env=sandbox_py.uv_environment())
        self.assertEqual(self.invoke('add', 'extra==1.0'), 0)
        current = self.current()
        lock = (self.project / 'uv.lock').read_bytes()
        self.write_project(['demo>=2'])
        self.assertEqual(self.invoke('sync'), 1)
        self.assertEqual((self.project / 'uv.lock').read_bytes(), lock)
        self.assertEqual(self.current(), current)

    def test_checks_dependencies_against_inherited_stack(self):
        self.wheel('dependent', '1.0', ['torch>=2', 'runtime-lib==1.0'])
        self.write_project(['dependent==1.0'])
        self.assertEqual(self.invoke('sync'), 0)
        self.assertEqual(self.invoke('run', 'python', '-c', 'import dependent, torch'), 0)

    def test_keeps_running_generation_during_pruning(self):
        self.assertEqual(self.invoke('sync'), 0)
        first = self.current()
        with sandbox_py.file_lock(first / '.lease', shared=True):
            self.assertEqual(self.invoke('sync', '--rebuild'), 0)
            self.assertEqual(self.invoke('sync', '--rebuild'), 0)
            self.assertTrue(first.is_dir())
        self.assertEqual(self.invoke('sync', '--rebuild'), 0)
        self.assertFalse(first.exists())
        self.assertEqual(len(list(self.current().parent.glob('env-*'))), 2)

    def test_manifest_matches_build_time_inventory(self):
        script = Path(__file__).resolve().parents[1] / 'images/python-contract.py'
        manifest = subprocess.check_output([str(self.base / 'bin/python'), str(script)])
        (self.root / 'manifest.json').write_bytes(manifest)
        self.assertEqual(self.invoke('sync'), 0)
        self.base_package('unexpected', '1.0')
        self.assertEqual(self.invoke('sync'), 1)

    def test_remembers_and_clears_group_selection(self):
        self.wheel('devtool', '1.0')
        with (self.project / 'pyproject.toml').open('a') as stream:
            stream.write('\n[dependency-groups]\ndev = ["devtool==1.0"]\n')
        self.assertEqual(self.invoke('sync', '--group', 'dev'), 0)
        selected = self.current()
        self.assertEqual(self.invoke('run', 'python', '-c', 'import devtool'), 0)
        self.assertEqual(self.current(), selected)
        self.assertEqual(self.invoke('sync', '--no-groups'), 0)
        self.assertNotEqual(self.current(), selected)
        self.assertEqual(self.invoke('run', 'python', '-c',
                                    'import importlib.util; assert importlib.util.find_spec("devtool") is None'), 0)

    def test_add_dev_writes_group_not_main_dependencies_and_enables_it(self):
        self.wheel('devtool', '1.0')
        self.assertEqual(self.invoke('add', '--dev', 'devtool==1.0'), 0)
        project = sandbox_py.tomllib.loads((self.project / 'pyproject.toml').read_text())
        self.assertNotIn('devtool==1.0', project['project']['dependencies'])
        self.assertIn('devtool==1.0', project['dependency-groups']['dev'])
        self.assertEqual(self.invoke('run', 'python', '-c', 'import devtool'), 0)

    def test_interrupted_install_does_not_replace_current(self):
        self.assertEqual(self.invoke('sync'), 0)
        current = self.current()
        with mock.patch.object(sandbox_py, 'validate_generation', side_effect=KeyboardInterrupt):
            self.assertEqual(self.invoke('sync', '--rebuild'), 130)
        self.assertEqual(self.current(), current)
        self.assertEqual(len(list(current.parent.glob('env-*'))), 1)

    def test_interrupt_after_atomic_commit_does_not_delete_active_generation(self):
        self.assertEqual(self.invoke('sync'), 0)
        current = self.current()
        replace = os.replace

        def interrupt_after_commit(source, dest):
            replace(source, dest)
            if Path(dest).name == 'current':
                raise KeyboardInterrupt

        with mock.patch.object(sandbox_py.os, 'replace', side_effect=interrupt_after_commit):
            self.assertEqual(self.invoke('sync', '--rebuild'), 130)
        self.assertNotEqual(self.current(), current)
        self.assertTrue((self.current() / 'bin/python').is_file())
        self.assertEqual(self.invoke('sync'), 0)

    def test_concurrent_syncs_select_one_generation(self):
        context = multiprocessing.get_context('fork')
        def sync():
            raise SystemExit(self.invoke('sync'))
        processes = [context.Process(target=sync) for _ in range(2)]
        for process in processes:
            process.start()
            self.addCleanup(lambda p=process: p.kill() if p.is_alive() else None)
        for process in processes:
            process.join(timeout=30)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(list(self.current().parent.glob('env-*'))), 1)

    def test_add_rollback_does_not_overwrite_concurrent_editor(self):
        self.wheel('extra', '1.0')
        def edit_then_fail(*args, **kwargs):
            with (self.project / 'pyproject.toml').open('a') as stream:
                stream.write('\n# concurrent editor\n')
            raise ValueError('simulated install failure')
        with mock.patch.object(sandbox_py, 'sync_managed', side_effect=edit_then_fail):
            self.assertEqual(self.invoke('add', 'extra==1.0'), 1)
        self.assertIn('# concurrent editor', (self.project / 'pyproject.toml').read_text())

    def test_plain_runtime_does_not_inherit_system_packages(self):
        self.write_project(['demo==1.0'])
        with mock.patch.object(sandbox_py, 'BASE_PYTHON', self.root / 'missing'):
            self.assertEqual(self.invoke('sync'), 0)
        info = sandbox_py.py_info(self.current() / 'bin/python')
        self.assertIsNone(info['torch'])

    def test_workspace_is_rejected_before_environment_creation(self):
        with (self.project / 'pyproject.toml').open('a') as stream:
            stream.write('\n[tool.uv.workspace]\nmembers = []\n')
        self.assertEqual(self.invoke('sync'), 1)
        self.assertEqual(list(self.venvs.iterdir()), [])


class ManagedPolicy(unittest.TestCase):
    def test_lockfile_export_preserves_hashes_and_excludes_runtime(self):
        export = ('demo==1.0 ' + '\\' + '\n' +
                  '    --hash=sha256:aaa ' + '\\' + '\n' +
                  '    --hash=sha256:bbb\n' +
                  'torch==2.1 ' + '\\' + '\n' + '    --hash=sha256:ccc\n')
        install = sandbox_py.exported_requirements(export, {'torch'}, hashes=True)
        self.assertIn('--hash=sha256:aaa', install)
        self.assertIn('--hash=sha256:bbb', install)
        self.assertNotIn('torch', install)
        source = sandbox_py.exported_requirements(export, {'torch'}, hashes=False)
        self.assertNotIn('--hash', source)
        self.assertIn('torch==2.1', source)

    def test_resolver_override_cannot_replace_protected_package(self):
        with self.assertRaisesRegex(ValueError, 'replace runtime package'):
            sandbox_py.strip_runtime_requirements('torch==3.0\n', {'torch': 'file:///runtime.whl'})

    def test_missing_runtime_package_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'omitted protected'):
            sandbox_py.strip_runtime_requirements('demo==1.0\n', {'torch': 'file:///runtime.whl'})

    def test_inherited_installer_options_cannot_override_policy(self):
        with mock.patch.dict(os.environ, {'UV_OVERRIDE': 'bad.txt', 'UV_NO_BUILD': '0',
                                          'UV_LINK_MODE': 'symlink', 'PIP_TARGET': '/tmp/bad'}):
            env = sandbox_py.uv_environment()
        self.assertNotIn('UV_OVERRIDE', env)
        self.assertNotIn('PIP_TARGET', env)
        self.assertEqual(env['UV_LINK_MODE'], 'hardlink')


if __name__ == '__main__':
    unittest.main()
