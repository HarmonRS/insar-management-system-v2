# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['lt1_data_sync_gui.py'],
    pathex=[],
    binaries=[('C:\\ProgramData\\anaconda3\\envs\\InSAR\\Library\\bin\\tcl86t.dll', '.'), ('C:\\ProgramData\\anaconda3\\envs\\InSAR\\Library\\bin\\tk86t.dll', '.'), ('C:\\ProgramData\\anaconda3\\envs\\InSAR\\Library\\bin\\libcrypto-3-x64.dll', '.'), ('C:\\ProgramData\\anaconda3\\envs\\InSAR\\Library\\bin\\liblzma.dll', '.'), ('C:\\ProgramData\\anaconda3\\envs\\InSAR\\Library\\bin\\libbz2.dll', '.')],
    datas=[],
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
    a.binaries,
    a.datas,
    [],
    name='LT1DataSync',
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
)
