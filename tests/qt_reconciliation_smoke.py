"""Real current QML events on temporary repositories, never owner AI sessions."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from pathlib import Path
import sys
import tempfile
import time
from unittest import mock

from PySide6.QtCore import QObject, QPointF, Qt, QUrl, QCoreApplication, QEvent
from PySide6.QtGui import QDesktopServices, QKeyEvent
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from repo_manager import projects, store
from repo_manager.qt_bridge import RepoManagerBridge
from repo_manager.qt_icons import IconProvider
from repo_manager.repository_service import RepositorySession
from tests.git_repository import init_repository, git, commit
from tests.qt_messages import QtMessages


def main(scenario):
    messages = QtMessages()
    QQuickStyle.setStyle("Basic")
    app = QApplication([])
    checks = []
    with tempfile.TemporaryDirectory(prefix="repomanager-current-qml-") as folder:
        root = Path(folder)
        data = root / "data"
        with mock.patch.multiple(store, APP_DIR=data, REPOS_FILE=data / "repos.json",
                                 SETTINGS_FILE=data / "settings.json", NOTES_DIR=data / "notes"):
            session = RepositorySession()
            count = 20 if scenario == "filter" else 2
            for index in range(count):
                path = init_repository(root / f"project-{index:02}")
                record = {"name": path.name, "path": str(path), "status": "idea"}
                projects.ensure_project_id(record)
                session.records.append(record)
            session.settings["roots"] = [str(root)]
            store.save_settings(session.settings)
            original_path = os.environ.get("PATH", "")
            if scenario == "onboarding":
                # Retain Qt's DLL directories; remove only directories that
                # expose Git. An empty PATH breaks Windows plugin loading.
                os.environ["PATH"] = os.pathsep.join(part for part in original_path.split(os.pathsep)
                    if part and not (Path(part) / "git.exe").is_file())
            bridge = RepoManagerBridge(session, auto_scan=False)
            qmlRegisterSingletonInstance(RepoManagerBridge, "RepoManager", 1, 0, "App", bridge)
            engine = QQmlApplicationEngine()
            engine.warnings.connect(lambda errors: messages.warnings.extend(error.toString() for error in errors))
            engine.addImageProvider("icons", IconProvider())
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "repo_manager/qml/Main.qml")))
            assert engine.rootObjects(), messages.warnings
            window = engine.rootObjects()[0]
            QTest.qWait(100)

            def check(condition, label):
                assert condition, label
                checks.append(label)

            def wait(condition):
                deadline = time.monotonic() + 20
                while not condition() and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
                check(condition(), "bounded worker/UI operation completed")

            def items():
                pending = [window.contentItem()]
                result = []
                while pending:
                    item = pending.pop()
                    result.append(item)
                    pending.extend(item.childItems())
                return result

            def find(name):
                item = window.findChild(QObject, name)
                return item if item is not None else next(x for x in items() if x.objectName() == name)

            def text_item(text):
                return next(x for x in items() if x.property("text") == text and x.isVisible()
                            and x.property("contentItem") is not None)

            def click(item):
                point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
                QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint())
                QTest.qWait(50)

            def type_text(item, text):
                item.forceActiveFocus()
                for char in text:
                    for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                        QCoreApplication.sendEvent(window, QKeyEvent(kind, 0, Qt.KeyboardModifier.NoModifier, char))
                app.processEvents()

            try:
                if scenario == "onboarding":
                    check(bridge.gitState == "not_found", "real isolated PATH reports Git not found")
                    check(not find("scanButton").isEnabled(), "scan disabled until Git is available")
                    with mock.patch.object(QDesktopServices, "openUrl", return_value=True) as open_url:
                        click(text_item("Install Git"))
                        check(open_url.call_args.args[0].toString() == "https://git-scm.com/install/windows", "actual Install button opens official destination")
                    click(text_item("Check again"))
                    check(bridge.gitState == "not_found", "recheck on unchanged PATH never pretends success")
                    os.environ["PATH"] = original_path
                    click(text_item("Check again"))
                    wait(lambda: not bridge.scanning)
                    check(bridge.gitState == "available" and find("scanButton").isEnabled(), "real recheck discovers Git in the current process")
                else:
                    bridge.selectProject(bridge.repositoryModel.row(0)["projectId"])
                    wait(lambda: not bridge._detail_loading)
                    if scenario == "git":
                        path = Path(bridge.selectedProject["path"])
                        commit(path, "baseline", content="old\n")
                        git(path, "config", "core.autocrlf", "true")
                        git(path, "config", "core.safecrlf", "warn")
                        (path / "tracked.txt").write_bytes(b"new current QML diff\n")
                        baseline = git(path, "rev-parse", "HEAD")
                        bridge.openGit("changes")
                        controller = bridge.gitController
                        wait(lambda: not controller.busy)
                        check(find("gitDialog").property("visible"), "current GitDialog opened")
                        click(next(x for x in items() if x.property("tip") == "Preview file differences" and x.isVisible()))
                        wait(lambda: not controller.busy)
                        check("+new current QML diff" in controller.content, "QML file preview displays real diff")
                        (path / "tracked.txt").write_bytes(b"external editor refresh\n")
                        click(text_item("Refresh"))
                        wait(lambda: not controller.busy)
                        click(next(x for x in items() if x.property("tip") == "Preview file differences" and x.isVisible()))
                        wait(lambda: not controller.busy)
                        check("+external editor refresh" in controller.content, "QML Refresh observes external LF edit under autocrlf")
                        def file_checkbox():
                            return next(x for x in items() if x.isVisible() and x.property("checked") is not None
                                        and x.property("text") == "" and x.property("indicator") is not None)
                        click(file_checkbox())
                        click(text_item("Stage selected"))
                        wait(lambda: not controller.busy)
                        check(git(path, "diff", "--cached", "--name-only") == "tracked.txt", "QML stage button stages selected file")
                        click(text_item("Staged"))
                        click(file_checkbox())
                        click(text_item("Unstage selected"))
                        wait(lambda: not controller.busy)
                        check(git(path, "diff", "--cached", "--name-only") == "", "QML unstage preserves working content")
                        click(text_item("Unstaged / untracked"))
                        click(file_checkbox())
                        click(text_item("Stage selected"))
                        wait(lambda: not controller.busy)
                        message = next(x for x in items() if x.property("placeholderText") == "Local commit message")
                        type_text(message, "QML approved local commit")
                        click(text_item("Commit…"))
                        check(git(path, "rev-parse", "HEAD") == baseline, "commit preview cannot mutate HEAD")
                        QTest.keyClick(window, Qt.Key.Key_Escape)
                        check(git(path, "rev-parse", "HEAD") == baseline, "Escape cancels commit confirmation")
                        click(text_item("Commit…"))
                        click(text_item("Create local commit"))
                        wait(lambda: not controller.busy and not bridge._detail_loading and not bridge.scanning)
                        check(git(path, "log", "-1", "--format=%s") == "QML approved local commit", "explicit QML confirmation creates local commit")
                        find("gitDialog").close()
                    elif scenario == "keyboard":
                        table = find("repositoryList")
                        table.forceActiveFocus()
                        QTest.keyClick(window, Qt.Key.Key_End)
                        wait(lambda: not bridge._detail_loading)
                        check(bridge.selectedIndex == 1, "End selects last real repository")
                        QTest.keyClick(window, Qt.Key.Key_Up)
                        wait(lambda: not bridge._detail_loading)
                        check(bridge.selectedIndex == 0, "Up selects preceding repository")
                        search = find("repositorySearch")
                        search.forceActiveFocus()
                        QTest.keyClick(window, Qt.Key.Key_Tab)
                        check(window.activeFocusItem() != search, "Tab moves focus forward")
                        QTest.keyClick(window, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
                        check(window.activeFocusItem() == search, "Shift+Tab restores search focus")
                        type_text(search, "project-01")
                        check(bridge.visibleCount == 1 and bridge.query == "project-01", "keyboard search filters actual model")
                        table.forceActiveFocus()
                        QTest.keyClick(window, Qt.Key.Key_Escape)
                        check(bridge.query == "" and bridge.visibleCount == 2, "Escape clears table search")
                        click(text_item("Help"))
                        check(find("helpDialog").property("visible"), "current Help opens")
                        QTest.keyClick(window, Qt.Key.Key_Escape)
                        check(not find("helpDialog").property("visible"), "Escape closes current Help")
                        panel = find("detailPanel")
                        for tab in ("Technical", "Health", "Run", "Notes"):
                            click(next(x for x in items() if x.property("modelData") == tab
                                       and x.property("contentItem") is not None))
                            check(panel.property("tab") == tab, "actual detail tab click routes to " + tab)
                        editor = next(x for x in items() if x.property("placeholderText") == "Notes, next steps, useful context…")
                        type_text(editor, "Current QML note")
                        click(text_item("Save notes"))
                        check(session.load_note(bridge._selected_target()) == "Current QML note", "QML Notes edit/save persists for the selected stable target")
                    elif scenario == "filter":
                        table = find("repositoryList")
                        search = find("repositorySearch")
                        for attempt in range(3):
                            for query in ("project-1", "no-match", "", "project-0", "", "no-match", "") * 3:
                                search.setProperty("text", query)
                                QTest.qWait(5)
                                if bridge.visibleCount:
                                    table.setProperty("contentY", max(0, table.property("contentHeight") - table.height()))
                                app.processEvents()
                                check(table.property("count") == bridge.visibleCount, "delegate count agrees after filter/reset")
                            window.resize(1120 if attempt % 2 else 1660, 640 if attempt % 2 else 940)
                            bridge.toggleTheme()
                            QTest.qWait(50)
                        check(bridge.visibleCount == 20, "three bounded filter/scroll/resize/theme runs restore complete model")
                        check(not messages.warnings, "DelegateModel warning not reproduced in live reset cycles")
                    else:
                        raise ValueError("Unknown QML scenario")
            finally:
                os.environ["PATH"] = original_path
                if bridge.scanning:
                    bridge.cancelScan()
                wait(lambda: not bridge.scanning and not bridge._detail_loading and not bridge.gitController.busy)
                window.close()
                engine.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
            check(not messages.warnings, "no global Qt or QML warnings, including teardown")
    print(f"CURRENT_QML scenario={scenario} checks={len(checks)} QML warnings={len(messages.warnings)}")
    messages.restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
