"""LEGACY / PARITY REFERENCE — NOT CURRENT UI PROOF."""
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from repo_manager import git_dialogs, git_operations, main, project_actions, theme
from tests.git_repository import create_repository, create_file, git
from tests.legacy.test_gui_scaling import (_StoreIsolationMixin, _build_real_app,
                                    _pump_until, TK_AVAILABLE)


@unittest.skipUnless(TK_AVAILABLE, "Tk unavailable")
class EverydayGitGuiTests(_StoreIsolationMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = create_repository(Path(self.tmp.name) / "repo")
        self.app = _build_real_app(2)
        self.addCleanup(self.app.destroy)
        self.project = self.app.projects[0]
        self.project.update(project_id="real-project", path=str(self.path), remote=None,
                            branch="main", staged=0, unstaged=0, untracked=0)
        self.app._populate_trees()
        self.app.tree.selection_set("real-project")
        self.app._set_active("main")
        self.app._show_detail("real-project")
        self.app.update()

    def test_changes_dialog_stage_unstage_and_local_commit(self):
        create_file(self.path, "new.txt", "content")
        view = self.app.git_changes()
        self.addCleanup(view.window.destroy)
        self.assertTrue(_pump_until(self.app, lambda: view.state is not None))
        self.assertEqual(view.mode.get(), "staged")
        view.files.selection_set("0")
        self.app.update()
        self.assertTrue(_pump_until(self.app, lambda: not view.busy))
        with mock.patch.object(self.app, "_refresh_metadata_async"):
            view.stage(False)
            self.assertTrue(_pump_until(self.app, lambda: not view.busy and view.state.changes[0].staged, timeout=10))
            view.view.set("staged")
            view.populate()
            view.files.selection_set("0")
            self.app.update()
            self.assertTrue(_pump_until(self.app, lambda: not view.busy))
            view.message.insert(0, "gui local")
            with mock.patch.object(git_dialogs.messagebox, "askyesno", return_value=True):
                view.commit()
            self.assertTrue(_pump_until(self.app, lambda: not view.busy and not view.state.changes, timeout=10))
        self.assertEqual(git(self.path, "log", "-1", "--format=%s"), "gui local")
        self.assertEqual(git(self.path, "remote"), "")

    def test_menu_target_survives_selection_change_and_blocks_reassociation(self):
        self.app._build_context_menu()
        # Copy > Branch targets the captured project, not a newly selected row.
        copy_menu = self.app.nametowidget(self.app._ctx_menu.entrycget(14, "menu"))
        other = self.app.projects[1]
        self.app.tree.selection_set(main.project_row_id(other))
        self.app.update()
        copy_menu.invoke(1)
        self.assertEqual(self.app.clipboard_get(), "main")
        self.project["path"] = str(self.path / "replacement")
        copy_menu.invoke(1)
        self.assertIn("association changed", self.app.status_var.get())

    def test_keyboard_menu_bindings_and_checked_status(self):
        self.app._bind_shortcuts()
        self.assertTrue(self.app.tree.bind("<Shift-F10>"))
        self.assertTrue(self.app.now_tree.bind("<Menu>"))
        self.app._build_context_menu()
        project_menu = self.app.nametowidget(self.app._ctx_menu.entrycget(13, "menu"))
        status_menu = self.app.nametowidget(project_menu.entrycget(0, "menu"))
        self.assertEqual(status_menu.type(0), "radiobutton")
        self.assertEqual(status_menu._status_var.get(), self.project["status"])
        self.assertEqual(self.app._ctx_menu.entrycget(9, "state"), "disabled")
        self.assertIn("remote", self.app._ctx_menu.entrycget(9, "accelerator"))

    def test_closed_dialog_discards_late_worker_response(self):
        from threading import Event
        entered, release = Event(), Event()
        original = git_operations.Repository.state
        def delayed(service):
            entered.set()
            release.wait(5)
            return original(service)
        with mock.patch.object(git_operations.Repository, "state", delayed):
            view = self.app.git_changes()
            self.assertTrue(entered.wait(2))
            view.window.destroy()
            release.set()
            self.assertTrue(_pump_until(self.app, lambda: not self.app.__dict__.get("_git_workers")))

    def test_all_git_viewers_construct_in_both_themes(self):
        for name in ("light", "dark"):
            self.app.pal = theme.apply(self.app, name)
            history = self.app.git_history()
            remotes = self.app.git_remotes()
            network = self.app.git_push()
            self.assertTrue(_pump_until(self.app, lambda: not any(view.busy for view in (history, remotes, network)), timeout=10))
            self.assertIn("initial", history.content.get("1.0", "end"))
            def texts(widget):
                import tkinter as tk
                if isinstance(widget, tk.Text):
                    yield widget.get("1.0", "end")
                for child in widget.winfo_children():
                    yield from texts(child)
            self.assertIn("Upstream", "\n".join(texts(remotes.window)))
            self.assertIn("No remotes", network.status.get())
            for view in (history, remotes, network):
                view.window.destroy()

    def test_move_review_has_filter_separate_tabs_and_copyable_details(self):
        self.app._problems = [{"path": "unreadable-root", "reason": "access denied"}]
        self.app._move_suggestions = [{"category": "ambiguous", "name": "example",
                                      "old_path": "old", "new_paths": ["candidate"],
                                      "evidence": ["Multiple candidates"]}]
        view = self.app._show_problems()
        self.addCleanup(view.window.destroy)
        self.app.update()
        self.assertEqual(len(view.tabs.tabs()), 2)
        view.list.selection_set("0")
        view.show_selected()
        self.assertIn("provenance unavailable", view.detail.get("1.0", "end"))
        view.copy_detail()
        self.assertIn("candidate", self.app.clipboard_get())
        view.filter.set("not-present")
        self.assertEqual(view.list.get_children(), ())

    def test_targeted_refresh_only_observes_approved_project(self):
        observed = []
        def collect(path):
            observed.append(path)
            return {"path": path, "branch": "main"}, frozenset({"branch"})
        with mock.patch.object(main.scanner, "collect_metadata_observation", side_effect=collect), \
                mock.patch.object(self.app, "_persist_projects"), \
                mock.patch.object(self.app, "_refresh_current_detail"):
            self.app._refresh_metadata_async(target_project=self.project)
            self.assertTrue(_pump_until(self.app, lambda: not self.app._scanning))
        self.assertEqual(observed, [str(self.path)])
        self.assertNotIn(main.RepoManagerApp.FULL_SCAN_AT_KEY, self.app.settings)


if __name__ == "__main__":
    unittest.main()
