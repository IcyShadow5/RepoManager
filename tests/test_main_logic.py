import inspect
import json
import logging
import queue
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import main as main_module
from repo_manager.main import (MOVE_OK,
                               MOVE_ROLLBACK_FAILED,
                               MOVE_STALE_TARGET,
                               apply_metadata_refresh,
                               coalesce_worker_errors,
                               commit_steps_for,
                               evaluate_git_steps,
                               filter_superseded_rows,
                               git_target_is_current,
                               group_move_suggestions,
                               move_outcome,
                               perform_confirmed_move,
                               reconcile_scan_result,
                               resolve_primary_for_project,
                               run_batch_moves, guarded_worker,
                               select_push_remote,
                               is_visible, row_tag, select_strong_suggestions,
                               sorted_projects, thread_excepthook,
                               git_target_snapshot, git_target_is_authorized,
                               problem_kind, group_problems, problem_display_reason,
                               attention_summary, custom_launcher_form_cells,
                               custom_launcher_validation_message,
                               custom_launcher_validation_state,
                               git_step_outcome, GIT_SUCCESS, GIT_FAILED,
                               GIT_CANCELLED, GIT_OUTCOME_UNKNOWN, GIT_PARTIAL,
                               working_on_now_rows, StatusLine)


class ProblemTaxonomyTests(unittest.TestCase):
    def test_scan_root_is_not_a_repository_problem(self):
        problem = {"kind": "scan_root", "path": "C:/offline",
                   "reason": "scan root unavailable"}
        self.assertEqual(problem_kind(problem), "scan_root")
        self.assertEqual(problem_display_reason(problem), "Scan issue")
        groups = group_problems([problem])
        self.assertEqual(groups["scan_root"], [problem])
        self.assertEqual(groups["repository"], [])

    def test_invalid_git_candidate_remains_a_repository_problem(self):
        problem = {"kind": "repository", "path": "C:/broken",
                   "reason": ".git exists but is not a valid repository"}
        self.assertEqual(problem_kind(problem), "repository")
        self.assertEqual(problem_display_reason(problem), "Repository problem")
        groups = group_problems([problem])
        self.assertEqual(groups["repository"], [problem])
        self.assertEqual(groups["scan_root"], [])

    def test_legacy_repository_problem_defaults_to_repository_category(self):
        problem = {"path": "C:/broken", "reason": "invalid"}
        self.assertEqual(problem_kind(problem), "repository")

    def test_attention_summary_uses_accurate_categories(self):
        text = attention_summary([
            {"kind": "scan_root", "path": "C:/offline", "reason": "unavailable"},
            {"kind": "repository", "path": "C:/broken", "reason": "invalid"},
        ], move_count=1)
        self.assertIn("1 repository problem", text)
        self.assertIn("1 scan issue", text)
        self.assertIn("1 possible move", text)
        self.assertNotIn("broken repo", text.casefold())


class CustomLauncherDialogLogicTests(unittest.TestCase):
    def test_form_controls_have_unique_grid_cells(self):
        cells = custom_launcher_form_cells()
        self.assertEqual(len(cells), len(set(cells.values())))
        self.assertEqual(cells["args_help"], (3, 1))
        self.assertEqual(cells["cwd_input"], (4, 1))
        self.assertNotEqual(cells["args_help"], cells["cwd_input"])

    def test_empty_validation_state_is_hidden_and_neutral(self):
        self.assertIsNone(custom_launcher_validation_message("Name", "tool", "C:/work"))
        self.assertEqual(custom_launcher_validation_state(None), "hidden")
        self.assertEqual(custom_launcher_validation_state(""), "hidden")

    def test_invalid_input_has_actionable_feedback(self):
        message = custom_launcher_validation_message("Name", "", "C:/work")
        self.assertEqual(
            message, "Name, executable, and working directory are required.")
        self.assertEqual(custom_launcher_validation_state(message), "visible")

    def test_corrected_input_clears_validation_feedback(self):
        invalid = custom_launcher_validation_message("Name", "", "C:/work")
        self.assertEqual(custom_launcher_validation_state(invalid), "visible")
        corrected = custom_launcher_validation_message(
            "Name", "tool", "C:/work")
        self.assertIsNone(corrected)
        self.assertEqual(custom_launcher_validation_state(corrected), "hidden")


class AvailableProjectFolderTests(unittest.TestCase):
    def test_empty_missing_and_oserror_targets_are_unavailable(self):
        self.assertIsNone(main_module.available_project_folder({}))
        self.assertIsNone(main_module.available_project_folder(
            {"path": "C:/missing"}, is_dir=lambda _path: False))

        def unavailable(_path):
            raise OSError("denied")

        self.assertIsNone(main_module.available_project_folder(
            {"path": "C:/denied"}, is_dir=unavailable))

    def test_existing_folder_is_returned_unchanged(self):
        project = {"folder_path": "C:/Project Folder"}
        self.assertEqual(
            main_module.available_project_folder(
                project, is_dir=lambda _path: True),
            "C:/Project Folder",
        )

    def test_scan_root_validation_is_accessible_and_canonical(self):
        self.assertFalse(main_module.scan_root_is_available(""))
        self.assertFalse(main_module.scan_root_is_available(
            "C:/missing", is_dir=lambda _path: False))
        self.assertTrue(main_module.scan_root_is_available(
            "C:/Present", is_dir=lambda _path: True))
        self.assertEqual(
            main_module.scan_root_key(r"C:\Present\."),
            main_module.scan_root_key(r"c:\present"))


class SafeRemoteWebUrlTests(unittest.TestCase):
    def test_recognized_remote_forms_become_credential_free_https(self):
        self.assertEqual(
            main_module.safe_remote_web_url("git@github.com:owner/repo.git"),
            "https://github.com/owner/repo")
        self.assertEqual(
            main_module.safe_remote_web_url("github.com/owner/repo"),
            "https://github.com/owner/repo")

    def test_arbitrary_or_credential_like_text_is_unavailable(self):
        for value in ("javascript:alert(1)", "C:/secret", "not a remote"):
            self.assertIsNone(main_module.safe_remote_web_url(value), value)
        self.assertEqual(
            main_module.safe_remote_web_url(
                "https://user:password@example.com/o/r"),
            "https://example.com/o/r")


def proj(name, **kw):
    p = {
        "path": rf"C:\repos\{name}", "name": name, "status": "idea",
        "focus": "", "pinned": False, "dirty": 0, "ahead": 0, "behind": 0,
        "remote": f"github.com/x/{name.lower()}",
    }
    p.update(kw)
    return p


class MetadataRefreshTests(unittest.TestCase):
    def test_refresh_preserves_identity_name_and_curation(self):
        project = proj(
            "repository",
            path=r"C:\Projects\PocketLedger\repository",
            project_id="stable-id",
            status="active",
            focus="keep",
            pinned=True,
        )
        metadata = dict(project)
        metadata.update({
            "name": "PocketLedger",
            "project_id": "replacement-id",
            "status": "idea",
            "focus": "",
            "pinned": False,
            "dirty": 7,
            "branch": "feature/runtime",
        })

        apply_metadata_refresh([project], [metadata])

        self.assertEqual(project["name"], "repository")
        self.assertEqual(project["project_id"], "stable-id")
        self.assertEqual(project["status"], "active")
        self.assertEqual(project["focus"], "keep")
        self.assertTrue(project["pinned"])
        self.assertEqual(project["dirty"], 7)
        self.assertEqual(project["branch"], "feature/runtime")


class RowTagTests(unittest.TestCase):
    def test_clean_is_untagged(self):
        self.assertEqual(row_tag(proj("A")), "")

    def test_precedence_archived_beats_all(self):
        self.assertEqual(row_tag(proj("A", status="archived", dirty=3,
                                       ahead=1)), "archived")

    def test_no_remote_before_dirty(self):
        self.assertEqual(row_tag(proj("A", dirty=2, remote=None)), "noremote")

    def test_dirty_before_sync(self):
        self.assertEqual(row_tag(proj("A", dirty=1, ahead=2)), "dirty")

    def test_sync_only(self):
        self.assertEqual(row_tag(proj("A", behind=1)), "sync")


