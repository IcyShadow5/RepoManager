"""Collect Core without translation catalogs the application never loads."""
from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
# RepoManager has no QTranslator or language selector. Windows owns native
# dialog localization; the English QML strings do not use these catalogs.
datas = [(source, destination) for source, destination in datas
         if Path(source).suffix != ".qm"]
