"""Exercise real QML controls with isolated settings and real Git folders."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
import tempfile
import time
import sys
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from repo_manager import agents, store
from repo_manager.qt_bridge import RepoManagerBridge
from repo_manager.qt_icons import IconProvider
from repo_manager.repository_service import RepositorySession
from tests.git_repository import canonical_path, init_repository
from tests.qt_messages import QtMessages


def main():
    messages = QtMessages()
    QQuickStyle.setStyle("Basic")
    app = QApplication([])
    with tempfile.TemporaryDirectory(prefix="repomanager-controls-") as folder:
        root = Path(folder)
        data = root / "app"
        with mock.patch.multiple(store, APP_DIR=data, SETTINGS_FILE=data / "settings.json",
                                 REPOS_FILE=data / "repos.json", NOTES_DIR=data / "notes"):
            collection = root / "repositories"
            selected = init_repository(collection / "clean")
            init_repository(collection / "group" / "subgroup" / "deep")
            session = RepositorySession()
            session.save_settings([str(collection)], 2, "opencode", "")
            session.accept_scan(session.scan())
            bridge = RepoManagerBridge(session, auto_scan=False)
            qmlRegisterSingletonInstance(RepoManagerBridge, "RepoManager", 1, 0, "App", bridge)
            engine = QQmlApplicationEngine()
            warnings = messages.warnings
            engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
            engine.addImageProvider("icons", IconProvider())
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "repo_manager/qml/Main.qml")))
            assert engine.rootObjects(), warnings
            window = engine.rootObjects()[0]
            checks = []

            def wait_for(condition):
                deadline = time.monotonic() + 15
                while not condition() and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
                assert condition(), "Control operation did not complete"

            def check(condition, description):
                assert condition, description
                checks.append(description)

            def click(item):
                app.processEvents()
                point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
                QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint())
                QTest.qWait(80)

            def find(name):
                value = window.findChild(QObject, name)
                pending = [window.contentItem()]
                while value is None and pending:
                    item = pending.pop()
                    if item.objectName() == name:
                        value = item
                        break
                    pending.extend(item.childItems())
                assert value is not None, name
                return value

            try:
                bridge.selectProject(bridge.repositoryModel.row(0)["projectId"])
                wait_for(lambda: not bridge._detail_loading)
                check(bridge.visibleCount == 1, "depth two excludes the deeper real repository")
                check(not find("quickRunButton").property("enabled"), "Quick Run disabled without a usable launcher")
                panel = None
                pending = [window.contentItem()]
                while pending and panel is None:
                    item = pending.pop()
                    if item.property("healthFilter") is not None:
                        panel = item
                    pending.extend(item.childItems())
                assert panel is not None
                panel.setProperty("tab", "Health")
                QTest.qWait(100)
                health_control = find("healthPreference_readme_presence")
                flick = find("detailScroll").property("contentItem")
                flick.setProperty("contentY", health_control.mapToItem(flick, QPointF()).y() + flick.property("contentY") - 50)
                QTest.qWait(100)
                click(health_control)
                wait_for(lambda: not bridge._detail_loading)
                check(bridge.healthEvidence["ignored"] == 1 and bridge.healthEvidence["counts"]["ignored"] == 1, "real Health control persists an explicit ignored finding")
                restored = RepositorySession()
                check(restored.inspect(bridge._selected_target())[0].summary.ignored_count == 1, "ignored check survives a fresh session")
                QTest.qWait(150)
                health_control = find("healthPreference_readme_presence")
                flick.setProperty("contentY", health_control.mapToItem(flick, QPointF()).y() + flick.property("contentY") - 50)
                QTest.qWait(100)
                click(health_control)
                wait_for(lambda: not bridge._detail_loading)
                check(bridge.healthEvidence["ignored"] == 0, "real Restore check re-enables the finding")
                panel.setProperty("tab", "Overview")
                bridge._commands = [{"label": "First", "kind": "custom", "healthy": True}, {"label": "Second", "kind": "custom", "healthy": True}]
                bridge.changed.emit()
                run_button = find("quickRunButton")
                run_button.forceActiveFocus()
                with mock.patch.object(session, "run_launcher") as launch:
                    QTest.keyClick(window, Qt.Key.Key_Return)
                    QTest.qWait(100)
                    chooser = find("quickRunMenu")
                    check(chooser.property("visible"), "keyboard Enter opens Quick Run chooser for multiple launchers")
                    content = chooser.property("contentItem")
                    point = content.mapToScene(QPointF(80, 50))
                    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint())
                    QTest.qWait(100)
                    check(launch.call_args.args[1]["label"] == "Second", "real chooser sends the selected launcher through the existing launch path")
                    pending = [window.contentItem()]
                    run_tab = None
                    while pending and run_tab is None:
                        item = pending.pop()
                        if item.property("modelData") == "Run" and item.property("contentItem") is not None:
                            run_tab = item
                        pending.extend(item.childItems())
                    assert run_tab is not None
                    click(run_tab)
                    check(panel.property("tab") == "Run" and any(
                        item.property("text") == "Detected launchers" and item.isVisible()
                        for item in window.findChildren(QObject)),
                        "actual Run tab click exposes the full launcher management surface")
                panel.setProperty("tab", "Overview")
                bridge._request_detail()
                wait_for(lambda: not bridge._detail_loading)
                click(find("projectActionsButton"))
                menu = find("projectMenu")
                check(menu.property("visible") and menu.property("width") >= 260, "gear click opens a readable menu")
                content = menu.property("contentItem")
                point = content.mapToScene(QPointF())
                check(point.x() >= 0 and point.y() >= 0 and point.x() + content.width() <= window.width() and point.y() + content.height() <= window.height(), "menu is contained within the window")
                app.clipboard().clear()
                copy_point = content.mapToScene(QPointF(100, 48))
                QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, copy_point.toPoint())
                QTest.qWait(80)
                copied = app.clipboard().text()
                check(canonical_path(copied) == canonical_path(selected) and os.path.samefile(copied, selected), "actual menu Copy path action works")

                dialog = find("settingsDialog")
                dialog.open()
                QTest.qWait(100)
                before = store.SETTINGS_FILE.read_bytes()
                click(find("settingsTabHealth"))
                check(dialog.property("section") == "Health", "central advisory Health settings are reachable")
                click(find("globalHealth_readme_presence"))
                check(list(dialog.property("ignoredHealth").toVariant()) == ["readme_presence"], "global advisory checkbox stages the exclusion")
                click(find("settingsTabIntegrations"))
                check(dialog.property("section") == "Integrations" and find("agentExecutable").property("visible"), "integration tab reveals the preserved fields")
                click(find("settingsTabAppearance"))
                check(dialog.property("section") == "Appearance", "appearance tab is reachable")
                click(find("settingsTabIgnored projects"))
                check(dialog.property("section") == "Ignored projects", "ignored-project settings are reachable")
                click(find("settingsTabScan folders"))
                path = find("scanRootPath")
                path.setProperty("text", str(collection))
                click(find("addScanPath"))
                check("already included" in dialog.property("error"), "duplicate folder rejected by the actual form")
                path.setProperty("text", str(root / "missing"))
                click(find("addScanPath"))
                check("existing accessible" in dialog.property("error"), "missing folder rejected by the actual form")
                if os.name == "nt":
                    click(find("addCDrive"))
                    check(not dialog.property("error"), "C drive is a working opt-in form addition")
                    check(dialog.property("roots")[-1] == "C:\\", "whole-drive shortcut preserves the absolute drive root")
                click(find("settingsCancel"))
                check(store.SETTINGS_FILE.read_bytes() == before, "Cancel preserves saved search roots")
                dialog.open()
                QTest.qWait(100)
                find("scanDepth").setProperty("currentIndex", 2)
                click(find("settingsSave"))
                wait_for(lambda: not bridge.scanning)
                check(bridge.visibleCount == 2 and session.settings["roots"] == [str(collection)] and session.settings["depth"] == 3, "Save scans the updated relative depth without adding C automatically")
                check(not dialog.property("visible"), "successful settings save closes the dialog")

                app.processEvents()
                items = []
                pending = [window.contentItem()]
                while pending:
                    item = pending.pop()
                    items.append(item)
                    pending.extend(item.childItems())
                cells = [item for item in items if item.objectName() == "repositoryLastCommitCell"]
                paths = [item for item in items if item.objectName() == "repositoryPathCell"]
                check(len(cells) == 2 and len(paths) == 2, "both real repository rows are instantiated")
                header = find("repositoryHeaderlast_commit")
                header_x = header.mapToScene(QPointF()).x()
                check(all(abs(cell.mapToScene(QPointF()).x() - header_x) < 1 for cell in cells), "last-commit cells align with their header across different repository names")
                check(abs(paths[0].mapToScene(QPointF()).x() - paths[1].mapToScene(QPointF()).x()) < 1, "path cells use one shared column origin")
                work_button = next(item for item in items if item.property("text") == "Work on this" and item.property("contentItem") is not None)
                contents = work_button.property("contentItem")
                group = contents.childItems()[0]
                group_center = group.mapToScene(QPointF(group.width() / 2, 0)).x()
                button_center = work_button.mapToScene(QPointF(work_button.width() / 2, 0)).x()
                check(abs(group_center - button_center) < 1, "Work on this icon and label are centered together")

                working_nav = next(item for item in items if item.property("label") == "Working on now")
                repositories_nav = next(item for item in items if item.property("label") == "Repositories" and item.property("selected") is not None)
                settings_button = next(item for item in items if item.property("text") == "Settings" and item.property("contentItem") is not None)
                working_nav.forceActiveFocus()
                QTest.keyClick(window, Qt.Key.Key_Return)
                QTest.qWait(80)
                check(bridge.section == "working", "Enter activates focused sidebar navigation")
                repositories_nav.forceActiveFocus()
                QTest.keyClick(window, Qt.Key.Key_Enter)
                QTest.qWait(80)
                check(bridge.section == "repositories", "keypad Enter activates focused sidebar navigation")
                working_nav.forceActiveFocus()
                QTest.keyClick(window, Qt.Key.Key_Space)
                QTest.qWait(80)
                check(bridge.section == "working", "Space activates focused sidebar navigation")
                repositories_nav.forceActiveFocus()
                QTest.keyClick(window, Qt.Key.Key_Space)
                QTest.qWait(80)
                check(bridge.section == "repositories", "Space returns to repositories")
                settings_button.forceActiveFocus()
                QTest.keyClick(window, Qt.Key.Key_Return)
                QTest.qWait(80)
                check(dialog.property("visible"), "Enter opens Settings from its focused button")
                QTest.keyClick(window, Qt.Key.Key_Escape)
                QTest.qWait(80)
                check(not dialog.property("visible"), "Escape closes Settings after keyboard activation")

                feedback = find("feedbackDialog")
                feedback.open()
                QTest.qWait(100)
                find("feedbackSubject").setProperty("text", "Controlled UI feedback")
                find("feedbackMessage").setProperty("text", "Real button interaction")
                click(find("feedbackCategoryui"))
                click(find("feedbackSave"))
                check(bridge.feedbackStatus.startswith("Saved locally:"), "Feedback saves a real local report")
                click(find("feedbackCopy"))
                check("Real button interaction" in app.clipboard().text() and "UI issue" in app.clipboard().text(), "Feedback Copy works after the save status appears")
                with mock.patch.object(QDesktopServices, "openUrl", return_value=True) as open_url:
                    click(find("feedbackDraft"))
                    url = open_url.call_args.args[0].toString()
                    check(url.startswith("https://github.com/IcyShadow5/RepoManager/issues/new?")
                          and "Controlled+UI+feedback" in url,
                          "actual Feedback draft button sends the reviewed issue URL to the browser boundary")
                window.resize(1120, 640)
                QTest.qWait(100)
                feedback_content = feedback.property("contentItem")
                feedback_top = feedback_content.mapToScene(QPointF()).y()
                check(feedback_top >= 0 and feedback_top + feedback_content.height() <= window.height(), "Feedback fits the minimum window size after saving")
                feedback.close()

                help_dialog = find("helpDialog")
                help_dialog.open()
                for topic, phrase in (("Scanning and cancellation", "last complete"),
                                      ("Health and status", "never PASS"),
                                      ("Changes, diff and commits", "without discarding"),
                                      ("Run and Quick Run", "same launch path"),
                                      ("Agents", "remembers your selection"),
                                      ("Explorer, VS Code and Terminal", "PowerShell 7 is not required"),
                                      ("Versions and updates", "does not automatically")):
                    help_dialog.setProperty("topic", topic)
                    app.processEvents()
                    check(phrase in find("helpBody").property("text"), "current Help renders " + topic)
                    content = help_dialog.property("contentItem")
                    top = content.mapToScene(QPointF()).y()
                    check(top >= 0 and top + content.height() <= window.height(), "Help fits minimum window for " + topic)
                with mock.patch.object(QDesktopServices, "openUrl", return_value=True) as opened:
                    click(find("officialReleases"))
                check(opened.call_args.args[0].toString() == "https://github.com/IcyShadow5/RepoManager/releases", "real Help button opens only the official release page")
                help_dialog.close()

                if bridge.themeName != "light":
                    bridge.toggleTheme()
                app.processEvents()
                nav = next(item for item in window.findChildren(QObject) if item.property("label") == "Repositories")
                color = nav.property("background").property("color")
                check(color.lightnessF() > .7, "Light navigation selection has a light background")
                label = next(item for item in nav.property("contentItem").childItems() if item.property("text") == "Repositories")
                def luminance(value):
                    channels = [value.redF(), value.greenF(), value.blueF()]
                    linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4 for channel in channels]
                    return sum(channel * weight for channel, weight in zip(linear, (.2126, .7152, .0722)))
                foreground, background = sorted((luminance(label.property("color")), luminance(color)))
                check((background + .05) / (foreground + .05) >= 4.5, "Light navigation text has at least 4.5:1 contrast")
                window.resize(1120, 640)
                dialog.open()
                QTest.qWait(100)
                content = dialog.property("contentItem")
                point = content.mapToScene(QPointF())
                check(point.y() >= 0 and point.y() + content.height() <= window.height(), "Settings fits the minimum window size")
                dialog.close()
                def controlled_scan(control):
                    control.update(path=str(collection), directories=7, repositories=2)
                    control.cancelled.wait(10)
                    control.check()
                    raise AssertionError("Controlled scan must be cancelled")

                with mock.patch.object(session, "scan", side_effect=controlled_scan), mock.patch.object(session, "accept_scan") as save:
                    click(find("scanButton"))
                    wait_for(lambda: bridge.scanning)
                    check(not find("scanButton").isEnabled(), "overlapping scan button disabled")
                    wait_for(lambda: find("cancelScanButton").isVisible())
                    click(find("cancelScanButton"))
                    wait_for(lambda: not bridge.scanning)
                    check(not save.called, "real Cancel scan control does not save incomplete inventory")
                    click(find("scanButton"))
                    wait_for(lambda: bridge.scanning)
                    window.close()
                    close_scan = find("closeScanDialog")
                    wait_for(lambda: close_scan.property("visible"))
                    check(window.isVisible() and not bridge._close_after_scan, "close warning keeps application open without queued close")
                    click(find("keepScanning"))
                    check(bridge.scanning and not close_scan.property("visible"), "Keep scanning dismisses warning without cancelling")
                    window.close()
                    wait_for(lambda: close_scan.property("visible"))
                    QTest.keyClick(window, Qt.Key.Key_Escape)
                    check(not close_scan.property("visible") and bridge.scanning, "Escape returns to app while scan continues")
                    click(find("cancelScanButton"))
                    wait_for(lambda: not bridge.scanning)

                catalog = [{**agents.new_agent("custom:qa" + str(i), "QA Agent " + str(i), sys.executable), "availability": agents.AVAILABLE, "resolved": sys.executable} for i in range(2)]
                process = mock.Mock(pid=42, poll=mock.Mock(return_value=None))
                with mock.patch.object(agents, "agent_catalog", return_value=catalog), mock.patch("repo_manager.processes.spawn_agent", return_value=process):
                    bridge._refresh_agent()
                    bridge.changed.emit()
                    click(find("startAgentButton"))
                    chooser = find("agentChooser")
                    wait_for(lambda: chooser.property("visible"))
                    QTest.keyClick(window, Qt.Key.Key_Down)
                    QTest.keyClick(window, Qt.Key.Key_Down)
                    bridge.changed.emit()
                    app.processEvents()
                    QTest.keyClick(window, Qt.Key.Key_Return)
                    wait_for(lambda: bridge.agentActive)
                    check(bridge.agentInfo["agentName"] == "QA Agent 1", "keyboard chooses the requested Agent")
                    process.terminate.side_effect = lambda: setattr(process.poll, "return_value", 0)
                    bridge.stopAgent()
                    wait_for(lambda: not bridge.agentActive and not bridge._post_runs and not bridge.scanning)
                    check(session.settings["selected_agent_id"] == "custom:qa1", "explicit Agent choice is persisted")
                check(not warnings, "no QML warnings during actual control interaction")
                print(f"INTERACTION checks={len(checks)} QML warnings={len(warnings)}")
                return 0
            finally:
                wait_for(lambda: not bridge.scanning and not bridge._detail_loading)
                window.close()
                app.processEvents()


if __name__ == "__main__":
    raise SystemExit(main())
