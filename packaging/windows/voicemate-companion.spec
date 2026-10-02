# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec of the VoiceMate companion (tray app) for Windows: onedir, windowed.
#
# Build it with `make companion-build`, which runs from the repository root:
#   .venv-companion\Scripts\python -m PyInstaller --noconfirm --clean
#       --distpath dist --workpath build\companion packaging\windows\voicemate-companion.spec
# Output: dist\VoiceMate\VoiceMate.exe plus dist\VoiceMate\_internal\, which
# voicemate-companion.iss packages into the installer.
#
# What goes in: app.companion (except Linux-only modules), app.protocol, app.i18n with
# freshly compiled catalogs, the icons, PySide6 (QtCore, QtGui, QtWidgets, QtNetwork)
# and pywin32. What stays out: the engine stack and the Qt modules the tray never uses.
#
# SPECPATH, workpath, Analysis, PYZ, EXE and COLLECT are globals that PyInstaller
# provides when it executes this file.

import re
import tomllib
from pathlib import Path

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

APP_NAME = "VoiceMate"
ROOT = Path(SPECPATH).resolve().parents[1]
LOCALES_DIR = ROOT / "app" / "i18n" / "locales"
ASSETS_DIR = ROOT / "app" / "companion" / "assets"

# The engine's stack. tests/test_import_boundary.py keeps the companion away from it;
# excluding it here turns an accidental import into an ImportError at startup instead
# of a silent gigabyte-sized bundle.
ENGINE_EXCLUDES = [
    "app.main",
    "app.daemon",
    "app.core",
    "app.features",
    "app.cli",
    "app.setup",
    "numpy",
    "scipy",
    "sounddevice",
    "soundfile",
    "torch",
    "torchaudio",
    "faster_whisper",
    "ctranslate2",
    "whisper",
    "silero_vad",
    "onnxruntime",
    "claude_agent_sdk",
    "omnivoice",
    "kokoro",
    "voxcpm",
    "pyperclip",
    "keyboard",
    "mouse",
    "pynput",
    "evdev",
    "tkinter",
    "_tkinter",
]
# Qt bindings PySide6-Essentials ships that the tray, the status window and the settings
# window do not use (they need QtCore, QtGui, QtWidgets and QtNetwork for QLocalServer).
QT_EXCLUDES = [
    f"PySide6.{name}"
    for name in (
        "QtConcurrent",
        "QtDBus",
        "QtDesigner",
        "QtHelp",
        "QtOpenGL",
        "QtOpenGLWidgets",
        "QtPrintSupport",
        "QtQml",
        "QtQuick",
        "QtQuickControls2",
        "QtQuickTest",
        "QtQuickWidgets",
        "QtSql",
        "QtSvg",
        "QtSvgWidgets",
        "QtTest",
        "QtUiTools",
        "QtXml",
    )
]
# Files the Qt hooks collect anyway (plugins of QtGui/QtNetwork and their dependencies)
# that the companion does not need. Matched against the destination path, lower case,
# with forward slashes. The UI takes its own strings from gettext (`app.i18n._`); of
# Qt's translations only qtbase pt_BR, es, ru and zh_CN stay, so main.py can install a QTranslator
# for Qt's standard texts (the Undo/Cut/Copy/Paste context menu of text fields).
QT_UNUSED_FILES = re.compile(
    r"^pyside6/("
    r"translations/(?!qtbase_(pt_br|es|ru|zh_cn)\.qm$)"
    r"|opengl32sw\.dll$"  # software OpenGL: the widgets paint with the raster engine
    r"|d3dcompiler_47\.dll$"  # Windows 10+ ships its own copy
    r"|qt6(pdf|svg|qml|quick|opengl|virtualkeyboard)[a-z0-9]*\.dll$"
    r"|plugins/(generic|networkinformation|platforminputcontexts|tls|iconengines)/"
    r"|plugins/platforms/(?!qwindows\.dll$)"
    r"|plugins/imageformats/(?!qico\.dll$)"  # PNG is built into QtGui; .ico needs qico
    r")"
    # OpenSSL for the dropped Qt TLS plugin. PySide6 does not ship it, so PyInstaller
    # resolves it from PATH (Git for Windows has a copy): never bundle that. Python's
    # own libssl-3.dll / libcrypto-3.dll (for urllib and hashlib) stay.
    r"|^lib(ssl|crypto)-3-x64\.dll$"
)


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as file:
        return str(tomllib.load(file)["tool"]["poetry"]["version"])


