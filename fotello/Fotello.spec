# -*- mode: python ; coding: utf-8 -*-

import os
try:
    from PyInstaller.utils.hooks import collect_submodules
except ImportError:
    collect_submodules = None

hidden_imports = [
    "webview.platforms.edgechromium",
    "webview.platforms.qt",
    "webview.platforms.winforms",
    "PIL",
    "PIL.Image",
    "PIL.ImageChops",
    "PIL.ImageFilter",
    "PIL.ImageStat",
    "backend",
    "backend.auth",
    "backend.browser_auth",
    "backend.client",
    "backend.constants",
    "backend.downloads",
    "backend.firestore",
    "backend.fotello_api",
    "backend.license",
    "backend.service",
    "backend.watermark_cleaner",
    "backend.watermark_cleaner.cli",
    "backend.watermark_cleaner.compositor",
    "backend.watermark_cleaner.config",
    "backend.watermark_cleaner.detector",
    "backend.watermark_cleaner.exceptions",
    "backend.watermark_cleaner.models",
    "backend.watermark_cleaner.pipeline",
    "backend.watermark_cleaner.validator",
    "backend.watermark_workflow",
    "backend.watermark_workflow.cleaner",
    "backend.watermark_workflow.coordinator",
    "backend.watermark_workflow.manual",
    "backend.watermark_workflow.models",
    "backend.watermark_workflow.store",
    "pyarmor_runtime_000000",
]

if collect_submodules:
    try:
        hidden_imports.extend(collect_submodules("backend"))
    except Exception:
        pass

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("ui", "ui"),
        ("settings.json", "."),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="Fotello",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="ui/logo.ico",
)