class VisibleTests(unittest.TestCase):
    def test_empty_filter_matches_all(self):
        self.assertTrue(is_visible(proj("Alpha"), ""))

    def test_matches_name_path_focus_case_insensitive(self):
        p = proj("Alpha", focus="Refactor district system")
        self.assertTrue(is_visible(p, "alpha"))
        self.assertTrue(is_visible(p, r"c:\repos"))
        self.assertTrue(is_visible(p, "DISTRICT"))
        self.assertFalse(is_visible(p, "beta"))

    def test_missing_fields_do_not_crash(self):
        self.assertFalse(is_visible({"path": "x"}, "zzz"))


class SortTests(unittest.TestCase):
    def test_default_pinned_then_active_then_name(self):
        items = [proj("zeta"), proj("beta", status="active"),
                 proj("alpha", pinned=True)]
        self.assertEqual([p["name"] for p in sorted_projects(items)],
                         ["alpha", "beta", "zeta"])

    def test_by_name_asc_and_desc(self):
        items = [proj("B"), proj("a"), proj("C")]
        names = [p["name"] for p in sorted_projects(items, "name")]
        self.assertEqual(names, ["a", "B", "C"])
        names = [p["name"] for p in sorted_projects(items, "name", True)]
        self.assertEqual(names, ["C", "B", "a"])

    def test_dirty_numeric_not_string(self):
        items = [proj("A", dirty=10), proj("B", dirty=9)]
        self.assertEqual(sorted_projects(items, "dirty")[0]["name"], "B")

    def test_sync_sums_ahead_behind(self):
        items = [proj("A", ahead=1), proj("B", behind=3)]
        self.assertEqual(sorted_projects(items, "sync")[0]["name"], "A")
        self.assertEqual(
            sorted_projects(items, "sync", True)[0]["name"], "B")

    def test_missing_values_tolerated(self):
        items = [{"name": "X", "path": "p"}]
        self.assertEqual(len(sorted_projects(items, "branch")), 1)


class GuardedWorkerTests(unittest.TestCase):
    def test_success_emits_only_worker_event(self):
        q = queue.Queue()

        def work():
            q.put(("result", "payload"))

        guarded_worker(q, work)()
        self.assertEqual(q.get_nowait(), ("result", "payload"))
        with self.assertRaises(queue.Empty):
            q.get_nowait()

    def test_exception_emits_exactly_one_error_event(self):
        q = queue.Queue()

        def work():
            raise RuntimeError("boom")

        guarded_worker(q, work)()
        kind, payload = q.get_nowait()
        self.assertEqual(kind, "error")
        self.assertIn("failed", payload)
        with self.assertRaises(queue.Empty):
            q.get_nowait()

    def test_error_emission_failure_does_not_raise(self):
        class FailingQueue:
            def put(self, _item):
                raise RuntimeError("queue gone")

        def work():
            raise RuntimeError("boom")

        guarded_worker(FailingQueue(), work)()  # must not raise


class CoalesceErrorsTests(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(coalesce_worker_errors([]))

    def test_deduplicates_and_limits_to_three(self):
        msgs = ["a", "b", "a", "c", "d"]
        out = coalesce_worker_errors(msgs)
        self.assertIn("a", out)
        self.assertIn("b", out)
        self.assertIn("c", out)
        self.assertNotIn("\nd", out.split("(+1 more")[0])
        self.assertIn("(+1 more", out)

    def test_single_message_passthrough(self):
        self.assertEqual(coalesce_worker_errors(["only"]), "only")


class AssociationAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "project_id": "source", "path": r"C:\source", "name": "Source",
            "status": "active", "focus": "keep", "pinned": True,
        }
        self.owner = {
            "project_id": "owner", "path": r"D:\owned", "name": "Owner",
            "status": "idea", "focus": "owner", "pinned": False,
        }
        self.app = object.__new__(main_module.RepoManagerApp)
        self.app.projects = [self.source, self.owner]

    def test_application_api_has_no_subset_authority_parameter_or_bypass(self):
        parameters = inspect.signature(
            main_module.RepoManagerApp.associate_repository).parameters

        self.assertEqual(list(parameters), ["self", "project", "target"])
        self.assertFalse(hasattr(main_module.projects, "associate_repository"))
        # RM-002: the V0.1.0 GUI entry point is hidden; the validated
        # application-layer API remains the only association authority.
        self.assertFalse(hasattr(main_module.RepoManagerApp,
                                 "_choose_association"))

    def test_partial_context_cannot_be_supplied_to_authorize_mutation(self):
        target = {"path": r"D:\owned", "name": "Owned", "broken": False}
        before_source = dict(self.source)
        before_owner = dict(self.owner)

        for context in (None, [], [self.source], [self.source, self.owner]):
            with self.subTest(context=context):
                with self.assertRaises(TypeError):
                    self.app.associate_repository(
                        self.source, target, project_records=context)

        self.assertEqual(self.source, before_source)
        self.assertEqual(self.owner, before_owner)

    def test_live_registry_rejects_canonical_owner_before_mutation(self):
        target = {"path": r"d:\owned\.", "name": "Owned", "broken": False}
        before_source = dict(self.source)
        before_owner = dict(self.owner)

        with self.assertRaisesRegex(ValueError, "another Project"):
            self.app.associate_repository(self.source, target)

        self.assertEqual(self.source, before_source)
        self.assertEqual(self.owner, before_owner)

    def test_source_must_belong_to_live_registry(self):
        foreign = {"project_id": "foreign", "path": r"E:\foreign",
                   "name": "Foreign"}
        before = dict(foreign)

        with self.assertRaisesRegex(ValueError, "not in the live Registry"):
            self.app.associate_repository(
                foreign, {"path": r"F:\target", "name": "Target"})

        self.assertEqual(foreign, before)

    def test_unowned_target_mutates_only_source_and_preserves_curation(self):
        self.source.update({"dirty": 3, "branch": "old", "remote": "old/repo",
                            "custom": {"keep": True}})
        before_owner = dict(self.owner)

        result = self.app.associate_repository(
            self.source,
            {"path": r"E:\unowned", "name": "Unowned", "broken": False},
        )

        self.assertIs(result, self.source)
        self.assertEqual(self.source["project_id"], "source")
        self.assertEqual(self.source["path"], r"E:\unowned")
        self.assertEqual(self.source["status"], "active")
        self.assertEqual(self.source["focus"], "keep")
        self.assertTrue(self.source["pinned"])
        self.assertEqual(self.source["custom"], {"keep": True})
        self.assertNotIn("dirty", self.source)
        self.assertNotIn("branch", self.source)
        self.assertNotIn("remote", self.source)
        self.assertEqual(self.owner, before_owner)


