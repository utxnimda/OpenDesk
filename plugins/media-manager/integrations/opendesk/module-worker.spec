# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

def build_directory(suffix):
    key = f'MEDIA_MANAGER_MODULE_{suffix}'
    # Retain old direct build invocations without masking an invalid new value.
    value = os.environ[key] if key in os.environ else os.environ.get(f'NIMDA_MODULE_{suffix}')
    if not value:
        raise ValueError(f'{key} must identify a build directory')
    directory = Path(value)
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError(f'{key} must identify an existing absolute build directory')
    return directory.resolve()

integration = build_directory('INTEGRATION')
python_source = build_directory('PYTHON_SOURCE')
repo_root = build_directory('SOURCE')
framework_backend = repo_root / 'apps/framework/backend'
sys.path.insert(0, str(framework_backend))
from work_catalog_yaml.layout import backend_roots
backend_paths = backend_roots(repo_root)
for backend in backend_paths:
    sys.path.insert(0, str(backend))

datas, binaries, hiddenimports = [], [], []
def tree(source, target):
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            raise ValueError('Linked application resources are not packaged')
        if path.is_file():
            datas.append((str(path), str(Path(target) / path.relative_to(source).parent)))

tree(repo_root / 'apps/framework/frontend', 'application/apps/framework/frontend')
for frontend in sorted((repo_root / 'apps/features').glob('*/frontend')):
    tree(frontend, Path('application') / frontend.relative_to(repo_root))
datas += [(str(framework_backend / 'work_catalog_yaml/jp_tv/browse_config.default.yaml'), 'work_catalog_yaml/jp_tv'),
          (str(integration / 'nimda_opendesk/bootstrap.js'), 'nimda_opendesk')]
for name in ('webview', 'clr_loader', 'pythonnet'):
    package_datas, package_binaries, package_hidden = collect_all(name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden
hiddenimports += collect_submodules('uvicorn')
hiddenimports += ['work_catalog_yaml.jp_tv.browse_payload']
for backend in backend_paths:
    if backend == framework_backend:
        continue
    for package in sorted(backend.iterdir()):
        if package.is_dir() and (package / '__init__.py').is_file():
            hiddenimports += collect_submodules(package.name)

a = Analysis([str(python_source / 'worker_entry.py')], pathex=[str(python_source), *map(str, backend_paths)],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['cefpython3', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi'], noarchive=False, optimize=1)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='module-worker', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=True, contents_directory='_internal')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='module-worker')
