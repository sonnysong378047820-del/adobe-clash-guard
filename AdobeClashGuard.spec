# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（文件夹版 / onedir）。

在仓库根目录执行：

    pyinstaller --noconfirm --clean AdobeClashGuard.spec

产物位于 dist/AdobeClashGuard/，包含 AdobeClashGuard.exe 与 _internal/，
两者必须放在一起，不能只拷 exe。

必须保持 --onedir：若改回 --onefile，程序运行时会把自身解压到
%TEMP%\\_MEIxxxxxx，退出时若安全软件正在扫描其中某个 DLL，删除会失败并
弹出 "Failed to remove temporary directory" 警告；同时安装器会把
bundle_root() 判为空，只复制单个 exe，固定安装目录将不完整。
"""

import os

SRC = os.path.join(SPECPATH, "src", "adobe_clash_guard.py")

a = Analysis(
    [SRC],
    pathex=[os.path.join(SPECPATH, "src")],
    binaries=[],
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
    [],
    exclude_binaries=True,
    name="AdobeClashGuard",
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AdobeClashGuard",
)
