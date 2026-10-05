"""Retain Qt GUI dependencies without unused PDF/virtual-keyboard plug-ins."""
from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
# The app uses native Windows text input and renders SVG/ICO/PNG assets.
# PDF documents and the on-screen virtual keyboard are not application features.
binaries = [(src, dest) for src, dest in binaries
            if Path(src).name.lower() not in {"qpdf.dll", "qtvirtualkeyboardplugin.dll"}]
