"""Resolve the real QML import closure using this build runtime's Qt scanner."""
import json
import re
from pathlib import Path
import subprocess
import sys

import PySide6
from PySide6.QtCore import QLibraryInfo

# run_qt sets Basic before the engine loads. The Controls qmldir advertises
# every alternate style to the scanner, despite none being selectable here.
UNUSED_STYLE_MODULES = {
    "QtQuick.Controls.Fusion", "QtQuick.Controls.Imagine", "QtQuick.Controls.Material",
    "QtQuick.Controls.Universal", "QtQuick.Controls.FluentWinUI3", "QtQuick.Controls.Windows",
    "QtQuick.NativeStyle", "QtQuick.Shapes", "QtQuick.Effects",
}


def runtime_module(name):
    return not any(name == unused or name.startswith(unused + ".")
                   for unused in UNUSED_STYLE_MODULES)


def collect(root, output):
    for file in Path(root).rglob("*.qml"):
        imports = re.findall(r"^import\s+([\w.]+)", file.read_text(encoding="utf-8"), re.MULTILINE)
        if any(not runtime_module(name) for name in imports):
            raise ValueError(f"Application imports a module excluded by the Basic-style package: {file.name}")
    scanner = Path(PySide6.__file__).parent / "qmlimportscanner.exe"
    imports = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath))
    result = subprocess.run([str(scanner), "-rootPath", str(root), "-importPath", str(imports)],
                            capture_output=True, text=True, timeout=60, check=True)
    if result.stderr.strip():
        raise ValueError(f"QML import scanner warning: {result.stderr.strip()}")
    modules = json.loads(result.stdout)
    unresolved = [m["name"] for m in modules if m.get("type") == "module"
                  and not m.get("path") and m["name"] not in {"QML", "RepoManager"}]
    if unresolved:
        raise ValueError(f"Unresolved QML modules: {unresolved}")
    # Persist module identities without build-machine source paths.
    identities = [{"name": m["name"], "relativePath": m.get("relativePath")}
                  for m in modules if m.get("type") == "module" and runtime_module(m["name"])]
    Path(output).write_text(json.dumps(identities, indent=2) + "\n", encoding="utf-8")
    return identities


if __name__ == "__main__":
    collect(Path(sys.argv[1]), Path(sys.argv[2]))
