"""Qt orchestration reuses real temporary Git state and existing approvals."""
import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from repo_manager.qt_git import GitController
except ImportError:
    GitController = None

from repo_manager import git_operations as git, projects
from tests.git_repository import init_repository, git as fixture_git
from tests.test_repository_service import IsolatedSessionTests


@unittest.skipIf(GitController is None, "Qt environment required")
class QtGitTests(IsolatedSessionTests):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.folder = init_repository(self.root / "git")
        self.record = {"name": "git", "path": str(self.folder), "status": "idea"}
        projects.ensure_project_id(self.record)
        self.session.records = [self.record]
        self.controller = GitController(self.session)
        self.outcomes = []
        self.controller.finished.connect(lambda outcome, text, mutation: self.outcomes.append((outcome, text, mutation)))

    def wait(self):
        deadline = time.monotonic() + 5
        while self.controller.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(self.controller.busy, "Git dialog worker did not finish")

    def open_changes(self):
        (self.folder / "new space ü.txt").write_text("literal contents\n", encoding="utf-8")
        self.assertTrue(self.controller.open("changes", self.record))
        self.wait()
        self.assertEqual(len(self.controller.files), 1)

    def test_real_diff_stage_and_unstage_preserve_working_file(self):
        self.open_changes()
        self.controller.previewFile(0)
        self.wait()
        self.assertIn("literal contents", self.controller.content)
        self.controller.toggleFile(0)
        self.controller.stageSelected(False)
        self.wait()
        self.controller.setView("staged")
        self.assertEqual(len(self.controller.files), 1)
        self.controller.toggleFile(0)
        self.controller.stageSelected(True)
        self.wait()
        self.assertEqual(self.controller.files, [])
        self.assertEqual((self.folder / "new space ü.txt").read_text(encoding="utf-8"), "literal contents\n")
        self.assertEqual(fixture_git(self.folder, "diff", "--cached", "--name-only"), "")

    def test_lf_diff_external_refresh_repository_switch_history_and_remotes(self):
        (self.folder / "src").mkdir()
        path = self.folder / "src/app.py"
        path.write_bytes(b'print("before")\r\n')
        fixture_git(self.folder, "config", "core.autocrlf", "true")
        fixture_git(self.folder, "config", "core.safecrlf", "warn")
        fixture_git(self.folder, "add", ".")
        fixture_git(self.folder, "commit", "-qm", "initial fixture")
        path.write_bytes(b'print("after!")\n')
        self.controller.open("changes", self.record)
        self.wait()
        self.controller.previewFile(0)
        self.wait()
        self.assertIn('+print("after!")', self.controller.content)
        path.write_bytes(b'print("external")\n')
        self.controller.refresh()
        self.wait()
        self.controller.previewFile(0)
        self.wait()
        self.assertIn('+print("external")', self.controller.content)
        other = init_repository(self.root / "other")
        other_record = {"path": str(other), "name": "other", "status": "idea"}
        projects.ensure_project_id(other_record)
        self.session.records.append(other_record)
        self.controller.open("changes", other_record)
        self.wait()
        self.assertEqual(self.controller.files, [])
        self.controller.open("changes", self.record)
        self.wait()
        self.controller.previewFile(0)
        self.wait()
        self.assertIn('+print("external")', self.controller.content)
        self.controller.open("history", self.record)
        self.wait()
        self.assertIn("initial fixture", self.controller.content)
        self.controller.open("remotes", self.record)
        self.wait()
        self.assertIn("Upstream: (none)", self.controller.content)
        self.assertEqual(path.read_bytes(), b'print("external")\n')

    def test_commit_requires_explicit_confirmation_and_retains_partial_result(self):
        self.open_changes()
        with mock.patch.object(git.Repository, "commit", return_value=git.Result(git.PARTIAL, "hook rejected; index retained")) as commit:
            self.controller.previewCommit("reviewed local message", True)
            commit.assert_not_called()
            self.assertIn("Stage all", self.controller.confirmationText)
            self.controller.executeConfirmed()
            self.wait()
            commit.assert_called_once()
        self.assertTrue(any(outcome == git.PARTIAL and mutation for outcome, _, mutation in self.outcomes))
        self.assertIn("index retained", self.controller.statusText)

    def test_cancelled_confirmation_cannot_commit(self):
        self.open_changes()
        with mock.patch.object(git.Repository, "commit") as commit:
            self.controller.previewCommit("message", True)
            self.controller.cancelConfirmation()
            self.controller.executeConfirmed()
            commit.assert_not_called()

    def test_reassociated_target_cannot_receive_approved_mutation(self):
        self.open_changes()
        self.controller.previewCommit("message", True)
        self.record["path"] = str(self.root / "different")
        with mock.patch.object(git.Repository, "commit") as commit:
            self.controller.executeConfirmed()
            self.wait()
            commit.assert_not_called()
        self.assertEqual(self.outcomes[-1][0], git.CANCELLED)

    def test_actual_ordinary_git_error_is_reported(self):
        self.controller.open("history", self.record)
        self.wait()
        self.controller.commitDetails("a" * 40)
        self.wait()
        self.assertTrue(self.outcomes)
        self.assertEqual(self.outcomes[-1][0], git.FAILED)
        self.assertNotIn("not installed", self.controller.statusText)

    def test_network_input_change_invalidates_approval(self):
        fixture_git(self.folder, "remote", "add", "origin", "https://example.invalid/owner/repo.git")
        self.controller.open("fetch", self.record)
        self.wait()
        self.controller.previewNetwork()
        self.wait()
        self.assertTrue(self.controller.hasNetworkPreview)
        self.controller.setNetwork("other", "", False)
        self.assertFalse(self.controller.hasNetworkPreview)
        with mock.patch.object(git.Repository, "network") as network:
            self.controller.confirmNetwork()
            self.controller.executeConfirmed()
            network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
