# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：ICD 工具原型 Windows 桌面版（onedir 单目录）。

资源打包策略：
- prompts/*.md、profiles/*/config.yaml、synonyms.yaml 用 datas 保持目录结构，
  运行时 `Path(__file__).parent` 仍能定位（PyInstaller 下 __file__ 指向 _MEIPASS）。
- profiles/*/hooks.py 由 importlib.import_module 动态导入，需 collect_submodules
  收集，否则运行时找不到模块。
- uvicorn 有动态导入（loops/protocols/lifespan），同样 collect_submodules。
- 前端 static/ 不打进包，运行时从 exe 同级目录读取（见 build.ps1 步骤 4）。
"""
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH 为 spec 所在目录（packaging/）；BACKEND 指向项目 backend/
_pkg = Path(os.path.abspath(SPECPATH))
BACKEND = _pkg.parent / 'backend'
sys.path.insert(0, str(BACKEND))

datas = []
for _f in (BACKEND / 'app' / 'v4' / 'prompts').glob('*.md'):
    datas.append((str(_f), 'app/v4/prompts'))
for _f in (BACKEND / 'app' / 'v4' / 'profiles').glob('*/config.yaml'):
    datas.append((str(_f), str(_f.parent.relative_to(BACKEND))))
datas.append((str(BACKEND / 'app' / 'v4' / 'synonyms.yaml'), 'app/v4'))

hiddenimports = collect_submodules('app.v4.profiles') + collect_submodules('uvicorn')

a = Analysis(
    [str(_pkg / 'run.py')],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ICDTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='ICDTool',
)