class MetadataRefreshProjectBoundaryTests(unittest.TestCase):
    def test_stale_metadata_result_is_not_applied_after_reassociation(self):
        app = object.__new__(main_module.RepoManagerApp)
        app.projects = [{"project_id": "p1", "path": r"C:\\new",
                         "name": "curated", "dirty": 0}]
        app._scan_queue = queue.Queue()
        app._metadata_gen = 2
        app._scanning = True
        app.scan_btn = mock.Mock()
        app._populate_coalesced = mock.Mock()
        app._schedule_after = mock.Mock()
        app._scan_queue.put(("meta", [("p1", r"C:\\old",
                                        {"path": r"C:\\old", "dirty": 9},
                                        None)], 1))
        with mock.patch.object(main_module.store, "save_projects") as save:
            app._drain_scan_queue()
        self.assertEqual(app.projects[0]["dirty"], 0)
        save.assert_not_called()

    def test_second_refresh_generation_supersedes_first(self):
        app = object.__new__(main_module.RepoManagerApp)
        app.projects = [{"project_id": "p1", "path": r"C:\\repo", "dirty": 0}]
        app._scan_queue = queue.Queue()
        app._metadata_gen = 2
        app._scanning = True
        app.scan_btn = mock.Mock()
        app._populate_coalesced = mock.Mock()
        app._schedule_after = mock.Mock()
        app._scan_queue.put(("meta", [("p1", r"C:\\repo",
                                        {"path": r"C:\\repo", "dirty": 1},
                                        None)], 1))
        with mock.patch.object(main_module.store, "save_projects") as save:
            app._drain_scan_queue()
        self.assertEqual(app.projects[0]["dirty"], 0)
        self.assertTrue(app._scanning)
        app._populate_coalesced.assert_not_called()
        save.assert_not_called()

    def test_stale_metadata_worker_error_does_not_end_newer_refresh(self):
        app = object.__new__(main_module.RepoManagerApp)
        app.projects = [{"project_id": "p1", "path": r"C:\\repo", "dirty": 0}]
        app._scan_queue = queue.Queue()
        app._metadata_gen = 2
        app._scan_gen = 0
        app._scanning = True
        app.scan_btn = mock.Mock()
        app._populate_coalesced = mock.Mock()
        app._schedule_after = mock.Mock()
        app._scan_queue.put(("error", {
            "message": "old metadata worker failed",
            "operation": "metadata",
            "generation": 1,
        }))
        with mock.patch.object(main_module.messagebox, "showerror") as shown:
            app._drain_scan_queue()
        self.assertTrue(app._scanning)
        app._populate_coalesced.assert_not_called()
        shown.assert_not_called()

    def test_stale_scan_worker_error_does_not_end_newer_scan(self):
        app = object.__new__(main_module.RepoManagerApp)
        app.projects = []
        app._scan_queue = queue.Queue()
        app._scan_gen = 2
        app._metadata_gen = 0
        app._scanning = True
        app.scan_btn = mock.Mock()
        app._populate_coalesced = mock.Mock()
        app._schedule_after = mock.Mock()
        app._scan_queue.put(("error", {
            "message": "old scan worker failed",
            "operation": "scan",
            "generation": 1,
        }))
        with mock.patch.object(main_module.messagebox, "showerror") as shown:
            app._drain_scan_queue()
        self.assertTrue(app._scanning)
        app._populate_coalesced.assert_not_called()
        shown.assert_not_called()

    def test_deleted_project_discards_delayed_metadata_result(self):
        app = object.__new__(main_module.RepoManagerApp)
        app.projects = []
        app._scan_queue = queue.Queue()
        app._metadata_gen = 1
        app._scanning = True
        app.scan_btn = mock.Mock()
        app._populate_coalesced = mock.Mock()
        app._schedule_after = mock.Mock()
        app._scan_queue.put(("meta", [("deleted", r"C:\\repo",
                                        {"path": r"C:\\repo", "dirty": 8},
                                        None)], 1))
        with mock.patch.object(main_module.store, "save_projects") as save:
            app._drain_scan_queue()
        save.assert_not_called()

    def test_full_scan_result_does_not_allow_metadata_result_to_reassociate(self):
        app = object.__new__(main_module.RepoManagerApp)
        app.projects = [{"project_id": "p1", "path": r"C:\\new", "dirty": 0}]
        app._scan_queue = queue.Queue()
        app._metadata_gen = 1
        app._scanning = True
        app.scan_btn = mock.Mock()
        app._populate_coalesced = mock.Mock()
        app._schedule_after = mock.Mock()
        app._scan_queue.put(("meta", [("p1", r"C:\\old",
                                        {"path": r"C:\\old", "dirty": 9},
                                        None)], 1))
        with mock.patch.object(main_module.store, "save_projects") as save:
            app._drain_scan_queue()
        self.assertEqual(app.projects[0]["dirty"], 0)
        save.assert_not_called()

    def test_legacy_metadata_payload_is_discarded(self):
        app = object.__new__(main_module.RepoManagerApp)
        app.projects = [{"project_id": "p1", "path": r"C:\\repo", "dirty": 0}]
        app._scan_queue = queue.Queue()
        app._metadata_gen = 1
        app._scanning = True
        app.scan_btn = mock.Mock()
        app._populate_coalesced = mock.Mock()
        app._schedule_after = mock.Mock()
        app._scan_queue.put(("meta", [{"path": r"C:\\repo", "dirty": 9}]))
        with mock.patch.object(main_module.store, "save_projects") as save:
            app._drain_scan_queue()
        self.assertEqual(app.projects[0]["dirty"], 0)
        save.assert_not_called()

    def test_metadata_collection_failure_does_not_discard_other_results(self):
        app = object.__new__(main_module.RepoManagerApp)
        app.projects = [{"project_id": "p1", "path": r"C:\\one", "dirty": 0},
                        {"project_id": "p2", "path": r"C:\\two", "dirty": 0}]
        app._scan_queue = queue.Queue()
        app._metadata_gen = 1
        app._scanning = True
        app.scan_btn = mock.Mock()
        app._populate_coalesced = mock.Mock()
        app._schedule_after = mock.Mock()
        app._scan_queue.put(("meta", [
            ("p1", r"C:\\one", None, RuntimeError("failed")),
            ("p2", r"C:\\two", {"path": r"C:\\two", "dirty": 3}, None),
        ], 1))
        with mock.patch.object(main_module.store, "save_projects") as save, \
                mock.patch.object(main_module.messagebox, "showerror"):
            app._drain_scan_queue()
        self.assertEqual(app.projects[0]["dirty"], 0)
        self.assertEqual(app.projects[1]["dirty"], 3)
        save.assert_called_once_with(app.projects)

    def test_folder_only_projects_are_not_submitted_to_git_metadata(self):
        class InlineThread:
            def __init__(self, *, target, daemon):
                self.target = target

            def start(self):
                self.target()

        class InlinePool:
            def __init__(self, *, processes):
                self.processes = processes

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def map(self, callback, values):
                return list(map(callback, values))

        app = object.__new__(main_module.RepoManagerApp)
        app._scanning = False
        app._scan_queue = queue.Queue()
        app.projects = [
            {"path": r"C:\repos\git", "name": "git"},
            {"folder_path": r"C:\projects\planning", "name": "planning"},
        ]

        with mock.patch.object(main_module.scanner, "ThreadPool",
                               InlinePool), \
                mock.patch.object(main_module.threading, "Thread", InlineThread), \
                mock.patch.object(main_module.scanner, "collect_metadata",
                                  side_effect=lambda path: {"path": path}) as collect:
            app._refresh_metadata_async()

        kind, results, generation = app._scan_queue.get_nowait()
        self.assertEqual(kind, "meta")
        self.assertEqual(generation, 1)
        self.assertEqual(results[0][1], r"C:\repos\git")
        self.assertEqual(results[0][2], {"path": r"C:\repos\git"})
        collect.assert_called_once_with(r"C:\repos\git")

    def test_metadata_pool_does_not_hold_process_open_after_gui_worker_exit(self):
        code = (
            "import threading, time\n"
            "from repo_manager import scanner\n"
            "started = threading.Event()\n"
            "def block():\n"
            "    started.set()\n"
            "    time.sleep(30)\n"
            "def work():\n"
            "    with scanner.ThreadPool(processes=1) as pool:\n"
            "        pool.apply(block)\n"
            "threading.Thread(target=work, daemon=True).start()\n"
            "assert started.wait(2)\n"
        )
        completed = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=3,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_metadata_result_updates_repository_and_preserves_folder_only(self):
        app = object.__new__(main_module.RepoManagerApp)
        repository = {
            "path": r"C:\repos\git", "name": "git", "status": "active",
            "focus": "runtime", "pinned": True, "dirty": 0,
        }
        folder_only = {
            "folder_path": r"C:\projects\planning", "name": "planning",
            "status": "paused", "focus": "plan", "pinned": False,
        }
        app.projects = [repository, folder_only]
        app._scan_queue = queue.Queue()
        app._metadata_gen = 1
        app._scan_queue.put(("meta", [(None, r"C:\repos\git", {
            "path": r"C:\repos\git", "name": "git", "dirty": 2,
            "branch": "main", "worktrees": [{"path": r"C:\repos\git"}],
        }, None)], 1))
        app._scanning = True
        app.scan_btn = mock.Mock()
        app._populate_coalesced = mock.Mock()
        app._schedule_after = mock.Mock()

        with mock.patch.object(main_module.store, "save_projects") as save:
            app._drain_scan_queue()

        self.assertEqual(repository["dirty"], 2)
        self.assertEqual(repository["branch"], "main")
        self.assertEqual(len(repository["worktrees"]), 1)
        self.assertEqual(repository["status"], "active")
        self.assertEqual(repository["focus"], "runtime")
        self.assertTrue(repository["pinned"])
        self.assertEqual(folder_only, {
            "folder_path": r"C:\projects\planning", "name": "planning",
            "status": "paused", "focus": "plan", "pinned": False,
        })
        save.assert_called_once_with(app.projects)


