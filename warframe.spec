# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a single-file ``warframe-profile`` executable.

Build (on Windows, for a Windows .exe):

    py -3 -m pip install pyinstaller
    py -3 -m PyInstaller --clean --noconfirm warframe.spec

The resulting executable is ``dist/warframe-profile.exe``
(``dist/warframe-profile`` on Linux/macOS).  PyInstaller cannot
cross-compile: build on the target OS.

The application dispatches sub-commands through ``importlib``, so the
hidden-import list is generated from the whole ``warframe_profile``
package; static analysis alone would miss most presenters.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

PROJECT_ROOT = Path(SPECPATH).resolve()
sys.path.insert(0, str(PROJECT_ROOT))
hidden_imports = collect_submodules("warframe_profile")

a = Analysis(
    [str(PROJECT_ROOT / "warframe.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="warframe-profile",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
