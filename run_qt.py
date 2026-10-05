"""Development entry point for the incremental PySide6/QML presentation."""
import sys
import logging
import logging.handlers
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QMessageBox

from repo_manager import desktop_identity, instance_lock, store, version
from repo_manager.qt_bridge import RepoManagerBridge
from repo_manager.qt_icons import IconProvider
from repo_manager.repository_service import RepositorySession


def initial_window_size(available):
    """Leave room for native chrome within the screen's logical work area."""
    return {"width": max(1120, min(1660, available.width() - 40)),
            "height": max(640, min(940, available.height() - 60))}


def report_qml_load_failure(qml_path):
    message = ("RepoManager could not load its interface. Extract the complete "
               "portable ZIP into a new folder and try again. "
               f"Diagnostics: {store.APP_DIR / 'repo_manager.log'}")
    logging.getLogger(__name__).error("QML root load failed: %s", qml_path)
    QMessageBox.critical(None, "RepoManager — interface unavailable", message)


def main():
    desktop_identity.set_windows_app_user_model_id(desktop_identity.WINDOWS_DEVELOPMENT_APP_ID)
    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv)
    app.setApplicationName("RepoManager Development")
    app.setApplicationVersion(version.VERSION)
    icon = Path(__file__).resolve().parent / "appicon.ico"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    try:
        store.ensure_dirs()
        acquired = instance_lock.acquire()
    except OSError as exc:
        QMessageBox.critical(None, "RepoManager", f"Application-data lock unavailable: {exc}")
        return 2
    if not acquired:
        QMessageBox.warning(None, "RepoManager", "RepoManager is already running. Check the taskbar for its window.")
        return 2
    try:
        handler = logging.handlers.RotatingFileHandler(store.APP_DIR / "repo_manager.log",
            maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        logging.basicConfig(level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=[handler])
        session = RepositorySession()
    except (OSError, ValueError) as exc:
        QMessageBox.critical(None, "RepoManager", f"Application data could not be loaded: {exc}")
        return 2
    bridge = RepoManagerBridge(session)
    qmlRegisterSingletonInstance(RepoManagerBridge, "RepoManager", 1, 0,
                                 "App", bridge)
    engine = QQmlApplicationEngine()
    engine.addImageProvider("icons", IconProvider())
    screen = app.primaryScreen()
    if screen is not None:
        engine.setInitialProperties(initial_window_size(screen.availableGeometry()))
    qml_path = Path(__file__).resolve().parent / "repo_manager" / "qml" / "Main.qml"
    engine.warnings.connect(lambda warnings: logging.getLogger(__name__).error(
        "QML diagnostics: %s", "\n".join(warning.toString() for warning in warnings)))
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        report_qml_load_failure(qml_path)
        return 1
    window = engine.rootObjects()[0]
    window.setIcon(app.windowIcon())
    desktop_identity.set_windows_window_identity(int(window.winId()))
    result = app.exec()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