class ConfirmedMoveTransactionTests(unittest.TestCase):
    """Confirmed moves preserve registry and note consistency."""

    OLD_PATH = r"C:\Users\u\Desktop\Iron"
    NEW_PATH = r"C:\Users\u\Desktop\Projekte\Iron"
    NOW = "2026-08-26T12:00:00Z"

    @staticmethod
    def suggestion():
        return {"kind": "move", "category": "strong",
                "old_path": ConfirmedMoveTransactionTests.OLD_PATH,
                "new_path": ConfirmedMoveTransactionTests.NEW_PATH,
                "name": "Iron", "evidence": ["Same normalized remote"]}

    def _projects(self):
        return [{"path": self.OLD_PATH, "name": "Iron",
                 "status": "active", "focus": "district refactor",
                 "pinned": True, "added_at": "2026-01-01T00:00:00Z",
                 "last_seen": "2026-08-25T00:00:00Z"}]

    def test_happy_path_migrates_and_adopts_name(self):
        projects = self._projects()
        calls = {"save": 0, "note": []}
        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=lambda: calls.__setitem__("save", calls["save"]+1),
            move_note=lambda *a: calls["note"].append(a))
        self.assertEqual(outcome, "migrated")
        self.assertEqual(calls["save"], 1)
        self.assertEqual(calls["note"],
                         [("Iron", self.OLD_PATH,
                           Path(self.NEW_PATH).name, self.NEW_PATH)])
        self.assertEqual(entry["path"], self.NEW_PATH)
        self.assertEqual(entry["name"], "Iron")  # folder name adopted
        self.assertEqual(entry["moved_from"], self.OLD_PATH)
        self.assertEqual(entry["last_seen"], self.NOW)
        # curation and added_at survive
        self.assertEqual(entry["status"], "active")
        self.assertEqual(entry["focus"], "district refactor")
        self.assertTrue(entry["pinned"])
        self.assertEqual(entry["added_at"], "2026-01-01T00:00:00Z")

    def test_duplicate_new_path_blocked(self):
        projects = self._projects()
        projects.append({"path": self.NEW_PATH, "name": "Iron"})
        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=self._fail, move_note=self._fail)
        self.assertEqual(outcome, "duplicate")
        self.assertIsNone(entry)
        self.assertEqual(len(projects), 2)  # nothing changed

    def test_equivalent_duplicate_new_path_is_blocked(self):
        projects = self._projects()
        projects.append({"path": self.NEW_PATH + r"\.", "name": "Iron"})

        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=self._fail, move_note=self._fail)

        self.assertEqual(outcome, "duplicate")
        self.assertIsNone(entry)
        self.assertEqual(projects[0]["path"], self.OLD_PATH)

    def test_missing_old_blocked(self):
        outcome, _ = perform_confirmed_move(
            [], self.suggestion(), self.NOW,
            save_projects=self._fail, move_note=self._fail)
        self.assertEqual(outcome, "missing_old")

    def test_registry_failure_cancels_before_note_touch(self):
        projects = self._projects()
        snapshot = dict(projects[0])
        note_calls = []

        def failing_save():
            raise OSError("disk full")

        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=failing_save,
            move_note=lambda *a: note_calls.append(a))
        self.assertEqual(outcome, "rolled_back_registry")
        self.assertEqual(projects[0], snapshot)  # fully reverted
        self.assertEqual(note_calls, [])         # note never touched

    def test_note_failure_rolls_registry_back(self):
        projects = self._projects()
        snapshot = dict(projects[0])
        saves = []

        def save():
            saves.append(1)

        def failing_note(*a):
            raise OSError("rename locked")

        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=save, move_note=failing_note)
        self.assertEqual(outcome, "rolled_back_note")
        self.assertEqual(projects[0], snapshot)
        self.assertEqual(len(saves), 2)  # commit attempt + rollback restore

    def test_stable_project_note_migrates_before_registry_commit(self):
        projects = self._projects()
        projects[0]["project_id"] = "project-1"
        calls = []

        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=lambda: calls.append("save"),
            move_note=lambda *args: calls.append(("note", args)) or "moved")

        self.assertEqual(outcome, "migrated")
        self.assertEqual(calls[0][0], "note")
        self.assertEqual(calls[0][1][-1], "project-1")
        self.assertEqual(calls[1], "save")
        self.assertEqual(entry["project_id"], "project-1")

    def test_stable_note_failure_never_mutates_registry(self):
        projects = self._projects()
        projects[0]["project_id"] = "project-1"
        snapshot = dict(projects[0])
        saves = []

        outcome, _entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=lambda: saves.append(1),
            move_note=lambda *args: (_ for _ in ()).throw(
                OSError("note locked")))

        self.assertEqual(outcome, "rolled_back_note")
        self.assertEqual(projects[0], snapshot)
        self.assertEqual(saves, [])

    def _fail(self, *a, **k):
        raise OSError("injected failure")


class MoveRaceGuardTests(unittest.TestCase):
    OLD = r"C:\Users\u\Desktop\Iron"
    NEW = r"C:\Users\u\Desktop\Projekte\Iron"

    @staticmethod
    def row(path, status="idea"):
        return {"path": path, "name": Path(path).name, "status": status}

    def test_stale_snapshot_cannot_resurrect_moved_row(self):
        # A scan may finish after a confirmed move; stale rows must not return.
        moved_away = [(1, self.OLD.lower())]
        rows = [self.row(self.OLD, status="active"),  # pre-move snapshot
                self.row(self.NEW)]                   # fresh discovery
        kept = filter_superseded_rows(rows, moved_away, result_gen=0)
        paths = [r["path"] for r in kept]
        self.assertNotIn(self.OLD, paths)
        self.assertIn(self.NEW, paths)

    def test_current_generation_result_unfiltered(self):
        rows = [self.row(r"C:\x\A"), self.row(r"C:\x\B")]
        self.assertEqual(filter_superseded_rows(rows, [], result_gen=3),
                         rows)

    def test_multiple_sequential_moves_scoped_by_gen(self):
        moved_away = [(2, r"c:\old\a"), (5, r"c:\old\b")]
        rows = [self.row(r"C:\old\a"), self.row(r"C:\old\b"),
                self.row(r"C:\old\c")]
        # result_gen=4: move at gen 2 predates this scan (snapshot already
        # lacks 'a' -> nothing to do), move at gen 5 is in-flight -> drop 'b'
        kept = [r["path"] for r in filter_superseded_rows(
            rows, moved_away, result_gen=4)]
        self.assertEqual(kept, [r"C:\old\a", r"C:\old\c"])

    def test_full_ordering_scan_confirm_result(self):
        """A stale scan result cannot restore a moved project."""
        projects = [self.row(self.OLD, status="active")]
        gen_at_start = 0
        snapshot = [dict(p) for p in projects]          # worker's view
        # user confirms: perform move + bump generation
        sug = {"kind": "move", "old_path": self.OLD, "new_path": self.NEW}
        now = "2026-08-26T12:00:00Z"
        perform_confirmed_move(projects, sug, now,
                               save_projects=lambda: None,
                               move_note=lambda *a: "absent")
        gen_after = 1
        self.assertGreater(gen_after, gen_at_start)
        moved_away = [(gen_after, self.OLD.lower())]
        # stale result arrives built from the old snapshot + discovery
        stale_result = snapshot + [self.row(self.NEW)]
        applied = filter_superseded_rows(stale_result, moved_away,
                                         result_gen=gen_at_start)
        self.assertEqual([r["path"] for r in applied], [self.NEW])


