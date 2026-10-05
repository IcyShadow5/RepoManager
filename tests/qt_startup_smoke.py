"""Run the actual Qt entry point with isolated application data and close it."""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
if "--missing" in sys.argv:
    # Initialize Qt in the isolated environment; PySide adds its own DLL path.
    # Replacing PATH after that would remove required Qt plugin dependencies.
    os.environ["PATH"] = str(Path(sys.executable).parent)
from PySide6.QtCore import QTimer
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from repo_manager import instance_lock, store
import run_qt
import run
from tests.qt_messages import QtMessages


def main():
    messages = QtMessages()
    with tempfile.TemporaryDirectory(prefix="repomanager-startup-qa-") as folder:
        data = Path(folder)
        patches = {"APP_DIR": data, "REPOS_FILE": data / "repos.json", "SETTINGS_FILE": data / "settings.json", "NOTES_DIR": data / "notes"}
        evidence = []
        warnings = messages.warnings

        def create_engine():
            engine = QQmlApplicationEngine()
            engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
            def loaded(root, url):
                if root is None:
                    return
                evidence.append((root.property("title"), root.property("visible"), root.devicePixelRatio()))
                QTimer.singleShot(1500, root.close)
                QTimer.singleShot(15000, QApplication.instance().quit)
            engine.objectCreated.connect(loaded)
            return engine

        with mock.patch.multiple(store, **patches), mock.patch.object(run_qt, "QQmlApplicationEngine", side_effect=create_engine):
            store.ensure_dirs()
            # An empty roots list is intentionally replaced by owner defaults.
            # Use a real empty QA folder so startup never scans owner data.
            collection = data / "empty-repositories"
            collection.mkdir()
            store.save_settings({**store.DEFAULT_SETTINGS, "roots": [str(collection)], "qt_theme": "dark"})
            try:
                result = run.main()
                tkinter_loaded = any(name == "tkinter" or name.startswith("tkinter.") for name in sys.modules)
                print(f"STARTUP result={result} roots={evidence} QML warnings={len(warnings)} tkinter_loaded={tkinter_loaded}")
                for warning in warnings:
                    print(warning)
                return 0 if result == 0 and len(evidence) == 1 and evidence[0][1] and not warnings and not tkinter_loaded and evidence[0][2] == float(os.environ.get("QT_SCALE_FACTOR", "1")) else 1
            finally:
                if instance_lock._handle:
                    instance_lock._handle.close()
                    instance_lock._handle = None
                for handler in list(__import__("logging").getLogger().handlers):
                    handler.close()
                    __import__("logging").getLogger().removeHandler(handler)


if __name__ == "__main__":
    raise SystemExit(main())
