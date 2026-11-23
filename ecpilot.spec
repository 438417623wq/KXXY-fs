# -*- mode: python ; coding: utf-8 -*-
"""EC Pilot 打包配置。

产物：dist/ECPilot.exe（单文件、32 位、无控制台窗口）。
必须用 32 位 Python 打包：Ntport.dll 是 32 位库，64 位进程载不进去。
"""
import os

ROOT = os.path.abspath(os.getcwd())
APP = os.path.join(ROOT, "app")

# EC 访问链路都是“模块名直接 import”，显式列出来防止动态导入被漏掉
HIDDEN = [
    "control.engine",
    "control.light",
    "control.safety",
    "control.curve",
    "control.autostart",
    "control.profiles",
    "control.perfmode",
    "hardware.backend",
    "hardware.ec",
    "hardware.ntport",
    "hardware.system",
    "hardware.vendor",
    "hardware.conflicts",
    "hardware.dchu_bridge",
    "server",
    "store",
    "window",
    "version",
    # 原生界面（wxPython）
    "ui.app_native",
    "ui.shell",
    "ui.kit",
    "ui.charts",
    "ui.theme",
    "ui.actions",
    "ui.pages_overview",
    "ui.pages_perf",
    "ui.pages_fans",
    "ui.pages_light",
    "ui.pages_safety",
    "ui.pages_diag",
    "ui.pages_settings",
]

# 这些东西这个程序一个都用不到，排掉能明显缩小体积
EXCLUDES = [
    "tkinter", "_tkinter", "unittest", "pydoc", "doctest", "test", "test.support",
    "pip", "setuptools", "pkg_resources", "wheel", "distutils", "lib2to3",
    "sqlite3", "bz2", "lzma", "multiprocessing", "asyncio", "concurrent",
    "curses", "idlelib", "ensurepip", "venv", "xmlrpc", "pdb", "turtle",
]

a = Analysis(
    [os.path.join(APP, "main.py")],
    pathex=[APP],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "drivers"), "drivers"),
        (os.path.join(ROOT, "driver_setup"), "driver_setup"),
    ],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
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
    name="ECPilot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "ecpilot.ico"),
    version=os.path.join(ROOT, "assets", "version_info.txt"),
)
