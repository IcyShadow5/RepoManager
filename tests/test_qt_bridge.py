"""Qt view-model behavior, isolated from owner settings and external processes."""
import os
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from repo_manager.qt_bridge import RepoManagerBridge, project_row
except ImportError:
    RepoManagerBridge = None

from repo_manager import agents, feedback, git_availability, git_operations, health, providers, store
from repo_manager.repository_service import ScanOutcome
from tests.git_repository import init_repository, require_git
from tests.test_repository_service import IsolatedSessionTests


@unittest.skipIf(RepoManagerBridge is None, "Run with the Qt development environment")
class QtBridgeTests(IsolatedSessionTests):
    def test_root_form_validation_does_not_save_or_scan(self):
        before = dict(self.session.settings)
        with mock.patch.object(self.bridge, "scan") as scan:
            result = self.bridge.addScanRoot([], str(self.root))
            self.assertEqual(result, {"roots": [str(self.root)], "error": ""})
            duplicate = self.bridge.addScanRoot(result["roots"], str(self.root))
            self.assertIn("already included", duplicate["error"])
            self.assertEqual(duplicate["roots"], result["roots"])
            scan.assert_not_called()
        self.assertEqual(self.session.settings, before)

    def test_ignored_settings_rows_preserve_status_and_identity(self):
        self.first.update(ignored=True, status="active")
        self.assertEqual(self.bridge.ignoredProjects, [{"id": self.first["project_id"], "name": "alpha", "path": self.first["path"], "status": "active"}])

    def test_unavailable_git_does_not_present_cached_clean_counts_as_current(self):
        self.bridge._git = git_availability.GitAvailability("not_found")
        self.assertEqual(self.bridge.cleanCount, 0)
        self.assertEqual(self.bridge.modifiedCount, 0)
        self.assertEqual(self.bridge.unobservedCount, self.bridge.totalCount)

    def test_broken_repository_never_counts_cached_clean_as_current(self):
        self.first.update(broken=True, dirty=0, status_available=True)
        self.bridge._refresh_rows()
        row = self.bridge.repositoryModel.row(0)
        self.assertTrue(row["broken"])
        self.assertFalse(row["dirtyKnown"])
        self.assertEqual(row["branch"], "—")
        self.assertEqual(self.bridge.cleanCount, 0)
        self.assertEqual(self.bridge.unobservedCount, 1)

    def test_compact_location_keeps_full_authoritative_path(self):
        self.session.settings["roots"] = [str(self.root)]
        self.bridge._refresh_rows()
        row = self.bridge.repositoryModel.row(0)
        self.assertEqual(row["path"], self.first["path"])
        self.assertEqual(row["displayPath"], os.path.join(self.root.name, "alpha"))

    def test_keep_both_confirmation_cannot_follow_reordered_suggestion(self):
        first = {"old_path": self.first["path"], "new_path": self.second["path"]}
        other = {"old_path": "unrelated-old", "new_path": "unrelated-new"}
        self.bridge._moves = [first, other]
        self.bridge.requestKeepBoth(0)
        self.bridge._moves.reverse()
        self.bridge.executeAction()
        self.assertEqual(self.bridge._moves, [other])
        self.assertEqual(self.session.settings["move_suppressions"], [
            {"old": self.first["path"].lower(), "new": self.second["path"].lower()}])

    def test_keep_both_write_failure_preserves_suggestion_and_records(self):
        suggestion = {"old_path": self.first["path"], "new_path": self.second["path"]}
        self.bridge._moves = [suggestion]
        self.bridge.requestKeepBoth(0)
        with mock.patch.object(store, "save_settings", side_effect=OSError("locked")):
            self.bridge.executeAction()
        self.assertEqual(self.bridge._moves, [suggestion])
        self.assertEqual(self.session.settings.get("move_suppressions", []), [])
        self.assertEqual(len(self.session.records), 2)
        self.assertIn("locked", self.bridge.statusText)

    def test_move_confirmation_rechecks_new_activity_before_mutation(self):
        self.bridge._approved_move = {"old_path": self.first["path"], "new_path": self.second["path"]}
        self.bridge._action = "move"
        self.bridge._scanning = True
        with mock.patch.object(self.session, "apply_move") as apply:
            self.bridge.executeAction()
            apply.assert_not_called()
        self.bridge._scanning = False
        self.assertIn("Current activity changed", self.bridge.statusText)

    def test_close_scan_confirmation_does_not_queue_exit(self):
        ready = []
        requested = []
        self.bridge.closeReady.connect(lambda: ready.append(True))
        self.bridge.confirmScanCloseRequested.connect(lambda: requested.append(True))
        self.bridge._scanning = True
        self.assertFalse(self.bridge.prepareClose())
        self.assertFalse(self.bridge._closing)
        self.assertFalse(self.bridge._close_after_scan)
        self.assertEqual(requested, [True])
        self.bridge._accept_scan(ScanOutcome(list(self.session.records), []))
        self.assertEqual(ready, [])
        self.assertTrue(self.bridge.prepareClose())

    def test_return_to_app_during_scan_failure_does_not_exit_or_fake_success(self):
        ready = []
        self.bridge.closeReady.connect(lambda: ready.append(True))
        self.bridge._scanning = True
        self.bridge.prepareClose()
        self.bridge._scan_failed("unavailable folder")
        self.assertEqual(ready, [])
        self.assertIn("Scan failed", self.bridge.statusText)

    def test_agent_duplicate_and_close_confirmation_preserve_running_target(self):
        run = {"run_id": "one", "agent_id": "configured", "cwd": self.first["path"],
               "target": {"kind": "repository", "path": self.first["path"], "project_id": self.first["project_id"]},
               "process_state": agents.RUNNING, "verification": agents.NOT_RUN}
        self.bridge._runs = [run]
        requested = []
        self.bridge.confirmAgentCloseRequested.connect(lambda: requested.append(True))
        self.bridge.startAgent()
        self.assertEqual(len(self.bridge._runs), 1)
        self.assertIn("already running", self.bridge.statusText)
        self.assertFalse(self.bridge.prepareClose())
        self.assertEqual(requested, [True])
        self.assertFalse(self.bridge._closing)

    def test_unknown_agent_exit_keeps_application_open(self):
        run = {"run_id": "one", "process_state": agents.RUNNING}
        self.bridge._runs = [run]
        self.bridge._agent_processes = {"one": mock.Mock(terminate=mock.Mock(side_effect=OSError("access denied")))}
        self.bridge.stopAgentsAndClose()
        self.assertEqual(run["process_state"], agents.UNKNOWN_PROCESS)
        self.assertFalse(self.bridge._close_after_agents)
        self.assertFalse(self.bridge.prepareClose())

    def test_post_run_close_waits_for_observation_and_reports_its_limits(self):
        run = {"run_id": "one", "process_state": agents.TERMINATED, "verification": agents.NOT_RUN}
        self.bridge._runs = [run]
        self.bridge._post_runs = {"one"}
        ready = []
        self.bridge.closeReady.connect(lambda: ready.append(True))
        self.assertFalse(self.bridge.prepareClose())
        self.bridge._accept_post_run({**run, "verification": agents.UNKNOWN})
        self.assertEqual(ready, [True])
        self.assertIn("repository observation only", self.bridge.statusText)
        self.assertEqual(run["verification"], agents.UNKNOWN)

    def test_feedback_is_user_requested_and_browser_failure_preserves_draft(self):
        with mock.patch.object(feedback, "save_report") as saved, mock.patch("repo_manager.qt_bridge.QDesktopServices.openUrl", return_value=False):
            saved.assert_not_called()
            self.assertFalse(self.bridge.sendFeedback("bug", "UI bug", "Details", False, "issue"))
            self.assertIn("save or copy", self.bridge.feedbackStatus)
        self.assertTrue(self.bridge.sendFeedback("positive", "Works", "Good", False, "copy"))
        self.assertIn("Feedback copied", self.bridge.feedbackStatus)

    def test_approved_ignore_does_not_follow_new_selection(self):
        self.bridge.requestAction("ignore")
        self.bridge.selectProject(self.second["project_id"])
        self.bridge.executeAction()
        self.assertTrue(self.session.resolve(self.first_target)["ignored"])
        self.assertFalse(self.session.resolve(self.second_target).get("ignored", False))

    def test_provider_result_cannot_cross_selection_or_changed_remote(self):
        self.first["remote"] = "https://github.com/example/alpha.git"
        result = providers.ProviderObservation("github", providers.AVAILABLE, providers.CURRENT, "2026-10-04", visibility="public")
        self.bridge._provider_generation = 3
        self.bridge.selectProject(self.second["project_id"])
        self.bridge._accept_provider(self.first_target, self.first["remote"], 3, result)
        self.assertEqual(self.bridge.providerData["online"], {})
        self.bridge.selectProject(self.first["project_id"])
        self.bridge._provider_generation = 5
        self.first["remote"] = "https://github.com/example/changed.git"
        self.bridge._accept_provider(self.first_target, "https://github.com/example/alpha.git", 5, result)
        self.assertEqual(self.bridge.providerData["online"], {})

    def test_column_bounds_and_theme_persist_without_overwriting_settings(self):
        self.session.settings["owner_field"] = "preserved"
        self.bridge.resizeColumn("name", 1, True)
        self.assertEqual(self.bridge.columnWidths["name"], 130)
        self.bridge.resizeColumn("name", 9000, True)
        self.assertEqual(self.bridge.columnWidths["name"], 420)
        previous = self.bridge.themeName
        self.bridge.toggleTheme()
        self.assertNotEqual(previous, self.bridge.themeName)
        self.assertEqual(self.session.settings["owner_field"], "preserved")

    def test_recovery_gate_explains_block_and_missing_retry(self):
        self.session.report = {"status": "unavailable", "write_blocked": True}
        self.assertIn("Scanning and registry writes are paused", self.bridge.recoveryText)
        with mock.patch.object(store, "read_registry", return_value=([], {"status": "fresh"})):
            self.bridge.retryRegistry()
        self.assertTrue(self.bridge.registryBlocked)
        self.assertIn("remains blocked", self.bridge.statusText)

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.first, self.first_target = self.seed("alpha", dirty=0, status_available=True)
        self.second, self.second_target = self.seed("beta", dirty=2, status_available=True)
        probe = mock.patch.object(git_availability, "check_git",
                                  return_value=git_availability.GitAvailability("available"))
        probe.start()
        self.addCleanup(probe.stop)
        details = mock.patch.object(RepoManagerBridge, "_request_detail")
        details.start()
        self.addCleanup(details.stop)
        self.bridge = RepoManagerBridge(self.session, auto_scan=False)
        self.addCleanup(self.bridge.prepareClose)

    def wait_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(predicate(), "Qt worker did not finish within 5 seconds")

    def test_model_roles_and_counts_are_real(self):
        self.assertEqual(self.bridge.visibleCount, 2)
        self.assertEqual((self.bridge.totalCount, self.bridge.cleanCount, self.bridge.modifiedCount), (2, 1, 1))
        model = self.bridge.repositoryModel
        role = next(role for role, name in model.roleNames().items() if name == b"projectName")
        self.assertEqual(model.data(model.index(0, 0), role), "alpha")

    def test_search_sort_and_keyboard_selection(self):
        self.bridge.moveSelection(1)
        self.assertEqual(self.bridge.selectedProject["projectName"], "beta")
        self.bridge.sortBy("name")
        self.bridge.sortBy("name")
        self.assertTrue(self.bridge.sortDescending)
        self.assertEqual(self.bridge.repositoryModel.row(0)["projectName"], "beta")
        self.bridge.setQuery("alpha")
        self.assertEqual(self.bridge.visibleCount, 1)
        self.assertEqual(self.bridge.selectedProject["projectName"], "alpha")

    def test_note_autosave_is_flushed_to_previous_target_on_selection(self):
        self.bridge.editNotes("alpha note")
        self.bridge.selectProject(self.second["project_id"])
        self.assertEqual(self.session.load_note(self.first_target), "alpha note")
        self.assertEqual(self.bridge.notesText, "")
        self.bridge.editNotes("beta note")
        self.assertTrue(self.bridge.saveNotes())
        self.assertEqual(self.session.load_note(self.second_target), "beta note")

    def test_note_save_failure_blocks_selection_filter_navigation_and_close(self):
        self.bridge.editNotes("must survive")
        with mock.patch.object(store, "save_note", side_effect=OSError("disk full")):
            self.bridge.selectProject(self.second["project_id"])
            self.bridge.setQuery("beta")
            self.bridge.setSection("working")
            self.assertFalse(self.bridge.prepareClose())
        self.assertEqual(self.bridge.selectedProject["projectName"], "alpha")
        self.assertEqual(self.bridge.query, "")
        self.assertEqual(self.bridge.section, "repositories")
        self.assertEqual(self.bridge.notesText, "must survive")
        self.assertTrue(self.bridge.notesDirty)
        self.assertIn("disk full", self.bridge.statusText)

    def test_curation_and_working_toggle_persist(self):
        self.bridge.saveCuration("paused", False, "next step")
        self.bridge.toggleWorking()
        self.assertEqual(self.bridge.selectedProject["projectStatus"], "active")
        self.assertTrue(self.bridge.selectedProject["pinned"])
        self.assertEqual(self.bridge.workingProject["projectName"], "alpha")
        self.bridge.toggleWorking()
        self.assertEqual(self.bridge.selectedProject["projectStatus"], "paused")
        self.assertTrue(self.bridge.selectedProject["pinned"])
        self.assertEqual(self.bridge.workingProject, {})

    def test_missing_git_blocks_scan_then_recheck_repeats_probe(self):
        with mock.patch.object(git_availability, "check_git", return_value=git_availability.GitAvailability("not_found")):
            self.bridge.scan()
        self.assertEqual(self.bridge.gitState, "not_found")
        self.assertFalse(self.bridge.scanning)
        with mock.patch.object(self.bridge, "scan") as scan, \
                mock.patch.object(git_availability, "check_git", return_value=git_availability.GitAvailability("available")) as probe:
            self.bridge.checkAgain()
        probe.assert_called_once()
        scan.assert_called_once()
        self.assertEqual(self.bridge.gitState, "available")

    def test_launch_failure_is_distinct_from_missing(self):
        with mock.patch.object(git_availability, "check_git", return_value=git_availability.GitAvailability("launch_failed", "access denied")):
            self.bridge.checkAgain()
        self.assertEqual(self.bridge.gitState, "launch_failed")
        self.assertFalse(self.bridge.scanning)

    def test_repository_error_does_not_change_git_prerequisite_state(self):
        require_git()
        with self.assertRaisesRegex(git_operations.GitError, "not a git repository"):
            git_operations.Repository(self.first["path"]).run("status")
        with mock.patch.object(self.session, "scan", side_effect=git_operations.GitError("repository error")):
            self.bridge.scan()
            self.wait_until(lambda: not self.bridge.scanning)
        self.assertEqual(self.bridge.gitState, "available")
        self.assertIn("repository error", self.bridge.statusText)

    def test_async_scan_uses_existing_scanner_and_updates_model(self):
        root = self.root / "scan"
        root.mkdir()
        init_repository(root / "discovered")
        self.session.save_settings([str(root)], 4, "opencode", "")
        self.bridge.scan()
        self.wait_until(lambda: not self.bridge.scanning)
        self.assertIn("discovered", [row["projectName"] for row in self.bridge.repositoryModel._rows])
        self.assertEqual(self.bridge.statusText, "Scan complete")

    def test_second_scan_is_queued_instead_of_lost(self):
        self.bridge._scanning = True
        self.bridge.scan()
        self.assertTrue(self.bridge._pending_scan)
        with mock.patch.object(self.bridge, "scan") as scan:
            self.bridge._accept_scan(ScanOutcome(self.session.records, []))
            self.app.processEvents()
        scan.assert_called_once()
        self.assertFalse(self.bridge._pending_scan)

    def test_git_disappearance_drops_scan_results_without_saving(self):
        self.bridge._scanning = True
        with mock.patch.object(git_availability, "check_git", return_value=git_availability.GitAvailability("not_found")), \
                mock.patch.object(self.session, "accept_scan") as save:
            self.bridge._accept_scan(ScanOutcome([], []))
        save.assert_not_called()
        self.assertEqual(self.bridge.gitState, "not_found")
        self.assertIn("not saved", self.bridge.statusText)

    def test_closed_bridge_ignores_late_scan_and_detail_results(self):
        self.bridge.prepareClose()
        with mock.patch.object(self.session, "accept_scan") as save:
            self.bridge._accept_scan(ScanOutcome([], []))
            self.bridge._accept_detail(self.first_target, self.bridge._detail_gen, None, [])
        save.assert_not_called()
        self.assertEqual(self.bridge.healthEvidence, {})

    def test_stale_detail_result_cannot_replace_new_selection(self):
        self.bridge._detail_gen = 3
        self.bridge._accept_detail(self.first_target, 2, None, [{"label": "stale"}])
        self.assertEqual(self.bridge.detectedLaunchers, [])

    def test_health_result_exposes_real_findings_and_uncertainty(self):
        result = health.evaluate_repository(self.first["path"], self.first)
        self.bridge._detail_gen = 3
        self.bridge._accept_detail(self.first_target, 3, result, [])
        evidence = self.bridge.healthEvidence
        self.assertEqual(evidence["status"], result.status)
        self.assertEqual(evidence["count"], len(result.findings))
        self.assertEqual(evidence["unknown"], result.summary.unknown_count)

    def test_install_opens_only_official_destination(self):
        with mock.patch("repo_manager.qt_bridge.QDesktopServices.openUrl", return_value=True) as opened:
            self.bridge.openGitInstall()
        self.assertEqual(opened.call_args.args[0].toString(), "https://git-scm.com/install/windows")

    def test_browser_failure_is_visible(self):
        with mock.patch("repo_manager.qt_bridge.QDesktopServices.openUrl", return_value=False):
            self.bridge.openGitInstall()
        self.assertIn("browser could not be opened", self.bridge.statusText)

    def test_official_releases_is_explicit_browser_action_and_reports_failure(self):
        with mock.patch("repo_manager.qt_bridge.QDesktopServices.openUrl", return_value=True) as opened:
            self.bridge.openOfficialReleases()
        self.assertEqual(opened.call_args.args[0].toString(), "https://github.com/IcyShadow5/RepoManager/releases")
        with mock.patch("repo_manager.qt_bridge.QDesktopServices.openUrl", return_value=False):
            self.bridge.openOfficialReleases()
        self.assertIn("official release page could not be opened", self.bridge.statusText)

    def test_remote_userinfo_is_redacted_and_invalid_dirty_remains_unknown(self):
        row = project_row({**self.first, "remote": "https://user:secret@example.invalid/repo?token=secret", "dirty": True})
        self.assertEqual(row["remote"], "https://example.invalid/repo")
        self.assertFalse(row["dirtyKnown"])


if __name__ == "__main__":
    unittest.main()
