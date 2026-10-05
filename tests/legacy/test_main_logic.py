"""LEGACY / PARITY REFERENCE — NOT CURRENT UI PROOF."""
import inspect
import json
import logging
import os
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
                               is_visible, location_label, row_tag,
                               select_strong_suggestions,
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

    def test_local_only_repository_emphasizes_dirty_state(self):
        self.assertEqual(row_tag(proj("A", dirty=2, remote=None)), "dirty")
        self.assertEqual(row_tag(proj("A", dirty=0, remote=None)), "")

    def test_dirty_before_sync(self):
        self.assertEqual(row_tag(proj("A", dirty=1, ahead=2)), "dirty")

    def test_sync_only(self):
        self.assertEqual(row_tag(proj("A", behind=1)), "sync")








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
        # The V0.1.1 UI has no association picker; the validated
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
                mock.patch.object(main_module.scanner,
                                  "collect_metadata_observation",
                                  side_effect=lambda path: ({"path": path},
                                                            None)) as collect:
            app._refresh_metadata_async()

        kind, results, generation = app._scan_queue.get_nowait()
        self.assertEqual(kind, "meta")
        self.assertEqual(generation, 1)
        self.assertEqual(results[0][1], r"C:\repos\git")
        self.assertEqual(results[0][2], {"path": r"C:\repos\git"})
        collect.assert_called_once_with(r"C:\repos\git")

    def test_metadata_pool_does_not_hold_process_open_after_gui_worker_exit(self):
        # The child asserts its own dispatch, so returncode 0 means the pool
        # released the interpreter. The outer timeout is only a deadlock
        # watchdog, not a performance budget: a healthy exit needs well under a
        # second, while a pool that wrongly held the process open would keep
        # the child alive for the full sleep below. Interpreter startup and
        # scheduler latency are not what this test measures, so the bound is
        # deliberately generous yet stays under that sleep, preserving the
        # signal on a loaded machine.
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
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, timeout=20,
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




class GitMutationGuardTests(unittest.TestCase):

    def test_upstream_parser_preserves_branch_slashes(self):
        self.assertEqual(main_module.parse_upstream("upstream/feature/x"),
                         ("upstream", "feature/x"))
        self.assertIsNone(main_module.parse_upstream("main"))




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




if __name__ == "__main__":
    unittest.main()


class ClassicGitStepRegressionTests(unittest.TestCase):
    def test_failed_add_is_fatal_not_partial(self):
        """A failed `git add` must never degrade into a partial-success story."""
        outcome, message = git_step_outcome([
            ("add", 128, "error: bad path", None),
            ("commit", 0, "created", None)])
        self.assertEqual(outcome, GIT_FAILED)
        self.assertIn("add failed", message)
