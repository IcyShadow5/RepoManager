"""Opt-in RC instrumentation; normal startup has no probe or test fixtures."""
import sys

if "--rc-launch-receipt" in sys.argv:
    import ctypes
    import json
    import os
    from pathlib import Path
    from repo_manager.version import is_prerelease

    receipt_config = Path(sys.argv[sys.argv.index("--rc-launch-receipt") + 1]).resolve()
    controlled = receipt_config.parent
    if (not getattr(sys, "frozen", False) or not is_prerelease()
            or (controlled / "CONTROLLED_QA_ONLY").read_text(encoding="ascii").strip() != "RepoManager RC QA"
            or not Path(os.environ.get("LOCALAPPDATA", "")).resolve().is_relative_to(controlled)
            or not Path.cwd().resolve().is_relative_to(controlled)):
        raise RuntimeError("Launcher receipt requires an isolated controlled portable QA process")
    payload = json.loads(receipt_config.read_text(encoding="utf-8"))
    receipt = controlled / payload["receipt"]
    if receipt.parent != controlled or receipt.suffix != ".json":
        raise RuntimeError("Launcher receipt must remain inside the controlled directory")
    receipt.write_text(json.dumps({"cwd": str(Path.cwd()), "executable": sys.executable,
                                  "console": bool(ctypes.windll.kernel32.GetConsoleWindow()),
                                  "label": payload["label"]}), encoding="utf-8")
    os._exit(0)