class MoveRevalidationWiringTests(unittest.TestCase):
    """Optional callback compatibility and move revalidation guards.

    The UI supplies fresh path and identity checks. Calls without callbacks
    remain supported for callers that manage their own validation.
    """

    OLD = r"C:\Users\u\Desktop\Iron"
    NEW = r"C:\Users\u\Desktop\Projekte\Iron"
    NOW = "2026-08-30T00:00:00Z"

    @staticmethod
    def suggestion():
        return {"kind": "move", "category": "strong",
                "old_path": MoveRevalidationWiringTests.OLD,
                "new_path": MoveRevalidationWiringTests.NEW,
                "name": "Iron", "evidence": ["Same normalized remote"]}

    def _projects(self):
        return [{"path": self.OLD, "name": "Iron", "status": "active"}]

    def test_compatibility_call_without_callbacks_still_moves(self):
        outcome, entry = perform_confirmed_move(
            self._projects(), self.suggestion(), self.NOW,
            save_projects=lambda: None, move_note=lambda *a: "absent")
        self.assertEqual(outcome, MOVE_OK)
        self.assertEqual(entry["path"], self.NEW)

    def test_wired_path_exists_blocks_stale_target(self):
        for label, path_exists in (
                ("old path present again", lambda path: path == self.OLD),
                ("new path missing", lambda path: False)):
            with self.subTest(label=label):
                result = move_outcome(
                    self._projects(), self.suggestion(), self.NOW,
                    lambda: self.fail("registry must not be saved"),
                    lambda *a: self.fail("note must not move"),
                    path_exists=path_exists)
                self.assertEqual(result.status, MOVE_STALE_TARGET)

    def test_run_batch_moves_compatibility_call_without_path_exists(self):
        accepted, failures = run_batch_moves(
            [self.suggestion()], self._projects(), self.NOW,
            save_projects=lambda: None, move_note=lambda *a: "absent")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(failures, [])

    def test_run_batch_moves_forwards_path_exists_guard(self):
        projects = self._projects()
        accepted, failures = run_batch_moves(
            [self.suggestion()], projects, self.NOW,
            save_projects=lambda: None, move_note=lambda *a: "absent",
            path_exists=lambda path: False)  # new path missing -> stale
        self.assertEqual(accepted, [])
        self.assertEqual([o for _s, o in failures], ["stale_target"])
        self.assertEqual(projects[0]["path"], self.OLD)  # nothing moved


class BatchReconciliationTests(unittest.TestCase):
    OLD = r"C:\u\Desktop\Foo"
    NEW = r"C:\u\Projekte\Foo"
    NOW = "2026-08-26T12:00:00Z"

    @staticmethod
    def sug(category, old=None, new=None, name="Foo"):
        return {"kind": "move", "category": category,
                "old_path": old or rf"C:\old\{name}",
                "new_path": new or rf"C:\new\{name}",
                "name": name, "evidence": ["Same normalized remote"]}

    def _projects(self):
        return [{"path": self.OLD, "name": "Foo", "status": "active",
                 "focus": "work", "pinned": True,
                 "last_seen": "2099-01-01T00:00:00Z"}]

    # ---- grouping -------------------------------------------------------
    def test_grouping_splits_and_sorts_categories(self):
        sugs = [self.sug("strong", name="b"),
                self.sug("ambiguous", old=r"C:\old\Relic",
                         new=r"C:\a\Relic"),
                self.sug("strong", name="a"),
                self.sug("possible", name="zz")]
        groups = group_move_suggestions(sugs)
        self.assertEqual([s["name"] for s in groups["strong"]],
                         ["a", "b"])          # sorted by old path
        self.assertEqual(len(groups["ambiguous"]), 1)
        self.assertEqual([s["name"] for s in groups["possible"]], ["zz"])

    def test_unknown_category_lands_in_possible(self):
        groups = group_move_suggestions([self.sug("weird")])
        self.assertEqual(len(groups["possible"]), 1)
        self.assertEqual(groups["strong"], [])

    # ---- batch selection & execution ------------------------------------
    def test_batch_selects_strong_only(self):
        sugs = [self.sug("strong"), self.sug("ambiguous"),
                self.sug("possible")]
        selected = select_strong_suggestions(sugs)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["category"], "strong")

    def test_batch_empty_state(self):
        accepted, failures = run_batch_moves(
            [], self._projects(), self.NOW, lambda: None,
            lambda *a: "absent")
        self.assertEqual((accepted, failures), ([], []))

    def test_batch_reuses_transaction_and_reports(self):
        projects = self._projects()
        strong = [self.sug("strong", name=f"repo{i}") for i in range(3)]
        projects += [{"path": rf"C:\old\repo{i}", "name": f"repo{i}",
                      "status": "idea"} for i in range(3)]
        sugs = strong + [self.sug("ambiguous")]
        accepted, failures = run_batch_moves(
            sugs, projects, self.NOW,
            save_projects=lambda: None,
            move_note=lambda *a: "absent")
        self.assertEqual(len(accepted), 3)
        self.assertEqual(failures, [])
        for (s, entry, outcome) in zip(strong, [e for _, e, _ in accepted],
                                       [o for _, _, o in accepted]):
            self.assertEqual(outcome, "migrated")
            self.assertTrue(entry["path"].endswith(s["new_path"]))

    def store_save(self, projects):
        pass

    def test_one_failure_does_not_block_rest(self):
        projects = self._projects()
        s_bad = self.sug("strong", name="bad")
        s_good1 = self.sug("strong", name="good1")
        s_good2 = self.sug("strong", name="good2")
        projects += [{"path": r"C:\old\bad", "name": "bad"},
                     {"path": r"C:\old\good1", "name": "good1"},
                     {"path": r"C:\old\good2", "name": "good2"}]

        def note_fn(old_name, old_path, new_name, new_path):
            if old_name == "bad":
                raise OSError("locked")
            return "absent"

        with mock.patch.object(main_module, "select_strong_suggestions",
                               return_value=[s_bad, s_good1, s_good2]):
            accepted, failures = run_batch_moves(
                [s_bad, s_good1, s_good2], projects, self.NOW,
                save_projects=lambda: None, move_note=note_fn)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][1], "rolled_back_note")
        self.assertEqual(failures[0][0]["name"], "bad")

    def test_consumed_suggestions_disappear_no_double_processing(self):
        suggestions = [self.sug("strong"), self.sug("strong", name="B"),
                       self.sug("ambiguous", old=r"C:\old\Relic",
                                new=r"C:\a\Relic")]
        projects = [{"path": rf"C:\old\{n}", "name": n, "status": "idea"}
                    for n in ("Foo", "B")]
        accepted, failures = run_batch_moves(
            suggestions, projects, self.NOW,
            save_projects=lambda: None,
            move_note=lambda *a: "absent")
        consumed_ids = {id(s) for s, _, _ in accepted}
        self.assertEqual(len(consumed_ids), 2)
        self.assertEqual(failures, [])
        remaining = [s for s in suggestions if id(s) not in consumed_ids]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["category"], "ambiguous")


