"""Exercise optional Postiz contracts from the actual public export, offline."""
import subprocess
import sys

from scripts.package import export_public_repo as exporter


def test_postiz_contracts_run_from_clean_export(tmp_path):
    exported = tmp_path / 'public-source'
    assert exporter.main(['--out', str(exported)]) == 0
    packages = ('postiz_readonly',)
    for package in packages:
        root = exported / 'prototypes' / package
        assert (root / '__init__.py').is_file()
        assert (root / 'README.md').is_file()
        assert not (root / 'evidence').exists()
        assert not (root / 'storage-summary.md').exists()
        assert not (root / 'storage-findings.md').exists()
        assert not list(root.rglob('*.sqlite'))
        assert not list(root.rglob('*.db'))
        assert not list(root.rglob('.env'))
        assert not list(root.rglob('compose.json'))
    environment = exported / 'prototypes/postiz_readonly/environment'
    for name in ('LICENSE.Postiz.txt', 'ingress.conf', 'upstream-compose.yaml',
                 'dynamicconfig/development-sql.yaml', 'images.lock.json'):
        assert (environment / name).is_file()
    # -I ignores PYTHONPATH, the caller's cwd and user-site startup hooks.
    # Only the exported cwd is explicitly added; no development source fallback.
    script = '''
import importlib, pathlib, sys
root = pathlib.Path.cwd().resolve()
sys.path.insert(0, str(root))
for name in sys.argv[1:]:
    module = importlib.import_module('prototypes.' + name)
    assert pathlib.Path(module.__file__).resolve().is_relative_to(root)
import pytest
raise SystemExit(pytest.main([
    *['app/tests/test_' + name + '.py' for name in sys.argv[1:]],
    '-q', '-p', 'no:cacheprovider', '--basetemp', str(root / '.test-tmp'),
]))
'''
    result = subprocess.run([sys.executable, '-I', '-c', script, *packages],
        cwd=exported, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'passed' in result.stdout
