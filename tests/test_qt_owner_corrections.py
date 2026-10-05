"""Quick Run approval binding and Health preference bridge regressions."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from unittest import mock

from repo_manager import git_availability, health, health_preferences, project_actions
from tests.test_repository_service import IsolatedSessionTests

try:
    from PySide6.QtWidgets import QApplication
    from repo_manager.qt_bridge import RepoManagerBridge
except ImportError:
    RepoManagerBridge = None


@unittest.skipIf(RepoManagerBridge is None, "Qt development dependency required")
class OwnerCorrectionBridgeTests(IsolatedSessionTests):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.record, self.target = self.seed()
        for patch in (mock.patch.object(RepoManagerBridge, "_request_detail"),
                      mock.patch.object(git_availability, "check_git", return_value=git_availability.GitAvailability("available"))):
            patch.start()
            self.addCleanup(patch.stop)
        self.bridge = RepoManagerBridge(self.session, auto_scan=False)
        self.bridge._command_target = self.target
        self.addCleanup(self.bridge.prepareClose)
        self.one = {"label": "One", "healthy": True, "kind": "custom"}
        self.two = {"label": "Two", "healthy": True, "kind": "custom"}

    def test_zero_launchers_and_unhealthy_launchers_do_not_execute(self):
        for commands in ([], [{**self.one, "healthy": False}]):
            self.bridge._commands = commands
            with mock.patch.object(self.session, "run_launcher") as run:
                self.assertEqual(self.bridge.quickRunCount, 0)
                self.bridge.requestQuickRun()
                run.assert_not_called()

    def test_single_healthy_launcher_uses_existing_safe_execution(self):
        self.bridge._commands = [{**self.two, "healthy": False}, self.one]
        with mock.patch.object(self.session, "run_launcher") as run:
            self.bridge.requestQuickRun()
            run.assert_called_once_with(self.target, self.one)
        self.assertEqual(self.bridge.statusText, "Launcher started")

    def test_multiple_launchers_require_choice(self):
        self.bridge._commands = [self.one, self.two]
        opened = []
        self.bridge.quickRunChooserRequested.connect(lambda: opened.append(True))
        with mock.patch.object(self.session, "run_launcher") as run:
            self.bridge.requestQuickRun()
            run.assert_not_called()
            self.assertEqual(len(opened), 1)
            self.assertEqual(len(self.bridge.quickRunChoices), 2)
            self.bridge.runQuickLauncher(1)
            run.assert_called_once_with(self.target, self.two)

    def test_choice_cannot_follow_selection_change(self):
        self.bridge._commands = [self.one, self.two]
        self.bridge.requestQuickRun()
        other, target = self.seed("other")
        self.bridge._selected_id = other["project_id"]
        self.bridge._selected = other
        self.bridge._command_target = target
        with mock.patch.object(self.session, "run_launcher") as run:
            self.bridge.runQuickLauncher(0)
            run.assert_not_called()
        self.assertIn("selection changed", self.bridge.statusText)

    def test_changed_candidate_and_launch_failure_are_reported(self):
        self.bridge._commands = [self.one, self.two]
        self.bridge.requestQuickRun()
        self.bridge._commands = [self.two]
        with mock.patch.object(self.session, "run_launcher") as run:
            self.bridge.runQuickLauncher(0)
            run.assert_not_called()
        self.assertIn("Launcher changed", self.bridge.statusText)
        with mock.patch.object(self.session, "run_launcher", side_effect=OSError("cannot execute")):
            self.bridge.requestQuickRun()
        self.assertIn("Launch failed: cannot execute", self.bridge.statusText)

    def test_closing_and_loading_never_start_quick_run(self):
        self.bridge._commands = [self.one]
        for attr in ("_closing", "_detail_loading"):
            setattr(self.bridge, attr, True)
            with mock.patch.object(self.session, "run_launcher") as run:
                self.bridge.requestQuickRun()
                run.assert_not_called()
            setattr(self.bridge, attr, False)

    def test_detail_acceptance_reapplies_current_suppression(self):
        item = health.Finding("readme_presence", rule="readme_presence", status=health.WARN, importance=health.INFORMATIONAL)
        result = health.HealthResult(health.PASS, (item,), "now")
        self.session.settings[health_preferences.SETTINGS_KEY] = {"global": [item.rule]}
        self.bridge._accept_detail(self.target, self.bridge._detail_gen, result, [])
        self.assertEqual(self.bridge.healthEvidence["ignored"], 1)
        self.assertEqual(self.bridge.healthEvidence["findings"][0]["status"], "IGNORED")

    def test_preference_write_failure_is_visible_and_preserves_state(self):
        with mock.patch.object(self.session, "set_health_ignored", side_effect=OSError("locked")):
            self.assertFalse(self.bridge.setHealthIgnored("readme_presence", True))
        self.assertIn("not saved: locked", self.bridge.statusText)