class WorkingOnNowTests(unittest.TestCase):
    ALIVE = r"C:\Users\u\Projekte\Live"
    GONE = r"C:\Users\u\Projekte\Gone"

    @staticmethod
    def p(path, status="idea", pinned=False, commit="2026-08-01"):
        return {"path": path, "name": Path(path).name, "status": status,
                "pinned": pinned, "last_commit_date": commit}

    def setUp(self):
        self.exists = {self.ALIVE}
        self.exists_fn = lambda path: path in self.exists

    def rows(self, items):
        return [p["name"] for p in working_on_now_rows(
            items, exists=self.exists_fn)]

    def test_empty_items_yield_empty(self):
        self.assertEqual(working_on_now_rows([], exists=self.exists_fn), [])

    def test_active_alive_included(self):
        row = self.p(self.ALIVE, status="active")
        self.assertEqual(self.rows([row]), ["Live"])

    def test_pinned_only_is_not_included(self):
        row = self.p(self.ALIVE, pinned=True)
        self.assertEqual(self.rows([row]), [])

    def test_stale_active_excluded(self):
        self.rows_excluded(status="active")

    def rows_excluded(self, status="active", pinned=None):
        row = self.p(self.GONE, status=status,
                     pinned=pinned if pinned is not None else False)
        self.assertEqual(self.rows([row]), [])

    def test_stale_pinned_excluded(self):
        self.rows_excluded(pinned=True)

    def test_archived_alive_excluded(self):
        self.assertEqual(self.rows([self.p(self.ALIVE, status="archived")]),
                         [])

    def test_idea_alive_excluded(self):
        self.assertEqual(self.rows([self.p(self.ALIVE)]), [])

    def test_sorting_newest_first_and_cap(self):
        items = []
        for i in range(25):
            items.append(self.p(f"C:\\l\\p{i:02d}", status="active",
                                commit=f"2026-08-{i+1:02d}"))
            self.exists.add(f"C:\\l\\p{i:02d}")
        rows = working_on_now_rows(items, exists=self.exists_fn)
        self.assertEqual(len(rows), 20)
        dates = [r["last_commit_date"] for r in rows]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_membership_does_not_mutate_inputs(self):
        row = self.p(self.ALIVE, status="active")
        before = dict(row)
        working_on_now_rows([row], exists=self.exists_fn)
        self.assertEqual(row, before)


class StaleRowTagTests(unittest.TestCase):
    def test_stale_dominates_all_states(self):
        base = {"path": r"C:\g", "status": "active", "dirty": 3,
                "ahead": 1, "behind": 2}
        self.assertEqual(row_tag(base, available=False), "stale")
        base["status"] = "archived"
        self.assertEqual(row_tag(base, available=False), "stale")

    def test_available_rows_keep_legacy_tags(self):
        p = {"status": "archived", "remote": "x"}
        self.assertEqual(row_tag(p, available=True), "archived")
        self.assertEqual(row_tag({"dirty": 1, "remote": "x",
                                  "path": r"C:\p"}, available=True),
                         "dirty")

    def test_default_signature_backwards_compatible(self):
        self.assertEqual(row_tag({"path": r"C:\x", "remote": "r",
                                  "dirty": 0}), "")


class ResolvePrimaryTests(unittest.TestCase):
    def test_npm_dev_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            (r / "package.json").write_text(json.dumps(
                {"scripts": {"build": "x", "dev": "v"}}))
            pr = resolve_primary_for_project({"path": str(r)}, {})
            self.assertIsNotNone(pr)
            self.assertEqual(pr["label"], "npm run dev")

    def test_no_signals_yields_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            pr = resolve_primary_for_project({"path": tmp}, {})
            self.assertIsNone(pr)

    def test_utility_only_scripts_yield_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            (r / "cleanup-old.ps1").write_text("x")
            pr = resolve_primary_for_project({"path": str(r)}, {})
            self.assertIsNone(pr)

    def test_missing_path_yields_none(self):
        pr = resolve_primary_for_project(
            {"path": r"C:\definitely\not\here"}, {})
        self.assertIsNone(pr)



class GitOutcomeTests(unittest.TestCase):
    def test_success_and_partial_push_failure_are_distinct(self):
        self.assertEqual(git_step_outcome([
            ("add", 0, "", None), ("commit", 0, "created", None),
            ("push", 1, "rejected", None)])[0], GIT_PARTIAL)

    def test_timeout_is_unknown(self):
        self.assertEqual(git_step_outcome([
            ("commit", 1, "timed out", GIT_OUTCOME_UNKNOWN)]),
            (GIT_OUTCOME_UNKNOWN, "commit outcome is unknown: timed out"))

    def test_cancelled_is_explicit(self):
        self.assertEqual(GIT_CANCELLED, "CANCELLED")

    def test_partial_message_exposes_durable_commit(self):
        outcome, message = git_step_outcome([
            ("add", 0, "", None), ("commit", 0, "created", None),
            ("push", 1, "rejected", None)])
        self.assertEqual(outcome, GIT_PARTIAL)
        self.assertIn("commit succeeded", message)
        self.assertIn("push failed", message)


class GitStepEvaluationTests(unittest.TestCase):
    """Git-step evaluation reports the first failed step."""

    def test_all_steps_success(self):
        ok, msg = evaluate_git_steps([("add", 0, ""), ("commit", 0, "x"),
                                      ("push", 0, "To origin")])
        self.assertTrue(ok)
        self.assertEqual(msg, "To origin")

    def test_add_failure_named_and_fatal(self):
        ok, msg = evaluate_git_steps([("add", 128, "error: bad path"),
                                      ("commit", 0, ""), ("push", 0, "")])
        self.assertFalse(ok)
        self.assertIn("add failed", msg)

    def test_commit_failure_no_longer_masked_by_push(self):
        ok, msg = evaluate_git_steps(
            [("add", 0, ""),
             ("commit", 1, "nothing to commit, working tree clean"),
             ("push", 0, "Everything up-to-date")])
        self.assertFalse(ok)
        self.assertIn("commit failed", msg)
        self.assertIn("nothing to commit", msg)

    def test_push_failure_reported_as_push(self):
        ok, msg = evaluate_git_steps([("add", 0, ""), ("commit", 0, ""),
                                      ("push", 128, "rejected")])
        self.assertFalse(ok)
        self.assertIn("push failed", msg)

    def test_empty_results_is_failure(self):
        ok, msg = evaluate_git_steps([])
        self.assertFalse(ok)


class LauncherQualityTests(unittest.TestCase):
    """Launcher selection demotes utility scripts."""

    def test_configure_scripts_demoted(self):
        from repo_manager import launchers
        self.assertIsNotNone(
            launchers.UTILITY_NAME_RE.search("CONFIGURE_REMOTE_STUDIO"))

    def test_gradlew_demoted(self):
        from repo_manager import launchers
        self.assertIsNotNone(launchers.UTILITY_NAME_RE.search("gradlew"))

    def test_operational_verbs_demoted(self):
        from repo_manager import launchers
        for name in ("DISABLE_REMOTE_STUDIO", "MIGRATE_FROM_EXISTING",
                     "RECOVER_STUCK_VALIDATION"):
            self.assertIsNotNone(launchers.UTILITY_NAME_RE.search(name),
                                 name)

    def test_unknown_tier_ranks_below_build(self):
        from repo_manager import launchers
        self.assertLess(launchers.PRIORITY_NPM_BUILD,
                        launchers.npm_script_priority("catalog:check"))
        self.assertLess(launchers.PRIORITY_NPM_BUILD,
                        launchers.npm_script_priority("mappings:sync"))

    def test_build_wins_over_unknown_tier_in_selection(self):
        from repo_manager import launchers
        sugs = [{"label": "npm run build", "type": "npm",
                 "priority": launchers.PRIORITY_NPM_BUILD, "healthy": True},
                {"label": "[project] npm run catalog:check", "type": "npm",
                 "priority": launchers.PRIORITY_NPM_OTHER, "healthy": True}]
        star = launchers.select_primary_command(sugs)
        self.assertEqual(star["label"], "npm run build")


