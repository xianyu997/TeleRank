# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the TG Reaction Ranker Windows service EXE.

Build with:
    python -m PyInstaller --noconfirm --clean TeleRankService.spec

Uses --onedir layout (via COLLECT), which is the layout Windows services
require: the Service Control Manager starts TeleRankService.exe directly and
the _internal folder keeps working from any working directory.
"""

from PyInstaller.utils.hooks import collect_all

# telegram_sync.py imports telethon lazily and telegram_bot is imported lazily
# by TelegramSyncService.start_bot(), so collect every telethon/cryptg module.
telethon_datas, telethon_binaries, telethon_hidden = collect_all("telethon")
cryptg_datas, cryptg_binaries, cryptg_hidden = collect_all("cryptg")

hidden_imports = [
    "telegram_sync",
    "telegram_bot",
    # pywin32 service stack
    "win32timezone",
    "win32serviceutil",
    "win32service",
    "win32event",
    "win32api",
    "win32con",
    "pythoncom",
    "pywintypes",
    "servicemanager",
] + telethon_hidden + cryptg_hidden

datas = [
    ("tg_reaction_web.html", "."),
] + telethon_datas + cryptg_datas

binaries = telethon_binaries + cryptg_binaries

excludes = [
    "rumps",          # macOS menu bar only
    "PIL",            # not used by the service
    "matplotlib",
    "numpy",
    "tkinter",
]

a = Analysis(
    ["windows_service.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TeleRankService",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon="TGReactionRanker.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TeleRankService",
)
