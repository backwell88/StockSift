# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['launch.py'],
    pathex=['D:/Dev/Projects/StockSift/function'],
    binaries=[],
    datas=[('function/stockreport/starter', 'stockreport/starter'), ('function/stockreport/assets', 'stockreport/assets')],
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
    name='StockReport',
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
    icon=['D:/Dev/Projects/StockSift/function/stockreport/assets/stockreport.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='StockReport',
)