class CommitStepsTests(unittest.TestCase):
    def test_clean_tree_yields_no_steps(self):
        self.assertEqual(commit_steps_for("m", ""), [])

    def test_whitespace_only_tree_yields_no_steps(self):
        self.assertEqual(commit_steps_for("m", " \n\t"), [])

    def test_changes_yield_full_ladder(self):
        steps = commit_steps_for("my msg", " M f.py\n?? new.txt")
        self.assertEqual([s[0] for s in steps], ["add", "commit", "push"])
        self.assertEqual(steps[1][1], ("commit", "-m", "my msg"))

    def test_unknown_porcelain_authorizes_no_mutation(self):
        self.assertEqual(commit_steps_for("m", None), [])

    def test_custom_remote_is_used_for_push_step(self):
        steps = commit_steps_for("x", " M file", remote="upstream")
        self.assertEqual(steps[-1],
                         ("push", ("push", "-u", "upstream", "HEAD")))


class PushRemoteSelectionTests(unittest.TestCase):
    def test_single_non_origin_remote_is_selected(self):
        self.assertEqual(select_push_remote(["upstream"]), "upstream")

    def test_tracked_remote_wins(self):
        self.assertEqual(
            select_push_remote(["backup", "upstream"], "upstream/main"),
            "upstream")

    def test_origin_is_safe_default_but_ambiguous_custom_set_is_blocked(self):
        self.assertEqual(select_push_remote(["backup", "origin"]), "origin")
        self.assertIsNone(select_push_remote(["backup", "production"]))


class GitTargetFreshnessTests(unittest.TestCase):
    def test_snapshot_requires_same_live_project_and_path(self):
        live = {"project_id": "p-1", "path": r"C:\\repo"}
        snapshot = git_target_snapshot(live)
        self.assertTrue(git_target_is_authorized([live], snapshot))
        live["path"] = r"D:\\replacement"
        self.assertFalse(git_target_is_authorized([live], snapshot))

    def test_legacy_snapshot_is_not_authorized_by_equal_values(self):
        live = {"path": r"C:\\repo"}
        snapshot = git_target_snapshot(dict(live))
        self.assertFalse(git_target_is_authorized([live], snapshot))

    def test_same_project_and_path_is_current(self):
        current = {"project_id": "p-1", "path": r"C:\repo"}
        self.assertTrue(git_target_is_current(
            [current], {"project_id": "p-1", "path": r"C:\repo"}))

    def test_reassociation_or_removal_invalidates_preview(self):
        target = {"project_id": "p-1", "path": r"C:\old"}
        self.assertFalse(git_target_is_current(
            [{"project_id": "p-1", "path": r"D:\new"}], target))
        self.assertFalse(git_target_is_current([], target))

    def test_removed_target_cannot_be_authorized(self):
        live = {"project_id": "p-1", "path": r"C:\\repo"}
        snapshot = git_target_snapshot(live)
        self.assertFalse(git_target_is_authorized([], snapshot))

    def test_repository_marker_replacement_blocks_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            marker = root / ".git"
            marker.mkdir(parents=True)
            live = {"project_id": "p-1", "path": str(root)}
            snapshot = git_target_snapshot(live)
            self.assertTrue(main_module.git_mutation_target_is_authorized(
                [live], snapshot))
            marker.rename(root / ".git-old")
            marker.mkdir()
            self.assertFalse(main_module.git_mutation_target_is_authorized(
                [live], snapshot))


class GitMutationGuardTests(unittest.TestCase):
    def test_same_repository_is_serialized_and_release_reopens_it(self):
        guard = main_module.GitMutationGuard()
        key = ("C:/repo/.git", 1, 2)
        self.assertTrue(guard.acquire(key))
        self.assertFalse(guard.acquire(key))
        guard.release(key)
        self.assertTrue(guard.acquire(key))

    def test_upstream_parser_preserves_branch_slashes(self):
        self.assertEqual(main_module.parse_upstream("upstream/feature/x"),
                         ("upstream", "feature/x"))
        self.assertIsNone(main_module.parse_upstream("main"))


class ScanResultReconciliationTests(unittest.TestCase):
    def test_equivalent_path_variation_does_not_retain_duplicate_identity(self):
        live = [{"project_id": "live", "path": r"D:\Games\COLDLINE",
                 "name": "COLDLINE", "focus": "keep"}]
        merged = [
            dict(live[0]),
            {"project_id": "scan", "path": "d:/games/coldline/.",
             "name": "Scanner duplicate", "focus": "must not transfer"},
        ]

        result = reconcile_scan_result(merged, live)

        self.assertEqual(result, live)

    def test_distinct_canonical_paths_remain_distinct(self):
        live = [{"project_id": "repo", "path": r"D:\Repo",
                 "name": "Repo", "focus": "keep"}]
        merged = [dict(live[0]),
                  {"project_id": "repo-2", "path": r"D:\Repo-2",
                   "name": "Repo 2", "focus": "separate"}]

        result = reconcile_scan_result(merged, live)

        self.assertEqual({record["project_id"] for record in result},
                         {"repo", "repo-2"})

    def test_current_project_fields_survive_same_path_scan(self):
        live = [{"project_id": "p-1", "path": r"C:\repo",
                 "name": "Curated", "focus": "new", "future": 7,
                 "dirty": 0}]
        merged = [{"project_id": "p-1", "path": r"C:\repo",
                   "name": "Snapshot", "focus": "old", "future": 1,
                   "dirty": 3}]
        result = reconcile_scan_result(merged, live)
        self.assertEqual(result[0]["name"], "Curated")
        self.assertEqual(result[0]["focus"], "new")
        self.assertEqual(result[0]["future"], 7)
        self.assertEqual(result[0]["dirty"], 3)

    def test_reassociation_during_scan_keeps_live_identity_once(self):
        live = [{"project_id": "p-1", "path": r"D:\new",
                 "name": "Current", "focus": "keep"}]
        merged = [
            {"project_id": "p-1", "path": r"C:\old", "name": "Old"},
            {"project_id": "scan-new", "path": r"D:\new", "name": "New"},
        ]
        result = reconcile_scan_result(merged, live)
        self.assertEqual(result, live)

    def test_name_and_remote_similarity_do_not_transfer_stale_identity(self):
        live = [{"project_id": "old-ego", "path": r"C:\old\Ego Shooter",
                 "name": "Ego Shooter", "status": "active",
                 "focus": "old curation", "remote": "example/game"}]
        merged = [{"project_id": "new-coldline",
                   "path": r"D:\new\COLDLINE", "name": "COLDLINE",
                   "status": "idea", "focus": "",
                   "remote": "example/game"}]

        result = reconcile_scan_result(merged, live)
        by_id = {record["project_id"]: record for record in result}

        self.assertEqual(set(by_id), {"old-ego", "new-coldline"})
        self.assertEqual(by_id["old-ego"]["focus"], "old curation")
        self.assertEqual(by_id["old-ego"]["path"], r"C:\old\Ego Shooter")
        self.assertEqual(by_id["new-coldline"]["path"], r"D:\new\COLDLINE")

    def test_stale_scan_cannot_reactivate_newly_ignored_project(self):
        # The worker snapshot was captured before the current Project was
        # explicitly ignored, so its payload carries the old active state.
        live = [{"project_id": "p-1", "path": "repo",
                 "name": "Current", "status": "active", "ignored": True,
                 "focus": "keep", "pinned": True}]
        merged = [{"project_id": "p-1", "path": "repo",
                   "name": "Scanner snapshot", "status": "idea",
                   "ignored": False, "focus": "stale"}]

        result = reconcile_scan_result(merged, live)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["project_id"], "p-1")
        self.assertTrue(result[0]["ignored"])
        self.assertEqual(result[0]["status"], "active")
        self.assertEqual(result[0]["focus"], "keep")
        self.assertTrue(result[0]["pinned"])

    def test_stale_scan_cannot_reignore_explicitly_restored_project(self):
        live = [{"project_id": "p-1", "path": "repo",
                 "name": "Current", "status": "active", "ignored": False,
                 "focus": "keep", "pinned": True}]
        merged = [{"project_id": "p-1", "path": "repo",
                   "name": "Scanner snapshot", "status": "idea",
                   "ignored": True, "focus": "stale", "pinned": False}]

        result = reconcile_scan_result(merged, live)

        self.assertEqual(result, live)

    def test_different_project_ids_same_path_do_not_transfer_ignored_state(self):
        live = [{"project_id": "live-b", "path": r"C:\repo",
                 "name": "Live B", "status": "active", "ignored": True,
                 "focus": "B focus"}]
        merged = [{"project_id": "scan-a", "path": r"C:\repo",
                   "name": "Scan A", "status": "idea", "ignored": False,
                   "focus": "A focus"}]

        result = reconcile_scan_result(merged, live)

        self.assertEqual(result, live)

    def test_stale_ignored_identity_cannot_reignore_live_identity_at_same_path(self):
        live = [{"project_id": "live-b", "path": r"C:\repo",
                 "name": "Live B", "status": "active", "ignored": False,
                 "focus": "B focus", "pinned": True}]
        merged = [{"project_id": "scan-a", "path": r"C:\repo",
                   "name": "Scan A", "status": "idea", "ignored": True,
                   "focus": "A focus", "pinned": False}]

        result = reconcile_scan_result(merged, live)

        self.assertEqual(result, live)

    def test_idless_legacy_record_can_still_match_by_path(self):
        live = [{"path": r"C:\repo", "name": "Current",
                 "status": "active", "ignored": True}]
        merged = [{"project_id": "scan-a", "path": r"C:\repo",
                   "name": "Snapshot", "ignored": False}]

        result = reconcile_scan_result(merged, live)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Current")
        self.assertTrue(result[0]["ignored"])


