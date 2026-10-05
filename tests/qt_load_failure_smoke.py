"""Real engine failure with an observed error-dialog boundary and diagnostic log."""
import logging
import os
from pathlib import Path
import tempfile
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from repo_manager import instance_lock, store
import run_qt


def main():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        patches = {'APP_DIR': root, 'REPOS_FILE': root/'repos.json',
                   'SETTINGS_FILE': root/'settings.json', 'NOTES_DIR': root/'notes'}
        engines = []

        def create_engine():
            engine = QQmlApplicationEngine()
            original_load = engine.load
            engine.load = lambda url: original_load(QUrl.fromLocalFile(str(root/'absent.qml')))
            engines.append(engine)
            return engine

        try:
            with mock.patch.multiple(store, **patches), mock.patch.object(run_qt, 'QQmlApplicationEngine', side_effect=create_engine), mock.patch.object(run_qt.QMessageBox, 'critical') as dialog:
                store.ensure_dirs()
                store.save_settings({**store.DEFAULT_SETTINGS, 'roots': [str(root)], 'qt_theme': 'dark'})
                result = run_qt.main()
                assert result == 1 and dialog.call_count == 1
                assert 'Extract the complete portable ZIP' in dialog.call_args.args[2]
                assert 'repo_manager.log' in dialog.call_args.args[2]
                for handler in logging.getLogger().handlers:
                    handler.flush()
                log = (root/'repo_manager.log').read_text(encoding='utf-8')
                assert 'QML root load failed' in log and 'QML diagnostics:' in log and 'absent.qml' in log
                print('QML root failure: exit=1 dialog=1 diagnostic_log=PASS')
                return 0
        finally:
            if instance_lock._handle:
                instance_lock._handle.close()
                instance_lock._handle = None
            for handler in list(logging.getLogger().handlers):
                handler.close()
                logging.getLogger().removeHandler(handler)


if __name__ == '__main__':
    raise SystemExit(main())
