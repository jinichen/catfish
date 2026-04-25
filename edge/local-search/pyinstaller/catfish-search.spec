# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for catfish-search 单文件二进制打包。

用法：
    pyinstaller pyinstaller/catfish-search.spec
产物：
    dist/catfish-search           (macOS / Linux)
    dist/catfish-search.exe       (Windows)

为什么要单文件：
    员工拿到一个文件就能跑，不用装 Python，不用 pip，
    企业 EDR 对单一签名文件也更友好。
"""
from PyInstaller.utils.hooks import collect_all

# markitdown 有大量"按需 import"的子模块（pdf / docx / xlsx / pptx / ...），
# collect_all 会把元数据、数据文件、hidden imports 一次性抓全。
_md_datas, _md_bins, _md_hidden = collect_all("markitdown")
_pm_datas, _pm_bins, _pm_hidden = collect_all("pdfminer")

# watchdog 在不同平台用不同后端，强制全部打进来，避免"在 Mac 上打好的包放 Linux 跑崩"。
_watchdog_hidden = [
    "watchdog.observers.fsevents",
    "watchdog.observers.read_directory_changes",
    "watchdog.observers.inotify",
    "watchdog.observers.kqueue",
    "watchdog.observers.polling",
]

a = Analysis(
    ["../src/catfish_search/__main__.py"],
    pathex=["../src"],
    binaries=_md_bins + _pm_bins,
    datas=_md_datas + _pm_datas,
    hiddenimports=_watchdog_hidden + _md_hidden + _pm_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="catfish-search",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # upx 压缩在 macOS 上经常被 Gatekeeper 判木马，Windows Defender 也偶尔误判，统一关掉。
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