def version_resource(version: str) -> VSVersionInfo:
    """Explorer's Details tab, Task Manager's process name and the installer's version."""
    numbers = [int(part) for part in re.findall(r"\d+", version)[:4]]
    numbers += [0] * (4 - len(numbers))
    strings = [
        StringStruct("CompanyName", "NanoBR"),
        StringStruct("FileDescription", APP_NAME),
        StringStruct("FileVersion", version),
        StringStruct("InternalName", APP_NAME),
        StringStruct("LegalCopyright", "Copyright (c) 2026 Álli Terhorst. MIT License."),
        StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
        StringStruct("ProductName", APP_NAME),
        StringStruct("ProductVersion", version),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=tuple(numbers), prodvers=tuple(numbers)),
        kids=[
            StringFileInfo([StringTable("040904B0", strings)]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


def compile_catalogs(out_dir: Path) -> list[tuple[str, str]]:
    """Compile every voicemate.po (like `pybabel compile`) into the build directory.

    The .mo files in the source tree are gitignored build products and may be stale,
    so the bundle always gets fresh ones. A fuzzy or broken catalog fails the build
    instead of shipping an app that silently falls back to English.
    """
    datas = []
    for po_path in sorted(LOCALES_DIR.glob("*/LC_MESSAGES/voicemate.po")):
        language = po_path.parents[1].name
        with po_path.open("rb") as file:
            catalog = read_po(file, locale=language)
        if catalog.fuzzy:
            raise SystemExit(f"{po_path} is marked fuzzy: review it before building")
        problems = [f"{message.id!r}: {error}" for message, errors in catalog.check() for error in errors]
        if problems:
            raise SystemExit(f"{po_path} has errors:\n" + "\n".join(problems))
        mo_path = out_dir / language / "LC_MESSAGES" / "voicemate.mo"
        mo_path.parent.mkdir(parents=True, exist_ok=True)
        with mo_path.open("wb") as file:
            write_mo(file, catalog, use_fuzzy=False)
        # app.i18n looks for <package dir>/locales/<language>/LC_MESSAGES/voicemate.mo.
        datas.append((str(mo_path), f"app/i18n/locales/{language}/LC_MESSAGES"))
    if not datas:
        raise SystemExit(f"no catalogs found under {LOCALES_DIR}")
    return datas


def is_unused_qt_file(entry: tuple[str, str, str]) -> bool:
    return QT_UNUSED_FILES.match(entry[0].replace("\\", "/").lower()) is not None


VERSION = project_version()
ICON = ASSETS_DIR / "voicemate.ico"

hiddenimports = collect_submodules(
    "app.companion", filter=lambda name: "linux" not in name.split(".")
) + collect_submodules("app.protocol")
datas = compile_catalogs(Path(workpath) / "locales")
datas += [(str(path), "app/companion/assets") for path in sorted(ASSETS_DIR.glob("voicemate*.*"))]

a = Analysis(
    [str(Path(SPECPATH) / "voicemate_launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=ENGINE_EXCLUDES + QT_EXCLUDES,
    noarchive=False,
    optimize=0,
)
a.binaries = [entry for entry in a.binaries if not is_unused_qt_file(entry)]
a.datas = [entry for entry in a.datas if not is_unused_qt_file(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed Qt DLLs break and trip antivirus heuristics
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ICON)],
    version=version_resource(VERSION),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
