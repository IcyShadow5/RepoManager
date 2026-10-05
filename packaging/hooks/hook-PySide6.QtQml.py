"""Collect the application's scanned QML closure instead of every Qt add-on."""
import json
import os
from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
datas = [(source, destination) for source, destination in datas if Path(source).suffix != ".qm"]
# QML debugging tools are not enabled by this application.
binaries = [(src, dest) for src, dest in binaries if "qmltooling" not in Path(src).parts]
inventory = json.loads(Path(os.environ["REPOMANAGER_BUILD_QML_INVENTORY"]).read_text(encoding="utf-8"))
qml_root = Path(pyside6_library_info.location["QmlImportsPath"]).resolve()
for module in inventory:
    relative = module.get("relativePath")
    if not relative:
        continue
    directory = (qml_root / relative).resolve()
    if not directory.is_relative_to(qml_root):
        raise ValueError("QML module outside the build runtime")
    plugin_binaries, plugin_datas = pyside6_library_info._process_qml_plugin(directory / "qmldir")
    for source, output in ((plugin_binaries, binaries), (plugin_datas, datas)):
        for file in source:
            destination = Path("PySide6/qml") / file.relative_to(qml_root)
            output.append((str(file), str(destination if file.is_dir() else destination.parent)))
