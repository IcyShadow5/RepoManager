"""Capture the real QML application against isolated, genuinely scanned Git folders.

Run with the Qt development environment. No owner registry or Git installation
is touched. Optional committed fixtures remain entirely inside the QA folder.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest import mock

if "--native" in sys.argv:
    os.environ["QT_QPA_PLATFORM"] = "windows"
else:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import Q_ARG, QCoreApplication, QEvent, QMetaObject, Qt, QTimer, QUrl
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from repo_manager import git_availability, project_actions, store
from repo_manager.qt_bridge import RepoManagerBridge
from repo_manager.qt_icons import IconProvider
from repo_manager.repository_service import RepositorySession
from tests.git_repository import create_repository, git as fixture_git


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("normal", "missing", "unusable", "help", "recheck", "settings", "feedback", "changes", "history", "runner", "health", "technical", "launchers", "inventory", "light", "showcase", "extended", "interaction", "commit", "fetch", "remotes"), default="normal")
    parser.add_argument("--committed", action="store_true")
    parser.add_argument("--native", action="store_true", help="Windows Qt backend; show without activation behind other windows")
    parser.add_argument("--fixture-parent", type=Path)
    parser.add_argument("--width", type=int, default=1660)
    parser.add_argument("--height", type=int, default=940)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.fixture_parent:
        args.fixture_parent.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle("Basic")
    app = QApplication([])
    # The Windows offscreen plugin has no system font discovery.
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("segoeui.ttf", "segoeuib.ttf", "CascadiaMono.ttf"):
        QFontDatabase.addApplicationFont(str(fonts / name))
    source = Path(__file__).resolve().parents[1]
    app.setWindowIcon(QIcon(str(source / "appicon.ico")))
    with tempfile.TemporaryDirectory(prefix="repomanager-qt-qa-", dir=args.fixture_parent) as folder:
        root = Path(folder)
        data = root / "app-data"
        scan_root = root / "projects"
        scan_root.mkdir()
        names = ("aider", "Agent Intelligence Lab", "agent-framework", "Article50 Guard",
                 "astro-review", "backup-tool", "Beyond Shelter", "coding-agent-lab",
                 "documentation", "engine-tools", "IC Platform", "ideas-archive",
                 "launcher-tools", "RepoManager", "system-atlas", "workspace-tools",
                 "project-alpha", "project-beta", "project-gamma", "project-delta")
        for index, name in enumerate(names):
            location = scan_root / name
            if args.committed or args.mode == "showcase":
                create_repository(location)
                if index in (0, 2, 5):
                    fixture_git(location, "switch", "-qc", ("feature/context", "review/launchers", "maintenance")[ (0, 2, 5).index(index)])
            else:
                subprocess.run(["git", "init", "-q", "-b", "main", str(location)],
                               check=True, creationflags=git_availability.CREATE_NO_WINDOW)
            if index in (0, 3, 10):
                (location / "README.md").write_text("Synthetic QA repository\n", encoding="utf-8")
        patches = {
            "APP_DIR": data, "REPOS_FILE": data / "repos.json",
            "SETTINGS_FILE": data / "settings.json", "NOTES_DIR": data / "notes",
        }
        with mock.patch.multiple(store, **patches):
            session = RepositorySession()
            runner = root / "qa-agent.cmd"
            runner.write_text(f'@echo off\n"{sys.executable}" -c "import time; time.sleep(30)"\n', encoding="utf-8")
            session.save_settings([str(scan_root)], 4, str(runner) if args.mode == "runner" else "opencode", "")
            if args.mode == "showcase":
                session.settings["agent_cmd"] = str(runner)
                # Cached upstream refs are genuine local Git objects; no network
                # request or owner repository is involved in this fixture.
                ahead_repo = scan_root / "aider"
                fixture_git(ahead_repo, "remote", "add", "origin", "https://example.invalid/demo/aider.git")
                fixture_git(ahead_repo, "update-ref", "refs/remotes/origin/main", "HEAD")
                fixture_git(ahead_repo, "config", "branch.feature/context.remote", "origin")
                fixture_git(ahead_repo, "config", "branch.feature/context.merge", "refs/heads/main")
                fixture_git(ahead_repo, "add", "README.md")
                fixture_git(ahead_repo, "commit", "-qm", "Document the controlled QA fixture")
                (ahead_repo / "next-step.txt").write_text("Controlled demo change\n", encoding="utf-8")
            session.accept_scan(session.scan())
            if args.mode == "showcase":
                # A real invalid .git association exercises the unavailable state.
                unavailable = scan_root / "backup-tool"
                (unavailable / ".git").rename(unavailable / ".git-qa-disabled")
                (unavailable / ".git").write_text("gitdir: missing-qa-git\n", encoding="utf-8")
                session.accept_scan(session.scan())
            session.settings["qt_theme"] = "dark"
            selected = next(record for record in session.records if record["name"] == "aider")
            session.save_curation(project_actions.Target.capture(selected), "active", True,
                                  "Controlled QA - local Git folders")
            if args.mode in {"launchers", "showcase"}:
                session.save_launcher(project_actions.Target.capture(selected),
                    {"name": "Check Python", "executable": sys.executable, "args": ["-V"], "cwd": selected["path"]})
            state = {"available": args.mode not in {"missing", "help", "recheck"}}
            real_probe = git_availability.check_git
            isolated_path = root / "isolated-git-path"
            isolated_path.mkdir()
            if args.mode == "unusable":
                (isolated_path / "git.exe").write_bytes(b"synthetic invalid executable")

            def probe():
                if state["available"] and args.mode != "unusable":
                    return real_probe()
                with mock.patch.dict(os.environ, PATH=str(isolated_path)):
                    return real_probe()

            with mock.patch.object(git_availability, "check_git", side_effect=probe):
                bridge = RepoManagerBridge(session, auto_scan=False)
                qmlRegisterSingletonInstance(RepoManagerBridge, "RepoManager", 1, 0, "App", bridge)
                engine = QQmlApplicationEngine()
                warnings = []
                def report_warnings(errors):
                    warnings.extend(error.toString() for error in errors)
                    print("QML", "\n".join(warnings[-len(errors):]), flush=True)
                engine.warnings.connect(report_warnings)
                engine.addImageProvider("icons", IconProvider())
                if args.native:
                    engine.setInitialProperties({"visible": False})
                engine.load(QUrl.fromLocalFile(str(source / "repo_manager/qml/Main.qml")))
                if not engine.rootObjects():
                    return 1
                window = engine.rootObjects()[0]
                window.resize(args.width, args.height)
                if args.native:
                    window.setFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
                    window.setFlag(Qt.WindowType.WindowStaysOnBottomHint, True)
                    window.show()
                if args.mode == "showcase":
                    window.setProperty("extendedColumns", True)
                results = []

                def capture(name):
                    path = args.output / name
                    if args.native and window.isActive():
                        raise RuntimeError("QA window unexpectedly became active")
                    shot = window.grabWindow()
                    if shot.isNull() or not shot.save(str(path)):
                        raise RuntimeError("Application screenshot unavailable")
                    print(f"CAPTURE {path} {shot.width()}x{shot.height()} DPR={window.devicePixelRatio()} platform={app.platformName()} active={window.isActive()}")

                def finish():
                    try:
                        if args.mode == "recheck" and (bridge.scanning or bridge.gitState != "available"):
                            QTimer.singleShot(200, finish)
                            return
                        if args.mode in {"changes", "history", "commit", "fetch", "remotes"} and bridge.gitController.busy:
                            QTimer.singleShot(200, finish)
                            return
                        capture(f"qt-{args.mode}.png")
                        print(f"STATE git={bridge.gitState} rows={bridge.visibleCount} selected={bridge.selectedProject.get('projectName')} status={bridge.statusText}")
                        results.append(0 if not warnings else 4)
                    except Exception as exc:
                        print(f"QA ERROR {exc}")
                        results.append(2)
                    app.quit()

                def exercise():
                    try:
                        if args.mode == "help":
                            ok = QMetaObject.invokeMethod(window, "showHelp", Qt.ConnectionType.DirectConnection,
                                                          Q_ARG("QVariant", "Git requirement"))
                            if not ok:
                                raise RuntimeError("Help dialog could not be opened")
                        elif args.mode in {"settings", "feedback"}:
                            # QML Popup is exposed as QObject, not QWindow.
                            from PySide6.QtCore import QObject
                            dialog = window.findChild(QObject, args.mode + "Dialog")
                            if dialog is None or not QMetaObject.invokeMethod(dialog, "open"):
                                raise RuntimeError("Dialog could not be opened")
                        elif args.mode == "interaction":
                            from PySide6.QtCore import QObject
                            from PySide6.QtTest import QTest
                            window.requestActivate()
                            app.processEvents()
                            listing = window.findChild(QObject, "repositoryList")
                            QMetaObject.invokeMethod(listing, "forceActiveFocus")
                            original = bridge.selectedProject["projectId"]
                            QTest.keyClick(window, Qt.Key_Down)
                            app.processEvents()
                            assert bridge.selectedProject["projectId"] != original, "Down selection failed"
                            QTest.keyClick(window, Qt.Key_Home)
                            assert bridge.selectedProject["projectId"] == original, "Home selection failed"
                            QTest.keyClick(window, Qt.Key_F10, Qt.ShiftModifier)
                            app.processEvents()
                            menu = window.findChild(QObject, "projectMenu")
                            assert menu.property("visible"), "Context menu did not open"
                            QTest.keyClick(window, Qt.Key_Escape)
                            app.processEvents()
                            assert not menu.property("visible"), "Escape did not close menu"
                            QTest.keyClick(window, Qt.Key_F1)
                            app.processEvents()
                            help_dialog = window.findChild(QObject, "helpDialog")
                            assert help_dialog.property("visible"), "F1 did not open Help"
                            QTest.keyClick(window, Qt.Key_Escape)
                            app.processEvents()
                            assert not help_dialog.property("visible"), "Escape did not close Help"
                            QTest.keyClick(window, Qt.Key_F, Qt.ControlModifier)
                            app.processEvents()
                            search = window.findChild(QObject, "repositorySearch")
                            assert search.property("activeFocus"), "Ctrl+F focus failed"
                            for letter in "Article50":
                                QTest.keyClick(window, Qt.Key(ord(letter.upper())), Qt.ShiftModifier if letter.isupper() else Qt.NoModifier)
                            app.processEvents()
                            assert bridge.visibleCount == 1, "Real search did not filter"
                            QTest.keyClick(window, Qt.Key_Escape)
                            assert bridge.visibleCount == 20, "Search clear did not restore rows"
                            feedback_dialog = window.findChild(QObject, "feedbackDialog")
                            QMetaObject.invokeMethod(feedback_dialog, "open")
                            app.processEvents()
                            feedback_dialog.setProperty("category", "ui")
                            feedback_dialog.findChild(QObject, "feedbackSubject").setProperty("text", "Controlled UI feedback")
                            feedback_dialog.findChild(QObject, "feedbackMessage").setProperty("text", "Keyboard QA report - local only")
                            QMetaObject.invokeMethod(feedback_dialog.findChild(QObject, "feedbackSave"), "clicked")
                            app.processEvents()
                            assert len(list((data / "feedback").glob("*.json"))) == 1, "Feedback UI did not persist"
                            print("INTERACTION keyboard selection / context menu / F1 / Ctrl+F / Escape / local feedback verified")
                        elif args.mode == "inventory":
                            from PySide6.QtCore import QObject
                            dialog = window.findChild(QObject, "inventoryDialog")
                            QMetaObject.invokeMethod(dialog, "open")
                        elif args.mode in {"health", "technical", "launchers"}:
                            from PySide6.QtCore import QObject
                            panel = window.findChild(QObject, "detailPanel")
                            panel.setProperty("tab", {"health": "Health", "technical": "Technical", "launchers": "Run"}[args.mode])
                        elif args.mode == "light":
                            bridge.toggleTheme()
                        elif args.mode == "extended":
                            window.setProperty("extendedColumns", True)
                        elif args.mode in {"changes", "history", "commit", "fetch", "remotes"}:
                            bridge.openGit(args.mode)
                        elif args.mode in {"runner", "showcase"}:
                            bridge.startAgent()
                            if not bridge.agentActive:
                                raise RuntimeError("Controlled Agent did not start")
                        elif args.mode == "recheck":
                            capture("qt-before-recheck.png")
                            state["available"] = True
                            bridge.checkAgain()
                        QTimer.singleShot(600, finish)
                    except Exception as exc:
                        print(f"QA ERROR {exc}")
                        results.append(2)
                        app.quit()

                QTimer.singleShot(1200, exercise)
                QTimer.singleShot(20000, app.quit)
                app.exec()
                if bridge.agentActive:
                    bridge.stopAgent()
                    # Wait for the actual controlled process, then its target observation.
                    for process in list(bridge._agent_processes.values()):
                        process.wait(timeout=5)
                    bridge._poll_agents()
                    deadline = time.monotonic() + 5
                    while bridge._post_runs and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(0.01)
                deadline = time.monotonic() + 10
                while not bridge.prepareClose() and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                if bridge.scanning or bridge.agentActive or bridge._post_runs:
                    raise RuntimeError("Controlled QA workers did not finish")
                engine.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                return results[0] if results else 3


if __name__ == "__main__":
    raise SystemExit(main())