class StatusLineTests(unittest.TestCase):
    class FakeVar:
        def __init__(self):
            self.value = None

        def set(self, v):
            self.value = v

    def test_important_resists_transient_overwrite(self):
        clock = {"t": 100.0}
        var = self.FakeVar()
        line = StatusLine(var, clock=lambda: clock["t"])
        line.set("moved: Iron", important=True)
        self.assertIn("moved", var.value)
        delivered = line.set("copied: something")  # inside hold window
        self.assertFalse(delivered)
        self.assertIn("moved", var.value)

    def test_hold_expires_and_transient_flows_again(self):
        clock = {"t": 0.0}
        var = self.FakeVar()
        line = StatusLine(var, clock=lambda: clock["t"], hold_ms=6000)
        line.set("failed: x", important=True)
        clock["t"] += 7.0
        ok = line.set("ready")
        self.assertTrue(ok)
        self.assertEqual(var.value, "ready")

    def test_important_replaces_important_immediately(self):
        var = self.FakeVar()
        line = StatusLine(var)
        line.set("first", important=True)
        line.set("second", important=True)
        self.assertEqual(var.value, "second")

    def test_transient_chain_unaffected_without_hold(self):
        var = self.FakeVar()
        line = StatusLine(var)
        for m in ("a", "b", "c"):
            self.assertTrue(line.set(m))
        self.assertEqual(var.value, "c")


class ThreadExcepthookTests(unittest.TestCase):
    def test_logs_critical_with_traceback(self):
        args = types.SimpleNamespace(
            exc_type=ValueError, exc_value=ValueError("x"),
            exc_traceback=None)
        try:
            raise ValueError("x")
        except ValueError:
            import sys
            args.exc_traceback = sys.exc_info()[2]
        with self.assertLogs("repomanager", level="CRITICAL") as captured:
            thread_excepthook(args)
        self.assertTrue(any("unhandled thread exception" in line
                            for line in captured.output))

    def test_system_exit_ignored(self):
        args = types.SimpleNamespace(exc_type=SystemExit, exc_value=None,
                                     exc_traceback=None)
        logging.disable(logging.CRITICAL)
        try:
            thread_excepthook(args)  # must not log or raise
        finally:
            logging.disable(logging.NOTSET)

    def test_hook_installed_by_setup_logging(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from repo_manager import store
        previous = main_module.threading.excepthook
        try:
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    with mock.patch.object(store, "APP_DIR",
                                           Path(tmp) / "app"):
                        main_module.setup_logging()
                    self.assertIs(main_module.threading.excepthook,
                                  main_module.thread_excepthook)
                finally:
                    root = logging.getLogger()
                    for h in list(root.handlers):
                        root.removeHandler(h)
                        h.close()
        finally:
            main_module.threading.excepthook = previous


class RecoveryHardeningTests(unittest.TestCase):
    """mutation failures must stop the chain and stay visible."""

    OLD_PATH = r"C:\Users\u\Desktop\Iron"
    NEW_PATH = r"C:\Users\u\Desktop\Projekte\Iron"
    NOW = "2026-08-26T12:00:00Z"

    @staticmethod
    def suggestion():
        return {"kind": "move", "category": "strong",
                "old_path": RecoveryHardeningTests.OLD_PATH,
                "new_path": RecoveryHardeningTests.NEW_PATH,
                "name": "Iron", "evidence": ["Same normalized remote"]}

    def _projects(self):
        return [{"path": self.OLD_PATH, "name": "Iron",
                 "status": "active", "focus": "district refactor",
                 "pinned": True, "added_at": "2026-01-01T00:00:00Z",
                 "last_seen": "2026-08-25T00:00:00Z"}]

    def test_failed_add_is_fatal_not_partial(self):
        """A failed `git add` must never degrade into a partial-success story."""
        outcome, message = git_step_outcome([
            ("add", 128, "error: bad path", None),
            ("commit", 0, "created", None)])
        self.assertEqual(outcome, GIT_FAILED)
        self.assertIn("add failed", message)

    def test_save_failure_after_note_migration_reports_rollback_failed(self):
        """save failure after the note moved is a failed rollback."""
        projects = self._projects()
        projects[0]["project_id"] = "project-1"
        snapshot = dict(projects[0])

        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=self._fail,
            move_note=lambda *a: "moved")

        self.assertEqual(outcome, MOVE_ROLLBACK_FAILED)
        self.assertEqual(projects[0], snapshot)  # in-memory entry restored
        self.assertEqual(entry, snapshot)

    def test_compensation_failure_after_note_failure_reports_rollback_failed(self):
        """an unconfirmed registry rollback must be reported as such."""
        projects = self._projects()
        snapshot = dict(projects[0])
        saves = []

        def save():
            saves.append(1)
            if len(saves) == 2:
                raise OSError("compensation save failed")

        outcome, _entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=save,
            move_note=lambda *a: (_ for _ in ()).throw(
                OSError("rename locked")))

        self.assertEqual(outcome, MOVE_ROLLBACK_FAILED)
        self.assertEqual(projects[0], snapshot)
        self.assertEqual(len(saves), 2)  # commit attempt + failed rollback

    def test_unchanged_note_save_failure_still_reports_registry_rollback(self):
        """When the note never moved, a save failure is a clean rollback."""
        projects = self._projects()
        projects[0]["project_id"] = "project-1"
        snapshot = dict(projects[0])

        outcome, _entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=self._fail,
            move_note=lambda *a: "note_failed")

        self.assertEqual(outcome, "rolled_back_registry")
        self.assertEqual(projects[0], snapshot)

    @staticmethod
    def _fail(*a, **k):
        raise OSError("injected failure")


if __name__ == "__main__":
    unittest.main()
