"""Qt scan lifecycle and explicit Agent choice on isolated application data."""
import copy
import os
import sys
import threading
import time
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from repo_manager import agents, git_availability, store
from repo_manager.qt_bridge import RepoManagerBridge
from repo_manager.repository_service import RepositorySession, ScanOutcome
from repo_manager.scan_control import ScanControl, ScanCancelled
from tests.test_repository_service import IsolatedSessionTests


class QtScanAgentTests(IsolatedSessionTests):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.record, self.target = self.seed()
        probe = mock.patch.object(git_availability, "check_git", return_value=git_availability.GitAvailability("available"))
        probe.start()
        self.addCleanup(probe.stop)
        details = mock.patch.object(RepoManagerBridge, "_request_detail")
        details.start()
        self.addCleanup(details.stop)
        self.bridge = RepoManagerBridge(self.session, auto_scan=False)
        self.addCleanup(self.cleanup_bridge)

    def cleanup_bridge(self):
        self.bridge.cancelScan()
        self.wait_until(lambda: not self.bridge.scanning)
        self.bridge._agent_timer.stop()
        self.bridge._closing = True

    def wait_until(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(predicate())

    def slow_scan(self, control):
        control.update(path=str(self.root), directories=1)
        control.cancelled.wait(3)
        control.check()
        return ScanOutcome([], [])

    def test_cancel_keeps_ui_responsive_and_allows_next_scan(self):
        with mock.patch.object(self.session, "scan", side_effect=self.slow_scan), mock.patch.object(self.session, "accept_scan") as save:
            self.bridge.scan()
            thread = self.bridge._scan_thread
            self.assertTrue(self.bridge.scanning)
            self.bridge.setQuery("alpha")
            self.assertEqual(self.bridge.query, "alpha")
            self.bridge.scan()
            self.assertIs(self.bridge._scan_thread, thread)
            self.bridge.cancelScan()
            self.assertTrue(self.bridge.scanProgress["cancelling"])
            self.wait_until(lambda: not self.bridge.scanning)
            save.assert_not_called()
            self.assertFalse(thread.is_alive())
            self.assertFalse(self.bridge._pending_scan)
        with mock.patch.object(self.session, "scan", return_value=ScanOutcome(self.session.records, [])):
            self.bridge.scan()
            self.wait_until(lambda: not self.bridge.scanning)
        self.assertEqual(self.bridge.statusText, "Scan complete")

    def test_close_warns_without_queuing_until_explicit_cancel_exit(self):
        warning, ready = [], []
        self.bridge.confirmScanCloseRequested.connect(lambda: warning.append(True))
        self.bridge.closeReady.connect(lambda: ready.append(True))
        with mock.patch.object(self.session, "scan", side_effect=self.slow_scan), mock.patch.object(self.session, "accept_scan") as save:
            self.bridge.scan()
            thread = self.bridge._scan_thread
            self.assertFalse(self.bridge.prepareClose())
            self.assertEqual(warning, [True])
            self.assertFalse(self.bridge._close_after_scan)
            self.assertEqual(ready, [])
            self.bridge.cancelScanAndClose()
            self.wait_until(lambda: bool(ready))
            self.assertFalse(thread.is_alive())
            save.assert_not_called()
            self.assertTrue(self.bridge.prepareClose())

    def test_cancel_after_result_is_queued_still_discards_it(self):
        self.bridge._scanning = True
        self.bridge._scan_control = ScanControl()
        self.bridge.cancelScan()
        with mock.patch.object(self.session, "accept_scan") as save:
            self.bridge._accept_scan(ScanOutcome([], []))
        save.assert_not_called()
        self.assertEqual(len(self.session.records), 1)
        self.assertIn("inventory unchanged", self.bridge.statusText)

    def catalog(self, count):
        return [{**agents.new_agent("custom:" + str(i), "Agent " + str(i), sys.executable),
                 "availability": agents.AVAILABLE, "resolved": sys.executable} for i in range(count)]

    def test_zero_agent_has_explanation_and_does_not_launch(self):
        with mock.patch.object(agents, "agent_catalog", return_value=[]), mock.patch.object(agents, "start_run") as start:
            self.bridge.startAgent()
        start.assert_not_called()
        self.assertIn("No available Agent", self.bridge.statusText)

    def test_one_agent_starts_existing_launch_path_in_repository(self):
        process = mock.Mock(pid=12, poll=mock.Mock(return_value=None))
        with mock.patch.object(agents, "agent_catalog", return_value=self.catalog(1)), mock.patch("repo_manager.processes.spawn_agent", return_value=process), mock.patch.object(agents, "start_run", wraps=agents.start_run) as start:
            self.bridge.startAgent()
        self.assertEqual(start.call_args.kwargs["target"]["path"], self.record["path"])
        self.assertEqual(start.call_args.args[0]["argv"][0], sys.executable)
        self.assertTrue(self.bridge.agentActive)
        process.terminate.side_effect = lambda: setattr(process.poll, "return_value", -15)
        self.bridge.stopAgent()
        process.terminate.assert_called_once()
        self.assertFalse(self.bridge.agentActive)

    def test_multiple_requires_explicit_choice_and_persists_selection(self):
        requested = []
        self.bridge.agentChooserRequested.connect(lambda: requested.append(True))
        with mock.patch.object(agents, "agent_catalog", return_value=self.catalog(2)), mock.patch.object(agents, "start_run", return_value=mock.Mock(pid=13)) as start:
            self.bridge.startAgent()
            start.assert_not_called()
            self.assertEqual(requested, [True])
            self.bridge.chooseAgent("custom:1")
            self.assertEqual(start.call_args.kwargs["agent"]["agent_id"], "custom:1")
            self.assertEqual(self.bridge.agentInfo["agentName"], "Agent 1")
            self.assertEqual(RepositorySession().settings["selected_agent_id"], "custom:1")

    def test_changed_repository_rejects_menu_selection(self):
        with mock.patch.object(agents, "agent_catalog", return_value=self.catalog(2)), mock.patch.object(agents, "start_run") as start:
            self.bridge.startAgent()
            self.record["path"] = str(self.root / "changed")
            self.bridge.chooseAgent("custom:1")
        start.assert_not_called()
        self.assertIn("target changed", self.bridge.statusText)

    def test_disappeared_executable_or_failed_persistence_does_not_launch(self):
        for error in (ValueError("Agent unavailable"), OSError("settings locked")):
            with self.subTest(error=error), mock.patch.object(agents, "agent_catalog", return_value=self.catalog(2)), mock.patch.object(self.session, "select_agent", side_effect=error), mock.patch.object(agents, "start_run") as start:
                self.bridge.startAgent()
                self.bridge.chooseAgent("custom:1")
                start.assert_not_called()
                self.assertIn(str(error), self.bridge.statusText)

    def test_invalid_targets_settings_are_atomic(self):
        before = copy.deepcopy(self.session.settings)
        with self.assertRaises(ValueError):
            self.session.save_settings([str(self.root)], 3, "opencode", "", [], [{"display_name": "invalid"}])
        self.assertEqual(self.session.settings, before)

    def test_malformed_saved_targets_have_explicit_ui_error(self):
        self.session.settings["agent_targets"] = "invalid"
        self.assertEqual(self.bridge.settingsData["agentTargets"], [])
        self.assertTrue(self.bridge.settingsData["agentConfigurationError"])
