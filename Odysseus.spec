# -*- mode: python ; coding: utf-8 -*-

import os

# In-repo extensions (e.g. extensions/ithaca/) are excluded from the frozen
# build by default, matching the Docker image's default — a fresh install
# starts with zero extensions, added later via Settings > Extensions or
# ODYSSEUS_EXTENSIONS_AUTOINSTALL (see src/extension_host.py). Opt in with
# ODYSSEUS_BUNDLE_EXTENSIONS=true pyinstaller Odysseus.spec.
_extensions_datas = [('extensions', 'extensions')] if os.environ.get("ODYSSEUS_BUNDLE_EXTENSIONS") == "true" else []

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('static', 'static'), ('scripts', 'scripts'), ('mcp_servers', 'mcp_servers'), ('services/hwfit/data', 'services/hwfit/data'), ('config', 'config'), ('.env.example', '.env.example')] + _extensions_datas,
    hiddenimports=[],
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
    [],
    exclude_binaries=True,
    name='Odysseus',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['static\\icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Odysseus',
)