if "--rc-qa" in sys.argv:
    import json
    import os
    from pathlib import Path
    import time
    import ctypes
    import subprocess

    if not getattr(sys, "frozen", False):
        raise RuntimeError("RC probe requires the extracted portable executable")
    config_path = Path(sys.argv[sys.argv.index("--rc-qa") + 1]).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    qa_root = config_path.parent
    if (qa_root / "CONTROLLED_QA_ONLY").read_text(encoding="ascii").strip() != "RepoManager RC QA":
        raise RuntimeError("A marked controlled QA directory is required")
    appdata = Path(os.environ.get("LOCALAPPDATA", "")).resolve()
    if not appdata.is_relative_to(qa_root) or appdata == qa_root:
        raise RuntimeError("RC probe refuses owner application data")
    output = qa_root / config["output"]
    if output.parent != qa_root:
        raise RuntimeError("Probe output must remain in the controlled directory")
    output.mkdir(exist_ok=True)
    results = {"mode": config["mode"], "checks": [], "warnings": [], "errors": []}
    started = time.perf_counter()
    from PySide6.QtCore import (QCoreApplication, QTimer, QObject, QMetaObject, Qt, QUrl, QPoint, QPointF, QtMsgType, qInstallMessageHandler, qVersion, Slot)
    from PySide6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent, QWheelEvent, QDesktopServices, QFontInfo, QFont
    import PySide6
    import run_qt
    from repo_manager import agents, desktop_identity, health_preferences, processes, qt_bridge, scanner, store, version
    from repo_manager.scan_control import current_control

    if not version.is_prerelease():
        raise RuntimeError("RC probe is unavailable for public release identities")
    settings = json.loads(store.SETTINGS_FILE.read_text(encoding="utf-8"))
    if any(not Path(root).is_absolute() or not Path(root).resolve().is_relative_to(qa_root) for root in settings.get("roots", [])):
        raise RuntimeError("RC scan roots must remain in the controlled directory")
    # The synthetic Agent is noninteractive. Its cmd shim must not open a
    # desktop console while QA runs; ordinary Agent launches remain unchanged.
    original_spawn_agent = processes.spawn_agent
    def spawn_qa_agent(executable, args, *, cwd, creationflags=0):
        return original_spawn_agent(executable, args, cwd=cwd,
                                    creationflags=creationflags | subprocess.CREATE_NO_WINDOW)
    processes.spawn_agent = spawn_qa_agent
    results["qa_agent_console"] = "CREATE_NO_WINDOW for the opt-in controlled runner only"

    def message(kind, context, text):
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            results["warnings"].append(text)
        with (output / "qt-messages.log").open("a", encoding="utf-8") as log:
            log.write(text + "\n")
    qInstallMessageHandler(message)
    bridges = []
    old_init = qt_bridge.RepoManagerBridge.__init__
    def init_bridge(self, *args, **kwargs):
        old_init(self, *args, **kwargs)
        bridges.append(self)
    qt_bridge.RepoManagerBridge.__init__ = init_bridge
    BaseEngine = run_qt.QQmlApplicationEngine

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        results["checks"].append(name)

    def wait_for(condition, timeout=25):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        check(bool(condition()), "bounded operation completed")

    def key(window, code, modifiers=Qt.KeyboardModifier.NoModifier, text=""):
        for event_type in (QKeyEvent.Type.KeyPress, QKeyEvent.Type.KeyRelease):
            QCoreApplication.sendEvent(window, QKeyEvent(event_type, code, modifiers, text))
        QCoreApplication.processEvents()

    def find(window, name):
        value = window.findChild(QObject, name)
        pending = [window.contentItem()]
        while value is None and pending:
            item = pending.pop()
            if item.objectName() == name:
                value = item
                break
            pending.extend(item.childItems())
        check(value is not None, "control available: " + name)
        return value

    def click_at(window, point):
        for kind, buttons in ((QMouseEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                              (QMouseEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
            event = QMouseEvent(kind, point, point, Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
            QCoreApplication.sendEvent(window, event)
        QCoreApplication.processEvents()

    def settle():
        deadline = time.monotonic() + .1
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(.01)

    def click(window, item):
        settle()
        click_at(window, item.mapToScene(QPointF(item.width()/2, item.height()/2)))
        settle()

    def capture(window, name):
        QCoreApplication.processEvents()
        shot = window.grabWindow()
        check(not shot.isNull() and shot.save(str(output / (name + ".png"))), "actual portable screenshot " + name)

    def native_identity(window):
        identity = desktop_identity
        handle = ctypes.c_void_p()
        iid = identity._Guid.parse("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
        getter = ctypes.windll.shell32.SHGetPropertyStoreForWindow
        getter.argtypes = [ctypes.c_void_p, ctypes.POINTER(identity._Guid), ctypes.POINTER(ctypes.c_void_p)]
        getter.restype = ctypes.c_long
        check(getter(int(window.winId()), ctypes.byref(iid), ctypes.byref(handle)) >= 0, "native window property store available")
        table = ctypes.cast(handle, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        get_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
            ctypes.POINTER(identity._PropertyKey), ctypes.POINTER(identity._PropertyValue))(table[5])
        properties = {}
        try:
            for pid in (2, 3, 4, 5):
                key_value = identity._PropertyKey(identity._Guid.parse("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3"), pid)
                value = identity._PropertyValue()
                check(get_value(handle, ctypes.byref(key_value), ctypes.byref(value)) >= 0 and value.vt == 31, "explicit native identity property " + str(pid))
                properties[str(pid)] = ctypes.wstring_at(value.pointer)
                ctypes.windll.ole32.PropVariantClear(ctypes.byref(value))
        finally:
            ctypes.WINFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)(table[2])(handle)
        return properties

    class UrlCapture(QObject):
        @Slot(QUrl)
        def receive(self, url):
            results.setdefault("opened_urls", []).append(url.toString())
    url_capture = UrlCapture()

    def exercise(window):
        bridge = bridges[0]
        try:
            results.update({"frozen": sys.frozen, "executable": sys.executable, "internal": sys._MEIPASS,
                "sys_path": sys.path, "module_paths": {name: getattr(module, "__file__", None) for name, module in sys.modules.copy().items() if name in {"run_qt", "repo_manager.qt_bridge", "PySide6", "PySide6.QtCore", "repo_manager.store"}},
                "python": sys.version.split()[0], "pyside": PySide6.__version__, "qt": qVersion(),
                "title": window.title(), "dpr": window.devicePixelRatio(), "platform": QGuiApplication.platformName(),
                "icon_null": QGuiApplication.windowIcon().isNull(), "initial_theme": bridge.themeName,
                "font": QFontInfo(QFont("Segoe UI")).family()})
            results["console_window"] = bool(ctypes.windll.kernel32.GetConsoleWindow())
            results["window_seconds"] = getattr(window, "rc_window_seconds", None)
            results["operator_pending"] = ["Taskbar presentation, physical tab order and Ctrl+F in an active desktop window", "Explorer, VS Code and Terminal launch destinations"]
            check(version.VERSION in window.title(), "unreleased identity in actual window")
            check(not results["icon_null"], "actual application icon available")
            check(not any(n == "tkinter" or n.startswith("tkinter.") for n in sys.modules), "no Tkinter imported")
            check(not window.isActive(), "QA did not take owner focus")
            check(all(Path(p).resolve().is_relative_to(Path(sys._MEIPASS)) for p in sys.path), "all import paths confined to extracted package")
            check(not results["console_window"], "windowed package has no console window")
            results["native_identity"] = native_identity(window)
            check(results["native_identity"]["5"] == desktop_identity.WINDOWS_DEVELOPMENT_APP_ID, "Development taskbar grouping is explicit")
            check(results["native_identity"]["3"] == str(Path(sys.executable).resolve()) + ",0", "taskbar icon resource is the extracted executable")
            window.showMinimized()
            settle()
            window.showNormal()
            settle()
            check(native_identity(window) == results["native_identity"], "native identity survives minimize and restore")
            if config["mode"] in {"missing", "unusable"}:
                expected = "launch_failed" if config["mode"] == "unusable" else "not_found"
                check(bridge.gitState == expected, "actual Git availability state: " + expected)
                check(not bridge.scanning, "missing Git does not start a misleading scan")
                capture(window, "RC-missing-git")
                QDesktopServices.setUrlHandler("https", url_capture, "receive")
                bridge.openGitInstall()
                check(results.get("opened_urls") == ["https://git-scm.com/install/windows"], "official install URL through Qt desktop service")
                bridge.checkAgain()
                check(bridge.gitState == expected, "recheck did not falsely succeed")
                os.environ["PATH"] = config["git_path"] + os.pathsep + os.environ["PATH"]
                bridge.checkAgain()
                wait_for(lambda: not bridge.scanning)
                check(bridge.gitState == "available" and bridge.visibleCount >= 20, "fresh recheck recovered and scanned real repositories")
                capture(window, "RC-rechecked")
            else:
                wait_for(lambda: not bridge.scanning and bridge.visibleCount >= 20)
                results["ready_seconds"] = time.perf_counter() - started
                check(bridge.gitState == "available", "normal Git requires no onboarding")
                names = [row["projectName"] for row in bridge._model._rows]
                check("clean-repo" in names and "modified-repo" in names and "unavailable-repo" in names, "real clean/modified/unavailable inventory")
                modified = next(row for row in bridge._model._rows if row["projectName"] == "modified-repo")
                bridge.selectProject(modified["projectId"])
                wait_for(lambda: not bridge._detail_loading)
                check(bridge.selectedProject["projectName"] == "modified-repo", "repository selection and detail target")
                check(bool(bridge._health), "actual repository health evaluation")
                pending = [window.contentItem()]
                detail_panel = None
                while pending and detail_panel is None:
                    item = pending.pop()
                    if item.property("healthFilter") is not None:
                        detail_panel = item
                    pending.extend(item.childItems())
                check(detail_panel is not None, "real detail surface available")
                detail_panel.setProperty("tab", "Health")
                settle()
                def click_health():
                    control = find(window, "healthPreference_readme_presence")
                    flick = find(window, "detailScroll").property("contentItem")
                    flick.setProperty("contentY", control.mapToItem(flick, QPointF()).y() + flick.property("contentY") - 50)
                    settle()
                    click(window, control)
                    wait_for(lambda: not bridge._detail_loading)
                    settle()
                previous_observation = next(item["status"] for item in bridge.healthEvidence["findings"] if item["rule"] == "readme_presence")
                click_health()
                check(bridge.healthEvidence["ignored"] == 1, "real per-repository Ignore control")
                check(any(item["rule"] == "readme_presence" and item["status"] == "IGNORED" and item["observedStatus"] == previous_observation for item in bridge.healthEvidence["findings"]), "ignored advisory retains its actual observation without reporting PASS")
                from repo_manager.repository_service import RepositorySession
                reloaded = RepositorySession()
                check(reloaded.inspect(bridge._selected_target())[0].summary.ignored_count == 1, "Health preferences persist in a fresh session")
                capture(window, "RC-health-ignored")
                click_health()
                check(bridge.healthEvidence["ignored"] == 0, "real Restore check control")
                detail_panel.setProperty("tab", "Overview")
                check(not find(window, "quickRunButton").property("enabled"), "Quick Run explains unavailable launcher")
                target = bridge._selected_target()
                fixtures = []
                for number in (1, 2):
                    child_config = qa_root / f"launcher-{config['output']}-{number}.json"
                    receipt_name = f"receipt-{config['output']}-{number}.json"
                    (qa_root / receipt_name).unlink(missing_ok=True)
                    child_config.write_text(json.dumps({"receipt": receipt_name, "label": f"QA {number}"}), encoding="utf-8")
                    fixtures.append({"name": f"QA {number}", "executable": sys.executable,
                                     "args": ["--rc-launch-receipt", str(child_config)], "cwd": modified["path"]})
                bridge.session.save_launcher(target, fixtures[0])
                bridge._request_detail()
                wait_for(lambda: not bridge._detail_loading)
                settle()
                run_button = find(window, "quickRunButton")
                # This native probe intentionally cannot take owner focus.
                # Keyboard activation is exercised by the QML interaction gate
                # and the separate active-window acceptance pass.
                click(window, run_button)
                wait_for(lambda: (qa_root / f"receipt-{config['output']}-1.json").is_file(), timeout=10)
                receipt = json.loads((qa_root / f"receipt-{config['output']}-1.json").read_text())
                check(receipt["cwd"] == modified["path"] and not receipt["console"], "single Quick Run executes in selected cwd without console")
                bridge.session.save_launcher(target, fixtures[1])
                bridge._request_detail()
                wait_for(lambda: not bridge._detail_loading)
                settle()
                click(window, find(window, "quickRunButton"))
                chooser = find(window, "quickRunMenu")
                check(chooser.property("visible") and chooser.property("count") == 2, "multiple Quick Run launchers open a real chooser")
                content = chooser.property("contentItem")
                click_at(window, content.mapToScene(QPointF(80, 51)))
                wait_for(lambda: (qa_root / f"receipt-{config['output']}-2.json").is_file(), timeout=10)
                receipt = json.loads((qa_root / f"receipt-{config['output']}-2.json").read_text())
                check(receipt["label"] == "QA 2" and receipt["cwd"] == modified["path"] and not receipt["console"], "chooser executes selected launcher with correct cwd and no console")
                detail_panel.setProperty("tab", "Run")
                settle()
                capture(window, "RC-run-tab")
                check(len(bridge.detectedLaunchers) == 2, "Run tab retains both detected custom launchers")
                for value in list(bridge.session.resolve(target).get("custom_launchers", [])):
                    bridge.session.save_launcher(target, value, remove=True)
                bridge._request_detail()
                wait_for(lambda: not bridge._detail_loading)
                detail_panel.setProperty("tab", "Overview")
                bridge.saveCuration("active", True, "RC controlled repositories")
                bridge.setSection("working")
                check(bridge.visibleCount >= 1, "Working on now reflects saved curation")
                capture(window, "RC-working-on-now")
                bridge.setSection("repositories")
                bridge.setQuery("modified-repo")
                check(bridge.visibleCount == 1, "real search filter")
                bridge.setQuery("")
                listing = window.findChild(QObject, "repositoryList")
                listing.setProperty("currentIndex", 0)
                listing.forceActiveFocus()
                before = bridge.selectedProject["projectId"]
                key(window, Qt.Key.Key_Down)
                check(bridge.selectedProject["projectId"] != before, "actual QML keyboard selection")
                key(window, Qt.Key.Key_Home)
                key(window, Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
                menu = window.findChild(QObject, "projectMenu")
                check(menu.property("visible"), "actual QML context menu")
                key(window, Qt.Key.Key_Escape)
                bridge.selectProject(modified["projectId"])
                wait_for(lambda: not bridge._detail_loading)
                click(window, find(window, "projectActionsButton"))
                check(menu.property("visible") and menu.property("width") >= 260, "actual gear click opens readable actions")
                content = menu.property("contentItem")
                point = content.mapToScene(QPointF())
                check(point.x() >= 0 and point.y() >= 0 and point.x()+content.width() <= window.width() and point.y()+content.height() <= window.height(), "actions menu fits the actual window")
                capture(window, "RC-project-actions")
                click_at(window, content.mapToScene(QPointF(100, 48)))
                check(QGuiApplication.clipboard().text() == modified["path"], "actual menu Copy path works")
                key(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
                search = window.findChild(QObject, "repositorySearch")
                # Native shortcuts require an active desktop window. This probe
                # deliberately does not activate or take focus from its owner.
                search.forceActiveFocus()
                check(search.property("activeFocus"), "search field accepts QML focus")
                key(window, Qt.Key.Key_Escape)
                before = float(listing.property("contentY"))
                point = listing.mapToScene(QPointF(listing.width()/2, listing.height()/2))
                event = QWheelEvent(point, point, QPoint(), QPoint(0,-480), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
                QCoreApplication.sendEvent(window, event)
                wait_for(lambda: float(listing.property("contentY")) > before, timeout=3)
                results["wheel_content_y"] = float(listing.property("contentY"))
                check(True, "actual Qt mouse-wheel event scrolled the table")
                listing.setProperty("contentY", 0)
                for name in ("settingsDialog", "helpDialog", "feedbackDialog"):
                    dialog = window.findChild(QObject, name)
                    check(QMetaObject.invokeMethod(dialog, "open"), name + " opened")
                    QCoreApplication.processEvents()
                    check(dialog.property("visible"), name + " visible")
                    if name == "settingsDialog":
                        saved_settings = store.SETTINGS_FILE.read_bytes()
                        for section in ("Integrations", "Ignored projects", "Health", "Appearance", "Scan folders"):
                            click(window, find(window, "settingsTab" + section))
                            check(dialog.property("section") == section, "actual Settings tab " + section)
                            if section == "Integrations":
                                capture(window, "RC-settings-integrations")
                            if section == "Health":
                                click(window, find(window, "globalHealth_readme_presence"))
                                capture(window, "RC-settings-health")
                        path = find(window, "scanRootPath")
                        path.setProperty("text", bridge.settingsData["roots"][0])
                        click(window, find(window, "addScanPath"))
                        check("already included" in dialog.property("error"), "Settings rejects duplicate roots")
                        click(window, find(window, "addCDrive"))
                        check(not dialog.property("error"), "C drive opt-in works without scanning")
                        check(dialog.property("roots")[-1] == "C:\\", "C shortcut uses an absolute drive root")
                        click(window, find(window, "settingsCancel"))
                        check(store.SETTINGS_FILE.read_bytes() == saved_settings, "Cancel preserves real saved roots")
                        QMetaObject.invokeMethod(dialog, "open")
                        settle()
                        check(bridge.settingsData["roots"] == json.loads(saved_settings)["roots"], "Settings reopen restores saved search scope")
                        capture(window, "RC-settingsDialog")
                        click(window, find(window, "settingsSave"))
                        wait_for(lambda: not bridge.scanning)
                        check(bridge.visibleCount >= 20 and not dialog.property("visible"), "Settings save performs real controlled rescan")
                        check(bridge.settingsData["roots"] == json.loads(saved_settings)["roots"], "Settings rescan remains confined to the original controlled folders")
                        continue
                    if name == "feedbackDialog":
                        for category in ("positive", "improvement", "bug", "ui"):
                            click(window, find(window, "feedbackCategory" + category))
                            check(dialog.property("category") == category, "actual Feedback category " + category)
                        window.findChild(QObject, "feedbackSubject").setProperty("text", "Controlled portable RC feedback")
                        window.findChild(QObject, "feedbackMessage").setProperty("text", "Local feedback persistence from the extracted package.")
                        button = window.findChild(QObject, "feedbackSave")
                        click(window, button)
                        check(bridge.feedbackStatus.startswith("Saved locally:"), "feedback saved in isolated application data")
                        click(window, find(window, "feedbackCopy"))
                        check("Local feedback persistence from the extracted package." in QGuiApplication.clipboard().text(), "Feedback copy works")
                        QDesktopServices.setUrlHandler("https", url_capture, "receive")
                        click(window, find(window, "feedbackDraft"))
                        check(results.get("opened_urls", [""])[-1].startswith("https://github.com/IcyShadow5/RepoManager/issues/new?"), "Feedback opens a reviewable issue draft without submission")
                        QDesktopServices.unsetUrlHandler("https")
                    if name == "helpDialog":
                        for topic, phrase in (("Scanning and cancellation", "last complete"),
                                              ("Health and status", "never PASS"),
                                              ("Changes, diff and commits", "without discarding"),
                                              ("Run and Quick Run", "same launch path"),
                                              ("Explorer, VS Code and Terminal", "PowerShell 7 is not required"),
                                              ("Versions and updates", "does not automatically")):
                            dialog.setProperty("topic", topic)
                            settle()
                            check(phrase in find(window, "helpBody").property("text"), "packaged Help renders " + topic)
                        QDesktopServices.setUrlHandler("https", url_capture, "receive")
                        click(window, find(window, "officialReleases"))
                        check(results.get("opened_urls", [""])[-1] == "https://github.com/IcyShadow5/RepoManager/releases", "packaged official release button uses reviewable browser boundary")
                        QDesktopServices.unsetUrlHandler("https")
                    capture(window, "RC-" + name)
                    QMetaObject.invokeMethod(dialog, "close")
                def ui_items():
                    pending = [window.contentItem()]
                    while pending:
                        item = pending.pop()
                        yield item
                        pending.extend(item.childItems())
                def visible_button(text):
                    return next(item for item in ui_items() if item.isVisible()
                                and item.property("text") == text and item.property("contentItem") is not None)
                def git_read(*args):
                    from repo_manager.git_environment import git_environment
                    return subprocess.check_output(["git", "-C", modified["path"], *args],
                                                   env=git_environment(read_only=True),
                                                   creationflags=subprocess.CREATE_NO_WINDOW)
                baseline_head = git_read("rev-parse", "HEAD")
                baseline_index = git_read("diff", "--cached", "--name-only")
                baseline_status = git_read("status", "--porcelain", "-z")
                check(not baseline_index.strip(), "controlled Git fixture begins with empty index")
                bridge.openGit("changes")
                controller = bridge.gitController
                wait_for(lambda: not controller.busy)
                check(bool(controller.files), "packaged Changes shows controlled modified file")
                changed_file = Path(modified["path"]) / controller._state.changes[controller.files[0]["index"]].path
                baseline_content = changed_file.read_bytes()
                click(window, next(item for item in ui_items() if item.isVisible()
                                   and item.property("tip") == "Preview file differences"))
                wait_for(lambda: not controller.busy)
                check(bool(controller.content), "packaged QML diff displays real file content")
                capture(window, "RC-changes-diff")
                def file_checkbox():
                    return next(item for item in ui_items() if item.isVisible()
                                and item.property("checked") is not None
                                and item.property("text") == "" and item.property("indicator") is not None)
                click(window, file_checkbox())
                click(window, visible_button("Stage selected"))
                wait_for(lambda: not controller.busy)
                check(bool(git_read("diff", "--cached", "--name-only").strip()), "packaged Stage mutates the real controlled index")
                click(window, visible_button("Staged"))
                click(window, file_checkbox())
                click(window, visible_button("Unstage selected"))
                wait_for(lambda: not controller.busy and not bridge._detail_loading)
                check(git_read("diff", "--cached", "--name-only") == baseline_index
                      and git_read("rev-parse", "HEAD") == baseline_head
                      and git_read("status", "--porcelain", "-z") == baseline_status
                      and changed_file.read_bytes() == baseline_content, "packaged Unstage restores index without committing or discarding working changes")
                QMetaObject.invokeMethod(find(window, "gitDialog"), "close")
                old_theme = bridge.themeName
                bridge.toggleTheme()
                check(bridge.themeName != old_theme and json.loads(store.SETTINGS_FILE.read_text())["qt_theme"] == bridge.themeName, "theme toggled and persisted")
                capture(window, "RC-theme")
                if bridge.themeName != "light":
                    bridge.toggleTheme()
                capture(window, "RC-light")
                bridge.selectProject(modified["projectId"])
                bridge.scan()
                wait_for(lambda: not bridge.scanning)
                check(bridge.selectedProject["projectId"] == modified["projectId"], "rescan preserves selected repository")
                window.showMaximized()
                wait_for(lambda: window.visibility() == window.Visibility.Maximized, timeout=5)
                check(window.visibility() == window.Visibility.Maximized, "native maximize")
                window.showNormal()
                wait_for(lambda: window.visibility() == window.Visibility.Windowed, timeout=5)
                window.resize(1120,640)
                QCoreApplication.processEvents()
                check(window.width()==1120 and window.height()==640, "native restore and minimum resize")
                capture(window, "RC-minimum")
                settings_dialog = window.findChild(QObject, "settingsDialog")
                QMetaObject.invokeMethod(settings_dialog, "open")
                QCoreApplication.processEvents()
                settings_content = settings_dialog.property("contentItem")
                settings_top = settings_content.mapToScene(QPointF()).y()
                check(settings_top >= 0 and settings_top + settings_content.height() <= window.height(), "Settings remains usable at minimum window size")
                capture(window, "RC-settings-minimum")
                QMetaObject.invokeMethod(settings_dialog, "close")
                window.resize(*config.get("size", [1660,940]))
                if bridge.themeName != "dark":
                    bridge.toggleTheme()
                marker = qa_root / "runner-ready.txt"
                marker.unlink(missing_ok=True)
                bridge.startAgent()
                if bridge.agentInfo["choiceCount"] > 1:
                    chooser = find(window, "agentChooser")
                    wait_for(lambda: chooser.property("visible"))
                    check(not bridge.agentActive, "multiple Agents do not launch before explicit choice")
                    chosen = config.get("agent_choice", "configured")
                    bridge.chooseAgent(chosen)
                    QMetaObject.invokeMethod(chooser, "close")
                    check(bridge.session.settings["selected_agent_id"] == chosen, "Agent selection persisted")
                check(bridge.agentActive, "actual controlled Agent started")
                wait_for(marker.is_file, timeout=5)
                check(marker.read_text().strip() == "controlled runner ready", "actual Agent payload executed and signaled readiness")
                process = next(iter(bridge._agent_processes.values()))
                wait_for(lambda: bool(process._children), timeout=5)
                results["agent_pid"] = process.pid
                results["agent_child_pids"] = list(process._children)
                check(bool(results["agent_child_pids"]), "actual contained Agent child observed")
                check(bridge.agentRun and next(iter(bridge._runs))["cwd"] == bridge.selectedProject["path"], "Agent uses selected repository working directory")
                window.setProperty("extendedColumns", True)
                capture(window, "RC-main-agent-details")
                bridge.stopAgent()
                wait_for(lambda: not bridge.agentActive and not bridge._post_runs and not bridge.scanning)
                check(True, "actual Agent descendants stopped and post-run completed")
                capture(window, "RC-agent-stopped")
                unavailable = next(row for row in bridge._model._rows if row["projectName"] == "unavailable-repo")
                bridge.selectProject(unavailable["projectId"])
                wait_for(lambda: not bridge._detail_loading)
                capture(window, "RC-unavailable-repository")
                if config.get("scan_control"):
                    selected = next(row for row in bridge._model._rows if row["projectName"] == "modified-repo")
                    bridge.selectProject(selected["projectId"])
                    wait_for(lambda: not bridge._detail_loading)
                    previous = scanner.os.scandir

                    def slow_directory(path):
                        if current_control() is not None:
                            # Controlled disk latency makes real discovery
                            # observable without scanning owner/system folders.
                            time.sleep(.12)
                        return previous(path)

                    scanner.os.scandir = slow_directory
                    try:
                        registry = store.REPOS_FILE.read_bytes()
                        stamp = bridge.lastScan
                        click(window, find(window, "scanButton"))
                        wait_for(lambda: bridge.scanning and bridge.scanProgress.get("directories", 0) >= 3)
                        worker = bridge._scan_thread
                        check(not find(window, "scanButton").isEnabled(), "overlapping scan is disabled")
                        capture(window, "RC-scan-active")
                        click(window, find(window, "cancelScanButton"))
                        check(bridge.scanProgress.get("cancelling") or (not bridge.scanning and bridge.statusText == "Scan cancelled; inventory unchanged"), "cancellation is pending or already completed honestly")
                        capture(window, "RC-scan-cancelling" if bridge.scanning else "RC-scan-cancelled")
                        wait_for(lambda: not bridge.scanning)
                        check(not worker.is_alive(), "cancel joins scan worker and metadata pool")
                        check(store.REPOS_FILE.read_bytes() == registry and bridge.lastScan == stamp, "cancel preserves inventory and completed timestamp")
                        click(window, find(window, "scanButton"))
                        wait_for(lambda: not bridge.scanning)
                        check(bridge.statusText.startswith("Scan complete"), "new real scan succeeds after cancellation")
                        click(window, find(window, "scanButton"))
                        wait_for(lambda: bridge.scanning and bridge.scanProgress.get("directories", 0) >= 3)
                        registry = store.REPOS_FILE.read_bytes()
                        worker = bridge._scan_thread
                        check(not window.close(), "native close is refused while scan runs")
                        warning = find(window, "closeScanDialog")
                        wait_for(lambda: warning.property("visible"))
                        capture(window, "RC-close-scan-warning")
                        check(not bridge._close_after_scan, "close does not silently queue exit")
                        click(window, find(window, "keepScanning"))
                        check(bridge.scanning and not warning.property("visible"), "Keep scanning returns to app")
                        window.close()
                        wait_for(lambda: warning.property("visible"))
                        key(window, Qt.Key.Key_Escape)
                        check(bridge.scanning and not warning.property("visible"), "Escape returns to app without cancelling")
                        window.close()
                        wait_for(lambda: warning.property("visible"))
                        click(window, find(window, "cancelScanAndExit"))
                        wait_for(lambda: not bridge.scanning and not window.isVisible())
                        check(not worker.is_alive(), "cancel and exit completes only after worker cleanup")
                        check(store.REPOS_FILE.read_bytes() == registry, "cancel and exit never saves partial inventory")
                        results["scan_cancel_exit"] = "confirmed native close, joined worker, unchanged registry"
                    finally:
                        scanner.os.scandir = previous
            check(not results["warnings"], "no QML/Qt runtime warnings")
        except Exception as exc:
            results["errors"].append(str(exc))
        finally:
            QDesktopServices.unsetUrlHandler("https")
            if bridge.agentActive:
                bridge.stopAgent()
                try: wait_for(lambda: not bridge.agentActive and not bridge._post_runs)
                except Exception as exc: results["errors"].append(str(exc))
            (output / "result.json").write_text(json.dumps(results,indent=2,default=str)+'\n',encoding='utf-8')
            window.close()
            QTimer.singleShot(2000, QGuiApplication.instance().quit)

    class ProbeEngine(BaseEngine):
        def __init__(self):
            super().__init__()
            self.initial = {}
            self.objectCreated.connect(self.loaded)
        def setInitialProperties(self, values):
            self.initial.update(values)
        def load(self, url):
            super().setInitialProperties({**self.initial, "visible":False})
            super().load(url)
        def loaded(self, window, url):
            if window is None:
                results["errors"].append("QML root failed")
                (output / "result.json").write_text(json.dumps(results,indent=2),encoding='utf-8')
                return
            window.setFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
            window.setFlag(Qt.WindowType.WindowStaysOnBottomHint, True)
            window.resize(*config.get("size", [1660,940]))
            window.show()
            window.rc_window_seconds = time.perf_counter() - started
            QTimer.singleShot(1000, lambda: exercise(window))
    run_qt.QQmlApplicationEngine = ProbeEngine
