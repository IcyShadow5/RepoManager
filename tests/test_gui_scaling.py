"""Tests for large-dataset UI updates, caching, move matching, and persistence.

Tk-dependent cases are guarded so the suite also runs without a display;
pure planning and caching logic is tested without a widget.
"""
import copy
import json
import os
import queue
import random
import sys
import tempfile
import threading
import time
import unittest
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from unittest import mock

from repo_manager import main as main_module
from repo_manager import health as health_module
from repo_manager import providers, scanner, store, theme, workspaces
from repo_manager.main import (AvailabilityCache, reconcile_plan,
                               reconcile_tree, is_visible, sorted_projects)


class _IsolatedStorePaths:
    r"""Redirect the real app-data store paths to a temporary directory.

    GUI tests build a real ``RepoManagerApp`` whose actions (sorting, theme
    toggling, settings edits) persist through `store`, which by default
    writes to the real ``%LOCALAPPDATA%\RepoManager`` directory. Redirecting
    module-level paths directs settings, registry, and note writes through
    this temporary store. Tests must keep these paths redirected until their
    persistence actions finish.
    """

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name) / "app"
        self._originals = (store.APP_DIR, store.REPOS_FILE,
                           store.SETTINGS_FILE, store.NOTES_DIR)
        store.APP_DIR = base
        store.REPOS_FILE = base / "repos.json"
        store.SETTINGS_FILE = base / "settings.json"
        store.NOTES_DIR = base / "notes"
        store.ensure_dirs()

    def close(self):
        store.APP_DIR, store.REPOS_FILE, store.SETTINGS_FILE, \
            store.NOTES_DIR = self._originals
        self._tmp.cleanup()

    @property
    def app_dir(self):
        return Path(self._tmp.name) / "app"


class _StoreIsolationMixin:
    """Redirect store paths during each GUI test and restore them on cleanup."""

    def setUp(self):
        self._store_iso = _IsolatedStorePaths()
        self.addCleanup(self._store_iso.close)


def _tk_available():
    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except (tk.TclError, OSError, AttributeError):
        return False


TK_AVAILABLE = _tk_available()


def _mk_project(i, prefix="repo"):
    return {"path": rf"C:\repos\{prefix}{i:05d}", "name": f"{prefix}{i:05d}",
            "status": "active" if i % 5 == 0 else "idea", "focus": "", "pinned": i % 5 == 0,
            "dirty": i % 3, "ahead": i % 4, "behind": 0,
            "remote": f"github.com/o/{prefix}{i}", "branch": "main",
            "last_commit_date": "2026-08-01", "last_commit_msg": "x"}


class ShutdownCallbackSafetyTests(unittest.TestCase):
    """Deferred callbacks become inert once shutdown starts."""

    def _app(self):
        app = object.__new__(main_module.RepoManagerApp)
        app._closing = False
        app._after_jobs = set()
        app._tip_job = None
        app._filter_job = None
        app._save_job = None
        app._note_save_job = None
        app._callbacks = []
        app._note_target = None
        app._drained = 0
        app.after = lambda _delay, callback: (app._callbacks.append(callback),
                                               f"job-{len(app._callbacks)}")[1]
        app.after_idle = lambda callback: (app._callbacks.append(callback),
                                            f"idle-{len(app._callbacks)}")[1]
        app.after_cancel = lambda _job: None
        return app

    def test_callbacks_are_tracked_and_inert_after_shutdown(self):
        app = self._app()
        app._schedule_after(1, lambda: (_ for _ in ()).throw(
            AssertionError("callback ran after shutdown")))
        app._schedule_after_idle(lambda: (_ for _ in ()).throw(
            AssertionError("idle callback ran after shutdown")))
        self.assertEqual(len(app._after_jobs), 2)
        app._closing = True
        callbacks = list(app._callbacks)
        app._cancel_after_jobs()
        self.assertEqual(app._after_jobs, set())
        for callback in callbacks:
            callback()

    def test_late_queue_results_are_discarded_after_shutdown(self):
        app = self._app()
        app._closing = True
        app._scan_queue = queue.Queue()
        app._scan_queue.put(("meta", [{"path": r"C:\\repo", "dirty": 9}]))
        app._scan_queue.put(("provider", r"C:\\repo", object(), 1))
        with mock.patch.object(main_module.store, "save_projects") as save, \
                mock.patch.object(app, "destroy") as destroy:
            app._drain_scan_queue()
        self.assertTrue(app._scan_queue.empty())
        save.assert_not_called()
        destroy.assert_not_called()

    def test_late_error_event_is_discarded_after_shutdown(self):
        app = self._app()
        app._closing = True
        app._scan_queue = queue.Queue()
        app._scan_queue.put(("error", "late worker failure"))
        with mock.patch("tkinter.messagebox.showerror") as shown:
            app._drain_scan_queue()
        shown.assert_not_called()
        self.assertTrue(app._scan_queue.empty())

    def test_specialized_cancellation_is_idempotent_when_job_is_unavailable(self):
        app = self._app()
        app._tip_job = "missing-tip"
        app._filter_job = "missing-filter"
        app._save_job = "missing-save"
        app._note_save_job = "missing-note"
        app.after_cancel = lambda _job: (_ for _ in ()).throw(tk.TclError("gone"))
        app._cancel_tip_job()
        app._debounce_filter()
        app._cancel_project_save()
        app._flush_note_save()
        self.assertIsNone(app._tip_job)
        self.assertIsNotNone(app._filter_job)
        self.assertIsNone(app._save_job)
        self.assertIsNone(app._note_save_job)


class ReconcilePlanTests(unittest.TestCase):
    """Pure ordering/insert/delete planning — Tk-free, the core of reuse."""

    def test_unchanged_reuses_all_rows(self):
        self.assertEqual(reconcile_plan(["a", "b", "c"], ["a", "b", "c"]),
                         ([], []))

    def test_removed_row_flagged_only(self):
        self.assertEqual(reconcile_plan(["a", "b", "c"], ["a", "b"]),
                         (["c"], []))

    def test_new_row_flagged_only(self):
        self.assertEqual(reconcile_plan(["a", "b"], ["a", "b", "c"]),
                         ([], ["c"]))

    def test_remove_and_insert(self):
        self.assertEqual(reconcile_plan(["a", "b"], ["b", "c"]),
                         (["a"], ["c"]))

    def test_pure_reorder_is_no_op_for_plan(self):
        # Reordering reuses rows; only the move step realigns them.
        self.assertEqual(reconcile_plan(["a", "b"], ["b", "a"]), ([], []))


class AvailabilityCacheTests(unittest.TestCase):
    def test_stats_once_per_path_until_invalidate(self):
        seen = []

        def sampler(path):
            seen.append(str(path))
            return True

        cache = AvailabilityCache(sampler=sampler)
        self.assertTrue(cache.get(r"C:\a"))
        self.assertTrue(cache.get(r"C:\a"))
        self.assertTrue(cache.get(r"C:\b"))
        # each distinct path statted exactly once
        self.assertEqual(seen, [r"C:\a", r"C:\b"])
        cache.invalidate()
        self.assertTrue(cache.get(r"C:\a"))
        self.assertTrue(cache.get(r"C:\a"))
        self.assertEqual(seen.count(r"C:\a"), 2)  # re-statted once after reset

    def test_false_availability_cached(self):
        seen = []

        def sampler(path):
            seen.append(path)
            return False

        cache = AvailabilityCache(sampler=sampler)
        self.assertFalse(cache.get(r"C:\gone"))
        self.assertFalse(cache.get(r"C:\gone"))
        self.assertEqual(len(seen), 1)

    def test_real_isdir_lookups_return_consistent_booleans(self):
        cache = AvailabilityCache()
        p = _mk_project(1)
        self.assertIsInstance(cache.get(p["path"]), bool)
        # Repeated lookups remain consistent and do not raise.
        self.assertEqual(cache.get(p["path"]), cache.get(p["path"]))


class PersistProjectIgnoreTests(_StoreIsolationMixin, unittest.TestCase):
    def test_persists_only_stable_target_and_preserves_files_note_and_curation(self):
        repository = self._store_iso.app_dir / "repository"
        repository.mkdir()
        source = repository / "keep.txt"
        source.write_bytes(b"repository-content")
        target = {
            "project_id": "target-id", "path": str(repository),
            "name": "Same", "status": "archived", "focus": "keep",
            "pinned": True,
        }
        other = {
            "project_id": "other-id", "path": str(repository) + "-other",
            "name": "Same", "status": "active",
        }
        store.save_note(target["name"], target["path"], "note-body",
                        target["project_id"])

        applied = main_module.persist_project_ignore(
            [target, other], "target-id", store.save_projects)
        loaded = store.load_projects()

        self.assertIs(applied, target)
        self.assertTrue(target["ignored"])
        self.assertNotIn("ignored", other)
        persisted = {item["project_id"]: item for item in loaded}
        self.assertTrue(persisted["target-id"]["ignored"])
        self.assertEqual(persisted["target-id"]["status"], "archived")
        self.assertEqual(persisted["target-id"]["focus"], "keep")
        self.assertTrue(persisted["target-id"]["pinned"])
        self.assertEqual(source.read_bytes(), b"repository-content")
        self.assertEqual(
            store.load_note(target["name"], target["path"], "target-id"),
            "note-body")

    def test_save_failure_restores_exact_prior_ignored_state(self):
        for prior in (None, False):
            with self.subTest(prior=prior):
                project = {"project_id": "target-id", "path": "target",
                           "name": "Target"}
                if prior is not None:
                    project["ignored"] = prior

                with self.assertRaises(OSError):
                    main_module.persist_project_ignore(
                        [project], "target-id",
                        lambda _records: (_ for _ in ()).throw(
                            OSError("disk full")))

                if prior is None:
                    self.assertNotIn("ignored", project)
                else:
                    self.assertIs(project["ignored"], False)

    def test_missing_stable_identity_never_falls_back_to_name_or_path(self):
        replacement = {"project_id": "replacement-id", "path": "same",
                       "name": "Same"}
        saved = []

        applied = main_module.persist_project_ignore(
            [replacement], "vanished-id", saved.append)

        self.assertIsNone(applied)
        self.assertEqual(saved, [])
        self.assertNotIn("ignored", replacement)


class PersistProjectRestoreTests(_StoreIsolationMixin, unittest.TestCase):
    def test_persists_stable_target_and_preserves_files_note_and_metadata(self):
        repository = self._store_iso.app_dir / "repository"
        repository.mkdir()
        source = repository / "keep.txt"
        source.write_bytes(b"repository-content")
        target = {
            "project_id": "target-id", "path": str(repository),
            "name": "Same", "status": "archived", "focus": "keep",
            "pinned": True, "custom_metadata": {"owner": "user"},
            "ignored": True,
        }
        other = {
            "project_id": "other-id", "path": str(repository) + "-other",
            "name": "Same", "status": "active", "ignored": True,
        }
        store.save_note(target["name"], target["path"], "note-body",
                        target["project_id"])

        applied = main_module.persist_project_restore(
            [target, other], "target-id", store.save_projects)
        loaded = store.load_projects()

        self.assertIs(applied, target)
        self.assertIs(target["ignored"], False)
        self.assertTrue(other["ignored"])
        persisted = {item["project_id"]: item for item in loaded}
        self.assertIs(persisted["target-id"]["ignored"], False)
        self.assertEqual(persisted["target-id"]["status"], "archived")
        self.assertEqual(persisted["target-id"]["focus"], "keep")
        self.assertTrue(persisted["target-id"]["pinned"])
        self.assertEqual(persisted["target-id"]["custom_metadata"],
                         {"owner": "user"})
        self.assertEqual(source.read_bytes(), b"repository-content")
        self.assertEqual(
            store.load_note(target["name"], target["path"], "target-id"),
            "note-body")

    def test_save_failure_restores_ignored_state(self):
        project = {"project_id": "target-id", "path": "target",
                   "name": "Target", "ignored": True}

        with self.assertRaises(OSError):
            main_module.persist_project_restore(
                [project], "target-id",
                lambda _records: (_ for _ in ()).throw(OSError("disk full")))

        self.assertTrue(project["ignored"])

    def test_missing_stable_identity_never_falls_back_to_name_or_path(self):
        replacement = {"project_id": "replacement-id", "path": "same",
                       "name": "Same", "ignored": True}
        saved = []

        applied = main_module.persist_project_restore(
            [replacement], "vanished-id", saved.append)

        self.assertIsNone(applied)
        self.assertEqual(saved, [])
        self.assertTrue(replacement["ignored"])


class LargeDatasetToolsTests(unittest.TestCase):
    def test_filter_sort_large_dataset_deterministic(self):
        projects = [_mk_project(i) for i in range(10000)]
        visible = [p for p in projects if is_visible(p, "")]
        self.assertEqual(len(visible), 10000)
        ordered = sorted_projects(visible, "name", False)
        names = [p["name"] for p in ordered]
        self.assertEqual(names, sorted(names))
        subset = [p for p in projects if is_visible(p, "repo0")]
        self.assertTrue(len(subset) > 0)
        self.assertTrue(all("repo0" in p["name"] for p in subset))


class PersistenceDebounceTests(unittest.TestCase):
    """Full-registry writes are deferred/coalesced, never lost on flush."""

    def _app(self):
        app = object.__new__(main_module.RepoManagerApp)
        app.projects = [{"path": "x", "name": "X"}]
        app._save_job = None
        app._after_fn = None
        app.after_cancel = lambda job: setattr(app, "_after_fn", None)
        app.after = lambda ms, fn: (setattr(app, "_after_fn", fn), object())[1]
        return app

    def test_schedule_defers_write_until_flush(self):
        app = self._app()
        with mock.patch("repo_manager.store.save_projects") as save:
            app._schedule_project_save()
            save.assert_not_called()
            self.assertIsNotNone(app._after_fn)
            app._after_fn()  # debounced callback fires
            save.assert_called_once_with(app.projects)

    def test_rapid_edits_coalesce_to_one_write(self):
        app = self._app()
        with mock.patch("repo_manager.store.save_projects") as save:
            app._schedule_project_save()
            app._schedule_project_save()
            app._schedule_project_save()
            app._flush_project_save()
            save.assert_called_once_with(app.projects)

    def test_flush_cancels_pending_and_saves(self):
        app = self._app()
        with mock.patch("repo_manager.store.save_projects") as save:
            app._schedule_project_save()
            self.assertIsNotNone(app._after_fn)
            app._flush_project_save()
            save.assert_called_once_with(app.projects)
            self.assertIsNone(app._save_job)


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class ReconcileTreeGUITests(unittest.TestCase):
    """Real Treeview behavior: rows are reused, inserted, removed, reordered."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.tree = ttk.Treeview(self.root, columns=("c",), show="headings")
        self.tree.pack()

    def tearDown(self):
        self.root.destroy()

    def _apply(self, desired):
        reconcile_tree(self.tree, desired,
                       lambda iid: {"values": (iid.upper(),)})

    def test_builds_from_empty(self):
        self._apply(["a", "b", "c"])
        self.assertEqual(list(self.tree.get_children()), ["a", "b", "c"])

    def test_unchanged_rebuild_is_idempotent(self):
        self._apply(["a", "b", "c"])
        iids_before = list(self.tree.get_children())
        self._apply(["a", "b", "c"])  # no delete/recreate
        self.assertEqual(list(self.tree.get_children()), iids_before)

    def test_adds_new_rows_only(self):
        self._apply(["a", "b"])
        self._apply(["a", "b", "c", "d"])
        self.assertEqual(list(self.tree.get_children()),
                         ["a", "b", "c", "d"])

    def test_removes_stale_rows(self):
        self._apply(["a", "b", "c"])
        self._apply(["a", "c"])
        self.assertEqual(list(self.tree.get_children()), ["a", "c"])

    def test_reorders_rows(self):
        self._apply(["a", "b", "c"])
        self._apply(["c", "a", "b"])
        self.assertEqual(list(self.tree.get_children()), ["c", "a", "b"])

    def test_updates_row_values(self):
        self._apply(["a"])
        self.assertEqual(self.tree.set("a", "c"), "A")
        # refresh with new value reuses the same row
        self._apply(["a"])
        self.assertEqual(self.tree.set("a", "c"), "A")


class MoveMatcherBruteForceEquivalenceTests(unittest.TestCase):
    """The indexed matcher must produce byte-identical results to the original
    brute-force scan across representative large stale/fresh combinations."""

    @staticmethod
    def stale(path, remote=None, remotes=None, roots=None):
        entry = {"path": path, "name": os.path.basename(path), "status": "idea",
                 "focus": "", "pinned": False}
        if remote:
            entry["remote"] = remote
        fp = {}
        if remotes is not None:
            fp["remotes"] = remotes
        if roots is not None:
            fp["root_commits"] = roots
        if fp:
            entry["fingerprint"] = fp
        return entry

    @staticmethod
    def bruteforce(stale_entries, fresh_items, suppressed=None):
        """Reference implementation using every stale/fresh pair."""

        def evidence_for(entry):
            fp = entry.get("fingerprint") if isinstance(entry, dict) else None
            remotes = set()
            if isinstance(fp, dict):
                remotes = {r.lower() for r in fp.get("remotes", [])
                           if isinstance(r, str)}
            if not remotes and isinstance(entry.get("remote"), str):
                remotes = {entry["remote"].lower()}
            roots = set()
            if isinstance(fp, dict):
                roots = {r for r in fp.get("root_commits", [])
                         if isinstance(r, str)}
            return remotes, roots

        def fresh_evidence(fingerprint):
            if not isinstance(fingerprint, dict):
                return set(), set()
            remotes = {r.lower() for r in fingerprint.get("remotes", [])
                       if isinstance(r, str)}
            return remotes, {r for r in fingerprint.get("root_commits", [])
                             if isinstance(r, str)}

        supp_set = set()
        for s in suppressed or []:
            if isinstance(s, dict):
                supp_set.add((str(s.get("old", "")).lower(),
                              str(s.get("new", "")).lower()))
            else:
                o, n = s
                supp_set.add((str(o).lower(), str(n).lower()))

        pairs = []
        for entry in sorted(stale_entries,
                            key=lambda e: str(e.get("path", "")).lower()):
            old_path = entry.get("path")
            if not isinstance(old_path, str):
                continue
            old_remotes, old_roots = evidence_for(entry)
            old_folder = os.path.basename(str(old_path)).lower()
            for new_path, fp in sorted(fresh_items,
                                       key=lambda x: x[0].lower()):
                if (old_path.lower(), new_path.lower()) in supp_set:
                    continue
                new_remotes, new_roots = fresh_evidence(fp)
                evidence = []
                shared_remote = bool(old_remotes & new_remotes)
                shared_roots = old_roots & new_roots
                same_folder = old_folder == os.path.basename(new_path).lower()
                folder_eq = same_folder and len(old_folder) >= 5
                if shared_remote:
                    evidence.append("Same normalized remote: "
                                    + sorted(old_remotes & new_remotes)[0])
                if shared_roots:
                    evidence.append(f"Root history matches ({len(shared_roots)})")
                if folder_eq:
                    evidence.append("Folder name matches")
                if not evidence:
                    continue
                if shared_roots or (shared_remote and same_folder):
                    category = "strong"
                else:
                    category = "possible"
                pairs.append({"kind": "move", "category": category,
                              "old_path": old_path,
                              "new_path": new_path,
                              "name": entry.get("name")
                              or os.path.basename(old_path),
                              "evidence": evidence,
                              "identity": {
                                  "remotes": sorted(new_remotes),
                                  "root_commits": sorted(new_roots),
                              }})

        by_old, by_new = {}, {}
        for p in pairs:
            by_old.setdefault(p["old_path"].lower(), []).append(p)
            by_new.setdefault(p["new_path"].lower(), []).append(p)
        contested = {id(p) for p in pairs
                     if len(by_old[p["old_path"].lower()]) > 1
                     or len(by_new[p["new_path"].lower()]) > 1}
        final, emitted = [], set()
        for p in pairs:
            if id(p) not in contested:
                final.append(p)
                continue
            ok = p["old_path"].lower()
            nk = p["new_path"].lower()
            gkey = ("old", ok) if len(by_old[ok]) > 1 else ("new", nk)
            if gkey in emitted:
                continue
            emitted.add(gkey)
            if gkey[0] == "old":
                members = by_old[ok]
                final.append({"kind": "move", "category": "ambiguous",
                              "old_path": p["old_path"],
                              "new_paths": [m["new_path"] for m in members],
                              "name": p["name"],
                              "evidence": ["Multiple candidate locations"]})
            else:
                members = by_new[nk]
                final.append({"kind": "move", "category": "ambiguous",
                              "old_paths": [m["old_path"] for m in members],
                              "new_path": p["new_path"],
                              "name": os.path.basename(p["new_path"]),
                              "evidence": ["Matches multiple vanished entries"]})
        return sorted(final, key=lambda s: (
            str(s.get("old_path") or s.get("old_paths")[0]).lower(),
            str(s.get("new_path") or "").lower()))

    def test_distinct_remote_dataset(self):
        rnd = random.Random(1)
        stale = [self.stale(rf"C:\old\p{i}", remote=f"g.com/o/p{i}",
                            roots=[f"root{i}"]) for i in range(120)]
        fresh = [(rf"C:\new\m{i}",
                  {"remotes": [f"g.com/o/m{i}"],
                   "root_commits": [f"root{i}"]}) for i in range(300)]
        # embellish a few to create shared-remote/root and folder overlaps
        for i in rnd.sample(range(min(len(fresh), 60)), 60):
            j = rnd.randrange(len(stale))
            fresh[i] = (fresh[i][0], {"remotes": [f"g.com/o/p{j}"],
                                      "root_commits": [f"root{j}"]})
        self.assertEqual(scanner.match_move_candidates(stale, fresh),
                         self.bruteforce(stale, fresh))

    def test_folder_and_suppressed_dataset(self):
        rnd = random.Random(2)
        stale = [self.stale(rf"C:\old\widget-toolkit{i}")
                 for i in range(80)]
        fresh = [(rf"C:\new\widget-toolkit{i}", None) for i in range(200)]
        suppressed = [(rf"c:\old\widget-toolkit{i}",) and
                      (rf"c:\old\widget-toolkit{i}", rf"c:\new\widget-toolkit{i}")
                      for i in rnd.sample(range(80), 20)]
        self.assertEqual(
            scanner.match_move_candidates(stale, fresh, suppressed),
            self.bruteforce(stale, fresh, suppressed))

    def test_shared_remote_hub_dataset(self):
        # many stale and many fresh all sharing a single org remote + roots
        stale = [self.stale(rf"C:\old\p{i}", remote="g.com/org/shared",
                            remotes=["g.com/org/shared"], roots=["rr"])
                 for i in range(100)]
        fresh = [(rf"C:\new\m{i}", {"remotes": ["g.com/org/shared"],
                                    "root_commits": ["rr"]})
                 for i in range(150)]
        self.assertEqual(scanner.match_move_candidates(stale, fresh),
                         self.bruteforce(stale, fresh))

    def test_empty_and_disjoint(self):
        self.assertEqual(scanner.match_move_candidates([], []),
                         self.bruteforce([], []))
        stale = [self.stale(r"C:\x\A", remote="g.com/a")]
        fresh = [(r"C:\y\B", {"remotes": ["g.com/b"], "root_commits": ["q"]})]
        self.assertEqual(scanner.match_move_candidates(stale, fresh),
                         self.bruteforce(stale, fresh))


def _build_real_app(n=40):
    """Build a real RepoManagerApp (real Tk + real UI) with `n` projects.

    Isolates from real user data: settings come from the module defaults and
    no registry write is performed unless a test explicitly saves. Availability
    is cached and never stats the (non-existent) fixture paths on disk.
    """
    import copy
    import queue

    app = object.__new__(main_module.RepoManagerApp)
    tk.Tk.__init__(app)
    app.title("test")
    app.geometry("1200x700")
    app.deiconify()
    app.settings = copy.deepcopy(store.DEFAULT_SETTINGS)
    app.settings["roots"] = []
    app.projects = [_mk_project(i) for i in range(n)]
    app.workspaces = []
    app.agent = main_module.agents.new_agent(
        "configured", "Configured agent", app.settings["agent_cmd"])
    app.runs = []
    app._agent_processes = {}
    app._close_after_agents = False
    app._scan_queue = queue.Queue()
    app._scanning = False
    app._detail_gen = 0
    app._detail_lock = threading.Lock()
    app._detail_pending = None
    app._detail_worker = None
    app._problems = []
    app._move_suggestions = []
    app._scan_gen = 0
    app._moved_away = []
    app._current = None
    app._loading_detail = False
    app._health_result = None
    app._note_target = None
    app._note_save_job = None
    app._filter_job = None
    app._column_width_save_job = None
    app._tip = None
    app._tip_job = None
    app._tip_row = None
    app._closing = False
    app._after_jobs = set()
    # fixture paths never exist on disk; treat them as available so the
    # Working-on-now tree (which filters on availability) is populated and no
    # filesystem stat is performed during tests
    app._avail = main_module.AvailabilityCache(sampler=lambda _p: True)
    app._save_job = None
    app._populate_pending = False
    app._active_tree = "main"
    app._sort_col = None
    app._sort_desc = False
    app.pal = theme.apply(app, app.settings.get("theme", "dark"))
    app._build_ui()
    app._apply_row_colors()
    app._update_heading_marks()
    app._populate_trees()
    app.update()
    return app


def _pump_until(app, predicate, timeout=5.0):
    """Pump Tk and the shared worker queue until a deterministic state holds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.update()
        app._drain_scan_queue()
        app.update()
        if predicate():
            return True
        time.sleep(0.01)
    app.update()
    app._drain_scan_queue()
    return bool(predicate())


class _Event:
    def __init__(self, widget, y, x_root, y_root):
        self.widget = widget
        self.y = y
        self.x_root = x_root
        self.y_root = y_root


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class CustomLauncherDialogGuiTests(_StoreIsolationMixin, unittest.TestCase):
    def _dialog_parts(self, app, project):
        dialog = app._edit_custom_launcher(project)
        app.update_idletasks()
        form = next(widget for widget in dialog.winfo_children()
                    if isinstance(widget, ttk.Frame))
        fields = {}
        for widget in form.winfo_children():
            info = widget.grid_info()
            if str(info.get("column")) == "1":
                row = str(info.get("row"))
                if row in {"0", "1", "2", "4"}:
                    fields[row] = widget
        error = next(widget for widget in form.winfo_children()
                     if isinstance(widget, ttk.Label)
                     and widget.cget("style") == "Muted.TLabel"
                     and widget.cget("text") == "")
        actions = next(widget for widget in form.winfo_children()
                       if str(widget.grid_info().get("row")) == "6")
        buttons = {button.cget("text"): button
                   for button in actions.winfo_children()
                   if isinstance(button, ttk.Button)}
        return dialog, fields, error, buttons

    def test_dialog_starts_without_visible_error_surface(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        dialog, _fields, error, _buttons = self._dialog_parts(app, project)
        self.addCleanup(dialog.destroy)
        self.assertFalse(error.winfo_ismapped())
        self.assertEqual(error.cget("text"), "")
        self.assertEqual(error.cget("style"), "Muted.TLabel")

    def test_invalid_submission_shows_actionable_feedback(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        dialog, _fields, error, buttons = self._dialog_parts(app, project)
        self.addCleanup(dialog.destroy)

        buttons["Save"].invoke()
        app.update_idletasks()
        self.assertTrue(error.winfo_ismapped())
        self.assertIn("required", error.cget("text"))
        self.assertEqual(error.cget("style"), "Semantic.Error.TLabel")

    def test_valid_submission_persists_one_custom_launcher(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        project["project_id"] = "test-project"
        dialog, fields, _error, buttons = self._dialog_parts(app, project)
        self.addCleanup(dialog.destroy)
        fields["0"].insert(0, "Tool")
        fields["1"].insert(0, "python")
        fields["2"].insert("1.0", "-V")
        fields["4"].insert(0, tempfile.gettempdir())

        with mock.patch.object(main_module.store, "save_projects") as save, \
                mock.patch.object(app, "_populate_launchers"):
            buttons["Save"].invoke()

        save.assert_called_once()
        self.assertEqual(project["custom_launchers"][0]["name"], "Tool")
        self.assertFalse(dialog.winfo_exists())

    def test_cancel_does_not_mutate_project(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        before = copy.deepcopy(project)
        dialog, fields, _error, buttons = self._dialog_parts(app, project)
        fields["0"].insert(0, "Not saved")
        buttons["Cancel"].invoke()
        self.assertEqual(project, before)
        self.assertFalse(dialog.winfo_exists())


class TkCallbackReportingTests(unittest.TestCase):
    def test_reporter_accepts_tk_bound_method_arguments(self):
        error = RuntimeError("callback failed")
        with mock.patch.object(main_module.log, "critical") as critical, \
                mock.patch("tkinter.messagebox.showerror") as shown:
            main_module._report_tk_callback_exception(
                object(), RuntimeError, error, None)
        critical.assert_called_once_with(
            "Tk callback exception", exc_info=(RuntimeError, error, None))
        shown.assert_called_once()


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class NoteSelectionIntegrityTests(_StoreIsolationMixin, unittest.TestCase):
    def test_a_b_a_selection_never_writes_b_text_to_a(self):
        app = _build_real_app(2)
        self.addCleanup(app.destroy)
        a, b = app.projects
        a["project_id"], b["project_id"] = "project-a", "project-b"
        a["remote"] = b["remote"] = None
        store.save_note(b["name"], b["path"], "B note", b["project_id"])

        app._show_detail(main_module.project_row_id(a))
        app.d_notes.insert("1.0", "A note")
        app._schedule_note_save()
        app._show_detail(main_module.project_row_id(b))
        app._show_detail(main_module.project_row_id(a))

        self.assertEqual(
            store.load_note(a["name"], a["path"], a["project_id"]),
            "A note")

    def test_late_note_load_does_not_replace_same_selection_edit(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        project["project_id"] = "project-a"
        entered = threading.Event()
        release = threading.Event()

        def delayed_observation(_project, _settings):
            entered.set()
            release.wait(5)
            return {"health": None, "launchers": (), "note": "old note"}

        with mock.patch.object(main_module, "compute_detail_observation",
                               side_effect=delayed_observation):
            app._show_detail(main_module.project_row_id(project))
            self.assertTrue(entered.wait(2))
            app.d_notes.insert("1.0", "new typed text")
            app._schedule_note_save()
            release.set()
            self.assertTrue(_pump_until(
                app, lambda: app._note_loading_gen is None))

        self.assertEqual(app.d_notes.get("1.0", "end-1c"), "new typed text")

    def test_note_load_failure_does_not_leave_other_detail_surfaces_loading(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        result = main_module.health.HealthResult(main_module.health.PASS, (), "now")
        with mock.patch.object(main_module.health, "evaluate_repository",
                               return_value=result), \
                mock.patch.object(main_module.store, "load_note",
                                  side_effect=OSError("notes unavailable")):
            app._show_detail(main_module.project_row_id(app.projects[0]))
            self.assertTrue(_pump_until(
                app, lambda: "Loading" not in " ".join(
                    widget.cget("text") for widget in app.d_launch.winfo_children()
                    if "text" in widget.keys())))

        self.assertIs(app._health_result, result)
        self.assertNotIn("Loading", " ".join(
            widget.cget("text") for widget in app.d_launch.winfo_children()
            if "text" in widget.keys()))

    def test_detail_worker_start_failure_leaves_unknown_state(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        worker = mock.Mock()
        worker.is_alive.return_value = False
        worker.start.side_effect = RuntimeError("threads unavailable")
        with mock.patch.object(main_module.threading, "Thread",
                               return_value=worker):
            app._show_detail(main_module.project_row_id(app.projects[0]))

        self.assertIn("unknown", app.d_health_status.cget("text").casefold())
        self.assertIsNone(app._note_loading_gen)


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class ClosePersistenceSafetyTests(_StoreIsolationMixin, unittest.TestCase):
    def test_save_failure_keeps_window_open_and_allows_retry(self):
        app = _build_real_app(1)
        try:
            with mock.patch.object(app, "_flush_note_save",
                                   side_effect=OSError("disk full")), \
                    mock.patch.object(app, "destroy") as destroy, \
                    mock.patch("tkinter.messagebox.showerror") as shown:
                app._on_close()
            self.assertFalse(app._closing)
            destroy.assert_not_called()
            shown.assert_called_once()
        finally:
            app.destroy()

@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class TooltipStartupRegressionTests(_StoreIsolationMixin, unittest.TestCase):
    def test_startup_initializes_tip_row_before_motion_callback(self):
        with mock.patch.object(main_module.RepoManagerApp,
                               "_schedule_after", return_value=None), \
                mock.patch.object(main_module.RepoManagerApp, "start_scan"):
            app = main_module.RepoManagerApp()
        self.addCleanup(app.destroy)
        self.assertIsNone(app._tip_row)
        app._on_tree_motion(_Event(app.tree, -1, 0, 0))
        self.assertEqual(app._tip_row, "")


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class FolderOnlyGuiRegressionTests(_StoreIsolationMixin, unittest.TestCase):
    def test_folder_only_selection_updates_detail_without_stale_repository(self):
        app = _build_real_app(2)
        self.addCleanup(app.destroy)
        folder = app.projects[1]
        folder_path = folder.pop("path")
        folder["folder_path"] = folder_path
        folder.pop("broken", None)
        app._populate_trees()
        app.tree.selection_set(folder_path)
        app.update()

        self.assertIs(app._current, folder)
        self.assertEqual(app.d_name.cget("text"), folder["name"])
        self.assertEqual(app.d_path.cget("text"), folder_path)
        self.assertEqual(app.d_repository.cget("text"), "Repository: None")
        self.assertIn("Non Git Location", app.d_classification.cget("text"))

    def test_folder_only_enter_and_git_actions_are_contained(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        folder = app.projects[0]
        folder_path = folder.pop("path")
        folder["folder_path"] = folder_path
        app._populate_trees()
        app.tree.selection_set(folder_path)
        app.tree.focus_set()
        app.update()

        with mock.patch.object(app, "_run_launcher") as run_launcher, \
                mock.patch.object(app, "_git_async") as git_async:
            self.assertEqual(app._launch_primary(_Event(app.tree, 0, 0, 0)),
                             "break")
            app.git_pull()
            app.git_commit_push()

        run_launcher.assert_not_called()
        git_async.assert_not_called()
        self.assertIn("associated Git repository", app.status_var.get())


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class ProjectDisplayNameGuiRegressionTests(_StoreIsolationMixin,
                                           unittest.TestCase):
    def test_legacy_repository_row_and_detail_use_parent_project_name(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        project["path"] = r"C:\Projects\PocketLedger\repository"
        project["name"] = "repository"

        app._populate_trees()
        row_id = main_module.project_row_id(project)
        app.tree.selection_set(row_id)
        app.update()

        self.assertEqual(app.tree.item(row_id, "values")[0], "PocketLedger")
        self.assertEqual(app.d_name.cget("text"), "PocketLedger")
        self.assertEqual(app.d_repository.cget("text"),
                         "Repository: PocketLedger")

    def test_primary_launcher_uses_ttk_style_without_callback_error(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        command = {
            "label": "Run project",
            "type": "batch",
            "priority": 10,
            "healthy": True,
        }

        with mock.patch.object(main_module.launchers, "detect_commands",
                               return_value=[command]):
            app._populate_launchers(app.projects[0])

        buttons = [child for frame in app.d_launch.winfo_children()
                   for child in frame.winfo_children()
                   if isinstance(child, main_module.ttk.Button)]
        primary = [button for button in buttons
                   if button.cget("style") == "Primary.TButton"]
        self.assertEqual(len(primary), 1)
        self.assertTrue(primary[0].cget("text").startswith("Run ·"))


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class LauncherPresentationGuiRegressionTests(_StoreIsolationMixin,
                                             unittest.TestCase):
    @staticmethod
    def _candidate(label, priority, healthy=True, reason="", custom=None):
        candidate = {
            "label": label,
            "type": (main_module.launchers.TYPE_CUSTOM
                     if custom is not None else "test"),
            "priority": priority,
            "healthy": healthy, "reason": reason,
        }
        if custom is not None:
            candidate["custom"] = custom
        return candidate

    def _launcher_buttons(self, app):
        return [
            widget
            for frame in app.d_launch.winfo_children()
            for widget in frame.winfo_children()
            if isinstance(widget, ttk.Button)
        ]

    def test_long_launcher_actions_use_bounded_grid_and_keep_all_button_reachable(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        commands = [
            self._candidate("\u25b6 primary-with-a-very-long-name", 1),
            self._candidate("\u25b6 alternative-with-a-very-long-name-a", 2),
            self._candidate("\u25b6 alternative-with-a-very-long-name-b", 3),
            self._candidate("\u25b6 hidden-alternative", 4),
        ]
        app.geometry("1040x640")
        app._populate_launchers(app.projects[0], commands)
        app.update_idletasks()

        action_grid = app.d_launch.winfo_children()[0]
        buttons = [widget for widget in action_grid.winfo_children()
                   if isinstance(widget, ttk.Button)]
        self.assertEqual(len(buttons), 4)
        self.assertEqual(buttons[0].cget("style"), "Primary.TButton")
        self.assertEqual(buttons[0].grid_info()["columnspan"], 2)
        self.assertEqual(buttons[-1].cget("text"), "All launchers… (4)")
        self.assertEqual(buttons[-1].grid_info()["columnspan"], 2)
        self.assertEqual(len({button.grid_info()["row"] for button in buttons}), 3)
        self.assertLessEqual(
            max(button.winfo_x() + button.winfo_width() for button in buttons),
            action_grid.winfo_width())

    def test_more_than_two_healthy_alternatives_are_progressively_disclosed(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        commands = [self._candidate("primary", 10)] + [
            self._candidate(f"alternative-{index}", index + 11)
            for index in range(4)
        ]
        app._populate_launchers(app.projects[0], commands)
        app.update_idletasks()

        action_grid = app.d_launch.winfo_children()[0]
        visible_text = [widget.cget("text") for widget in action_grid.winfo_children()
                        if isinstance(widget, ttk.Button)]
        self.assertEqual(
            visible_text,
            ["Run · primary", "Run · alternative-0", "Run · alternative-1",
             "All launchers… (5)"],
        )

    def test_custom_edit_controls_use_multiple_rows_and_add_remains_reachable(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        commands = [self._candidate("primary", 1)]
        commands.extend(
            self._candidate(
                f"Custom: Tool {index}", 10 + index,
                custom={"name": f"Tool {index}"})
            for index in range(5)
        )
        app._populate_launchers(app.projects[0], commands)
        app.update_idletasks()

        edit_grid = next(frame for frame in app.d_launch.winfo_children()
                         if any(isinstance(widget, ttk.Button)
                                and widget.cget("text").startswith("Edit ")
                                for widget in frame.winfo_children()))
        edit_buttons = [widget for widget in edit_grid.winfo_children()
                        if isinstance(widget, ttk.Button)]
        self.assertEqual(len(edit_buttons), 6)
        self.assertGreater(len({button.grid_info()["row"] for button in edit_buttons}), 2)
        self.assertEqual(edit_buttons[-1].cget("text"), "Add Custom Launcher…")
        self.assertEqual(edit_buttons[-1].grid_info()["columnspan"], 2)

    def test_empty_state_is_vertical_and_bounded(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        app._populate_launchers(app.projects[0], [])
        app.update_idletasks()

        message, actions = app.d_launch.winfo_children()
        self.assertEqual(message.winfo_manager(), "pack")
        self.assertEqual(actions.winfo_manager(), "pack")
        buttons = [widget for widget in actions.winfo_children()
                   if isinstance(widget, ttk.Button)]
        self.assertEqual([button.cget("text") for button in buttons],
                         ["Add Custom Launcher…", "Generate run.bat"])
        self.assertTrue(all(button.winfo_x() == 0 for button in buttons))

    def test_long_unavailable_reason_wraps_inside_launch_section(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        reason = "missing tool with a very long diagnostic " * 8
        app._populate_launchers(app.projects[0], [
            self._candidate("primary", 1),
            self._candidate("\u25b6 unavailable", 2, healthy=False, reason=reason),
        ])
        app.update_idletasks()

        labels = [widget for widget in app.d_launch.winfo_children()
                  if isinstance(widget, ttk.Label)
                  and widget.cget("text").startswith("Unavailable ·")]
        self.assertEqual(len(labels), 1)
        self.assertIn("Unavailable · unavailable:", labels[0].cget("text"))
        self.assertLessEqual(int(labels[0].cget("wraplength")), 360)

    def test_all_launchers_name_filter_uses_clean_presentation_name(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        app._show_more_launchers([
            self._candidate("\u25b6 run", 1),
            self._candidate("\u25b6 start", 2),
        ])
        app.update_idletasks()
        dialog = next(child for child in app.winfo_children()
                      if isinstance(child, tk.Toplevel)
                      and child.title() == "All launchers")
        self.addCleanup(dialog.destroy)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        tree = next(widget for widget in descendants(dialog)
                    if isinstance(widget, ttk.Treeview))
        search = next(widget for widget in descendants(dialog)
                      if isinstance(widget, ttk.Entry))
        self.assertEqual(tree.heading("#1", "text"), "Name")
        self.assertEqual(tree.item(tree.get_children()[0], "values")[0], "run")
        search.insert(0, "start")
        app.update_idletasks()
        rows = tree.get_children()
        self.assertEqual(len(rows), 1)
        self.assertEqual(tree.item(rows[0], "values")[0], "start")


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class Ua02ResizeLayoutRegressionTests(_StoreIsolationMixin, unittest.TestCase):
    @staticmethod
    def _settle(app, geometry):
        app.geometry(geometry)
        for _ in range(3):
            app.update_idletasks()
            app.update()

    @staticmethod
    def _detail_widths(app):
        return (
            app.detail_canvas.winfo_width(),
            int(app.detail_canvas.itemcget(app._detail_window, "width")),
            app.detail_body.winfo_width(),
        )

    def test_detail_window_never_exceeds_canvas_at_default_and_minimum(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)

        for geometry in ("1400x820", "1040x640"):
            with self.subTest(geometry=geometry):
                self._settle(app, geometry)
                canvas_width, embedded_width, body_width = self._detail_widths(app)
                self.assertLessEqual(embedded_width, canvas_width)
                self.assertLessEqual(body_width, canvas_width)

        total_width = app._lower_pane.winfo_width()
        app._lower_pane.sashpos(0, total_width - 300)
        self._settle(app, "1040x640")
        canvas_width, embedded_width, body_width = self._detail_widths(app)
        self.assertLessEqual(embedded_width, canvas_width)
        self.assertLessEqual(body_width, canvas_width)

    def test_resize_sequence_preserves_legal_panes_and_no_horizontal_overflow(self):
        app = _build_real_app(2)
        self.addCleanup(app.destroy)

        for geometry in ("1400x820", "1200x720", "1040x640",
                         "1150x680", "1040x640", "1400x820"):
            with self.subTest(geometry=geometry):
                self._settle(app, geometry)
                self.assertGreaterEqual(app._outer_pane.sashpos(0), 100)
                self.assertGreaterEqual(app._lower_pane.sashpos(0), 400)
                canvas_width, embedded_width, body_width = self._detail_widths(app)
                self.assertLessEqual(embedded_width, canvas_width)
                self.assertLessEqual(body_width, canvas_width)
                self.assertFalse(app.detail_canvas.cget("xscrollcommand"))

    def test_settled_layout_is_idempotent(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        self._settle(app, "1040x640")
        before = (app._outer_pane.sashpos(0), app._lower_pane.sashpos(0))

        for _ in range(8):
            app.update_idletasks()
            app.update()
        app._clamp_panes()
        after = (app._outer_pane.sashpos(0), app._lower_pane.sashpos(0))
        self.assertEqual(after, before)

    def test_aggressive_sashes_are_recovered_without_resetting_valid_position(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        self._settle(app, "1040x640")

        lower_width = app._lower_pane.winfo_width()
        outer_height = app._outer_pane.winfo_height()
        legal_lower_max = max(400, lower_width - 360 - 6)
        legal_outer_max = max(100, outer_height - 340)

        app._lower_pane.sashpos(0, 1)
        app._outer_pane.sashpos(0, 1)
        app._clamp_panes()
        self.assertGreaterEqual(app._lower_pane.sashpos(0), 400)
        self.assertLessEqual(app._lower_pane.sashpos(0), legal_lower_max)
        self.assertGreaterEqual(app._outer_pane.sashpos(0), 100)
        self.assertLessEqual(app._outer_pane.sashpos(0), legal_outer_max)

        valid_lower = max(400, legal_lower_max - 40)
        valid_outer = max(100, legal_outer_max - 20)
        app._lower_pane.sashpos(0, valid_lower)
        app._outer_pane.sashpos(0, valid_outer)
        app._clamp_panes()
        self.assertEqual(app._lower_pane.sashpos(0), valid_lower)
        self.assertEqual(app._outer_pane.sashpos(0), valid_outer)

    def test_selection_survives_resize_sequence(self):
        app = _build_real_app(2)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        row = main_module.project_row_id(project)
        app.tree.selection_set(row)
        app.update_idletasks()
        app.update()
        self.assertIs(app._current, project)

        for geometry in ("1040x640", "1400x820", "1150x680", "1040x640"):
            self._settle(app, geometry)
            self.assertIs(app._current, project)
            self.assertEqual(tuple(app.tree.selection()), (row,))
            self.assertTrue(app.detail_content.winfo_manager())

    def test_detail_scrollregion_remains_vertical_and_reaches_lower_sections(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        row = main_module.project_row_id(app.projects[0])
        app.tree.selection_set(row)
        app.update_idletasks()
        app.update()
        self._settle(app, "1040x640")

        region = tuple(float(value) for value in app.detail_canvas.cget("scrollregion").split())
        self.assertEqual(len(region), 4)
        self.assertGreater(region[3], app.detail_canvas.winfo_height())
        self.assertFalse(app.detail_canvas.cget("xscrollcommand"))
        for section in (app.d_notes_section, app.d_health_section,
                        app.d_provider_section, app.d_launch_section):
            offset = section.winfo_y()
            app.detail_canvas.yview_moveto(offset / max(1, app.detail_body.winfo_height()))
            app.update_idletasks()
            self.assertGreater(section.winfo_height(), 0)

    def test_notes_wheel_scrolls_child_then_outer_at_child_boundary(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        row = main_module.project_row_id(app.projects[0])
        app.tree.selection_set(row)
        app.update_idletasks()
        app.update()
        self._settle(app, "1040x640")

        body_height = max(1, app.detail_body.winfo_height())
        notes_bottom = (app.d_notes_section.winfo_y()
                        + app.d_notes_section.winfo_height())
        app.detail_canvas.yview_moveto(
            max(0, notes_bottom - app.detail_canvas.winfo_height())
            / body_height)
        app.d_notes.delete("1.0", "end")
        app.d_notes.insert("1.0", "\n".join(f"note {i}" for i in range(40)))
        app.d_notes.yview_moveto(0.0)
        app.update()

        outer_before = app.detail_canvas.yview()
        app.d_notes.event_generate("<MouseWheel>", delta=-120, x=8, y=8)
        app.update()
        self.assertGreater(app.d_notes.yview()[0], 0.0)
        self.assertEqual(app.detail_canvas.yview(), outer_before)

        app.d_notes.yview_moveto(1.0)
        outer_before = app.detail_canvas.yview()
        app.d_notes.event_generate("<MouseWheel>", delta=-120, x=8, y=8)
        app.update()
        self.assertGreater(
            app.detail_canvas.yview()[0], outer_before[0],
            (app.d_notes.yview(), outer_before, app.detail_canvas.yview()))

        app.d_notes.insert("end", "\nstill editable")
        self.assertIn("still editable", app.d_notes.get("1.0", "end"))

    @staticmethod
    def _launcher_button(app, text="Generate run.bat"):
        pending = list(app.d_launch.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, ttk.Button) and widget.cget("text") == text:
                return widget
        raise AssertionError(f"launcher button not found: {text}")

    @staticmethod
    def _show_launcher_region(app):
        body_height = max(1, app.detail_body.winfo_height())
        offset = max(0, app.d_launch_section.winfo_y() - 10)
        app.detail_canvas.yview_moveto(offset / body_height)
        app.update()
        first, last = app.detail_canvas.yview()
        if last >= 1.0:
            raise AssertionError("launcher region left no outer scroll range")
        return first

    def test_dynamic_launcher_button_routes_wheel_to_detail_canvas(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        with mock.patch.object(app, "_request_detail_observation"):
            app._show_detail(main_module.project_row_id(project))
        app._populate_launchers(project, [])
        self._settle(app, "1040x640")

        button = self._launcher_button(app)
        before = self._show_launcher_region(app)
        button.event_generate("<MouseWheel>", delta=-120, x=8, y=8)
        app.update()

        self.assertGreater(app.detail_canvas.yview()[0], before)

    def test_project_switch_recreated_launchers_route_once_without_global_bind(self):
        app = _build_real_app(2)
        self.addCleanup(app.destroy)
        global_before = app.bind_all("<MouseWheel>")
        old_buttons = []
        button_movements = []
        container_movements = []

        with mock.patch.object(app, "_request_detail_observation"):
            for project in (app.projects[0], app.projects[1], app.projects[0]):
                app._show_detail(main_module.project_row_id(project))
                app._populate_launchers(project, [])
                self._settle(app, "1040x640")
                button = self._launcher_button(app)
                for old_button in old_buttons:
                    self.assertFalse(old_button.winfo_exists())
                before = self._show_launcher_region(app)
                button.event_generate(
                    "<MouseWheel>", delta=-120, x=8, y=8)
                app.update()
                button_movements.append(
                    app.detail_canvas.yview()[0] - before)
                before = self._show_launcher_region(app)
                app.d_launch.event_generate(
                    "<MouseWheel>", delta=-120, x=8, y=8)
                app.update()
                container_movements.append(
                    app.detail_canvas.yview()[0] - before)
                old_buttons = [button]

        for movements in (button_movements, container_movements):
            self.assertTrue(all(movement > 0.0 for movement in movements))
            self.assertAlmostEqual(max(movements), min(movements), places=7)
        self.assertEqual(app.bind_all("<MouseWheel>"), global_before)

    def test_loading_and_folder_only_replacement_route_wheel_to_detail(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        project["folder_path"] = project.pop("path")

        def suppress_observation(_project):
            app._detail_gen += 1

        with mock.patch.object(
                app, "_request_detail_observation",
                side_effect=suppress_observation):
            app._show_detail(main_module.project_row_id(project))
        self._settle(app, "1040x640")
        loading = next(
            child for child in app.d_launch.winfo_children()
            if isinstance(child, ttk.Label))
        before = self._show_launcher_region(app)
        loading.event_generate("<MouseWheel>", delta=-120, x=8, y=8)
        app.update()
        self.assertGreater(app.detail_canvas.yview()[0], before)

        target = {
            "project_id": main_module.projects.project_id(project),
            "path": project["folder_path"],
        }
        app._apply_detail_observation(
            target, {"health": None, "launchers": (), "note": ""},
            app._detail_gen)
        app.update()
        unavailable = next(
            child for child in app.d_launch.winfo_children()
            if isinstance(child, ttk.Label))
        self.assertIn("Unavailable", unavailable.cget("text"))
        before = self._show_launcher_region(app)
        unavailable.event_generate(
            "<MouseWheel>", delta=-120, x=8, y=8)
        app.update()
        self.assertGreater(app.detail_canvas.yview()[0], before)

    def test_detail_body_background_routes_wheel_to_detail_canvas(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        with mock.patch.object(app, "_request_detail_observation"):
            app._show_detail(main_module.project_row_id(project))
        self._settle(app, "1040x640")
        app.detail_canvas.yview_moveto(0.0)
        app.update()

        before = app.detail_canvas.yview()[0]
        app.detail_body.event_generate(
            "<MouseWheel>", delta=-120, x=8, y=8)
        app.update()

        self.assertGreater(app.detail_canvas.yview()[0], before)

    def test_rapid_resize_and_selection_leaves_final_detail_authoritative(self):
        app = _build_real_app(2)
        self.addCleanup(app.destroy)
        first, second = app.projects
        for index, geometry in enumerate(("1040x640", "1400x820", "1150x680",
                                          "1040x640", "1400x820")):
            project = first if index % 2 == 0 else second
            row = main_module.project_row_id(project)
            app.tree.selection_set(row)
            app._show_detail(row)
            self._settle(app, geometry)
            app._drain_scan_queue()
            canvas_width, embedded_width, body_width = self._detail_widths(app)
            self.assertLessEqual(embedded_width, canvas_width)
            self.assertLessEqual(body_width, canvas_width)

        final_row = main_module.project_row_id(first)
        app.tree.selection_set(final_row)
        app._show_detail(final_row)
        self._settle(app, "1400x820")
        self.assertIs(app._current, first)
        self.assertEqual(tuple(app.tree.selection()), (final_row,))


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class HealthDashboardGuiTests(_StoreIsolationMixin, unittest.TestCase):
    """Repository Health dashboard presentation regressions."""

    @staticmethod
    def _finding(rule, status, importance, severity=health_module.LOW,
                 freshness=health_module.CURRENT):
        evidence = (health_module.Evidence(
            "filesystem", rule, f"evidence-{rule}", "2026-01-01T00:00:00Z",
            freshness),)
        return health_module.Finding(
            rule, rule, "repository", status, severity, evidence,
            f"Explanation for {rule}.", "2026-01-01T00:00:00Z", freshness,
            remediation=f"Remediation for {rule}.", importance=importance)

    @staticmethod
    def _descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from HealthDashboardGuiTests._descendants(child)

    @staticmethod
    def _notebook(dialog):
        return next(widget for widget in HealthDashboardGuiTests._descendants(dialog)
                    if isinstance(widget, ttk.Notebook))

    @staticmethod
    def _tree(dialog):
        return next(widget for widget in HealthDashboardGuiTests._descendants(dialog)
                    if isinstance(widget, ttk.Treeview))

    @staticmethod
    def _all_texts(dialog):
        texts = []
        for widget in HealthDashboardGuiTests._descendants(dialog):
            try:
                texts.append(str(widget.cget("text")))
            except (tk.TclError, AttributeError):
                continue
        return texts

    def _open_all_checks(self, app, dialog):
        notebook = self._notebook(dialog)
        notebook.select(1)
        for _ in range(4):
            app.update_idletasks()
            app.update()
        return self._tree(dialog)

    def test_overview_is_default_and_all_checks_built_lazily_once(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        result = health_module.HealthResult(
            health_module.WARN,
            (self._finding("working_tree", health_module.WARN,
                           health_module.REQUIRED),
             self._finding("pass_rule", health_module.PASS,
                           health_module.REQUIRED)),
            "2026-01-01T00:00:00Z")
        app._health_result = result
        original = main_module.RepoManagerApp._health_build_all_checks
        calls = []

        def counting(self_, page, result_):
            calls.append(1)
            return original(self_, page, result_)

        with mock.patch.object(main_module.RepoManagerApp,
                               "_health_build_all_checks", counting):
            dialog = app._open_health_details()
            self.addCleanup(dialog.destroy)
            for _ in range(4):
                app.update_idletasks()
                app.update()
            notebook = self._notebook(dialog)
            self.assertEqual(notebook.tab(notebook.select(), "text"), "Overview")
            trees_before = [w for w in self._descendants(dialog)
                            if isinstance(w, ttk.Treeview)]
            self.assertEqual(trees_before, [])
            self.assertEqual(calls, [])
            tree = self._open_all_checks(app, dialog)
            self.assertEqual(calls, [1])
            self.assertTrue(tree.winfo_exists())
            for _ in range(3):
                notebook.select(0)
                app.update_idletasks()
                notebook.select(1)
                app.update_idletasks()
            self.assertIs(self._tree(dialog), tree)
            self.assertEqual(calls, [1])

    def test_all_findings_represented_exactly_once_in_groups(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        findings = (
            self._finding("repo_access", health_module.FAIL,
                          health_module.REQUIRED, health_module.HIGH),
            self._finding("git_metadata", health_module.UNKNOWN,
                          health_module.REQUIRED),
            self._finding("working_tree", health_module.WARN,
                          health_module.REQUIRED,
                          freshness=health_module.STALE),
            self._finding("upstream", health_module.WARN,
                          health_module.RECOMMENDED),
            self._finding("readme_presence", health_module.WARN,
                          health_module.INFORMATIONAL),
            self._finding("ci_presence", health_module.PASS,
                          health_module.REQUIRED),
            self._finding("docs_presence", health_module.NOT_APPLICABLE,
                          health_module.REQUIRED),
            self._finding("disabled_check", health_module.FAIL,
                          health_module.DISABLED),
        )
        result = health_module.HealthResult(
            health_module.UNKNOWN, findings, "2026-01-01T00:00:00Z")
        app._health_result = result
        dialog = app._open_health_details()
        self.addCleanup(dialog.destroy)
        tree = self._open_all_checks(app, dialog)
        row_ids = list(tree.get_children(""))
        group_names = [tree.item(item, "text") for item in row_ids]
        text = "\n".join(group_names)
        for expected in ("Action Needed", "Needs Attention", "Informational",
                         "Passed Checks", "Not Applicable", "Disabled"):
            self.assertIn(expected, text)
        child_count = sum(len(tree.get_children(item)) for item in row_ids)
        self.assertEqual(child_count, 8)
        self.assertEqual(tree.heading("#0", "text"), "Check")
        self.assertEqual(tree.heading("status", "text"), "Status")
        self.assertEqual(tree.heading("importance", "text"), "Importance")
        self.assertEqual(tree.heading("freshness", "text"), "Freshness")

    def test_navigator_columns_fit_without_clipping(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        result = health_module.HealthResult(
            health_module.WARN,
            (self._finding("working_tree", health_module.WARN,
                           health_module.REQUIRED),
             self._finding("documentation_presence", health_module.PASS,
                           health_module.REQUIRED),
             self._finding("remote_presence", health_module.FAIL,
                           health_module.REQUIRED)),
            "2026-01-01T00:00:00Z")
        app._health_result = result
        dialog = app._open_health_details()
        self.addCleanup(dialog.destroy)
        tree = self._open_all_checks(app, dialog)
        bars = [widget for widget in self._descendants(dialog)
                if isinstance(widget, ttk.Scrollbar)]
        self.assertTrue(
            any(str(b.cget("orient")) == "horizontal" for b in bars))
        total = sum(int(tree.column(column)["width"])
                    for column in ("#0", "status", "importance", "freshness"))
        self.assertLessEqual(total, tree.winfo_width() + 2)

    def test_required_warn_is_grouped_and_selectable(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        result = health_module.HealthResult(
            health_module.WARN,
            (self._finding("working_tree", health_module.WARN,
                           health_module.REQUIRED,
                           freshness=health_module.STALE),
             self._finding("pass_rule", health_module.PASS,
                           health_module.REQUIRED)),
            "2026-01-01T00:00:00Z")
        app._health_result = result
        dialog = app._open_health_details()
        self.addCleanup(dialog.destroy)
        tree = self._open_all_checks(app, dialog)
        attention = next(item for item in tree.get_children("")
                         if "Needs Attention" in tree.item(item, "text"))
        finding_item = tree.get_children(attention)[0]
        self.assertEqual(tree.item(finding_item, "text"), "Working tree")
        self.assertEqual(tree.item(finding_item, "values")[0], "Warn")
        tree.selection_set(finding_item)
        app.update_idletasks()
        labels = [widget.cget("text") for widget in self._descendants(dialog)
                  if isinstance(widget, ttk.Label)]
        self.assertIn("Remediation for working_tree.", labels)
        self.assertIn("Explanation for working_tree.", labels)
        self.assertIn("working_tree", labels)
        self.assertIn("WARN", labels)

    def test_detail_render_is_bounded_during_initial_open(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        result = health_module.HealthResult(
            health_module.WARN,
            (self._finding("repo_access", health_module.FAIL,
                           health_module.REQUIRED),
             self._finding("working_tree", health_module.WARN,
                           health_module.REQUIRED,
                           freshness=health_module.STALE),
             self._finding("pass_rule", health_module.PASS,
                           health_module.REQUIRED)),
            "2026-01-01T00:00:00Z")
        app._health_result = result
        original = main_module.RepoManagerApp._render_health_finding_detail
        renders = []

        def counting(self_, container, finding):
            renders.append(finding.rule)
            return original(self_, container, finding)

        with mock.patch.object(main_module.RepoManagerApp,
                               "_render_health_finding_detail", counting):
            dialog = app._open_health_details()
            self.addCleanup(dialog.destroy)
            for _ in range(4):
                app.update_idletasks()
                app.update()
            self.assertEqual(renders, [], "Overview must not render a detail")
            tree = self._open_all_checks(app, dialog)
            initial = len(renders)
            self.assertLessEqual(initial, 2)
            for _ in range(6):
                app.update_idletasks()
                app.update()
            self.assertEqual(len(renders), initial,
                             "settled layout must not keep rendering detail")
            # a real selection change may render once more
            fail_group = next(item for item in tree.get_children("")
                              if "Action Needed" in tree.item(item, "text"))
            other = tree.get_children(fail_group)[0]
            tree.selection_set(other)
            app.update_idletasks()
            self.assertLessEqual(len(renders), initial + 1)
            settled_renders = len(renders)
            for _ in range(4):
                app.update_idletasks()
                app.update()
            self.assertEqual(len(renders), settled_renders)

    def test_gauge_shows_score_with_semantic_color_drawn_once(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        result = health_module.HealthResult(
            health_module.FAIL,
            (self._finding("repo_access", health_module.FAIL,
                           health_module.REQUIRED),),
            "2026-01-01T00:00:00Z")
        app._health_result = result
        dialog = app._open_health_details()
        self.addCleanup(dialog.destroy)
        for _ in range(4):
            app.update_idletasks()
            app.update()
        gauges = [w for w in self._descendants(dialog)
                  if isinstance(w, tk.Canvas)
                  and hasattr(w, "_health_score")]
        self.assertEqual(len(gauges), 1)
        gauge = gauges[0]
        self.assertEqual(gauge._health_score, 39)
        self.assertEqual(str(gauge._health_color),
                         str(app.pal["danger"]))
        items = set(gauge.find_all())
        for _ in range(4):
            app.update_idletasks()
            app.update()
        self.assertEqual(set(gauge.find_all()), items,
                         "gauge must not be redrawn on idle/resize")
        texts = self._all_texts(dialog)
        self.assertIn("Problems found", texts)

    def test_gauge_ring_fits_inside_canvas_bounds(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        result = health_module.HealthResult(
            health_module.WARN,
            (self._finding("working_tree", health_module.WARN,
                           health_module.REQUIRED),),
            "2026-01-01T00:00:00Z")
        app._health_result = result
        dialog = app._open_health_details()
        self.addCleanup(dialog.destroy)
        for _ in range(4):
            app.update_idletasks()
            app.update()
        gauges = [w for w in self._descendants(dialog)
                  if isinstance(w, tk.Canvas)
                  and hasattr(w, "_health_score")]
        self.assertEqual(len(gauges), 1)
        gauge = gauges[0]
        width, height = gauge.winfo_width(), gauge.winfo_height()
        self.assertGreaterEqual(width, 144)
        self.assertGreaterEqual(height, 144)
        for item in gauge.find_all():
            x1, y1, x2, y2 = gauge.bbox(item)
            self.assertGreaterEqual(x1, 0)
            self.assertGreaterEqual(y1, 0)
            self.assertLessEqual(x2, width)
            self.assertLessEqual(y2, height)
            self.assertLessEqual(x2 - x1, width)
            self.assertLessEqual(y2 - y1, height)

    def test_counters_reflect_documented_enabled_rules(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        result = health_module.HealthResult(
            health_module.FAIL,
            (self._finding("git_metadata", health_module.PASS,
                           health_module.REQUIRED),
             self._finding("ci_presence", health_module.PASS,
                           health_module.REQUIRED),
             self._finding("working_tree", health_module.WARN,
                           health_module.REQUIRED,
                           freshness=health_module.STALE),
             self._finding("upstream", health_module.WARN,
                           health_module.RECOMMENDED),
             self._finding("repo_access", health_module.FAIL,
                           health_module.REQUIRED),
             self._finding("remote_state", health_module.UNKNOWN,
                           health_module.REQUIRED),
             self._finding("readme_presence", health_module.WARN,
                           health_module.INFORMATIONAL),
             self._finding("docs_presence", health_module.PASS,
                           health_module.INFORMATIONAL)),
            "2026-01-01T00:00:00Z")
        app._health_result = result
        dialog = app._open_health_details()
        self.addCleanup(dialog.destroy)
        for _ in range(4):
            app.update_idletasks()
            app.update()
        texts = self._all_texts(dialog)
        for expected in ("Passed 2", "Warnings 2", "Problems 1",
                         "Unknown 1", "Stale 1", "Informational observations: 2"):
            self.assertIn(expected, texts)

    def test_overview_action_cards_follow_prioritized_order(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        result = health_module.HealthResult(
            health_module.FAIL,
            (self._finding("readme_presence", health_module.WARN,
                           health_module.INFORMATIONAL),
             self._finding("ci_presence", health_module.PASS,
                           health_module.REQUIRED),
             self._finding("working_tree", health_module.WARN,
                           health_module.REQUIRED),
             self._finding("repo_access", health_module.FAIL,
                           health_module.REQUIRED, health_module.HIGH)),
            "2026-01-01T00:00:00Z")
        app._health_result = result
        dialog = app._open_health_details()
        self.addCleanup(dialog.destroy)
        for _ in range(4):
            app.update_idletasks()
            app.update()
        texts = self._all_texts(dialog)
        self.assertIn("Explanation for repo_access.", texts)
        self.assertIn("Explanation for working_tree.", texts)
        self.assertIn("Next:", texts)
        # FAIL (HIGH) precedes WARN because prioritized_findings is authority
        self.assertLess(texts.index("Repo access"),
                        texts.index("Working tree"))
        # informational and passed findings are not material action cards
        self.assertIn("Informational observations: 1", texts)
        self.assertNotIn("Explanation for readme_presence.", texts)

    def test_healthy_state_shows_positive_message(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        result = health_module.HealthResult(
            health_module.PASS,
            (self._finding("git_metadata", health_module.PASS,
                           health_module.REQUIRED),
             self._finding("ci_presence", health_module.PASS,
                           health_module.REQUIRED)),
            "2026-01-01T00:00:00Z")
        app._health_result = result
        dialog = app._open_health_details()
        self.addCleanup(dialog.destroy)
        for _ in range(4):
            app.update_idletasks()
            app.update()
        texts = self._all_texts(dialog)
        self.assertIn("No required action found in the checks that ran.", texts)
        self.assertNotIn("Explanation for git_metadata.", texts)

    def test_semantic_row_tags_match_status_colors(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        statuses = (health_module.FAIL, health_module.WARN,
                    health_module.PASS, health_module.UNKNOWN,
                    health_module.NOT_APPLICABLE)
        findings = tuple(self._finding(f"rule_{status.lower()}", status,
                                       health_module.REQUIRED)
                         for status in statuses)
        result = health_module.HealthResult(health_module.UNKNOWN, findings,
                                            "2026-01-01T00:00:00Z")
        app._health_result = result
        dialog = app._open_health_details()
        self.addCleanup(dialog.destroy)
        tree = self._open_all_checks(app, dialog)
        rows = {tree.item(row, "text"): row
                for group in tree.get_children("")
                for row in tree.get_children(group)}
        for status in statuses:
            display = main_module.health_rule_display_name(
                f"rule_{status.lower()}")
            row = rows[display]
            tags = tree.item(row, "tags")
            expected_tag = f"health_{status.lower()}"
            self.assertIn(expected_tag, tags)
            color = tree.tag_configure(expected_tag, "foreground")
            self.assertEqual(str(color),
                             str(theme.status_fg(app.pal, status)))
        for group in tree.get_children(""):
            self.assertIn(tree.item(group, "tags"), ((), ""),
                          "group rows stay structural")

    def test_opening_and_closing_does_not_mutate_health_state(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        app._current = project
        result = health_module.HealthResult(
            health_module.WARN,
            (self._finding("working_tree", health_module.WARN,
                           health_module.REQUIRED),
             self._finding("pass_rule", health_module.PASS,
                           health_module.REQUIRED)),
            "2026-01-01T00:00:00Z")
        before = result.findings
        app._health_result = result
        app.d_health_status.configure(text="before",
                                      style=theme.semantic_style("WARN"))
        dialog = app._open_health_details()
        self.addCleanup(dialog.destroy)
        self._open_all_checks(app, dialog)
        dialog.destroy()
        app.update_idletasks()
        self.assertIs(app._health_result, result)
        self.assertEqual(app._health_result.findings, before)
        self.assertIs(app._current, project)
        self.assertEqual(app.d_health_status.cget("text"), "before")


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class CoreSurfaceVisibilityRegressionTests(_StoreIsolationMixin, unittest.TestCase):
    def test_workspace_and_agent_surfaces_are_visible_at_default_size(self):
        with mock.patch.object(main_module.RepoManagerApp,
                               "_schedule_after", return_value=None), \
                mock.patch.object(main_module.RepoManagerApp, "start_scan"):
            app = main_module.RepoManagerApp()
        self.addCleanup(app.destroy)
        app.update_idletasks()
        app.update()

        for widget in (app.workspace_combo, app.workspace_status,
                       app.agent_status, app.run_status):
            with self.subTest(widget=str(widget)):
                self.assertTrue(widget.winfo_ismapped())
                self.assertGreater(widget.winfo_width(), 1)
                self.assertLessEqual(
                    widget.winfo_rooty() + widget.winfo_height(),
                    app.winfo_rooty() + app.winfo_height())


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class WP07LayoutRegressionTests(_StoreIsolationMixin, unittest.TestCase):
    def _app_with_selection(self):
        app = _build_real_app(3)
        row = main_module.project_row_id(app.projects[0])
        app.tree.selection_set(row)
        app.update_idletasks()
        app.update()
        return app

    def test_detail_sections_are_reachable_through_bounded_scroll_panel(self):
        app = self._app_with_selection()
        self.addCleanup(app.destroy)
        self.assertEqual(int(app.d_notes.cget("height")), 5)
        self.assertTrue(app.detail_canvas.winfo_ismapped())
        body_height = max(1, app.detail_body.winfo_height())
        canvas_top = app.detail_canvas.winfo_rooty()
        canvas_bottom = canvas_top + app.detail_canvas.winfo_height()

        for section in (app.d_notes_section, app.d_health_section,
                        app.d_export_section, app.d_provider_section,
                        app.d_launch_section):
            with self.subTest(section=section.cget("text")):
                offset = section.winfo_rooty() - app.detail_body.winfo_rooty()
                app.detail_canvas.yview_moveto(offset / body_height)
                app.update_idletasks()
                top = section.winfo_rooty()
                bottom = top + section.winfo_height()
                self.assertLess(top, canvas_bottom)
                self.assertGreater(bottom, canvas_top)

    def test_column_widths_and_panes_cannot_collapse_core_surfaces(self):
        app = self._app_with_selection()
        self.addCleanup(app.destroy)
        app.tree.column("name", width=1)
        app.tree.column("path", width=5000)
        app._persist_column_widths()
        self.assertEqual(
            app.settings["column_widths"]["name"],
            main_module.TABLE_COLUMN_LIMITS["name"][0])
        self.assertEqual(
            app.settings["column_widths"]["path"],
            main_module.TABLE_COLUMN_LIMITS["path"][1])

        app._lower_pane.sashpos(0, 1)
        app._outer_pane.sashpos(0, 1)
        app._clamp_panes()
        self.assertGreaterEqual(app._lower_pane.sashpos(0), 400)
        self.assertGreaterEqual(app._outer_pane.sashpos(0), 100)

    def test_ice_light_updates_plain_tk_detail_surfaces(self):
        app = self._app_with_selection()
        self.addCleanup(app.destroy)
        app.toggle_theme()
        self.assertEqual(app.settings["theme"], "light")
        self.assertNotEqual(app.pal["panel"].lower(), "#ffffff")
        self.assertEqual(app.detail_canvas.cget("background"),
                         app.pal["panel"])
        self.assertEqual(app.d_notes.cget("background"), app.pal["panel"])

    def test_help_and_workspace_dialog_share_theme_and_escape_behavior(self):
        app = self._app_with_selection()
        self.addCleanup(app.destroy)
        help_dialog = app.open_help("health")
        app.update()
        self.assertEqual(len(help_dialog.notebook.tabs()),
                         len(main_module.HELP_TOPICS))
        self.assertEqual(help_dialog.cget("background"), app.pal["bg"])
        self.assertTrue(help_dialog.bind("<Escape>"))
        help_dialog.destroy()
        app.update()
        self.assertFalse(help_dialog.winfo_exists())

        app._new_workspace()
        app.update()
        dialogs = [child for child in app.winfo_children()
                   if isinstance(child, tk.Toplevel)
                   and child.title() == "New Workspace"]
        self.assertEqual(len(dialogs), 1)
        workspace_dialog = dialogs[0]
        self.assertEqual(workspace_dialog.cget("background"), app.pal["bg"])
        self.assertTrue(workspace_dialog.bind("<Escape>"))
        workspace_dialog.destroy()
        app.update()
        self.assertFalse(workspace_dialog.winfo_exists())

        app.open_settings()
        app.update()
        settings_dialogs = [child for child in app.winfo_children()
                            if isinstance(child, tk.Toplevel)
                            and child.title() == "Settings"]
        self.assertEqual(len(settings_dialogs), 1)
        settings_dialog = settings_dialogs[0]
        notebooks = [child for child in settings_dialog.winfo_children()
                     if isinstance(child, ttk.Notebook)]
        self.assertEqual(len(notebooks), 1)
        labels = [notebooks[0].tab(tab, "text")
                  for tab in notebooks[0].tabs()]
        self.assertEqual(labels, ["Scanning", "Integrations", "Appearance"])
        self.assertTrue(settings_dialog.bind("<Escape>"))
        settings_dialog.destroy()
        app.update()
        self.assertFalse(settings_dialog.winfo_exists())


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class ActiveTreeAndContextMenuTests(_StoreIsolationMixin, unittest.TestCase):
    """Context-menu and active-tree routing through the real callback.

    Exercises the real ``_show_context_menu`` method (including ``_set_active``,
    selection/focus, and ``_build_context_menu``) end-to-end. Only the two
    display-blocking menu calls (``tk_popup``/``grab_release``) are stubbed;
    ``identify_row`` and row coordinates are real. Also drives a real
    ``<Button-3>`` ``event_generate`` when the environment maps rows reliably.
    """

    def _app(self, n=40):
        return _build_real_app(n)

    def _right_click(self, app, tree, iid):
        """Drive a real right-click through _show_context_menu on `iid`.

        Returns the iid that ``identify_row`` actually resolved, so callers can
        assert they reached the intended row.
        """
        tree.see(iid)
        app.update_idletasks()
        app.update()
        rect = tree.bbox(iid)
        if not rect:
            self.skipTest("tree not mapped for right-click")
        x, y, w, h = rect
        cy = y + h // 2
        with mock.patch.object(tk.Menu, "tk_popup"), \
                mock.patch.object(tk.Menu, "grab_release"):
            ev = _Event(tree, cy, tree.winfo_rootx() + x,
                        tree.winfo_rooty() + cy)
            app._show_context_menu(ev)
        app.update()
        return tree.identify_row(cy)

    # --- the isolated setter (required but NOT sufficient on its own) ------
    def test_set_active_unit(self):
        app = self._app(3)
        app._set_active("main")
        self.assertEqual(app._active_tree, "main")
        app._set_active("now")
        self.assertEqual(app._active_tree, "now")
        app.destroy()

    def test_selected_project_routes_by_active_tree(self):
        app = self._app(10)
        main_path = rf"C:\repos\repo00000"
        app.tree.selection_set(main_path)
        app.update()
        app._set_active("main")
        self.assertEqual(app._selected_project()["path"], main_path)
        # after switching to now with a now-selection, routing changes
        now_path = rf"C:\repos\repo00005"  # active (i % 5 == 0)
        app.now_tree.selection_set(now_path)
        app.update()
        app._set_active("now")
        self.assertEqual(app._selected_project()["path"], now_path)
        app.destroy()

    # --- real callback path: right-click reaches menu construction ---------
    def test_main_right_click_builds_menu_sets_main(self):
        app = self._app(40)
        target = rf"C:\repos\repo00005"
        self.assertEqual(self._right_click(app, app.tree, target), target)
        self.assertEqual(app._active_tree, "main")
        self.assertIsNotNone(app._ctx_menu)
        self.assertEqual(tuple(app.tree.selection()), (target,))
        app.destroy()

    def test_now_right_click_builds_menu_sets_now(self):
        app = self._app(40)
        # pick a row present in the Working-on-now tree (active: i % 5 == 0)
        now_path = rf"C:\repos\repo00005"
        self.assertIn(now_path, app.now_tree.get_children())
        self.assertEqual(self._right_click(app, app.now_tree, now_path),
                         now_path)
        self.assertEqual(app._active_tree, "now")
        self.assertIsNotNone(app._ctx_menu)
        app.destroy()

    def test_now_placeholder_right_click_no_error(self):
        app = self._app(5)
        for p in app.projects:
            p["status"] = "archived"
        app._populate_trees()
        app.update()
        self.assertIn("__won-empty__", app.now_tree.get_children())
        self.assertEqual(
            self._right_click(app, app.now_tree, "__won-empty__"),
            "__won-empty__")
        self.assertEqual(app._active_tree, "now")
        # placeholder must never be treated as a real project
        self.assertIsNone(app._selected_project())
        app.destroy()

    # --- adversarial: right-click after filter / sort / reconcile ----------
    def test_right_click_after_filter_reorders(self):
        app = self._app(200)
        # filter to a subset then sort -> incremental reconcile reuses rows
        app.filter_var.set("repo0")
        app._populate_trees()
        app._sort_by("name")
        app._populate_trees()
        app.update()
        first = app.tree.get_children()[0]
        self.assertEqual(self._right_click(app, app.tree, first), first)
        self.assertEqual(app._active_tree, "main")
        self.assertIsNotNone(app._ctx_menu)
        app.destroy()

    def test_right_click_unselected_after_selection_removed(self):
        app = self._app(40)
        # select a row, then clear it entirely, then right-click another
        a, b = rf"C:\repos\repo00001", rf"C:\repos\repo00002"
        app.tree.selection_set(a)
        app.tree.selection_remove(a)
        app.update()
        self.assertEqual(self._right_click(app, app.tree, b), b)
        self.assertEqual(tuple(app.tree.selection()), (b,))
        self.assertEqual(app._active_tree, "main")
        app.destroy()

    # --- large-dataset regression: menu still opens without AttributeError --
    def test_right_click_large_datasets(self):
        for n in (1000, 5000, 10000):
            app = self._app(n)
            target = rf"C:\repos\repo{n - 1:05d}"
            with self.subTest(n=n):
                try:
                    self.assertEqual(
                        self._right_click(app, app.tree, target), target)
                    self.assertEqual(app._active_tree, "main")
                    self.assertIsNotNone(app._ctx_menu)
                finally:
                    app.destroy()


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class RemoveFromRepoManagerGuiTests(_StoreIsolationMixin, unittest.TestCase):
    @staticmethod
    def _button(dialog, text):
        pending = list(dialog.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, ttk.Button) and widget.cget("text") == text:
                return widget
        raise AssertionError(f"button not found: {text}")

    @staticmethod
    def _dialog_text(dialog):
        values = []
        pending = list(dialog.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, ttk.Label):
                values.append(str(widget.cget("text")))
        return "\n".join(values)

    @staticmethod
    def _menu_labels(menu):
        end = menu.index("end")
        return [menu.entrycget(index, "label")
                for index in range((end if end is not None else -1) + 1)
                if menu.type(index) != "separator"]

    def _app(self, count=2):
        app = _build_real_app(count)
        for index, project in enumerate(app.projects):
            project["project_id"] = f"project-{index}"
        app._populate_trees()
        app.update()
        return app

    @staticmethod
    def _select(app, index=0):
        project = app.projects[index]
        row = project["project_id"]
        app._set_active("main")
        app.tree.selection_set(row)
        app.tree.focus(row)
        app.update()
        return project

    def test_context_menu_offers_remove_for_unavailable_and_archived_states(self):
        states = (
            ("normal", {}, True),
            ("missing folder", {}, False),
            ("broken Git", {"broken": True}, True),
            ("status unavailable", {"status_available": False}, True),
            ("archived", {"status": "archived"}, True),
        )
        for label, changes, available in states:
            with self.subTest(state=label):
                app = self._app(1)
                try:
                    target = app.projects[0]
                    target.update(changes)
                    app._avail = main_module.AvailabilityCache(
                        sampler=lambda _path, value=available: value)
                    app._populate_trees()
                    self._select(app)

                    app._build_context_menu()

                    self.assertIn("Remove from RepoManager\u2026",
                                  self._menu_labels(app._ctx_menu))
                finally:
                    app.destroy()

    def test_cancel_has_no_mutation_persistence_or_ui_removal(self):
        app = self._app(1)
        try:
            target = self._select(app)
            row = target["project_id"]
            with mock.patch.object(app, "_persist_projects") as persist:
                dialog = app._remove_from_repomanager()
                text = self._dialog_text(dialog)
                self.assertIn("repository and its files will not be changed",
                              text)
                self.assertIn("restore it later in Settings", text)

                self._button(dialog, "Cancel").invoke()
                app.update()

            persist.assert_not_called()
            self.assertNotIn("ignored", target)
            self.assertIn(row, app.tree.get_children())
            self.assertIn(row, app.now_tree.get_children())
        finally:
            app.destroy()

    def test_confirm_persists_then_hides_only_selected_stable_identity(self):
        app = self._app(2)
        try:
            target = self._select(app)
            other = app.projects[1]
            target["name"] = other["name"] = "Similar"
            target["path"] = r"C:\repos\similar"
            other["path"] = r"C:\repos\similar-child"
            target_id = target["project_id"]
            saved = []
            with mock.patch.object(
                    app, "_persist_projects",
                    side_effect=lambda records=None, **_kwargs:
                    saved.append(copy.deepcopy(records or app.projects))):
                dialog = app._remove_from_repomanager()
                self._button(dialog, "Remove").invoke()
                app.update()

            self.assertEqual(len(saved), 1)
            persisted = {item["project_id"]: item for item in saved[0]}
            self.assertTrue(persisted[target_id]["ignored"])
            self.assertNotIn("ignored", persisted[other["project_id"]])
            self.assertEqual(target["project_id"], target_id)
            self.assertNotIn(target_id, app.tree.get_children())
            self.assertNotIn(target_id, app.now_tree.get_children())
            self.assertIn(other["project_id"], app.tree.get_children())
        finally:
            app.destroy()

    def test_save_failure_rolls_back_and_keeps_complete_ui_state(self):
        app = self._app(1)
        try:
            target = self._select(app)
            row = target["project_id"]
            with mock.patch.object(
                    app, "_persist_projects", side_effect=OSError("disk full")), \
                    mock.patch.object(main_module.messagebox, "showerror") as shown:
                dialog = app._remove_from_repomanager()
                self._button(dialog, "Remove").invoke()
                app.update()

            shown.assert_called_once()
            self.assertNotIn("ignored", target)
            self.assertIn(row, app.tree.get_children())
            self.assertIn(row, app.now_tree.get_children())
            self.assertTrue(dialog.winfo_exists())
            dialog.destroy()
        finally:
            app.destroy()

    def test_post_commit_cache_failure_keeps_remove_successful_and_hidden(self):
        app = self._app(1)
        try:
            target = self._select(app)
            target_id = target["project_id"]
            with mock.patch.object(
                    store, "_cache_recovered_workspaces",
                    side_effect=OSError("post-replace cache failure")) as cache, \
                    self.assertLogs(
                        "repo_manager.store", level="ERROR") as logs, \
                    mock.patch.object(
                        main_module.messagebox, "showerror") as shown:
                dialog = app._remove_from_repomanager()
                self._button(dialog, "Remove").invoke()
                app.update()

            cache.assert_called_once_with([])
            shown.assert_not_called()
            self.assertFalse(dialog.winfo_exists())
            self.assertTrue(target["ignored"])
            self.assertEqual(target["project_id"], target_id)
            self.assertNotIn(target_id, app.tree.get_children())
            self.assertNotIn(target_id, app.now_tree.get_children())
            persisted = json.loads(
                store.REPOS_FILE.read_text(encoding="utf-8"))["projects"]
            self.assertEqual(len(persisted), 1)
            self.assertEqual(persisted[0]["project_id"], target_id)
            self.assertTrue(persisted[0]["ignored"])
            self.assertTrue(any(
                "registry committed but recovered Workspace cache update failed"
                in message for message in logs.output))
        finally:
            app.destroy()

    def test_dialog_reresolves_by_id_and_does_not_mutate_replacement(self):
        app = self._app(1)
        try:
            target = self._select(app)
            dialog = app._remove_from_repomanager()
            app.d_notes.insert("1.0", "unsaved")
            app._schedule_note_save()
            replacement = dict(target, project_id="replacement-id")
            app.projects[:] = [replacement]
            with mock.patch.object(app, "_persist_projects") as persist, \
                    mock.patch.object(store, "save_note") as save_note, \
                    mock.patch.object(main_module.messagebox, "showerror") as shown:
                self._button(dialog, "Remove").invoke()
                app.update()

            persist.assert_not_called()
            save_note.assert_not_called()
            shown.assert_called_once()
            self.assertNotIn("ignored", replacement)
            dialog.destroy()
        finally:
            app.destroy()

    def test_ignored_project_is_hidden_and_has_no_normal_remove_action(self):
        app = self._app(1)
        try:
            target = app.projects[0]
            target["ignored"] = True
            app._populate_trees()
            app.update()

            self.assertNotIn(target["project_id"], app.tree.get_children())
            self.assertNotIn(target["project_id"], app.now_tree.get_children())
            self.assertNotIn(
                "Remove from RepoManager\u2026",
                [item[1] for item in main_module.context_menu_layout(
                    target["status"], ignored=True) if item != "-sep-"])
        finally:
            app.destroy()

    def test_pending_edited_note_is_flushed_under_unchanged_project_id(self):
        app = self._app(1)
        try:
            target = self._select(app)
            target_id = target["project_id"]
            app.d_notes.delete("1.0", "end")
            app.d_notes.insert("1.0", "edited-note")
            app._schedule_note_save()

            dialog = app._remove_from_repomanager()
            self._button(dialog, "Remove").invoke()
            app.update()

            self.assertEqual(
                store.load_note(target["name"], target["path"], target_id),
                "edited-note")
            self.assertEqual(store.load_projects()[0]["project_id"],
                             target_id)
        finally:
            app.destroy()

    def test_stale_scan_cannot_resurrect_removed_project(self):
        app = self._app(1)
        try:
            target = self._select(app)
            target_id = target["project_id"]
            stale_result = [dict(target)]
            store.save_note(target["name"], target["path"], "keep-note",
                            target_id)
            dialog = app._remove_from_repomanager()
            self._button(dialog, "Remove").invoke()
            app.update()

            app._scan_queue.put(("result", stale_result, [], app._scan_gen))
            app._drain_scan_queue()
            app.update()

            self.assertEqual(len(app.projects), 1)
            self.assertEqual(app.projects[0]["project_id"], target_id)
            self.assertTrue(app.projects[0]["ignored"])
            self.assertNotIn(target_id, app.tree.get_children())
            self.assertNotIn(target_id, app.now_tree.get_children())
            loaded = store.load_projects()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["project_id"], target_id)
            self.assertTrue(loaded[0]["ignored"])
            self.assertEqual(
                store.load_note(target["name"], target["path"], target_id),
                "keep-note")
        finally:
            app.destroy()


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class IgnoredProjectsSettingsGuiTests(_StoreIsolationMixin,
                                      unittest.TestCase):
    @staticmethod
    def _button(dialog, text):
        pending = list(dialog.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, ttk.Button) and widget.cget("text") == text:
                return widget
        raise AssertionError(f"button not found: {text}")

    @staticmethod
    def _ignored_tree(dialog):
        pending = list(dialog.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if (isinstance(widget, ttk.Treeview)
                    and tuple(widget.cget("columns"))
                    == ("name", "status", "path", "project_id")):
                return widget
        raise AssertionError("ignored-project tree not found")

    def _app(self, count=2):
        app = _build_real_app(count)
        for index, project in enumerate(app.projects):
            project["project_id"] = f"project-{index}"
        app._populate_trees()
        app.update()
        return app

    def _open(self, app):
        app.open_settings()
        app.update()
        dialog = next(
            child for child in app.winfo_children()
            if isinstance(child, tk.Toplevel) and child.title() == "Settings")
        return dialog, self._ignored_tree(dialog), self._button(
            dialog, "Restore selected")

    @staticmethod
    def _vertical_canvas_ancestor(widget):
        current = widget.master
        while current is not None:
            if isinstance(current, tk.Canvas):
                return current
            current = current.master
        raise AssertionError("scrolling canvas not found")

    def _assert_vertically_reachable(self, app, widget, viewport):
        viewport.yview_moveto(1.0)
        app.update()
        self.assertTrue(widget.winfo_ismapped())
        self.assertGreater(widget.winfo_height(), 1)
        self.assertGreaterEqual(widget.winfo_rooty(), viewport.winfo_rooty())
        self.assertLessEqual(
            widget.winfo_rooty() + widget.winfo_height(),
            viewport.winfo_rooty() + viewport.winfo_height())

    def test_settings_controls_reachable_at_normal_and_constrained_heights(self):
        for screen_height in (1080, 720, 640):
            with self.subTest(screen_height=screen_height):
                app = self._app(1)
                try:
                    app.projects[0]["ignored"] = True
                    with mock.patch.object(
                            tk.Misc, "winfo_screenheight",
                            return_value=screen_height):
                        dialog, tree, restore = self._open(app)

                    save = self._button(dialog, "Save & Rescan")
                    canvas = self._vertical_canvas_ancestor(tree)
                    self.assertLessEqual(
                        dialog.winfo_height(), max(1, screen_height - 96))
                    self.assertTrue(save.winfo_ismapped())
                    self.assertGreater(save.winfo_height(), 1)
                    self.assertLessEqual(
                        save.winfo_rooty() + save.winfo_height(),
                        dialog.winfo_rooty() + dialog.winfo_height())

                    self._assert_vertically_reachable(app, tree, canvas)
                    self._assert_vertically_reachable(app, restore, canvas)
                    if screen_height == 1080:
                        self.assertEqual(dialog.winfo_height(), 700)
                    else:
                        first, last = canvas.yview()
                        self.assertGreater(first, 0.0)
                        self.assertEqual(last, 1.0)
                    dialog.destroy()
                finally:
                    app.destroy()

    def test_wheel_over_editable_child_routes_through_scanning_viewport(self):
        app = self._app(8)
        try:
            for project in app.projects:
                project["ignored"] = True
            with mock.patch.object(
                    tk.Misc, "winfo_screenheight", return_value=640):
                dialog, tree, restore = self._open(app)
            canvas = self._vertical_canvas_ancestor(tree)
            pending = list(canvas.winfo_children())
            entry = None
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if type(widget) is ttk.Entry:
                    entry = widget
                    break
            self.assertIsNotNone(entry)

            canvas.yview_moveto(0.0)
            app.update()
            entry.insert(0, "editable")
            before = canvas.yview()
            entry.event_generate("<MouseWheel>", delta=-120, x=8, y=8)
            app.update()
            self.assertGreater(canvas.yview()[0], before[0])
            self.assertIn("editable", entry.get())

            for _ in range(40):
                entry.event_generate("<MouseWheel>", delta=-120, x=8, y=8)
                app.update()
            self.assertEqual(canvas.yview()[1], 1.0)
            self._assert_vertically_reachable(app, tree, canvas)
            self._assert_vertically_reachable(app, restore, canvas)
            dialog.destroy()
        finally:
            app.destroy()

    def test_scanning_content_background_routes_wheel_to_viewport(self):
        app = self._app(8)
        try:
            for project in app.projects:
                project["ignored"] = True
            with mock.patch.object(
                    tk.Misc, "winfo_screenheight", return_value=640):
                dialog, tree, _restore = self._open(app)
            canvas = self._vertical_canvas_ancestor(tree)
            content = next(
                child for child in canvas.winfo_children()
                if isinstance(child, ttk.Frame))
            canvas.yview_moveto(0.0)
            app.update()

            before = canvas.yview()[0]
            content.event_generate(
                "<MouseWheel>", delta=-120, x=8, y=8)
            app.update()

            self.assertGreater(canvas.yview()[0], before)
            dialog.destroy()
        finally:
            app.destroy()

    def test_ignored_tree_scrolls_natively_then_hands_boundary_to_settings(self):
        app = self._app(8)
        try:
            for project in app.projects:
                project["ignored"] = True
            with mock.patch.object(
                    tk.Misc, "winfo_screenheight", return_value=640):
                dialog, tree, _restore = self._open(app)
            canvas = self._vertical_canvas_ancestor(tree)
            canvas.yview_moveto(0.25)
            tree.yview_moveto(0.0)
            tree.selection_set(tree.get_children()[0])
            app.update()

            outer_before = canvas.yview()
            tree.event_generate("<MouseWheel>", delta=-120, x=8, y=8)
            app.update()
            self.assertGreater(tree.yview()[0], 0.0)
            self.assertEqual(canvas.yview(), outer_before)
            self.assertTrue(tree.selection())

            tree.yview_moveto(1.0)
            outer_before = canvas.yview()
            tree.event_generate("<MouseWheel>", delta=-120, x=8, y=8)
            app.update()
            self.assertGreater(canvas.yview()[0], outer_before[0])
            self.assertTrue(tree.selection())
            dialog.destroy()
        finally:
            app.destroy()

    def test_repeated_settings_cycles_leave_no_global_or_duplicate_wheel_bind(self):
        app = self._app(1)
        try:
            global_before = app.bind_all("<MouseWheel>")
            movements = []
            for _ in range(3):
                with mock.patch.object(
                        tk.Misc, "winfo_screenheight", return_value=640):
                    dialog, tree, _restore = self._open(app)
                canvas = self._vertical_canvas_ancestor(tree)
                pending = list(canvas.winfo_children())
                entry = None
                while pending:
                    widget = pending.pop()
                    pending.extend(widget.winfo_children())
                    if type(widget) is ttk.Entry:
                        entry = widget
                        break
                self.assertIsNotNone(entry)
                canvas.yview_moveto(0.0)
                app.update()
                before = canvas.yview()[0]
                entry.event_generate(
                    "<MouseWheel>", delta=-120, x=8, y=8)
                app.update()
                movements.append(canvas.yview()[0] - before)
                dialog.destroy()
                app.update()
                self.assertEqual(app.bind_all("<MouseWheel>"), global_before)

            self.assertTrue(all(movement > 0.0 for movement in movements))
            self.assertAlmostEqual(max(movements), min(movements), places=7)
        finally:
            app.destroy()

    @staticmethod
    def _select(app, tree, button, project_id):
        tree.selection_set(project_id)
        tree.focus(project_id)
        app.update()
        if "disabled" in button.state():
            raise AssertionError("Restore selected was not enabled by selection")

    def test_view_lists_only_ignored_projects_with_stable_identity(self):
        app = self._app(2)
        try:
            ignored = app.projects[0]
            ignored["ignored"] = True
            active = app.projects[1]
            dialog, tree, button = self._open(app)

            self.assertEqual(tree.get_children(), (ignored["project_id"],))
            values = tree.item(ignored["project_id"], "values")
            self.assertEqual(values[0], ignored["name"])
            self.assertEqual(values[2], ignored["path"])
            self.assertEqual(values[3], ignored["project_id"])
            self.assertNotIn(active["project_id"], tree.get_children())
            self.assertIn("disabled", button.state())
            self.assertTrue(tree.winfo_ismapped())
            self.assertGreater(tree.winfo_height(), 60)
            self.assertTrue(button.winfo_ismapped())
            dialog.destroy()
        finally:
            app.destroy()

    def test_restore_persists_then_updates_ignored_and_normal_views(self):
        app = self._app(2)
        try:
            target = app.projects[0]
            target["ignored"] = True
            target_id = target["project_id"]
            store.save_projects(app.projects)
            app._populate_trees()
            dialog, tree, button = self._open(app)
            self._select(app, tree, button, target_id)

            button.invoke()
            app.update()

            self.assertIs(target["ignored"], False)
            self.assertNotIn(target_id, tree.get_children())
            self.assertIn(target_id, app.tree.get_children())
            persisted = {item["project_id"]: item
                         for item in store.load_projects()}
            self.assertIs(persisted[target_id]["ignored"], False)
            dialog.destroy()
        finally:
            app.destroy()

    def test_pre_commit_failure_rolls_memory_and_ignored_ui_back(self):
        app = self._app(1)
        try:
            target = app.projects[0]
            target["ignored"] = True
            target_id = target["project_id"]
            store.save_projects(app.projects)
            app._populate_trees()
            dialog, tree, button = self._open(app)
            self._select(app, tree, button, target_id)

            with mock.patch.object(
                    app, "_persist_projects", side_effect=OSError("disk full")), \
                    mock.patch.object(main_module.messagebox,
                                      "showerror") as shown:
                button.invoke()
                app.update()

            shown.assert_called_once()
            self.assertTrue(target["ignored"])
            self.assertTrue(store.load_projects()[0]["ignored"])
            self.assertIn(target_id, tree.get_children())
            self.assertNotIn(target_id, app.tree.get_children())
            self.assertTrue(dialog.winfo_exists())
            dialog.destroy()
        finally:
            app.destroy()

    def test_post_commit_cache_failure_remains_successful(self):
        app = self._app(1)
        try:
            target = app.projects[0]
            target["ignored"] = True
            target_id = target["project_id"]
            store.save_projects(app.projects)
            app._populate_trees()
            dialog, tree, button = self._open(app)
            self._select(app, tree, button, target_id)

            with mock.patch.object(
                    store, "_cache_recovered_workspaces",
                    side_effect=OSError("post-replace cache failure")) as cache, \
                    self.assertLogs(
                        "repo_manager.store", level="ERROR") as logs, \
                    mock.patch.object(
                        main_module.messagebox, "showerror") as shown:
                button.invoke()
                app.update()

            cache.assert_called_once_with([])
            shown.assert_not_called()
            self.assertIs(target["ignored"], False)
            self.assertNotIn(target_id, tree.get_children())
            self.assertIn(target_id, app.tree.get_children())
            persisted = json.loads(
                store.REPOS_FILE.read_text(encoding="utf-8"))["projects"]
            self.assertIs(persisted[0]["ignored"], False)
            self.assertTrue(any(
                "registry committed but recovered Workspace cache update failed"
                in message for message in logs.output))
            dialog.destroy()
        finally:
            app.destroy()

    def test_stale_ui_target_cannot_restore_same_path_replacement(self):
        app = self._app(1)
        try:
            target = app.projects[0]
            target["ignored"] = True
            target_id = target["project_id"]
            dialog, tree, button = self._open(app)
            self._select(app, tree, button, target_id)
            replacement = dict(target, project_id="replacement-id")
            app.projects[:] = [replacement]

            with mock.patch.object(app, "_persist_projects") as persist, \
                    mock.patch.object(main_module.messagebox,
                                      "showerror") as shown:
                button.invoke()
                app.update()

            persist.assert_not_called()
            shown.assert_called_once()
            self.assertTrue(replacement["ignored"])
            self.assertIn(target_id, tree.get_children())
            dialog.destroy()
        finally:
            app.destroy()


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class ScanStateOwnershipTests(_StoreIsolationMixin, unittest.TestCase):
    """Only the tracked scan's terminal event clears ``_scanning``.

    Git completion and failure events share the queue but must not clear scan
    UI state, enable the scan button, or allow a second scan to start.
    """

    def test_git_events_never_clear_scanning(self):
        app = _build_real_app(5)
        entered = threading.Event()
        release = threading.Event()
        calls = []
        real_find = scanner.find_repo_dirs

        def slow_find(roots, depth, skip):
            calls.append(1)
            entered.set()
            release.wait(10)
            return real_find(roots, depth, skip)

        with mock.patch.object(scanner, "find_repo_dirs", slow_find), \
                mock.patch("repo_manager.store.save_projects"):
            app.start_scan()
            self.assertTrue(entered.wait(5), "scan never entered the walk")
            self.assertTrue(app._scanning)

            # representative git completion event arrives mid-scan
            app._scan_queue.put(("done", (lambda ok, out: None, True, "")))
            app._drain_scan_queue()
            self.assertTrue(app._scanning)
            self.assertIn("disabled", app.scan_btn.state())

            # git failure surface is ("done", (cb, False, out)) — no clear
            app._scan_queue.put(
                ("done", (lambda ok, out: None, False, "boom")))
            app._drain_scan_queue()
            self.assertTrue(app._scanning)
            self.assertIn("disabled", app.scan_btn.state())

            # a second scan cannot start while the first is running
            app.start_scan()
            self.assertEqual(len(calls), 1)

            # the scan's own terminal event clears the state
            release.set()
            deadline = time.time() + 10
            while app._scan_queue.empty() and time.time() < deadline:
                time.sleep(0.02)
            app._drain_scan_queue()
            self.assertFalse(app._scanning)
            self.assertNotIn("disabled", app.scan_btn.state())
        app.destroy()


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class KeyboardEnterRoutingTests(_StoreIsolationMixin, unittest.TestCase):
    """Enter follows the focused tree, not stale mouse-click state."""

    MAIN = rf"C:\repos\repo00001"
    NOW = rf"C:\repos\repo00005"      # active (i % 5 == 0) -> in now tree
    NOW2 = rf"C:\repos\repo00010"     # active -> in now tree

    def _press_enter(self, app, tree):
        """Drive Enter exactly as the real binding does: the key event's
        widget is the focused tree that received the key."""
        launched = []
        with mock.patch.object(
                main_module, "resolve_primary_for_project",
                side_effect=lambda p, s: (launched.append(p["path"]) or
                                          {"label": "x", "type": "bat",
                                           "file": "x"})), \
                mock.patch.object(app, "_run_launcher"):
            app._launch_primary(_Event(tree, 0, 0, 0))
        return launched

    def test_enter_follows_focus_into_main_tree(self):
        app = _build_real_app(20)
        # last click was in Working-on-now (stale _active_tree == "now")
        app.tree.selection_set(self.MAIN)
        app._set_active("now")
        app.now_tree.selection_set(self.NOW)
        app.update()
        # keyboard-only: focus and selection move in the main tree
        app.tree.focus_set()
        app.tree.selection_set(self.MAIN)
        app.update()
        self.assertEqual(self._press_enter(app, app.tree), [self.MAIN])
        app.destroy()

    def test_enter_follows_focus_into_now_tree(self):
        app = _build_real_app(20)
        app.now_tree.selection_set(self.NOW)
        app._set_active("main")       # stale click history
        app.update()
        app.now_tree.focus_set()
        app.now_tree.selection_set(self.NOW2)
        app.update()
        self.assertEqual(self._press_enter(app, app.now_tree), [self.NOW2])
        app.destroy()

    def test_fast_enter_before_selection_sync(self):
        app = _build_real_app(20)
        app.tree.selection_set(self.MAIN)
        app._set_active("main")
        app.now_tree.focus_set()
        # selection changes in the now tree; the <<TreeviewSelect>> sync to
        # the main tree is still queued when Enter fires
        app.now_tree.selection_set(self.NOW)
        self.assertEqual(self._press_enter(app, app.now_tree), [self.NOW])
        app.destroy()

    def test_keyboard_selection_routes_detail_actions_to_main_project(self):
        app = _build_real_app(20)
        self.addCleanup(app.destroy)
        project_a = next(p for p in app.projects if p["path"] == self.NOW)
        project_b = next(p for p in app.projects if p["path"] == self.MAIN)
        main_module.projects.ensure_project_id(project_a)
        main_module.projects.ensure_project_id(project_b)
        app._populate_trees()

        # Select A in Working on now, then move keyboard focus and selection
        # to B in the main table without a mouse click.
        app.now_tree.selection_set(main_module.project_row_id(project_a))
        app.update()
        app.tree.focus_set()
        app.tree.selection_set(main_module.project_row_id(project_b))
        app.update()

        self.assertIs(app._current, project_b)
        self.assertIs(app._selected_project(), project_b)

        explorer_targets = []
        with mock.patch.object(
                main_module, "available_project_folder",
                side_effect=lambda project: (
                    explorer_targets.append(project["path"]) or project["path"])), \
                mock.patch.object(app, "_launch") as launch, \
                mock.patch.object(app, "_git_async") as git_async, \
                mock.patch.object(
                    main_module.agents, "agent_readiness",
                    return_value={
                        "state": main_module.agents.READY,
                        "availability": main_module.agents.AVAILABLE,
                        "reason": "ready",
                        "executable": sys.executable,
                        "cwd": project_b["path"],
                    }), \
                mock.patch.object(
                    main_module.agents, "start_run",
                    side_effect=lambda run, **kwargs: (
                        run.update(process_state=main_module.agents.RUNNING)
                        or mock.Mock())), \
                mock.patch.object(app, "_observe_agent_run"):
            app.open_explorer()
            app.git_pull()
            app._launch_agent()

        self.assertEqual(explorer_targets, [project_b["path"]])
        self.assertEqual(launch.call_args.args[0][-1], project_b["path"])
        git_target = git_async.call_args.args[0]
        self.assertEqual(git_target["project_id"], project_b["project_id"])
        self.assertEqual(git_target["path"], project_b["path"])
        run = app.runs[-1]
        self.assertEqual(run["target"]["project_id"], project_b["project_id"])
        self.assertEqual(run["target"]["path"], project_b["path"])


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class WorkingOnNowImmediateReconcileTests(_StoreIsolationMixin, unittest.TestCase):
    """Status edits update Working-on-now membership; pin state is separate."""

    def test_work_on_this_appears_immediately(self):
        app = _build_real_app(40)
        target = rf"C:\repos\repo00003"   # idea status (i % 5 != 0)
        self.assertNotIn(target, app.now_tree.get_children())
        proj = next(p for p in app.projects if p["path"] == target)
        app._current = None
        app._show_detail(main_module.project_row_id(proj))
        with mock.patch("repo_manager.store.save_projects"):
            app._toggle_working_on_this()
        app.update()
        self.assertEqual(proj["status"], "active")
        self.assertTrue(proj["pinned"])
        self.assertIn(target, app.now_tree.get_children())
        self.assertEqual(app.d_work_action_btn.cget("text"),
                         "Stop working on this")
        app.destroy()

    def test_stop_working_appears_immediately_and_preserves_pin(self):
        app = _build_real_app(40)
        target = rf"C:\repos\repo00005"   # active -> in now tree
        proj = next(p for p in app.projects if p["path"] == target)
        proj["status"] = "active"
        proj["pinned"] = True
        app._populate_trees()
        app._current = None
        app._show_detail(main_module.project_row_id(proj))
        app.d_status.set("active")
        app.d_pinned.set(True)
        with mock.patch("repo_manager.store.save_projects"):
            app._toggle_working_on_this()
        app.update()
        self.assertEqual(proj["status"], "paused")
        self.assertTrue(proj["pinned"])
        self.assertNotIn(target, app.now_tree.get_children())
        self.assertEqual(app.d_work_action_btn.cget("text"), "Work on this")
        app.destroy()

    def test_stop_working_preserves_unpinned_active_project(self):
        app = _build_real_app(1)
        proj = app.projects[0]
        proj["status"] = "active"
        proj["pinned"] = False
        app._populate_trees()
        app._current = None
        app._show_detail(main_module.project_row_id(proj))
        with mock.patch("repo_manager.store.save_projects"):
            app._toggle_working_on_this()
        self.assertEqual(proj["status"], "paused")
        self.assertFalse(proj["pinned"])
        app.destroy()

    def test_manual_status_change_updates_working_action(self):
        app = _build_real_app(1)
        proj = app.projects[0]
        app._current = None
        app._show_detail(main_module.project_row_id(proj))
        app.d_status.set("active")
        with mock.patch("repo_manager.store.save_projects"):
            app._save_detail()
        self.assertEqual(app.d_work_action_btn.cget("text"),
                         "Stop working on this")
        app.d_status.set("paused")
        with mock.patch("repo_manager.store.save_projects"):
            app._save_detail()
        self.assertEqual(app.d_work_action_btn.cget("text"), "Work on this")
        app.destroy()

    def test_context_menu_label_is_dynamic_and_pin_is_separate(self):
        app = _build_real_app(2)
        proj = app.projects[1]
        app._current = None
        app._show_detail(main_module.project_row_id(proj))
        app.tree.selection_set(main_module.project_row_id(proj))
        app._set_active("main")
        app._build_context_menu()
        self.assertEqual(app._ctx_menu.entrycget(0, "label"),
                         "Work on this (pin + Active)")
        self.assertEqual(app._ctx_menu.entrycget(8, "label"), "Pin / Unpin")
        proj["status"] = "active"
        app._build_context_menu()
        self.assertEqual(app._ctx_menu.entrycget(0, "label"),
                         "Stop working on this")
        self.assertEqual(app._ctx_menu.entrycget(8, "label"), "Pin / Unpin")
        app.destroy()

    def test_deactivation_removes_immediately(self):
        app = _build_real_app(40)
        target = rf"C:\repos\repo00005"   # active -> in now tree
        self.assertIn(target, app.now_tree.get_children())
        proj = next(p for p in app.projects if p["path"] == target)
        app._current = proj
        app.d_status.set("archived")
        app.d_pinned.set(False)
        with mock.patch("repo_manager.store.save_projects"):
            app._save_detail()
        app.update()
        self.assertNotIn(target, app.now_tree.get_children())
        app.destroy()

    def test_saving_inactive_form_status_removes_active_row(self):
        app = _build_real_app(40)
        target = rf"C:\repos\repo00005"   # active fixture; the untouched status field is inactive
        self.assertIn(target, app.now_tree.get_children())
        proj = next(p for p in app.projects if p["path"] == target)
        app._current = proj
        app.d_pinned.set(False)
        with mock.patch("repo_manager.store.save_projects"):
            app._save_detail()
        app.update()
        self.assertNotIn(target, app.now_tree.get_children())
        app.destroy()

    def test_unrelated_rows_keep_identity_and_order(self):
        app = _build_real_app(40)
        before = [i for i in app.now_tree.get_children()
                  if i != "__won-empty__"]
        target = before[0]
        proj = next(p for p in app.projects if p["path"] == target)
        app._current = proj
        app.d_status.set("archived")
        with mock.patch("repo_manager.store.save_projects"):
            app._save_detail()
        app.update()
        after = [i for i in app.now_tree.get_children()
                 if i != "__won-empty__"]
        self.assertNotIn(target, after)
        self.assertEqual(after, before[1:])   # identity + order preserved
        app.destroy()

    def test_placeholder_restored_when_now_empties(self):
        app = _build_real_app(40)
        for p in app.projects:
            p["status"] = "archived"
        app._populate_trees()
        app.update()
        self.assertEqual(app.now_tree.get_children(), ("__won-empty__",))
        app.destroy()


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class SettingsIsolationRegressionTests(unittest.TestCase):
    """GUI settings actions use the redirected store paths.

    These tests manage the isolation directly (not via the shared mixin) so
    both the redirected-target proof and the restoration proof can be checked
    inside a single test body.
    """

    def setUp(self):
        self._iso = _IsolatedStorePaths()
        self.addCleanup(self._iso.close)

    def test_sort_by_writes_settings_to_isolated_path_only(self):
        app = _build_real_app(5)
        try:
            app._sort_by("name")   # persists self.settings via store.save_settings
            app.update()
            isolated = Path(store.SETTINGS_FILE)
            self.assertEqual(isolated.parent, self._iso.app_dir)
            self.assertTrue(isolated.exists(),
                            "settings.json should be written in the isolated dir")
            written = __import__("json").loads(isolated.read_text(encoding="utf-8"))
            self.assertEqual(written["sort"], ["name", False])
        finally:
            app.destroy()

    def test_real_store_paths_restored_after_isolation_closes(self):
        real_dir = store_conf_dir()
        self.assertNotEqual(store.APP_DIR, real_dir)  # currently isolated
        self._iso.close()
        self._iso = None
        self.assertEqual(store.APP_DIR, real_dir)     # restored to real path
        self.assertEqual(store.SETTINGS_FILE, real_dir / "settings.json")


def store_conf_dir():
    """The un-patched base directory under which RepoManager app data lives."""
    import os as _os
    return Path(_os.environ.get("LOCALAPPDATA", str(Path.home()))) / "RepoManager"


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class MoveRevalidationWiringGuiTests(_StoreIsolationMixin, unittest.TestCase):
    """Confirmed moves revalidate targets with fresh filesystem checks.

    A vanished new path or reappeared old path must block the move rather than
    silently selecting another target.
    """

    def _suggestion(self, app, new_path):
        return {"kind": "move", "category": "strong",
                "old_path": app.projects[0]["path"],
                "new_path": new_path,
                "name": app.projects[0]["name"], "evidence": []}

    def test_accept_move_blocks_stale_target(self):
        app = _build_real_app(1)
        try:
            old = app.projects[0]["path"]
            new = old + r"\moved"  # does not exist on disk
            errors = []
            with mock.patch("tkinter.messagebox.showerror",
                            side_effect=lambda *a, **k: errors.append(a[1])), \
                    mock.patch("tkinter.messagebox.showinfo"), \
                    mock.patch("tkinter.messagebox.showwarning"):
                app._accept_move(self._suggestion(app, new))
            self.assertTrue(any("changed since the scan" in msg
                                for msg in errors))
            self.assertEqual(app.projects[0]["path"], old)  # nothing moved
        finally:
            app.destroy()

    def test_accept_move_still_succeeds_when_target_is_present(self):
        """The wiring must not break the happy path when the target exists."""
        app = _build_real_app(1)
        try:
            old = app.projects[0]["path"]
            with tempfile.TemporaryDirectory() as tmp:
                new = str(Path(tmp) / "Repo")
                Path(new).mkdir()
                with mock.patch("tkinter.messagebox.showerror"), \
                        mock.patch("tkinter.messagebox.showinfo"), \
                        mock.patch("tkinter.messagebox.showwarning"):
                    app._accept_move(self._suggestion(app, new))
                self.assertEqual(app.projects[0]["path"], new)  # moved
        finally:
            app.destroy()

    def test_accept_moves_batch_reports_stale_target_failure(self):
        app = _build_real_app(1)
        try:
            old = app.projects[0]["path"]
            new = old + r"\moved"  # does not exist on disk
            app._move_suggestions = [self._suggestion(app, new)]
            shown = []
            with mock.patch("tkinter.messagebox.showinfo",
                            side_effect=lambda *a, **k: shown.append(a[1])), \
                    mock.patch("tkinter.messagebox.showerror"):
                app._accept_moves_batch()
            self.assertTrue(any("Failed/skipped: 1" in msg for msg in shown))
            self.assertEqual(app.projects[0]["path"], old)  # nothing moved
        finally:
            app.destroy()


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class DetailObservationAsyncTests(_StoreIsolationMixin, unittest.TestCase):
    """Local health/launcher detail work must stay off the Tk callback."""

    def test_slow_health_does_not_block_selection_callback(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        entered = threading.Event()
        release = threading.Event()
        calls = []
        real_health = main_module.health.evaluate_repository

        def slow_health(path, metadata):
            entered.set()
            release.wait(5)
            calls.append(threading.current_thread().ident)
            return real_health(path, metadata)

        with mock.patch.object(main_module.health, "evaluate_repository",
                               side_effect=slow_health):
            started = time.monotonic()
            app._show_detail(main_module.project_row_id(app.projects[0]))
            callback_elapsed = time.monotonic() - started
            self.assertLess(callback_elapsed, 1.0)
            self.assertTrue(entered.wait(2))
            self.assertIsNone(app._health_result)
            self.assertIn("checking", app.d_health_status.cget("text").casefold())
            release.set()
            self.assertTrue(_pump_until(app, lambda: app._health_result is not None))
            self.assertTrue(calls)
            self.assertNotIn(threading.main_thread().ident, calls)

    def test_slow_launcher_discovery_does_not_block_selection_callback(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        entered = threading.Event()
        release = threading.Event()
        thread_ids = []
        real_detect = main_module.launchers.detect_commands

        def slow_detect(*args, **kwargs):
            thread_ids.append(threading.current_thread().ident)
            entered.set()
            release.wait(5)
            return real_detect(*args, **kwargs)

        with mock.patch.object(main_module.launchers, "detect_commands",
                               side_effect=slow_detect):
            started = time.monotonic()
            app._show_detail(main_module.project_row_id(app.projects[0]))
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertTrue(entered.wait(2))
            self.assertIn("loading", " ".join(
                widget.cget("text") for widget in app.d_launch.winfo_children()
                if "text" in widget.keys()).casefold())
            self.assertNotIn(threading.main_thread().ident, thread_ids)
            release.set()
            self.assertTrue(_pump_until(app, lambda: app._health_result is not None))

    def test_new_selection_clears_previous_health_until_result_arrives(self):
        app = _build_real_app(2)
        self.addCleanup(app.destroy)
        first, second = app.projects
        first_result = main_module.health.HealthResult(
            main_module.health.PASS, (), "first")
        with mock.patch.object(main_module, "compute_detail_observation",
                               return_value={"health": first_result,
                                             "launchers": (), "note": ""}):
            app._show_detail(main_module.project_row_id(first))
            self.assertTrue(_pump_until(app, lambda: app._health_result is first_result))

        entered = threading.Event()
        release = threading.Event()
        with mock.patch.object(
                main_module, "compute_detail_observation",
                side_effect=lambda *_args: (
                    entered.set(), release.wait(5),
                    {"health": first_result, "launchers": (), "note": ""})[-1]):
            app._show_detail(main_module.project_row_id(second))
            self.assertTrue(entered.wait(2))
            self.assertIsNone(app._health_result)
            release.set()

    def test_rapid_a_b_c_keeps_c_authoritative(self):
        app = _build_real_app(3)
        self.addCleanup(app.destroy)
        started = []
        releases = {}
        real_compute = main_module.compute_detail_observation
        for project in app.projects:
            project["project_id"] = f"detail-{project['name']}"
            releases[project["project_id"]] = threading.Event()

        def controlled_compute(project, settings):
            project_id = project["project_id"]
            started.append(project_id)
            releases[project_id].wait(5)
            return real_compute(project, settings)

        with mock.patch.object(main_module, "compute_detail_observation",
                               side_effect=controlled_compute):
            for project in app.projects:
                app._show_detail(main_module.project_row_id(project))
            self.assertEqual(app._current, app.projects[2])
            self.assertTrue(_pump_until(app, lambda: len(started) >= 1, timeout=2))
            releases[started[0]].set()
            # The single worker coalesces B while A is running; C is the only
            # pending request that may run after A finishes.
            self.assertTrue(_pump_until(
                app, lambda: len(started) >= 2, timeout=3))
            self.assertEqual(started, [
                app.projects[0]["project_id"], app.projects[2]["project_id"]])
            releases[started[1]].set()
            self.assertTrue(_pump_until(app, lambda: app._health_result is not None))
            self.assertEqual(app._current, app.projects[2])
            self.assertEqual(
                app.d_name.cget("text"),
                main_module.projects.project_display_name(app.projects[2]))
            final_result = app._health_result
            stale_result = main_module.health.HealthResult(
                main_module.health.FAIL, (), "stale-A")
            app._apply_detail_observation(
                {"project_id": app.projects[0]["project_id"],
                 "path": app.projects[0]["path"]},
                {"health": stale_result, "launchers": (), "note": "stale"},
                1)
            self.assertIs(app._health_result, final_result)
            self.assertEqual(app._current, app.projects[2])
            for project_id, release in releases.items():
                release.set()

    def test_selection_remains_valid_during_active_scan(self):
        app = _build_real_app(2)
        self.addCleanup(app.destroy)
        entered = threading.Event()
        release = threading.Event()
        original = main_module.compute_detail_observation

        def slow_compute(project, settings):
            entered.set()
            release.wait(5)
            return original(project, settings)

        app._scanning = True
        with mock.patch.object(main_module, "compute_detail_observation",
                               side_effect=slow_compute):
            target = app.projects[1]
            app._show_detail(main_module.project_row_id(target))
            self.assertIs(app._current, target)
            self.assertTrue(entered.wait(2))
            self.assertIn("checking", app.d_health_status.cget("text").casefold())
            release.set()
            self.assertTrue(_pump_until(app, lambda: app._health_result is not None))
            self.assertIs(app._current, target)
            self.assertEqual(
                app.d_name.cget("text"),
                main_module.projects.project_display_name(target))

    def test_folder_only_detail_never_uses_repository_launcher_path(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        folder_path = project.pop("path")
        project["folder_path"] = folder_path
        with mock.patch.object(main_module, "compute_detail_observation",
                               return_value={"health": None, "launchers": (),
                                             "note": ""}):
            app._show_detail(main_module.project_row_id(project))
            self.assertTrue(_pump_until(
                app, lambda: "Unavailable" in " ".join(
                    widget.cget("text") for widget in app.d_launch.winfo_children()
                    if "text" in widget.keys())))

        self.assertIn("no associated repository", " ".join(
            widget.cget("text") for widget in app.d_launch.winfo_children()
            if "text" in widget.keys()).casefold())

    def test_shutdown_with_pending_detail_has_no_late_widget_result(self):
        app = _build_real_app(1)
        entered = threading.Event()
        release = threading.Event()
        original = main_module.compute_detail_observation

        def slow_compute(project, settings):
            entered.set()
            release.wait(5)
            return original(project, settings)

        with mock.patch.object(main_module, "compute_detail_observation",
                               side_effect=slow_compute):
            app._show_detail(main_module.project_row_id(app.projects[0]))
            self.assertTrue(entered.wait(2))
            app._closing = True
            app.destroy()
            release.set()
            time.sleep(0.05)
        self.assertTrue(app._closing)


class QueueBudgetTests(unittest.TestCase):
    """A worker backlog cannot monopolize one Tk queue-drain callback."""

    def test_detail_events_are_bounded_and_remaining_events_are_drained(self):
        app = object.__new__(main_module.RepoManagerApp)
        app._closing = False
        app._scan_queue = queue.Queue()
        applied = []
        scheduled = []
        app._apply_detail_observation = lambda target, observation, gen: applied.append(gen)
        app._schedule_after = lambda delay, callback: scheduled.append((delay, callback)) or "job"
        for generation in range(40):
            app._scan_queue.put(("detail", {"path": str(generation)}, {}, generation))

        app._drain_scan_queue()
        self.assertEqual(len(applied), main_module.QUEUE_DRAIN_MAX_EVENTS)
        self.assertEqual(app._scan_queue.qsize(), 40 - main_module.QUEUE_DRAIN_MAX_EVENTS)
        self.assertEqual(scheduled[-1][0], 0)

        while not app._scan_queue.empty():
            app._drain_scan_queue()
        self.assertEqual(applied, list(range(40)))
        self.assertEqual(scheduled[-1][0], main_module.REFRESH_MS)


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class ProviderObservationAsyncTests(_StoreIsolationMixin, unittest.TestCase):
    """Provider observation must not block the Tk main thread.

    Requests run off-thread so a slow provider or DNS lookup does not freeze
    selection handling.
    """

    def _app(self, payload=None):
        app = _build_real_app(5)
        calls = []

        def transport(request, timeout):
            calls.append(request.full_url)
            if payload is not None:
                return payload
            owner, name = request.full_url.rsplit("/", 2)[-2:]
            return {"id": len(calls), "name": name, "private": False,
                    "default_branch": "main",
                    "html_url": f"https://github.com/{owner}/{name}",
                    "owner": {"login": owner}}

        app.provider = providers.GitHubAdapter(transport=transport)
        return app, calls

    @staticmethod
    def _wait_for_queue(app, timeout=10.0):
        """Wait until the worker's terminal event reaches the queue."""
        deadline = time.time() + timeout
        while app._scan_queue.empty() and time.time() < deadline:
            time.sleep(0.02)
        app._drain_scan_queue()

    def test_apply_provider_keeps_online_not_found_as_warning(self):
        """Online 404 evidence must not make valid local correspondence red."""
        app, _calls = self._app()
        try:
            project = app.projects[0]
            project.update({
                "remote": "github.com/acme/demo",
                "remote_name": "origin",
                "remotes": ["github.com/acme/demo"],
            })
            app._current = project
            app._provider_gen = 1
            correspondence = providers.provider_correspondence(project["remote"])
            observation = providers.ProviderObservation(
                "github", providers.NOT_FOUND, providers.CURRENT,
                "2026-09-03T00:00:00+00:00", correspondence=correspondence,
                evidence=("GitHub returned HTTP 404",),
                error="repository not found")

            app._apply_provider(
                {"project_id": main_module.projects.project_id(project),
                 "path": project["path"], "remote": project["remote"],
                 "_record": project},
                observation, 1)

            self.assertEqual(
                app.provider_status.cget("style"),
                "Semantic.Warning.TLabel")
            self.assertIn("Online details: Unavailable · repository not found",
                          app.provider_status.cget("text"))
            self.assertIs(app._provider_observation, observation)
        finally:
            app.destroy()

    def test_theme_toggle_preserves_provider_warning_semantics(self):
        """Theme changes must not reinterpret online Provider evidence."""
        app, _calls = self._app()
        try:
            project = app.projects[0]
            project.update({
                "remote": "github.com/acme/demo",
                "remote_name": "origin",
                "remotes": ["github.com/acme/demo"],
            })
            app._current = project
            app._provider_gen = 1
            correspondence = providers.provider_correspondence(project["remote"])
            observation = providers.ProviderObservation(
                "github", providers.NOT_FOUND, providers.CURRENT,
                "2026-09-03T00:00:00+00:00", correspondence=correspondence,
                evidence=("GitHub returned HTTP 404",),
                error="repository not found")
            target = {
                "project_id": main_module.projects.project_id(project),
                "path": project["path"], "remote": project["remote"],
                "_record": project,
            }

            app._apply_provider(target, observation, 1)
            self.assertEqual(app.provider_status.cget("style"),
                             "Semantic.Warning.TLabel")
            self.assertIn("Online details: Unavailable · repository not found",
                          app.provider_status.cget("text"))

            app.toggle_theme()

            self.assertEqual(app.provider_status.cget("style"),
                             "Semantic.Warning.TLabel")
            self.assertIn("Online details: Unavailable · repository not found",
                          app.provider_status.cget("text"))
            self.assertIs(app._provider_observation, observation)
        finally:
            app.destroy()

    def test_show_detail_does_not_request_provider_on_tk_thread(self):
        """The request must run on a worker thread, not in the Tk callback."""
        app, _calls = self._app()
        try:
            target = app.projects[0]["path"]
            entered = threading.Event()
            release = threading.Event()
            thread_ids = []
            real_observe = app.provider.observe

            def slow_observe(remote):
                thread_ids.append(threading.current_thread().ident)
                entered.set()
                release.wait(10)  # hold the worker until the test releases it
                return real_observe(remote)

            with mock.patch.object(app.provider, "observe", slow_observe):
                app._show_detail(target)
                # the callback returned without issuing the request
                self.assertTrue(entered.wait(5),
                                "observe() never entered the worker")
                self.assertNotIn(threading.main_thread().ident, thread_ids,
                                 "observe() ran on the Tk main thread")
                self.assertIsNone(app._provider_observation)
                self.assertIn("checking",
                              app.provider_status.cget("text").casefold())
                release.set()
                # the queued result then updates the label on the main thread
                self._wait_for_queue(app)
            self.assertEqual(app._provider_observation.status,
                             providers.AVAILABLE)
            self.assertIn("GitHub", app.provider_status.cget("text"))
        finally:
            app.destroy()

    def test_stale_result_for_previous_selection_is_discarded(self):
        """A slow response for a superseded selection must not be applied."""
        app, calls = self._app()
        try:
            first = app.projects[0]["path"]
            app._show_detail(first)
            second = app.projects[1]["path"]
            app._current = next(p for p in app.projects
                                if p["path"] == second)
            self._wait_for_queue(app)
            self.assertEqual(len(calls), 1)
            self.assertIsNone(app._provider_observation,
                              "stale observation was applied")
        finally:
            app.destroy()

    def test_newer_selection_supersedes_by_generation(self):
        """Two rapid selections: only the newest request may render."""
        app, calls = self._app()
        try:
            app._show_detail(app.projects[0]["path"])
            app._show_detail(app.projects[1]["path"])
            self._wait_for_queue(app)
            self.assertEqual(len(calls), 2)
            self.assertEqual(app._provider_observation.status,
                             providers.AVAILABLE)
            self.assertEqual(
                (app._current.get("path") or ""),
                app.projects[1]["path"])
        finally:
            app.destroy()

    def test_full_scan_remote_change_supersedes_old_observation(self):
        """A scan changing the remote must invalidate its old response."""
        app, _calls = self._app()
        project = app.projects[0]
        project["project_id"] = "provider-project"
        project["remote"] = "github.com/acme/old"
        project["remote_name"] = "origin"
        project["remotes"] = [project["remote"]]
        old_entered = threading.Event()
        release_old = threading.Event()
        new_entered = threading.Event()
        calls = []

        old_payload = {
            "id": 1, "name": "old", "private": False,
            "default_branch": "old-main",
            "html_url": "https://github.com/acme/old",
            "owner": {"login": "acme"},
        }
        new_payload = {
            "id": 2, "name": "new", "private": False,
            "default_branch": "new-main",
            "html_url": "https://github.com/acme/new",
            "owner": {"login": "acme"},
        }

        def transport(request, timeout):
            calls.append(request.full_url)
            if request.full_url.endswith("/old"):
                old_entered.set()
                release_old.wait(10)
                return old_payload
            new_entered.set()
            return new_payload

        app.provider = providers.GitHubAdapter(transport=transport)
        try:
            app._show_detail(main_module.project_row_id(project))
            self.assertTrue(old_entered.wait(5), "old provider request did not start")

            scanned = dict(project)
            scanned.update({
                "remote": "github.com/acme/new",
                "remote_name": "origin",
                "remotes": ["github.com/acme/new"],
                "branch": "main",
                "dirty": 0,
                "status_available": True,
            })
            app._scan_queue.put(("result", [scanned], [], app._scan_gen))
            with mock.patch.object(main_module.store, "save_projects"):
                app._drain_scan_queue()

            self.assertEqual(app._current["remote"], "github.com/acme/new")
            self.assertIn("acme/new", app.provider_status.cget("text"))
            release_old.set()
            self.assertTrue(new_entered.wait(5), "new provider request did not start")

            deadline = time.time() + 10
            while time.time() < deadline:
                app._drain_scan_queue()
                observation = app._provider_observation
                if (observation is not None
                        and observation.default_branch == "new-main"):
                    break
                time.sleep(0.02)

            self.assertEqual(len(calls), 2)
            self.assertIsNotNone(app._provider_observation)
            self.assertEqual(app._provider_observation.default_branch, "new-main")
            text = app.provider_status.cget("text")
            self.assertIn("acme/new", text)
            self.assertIn("new-main", text)
            self.assertNotIn("old-main", text)
        finally:
            release_old.set()
            app.destroy()


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class V1AcceptanceInteractionTests(_StoreIsolationMixin, unittest.TestCase):
    def test_project_id_keeps_windows_path_out_of_treeview_identity(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        project["path"] = r"C:\Repos with spaces\Grüße\demo"
        project_id = main_module.projects.ensure_project_id(project)
        app._populate_trees()
        app.update()

        self.assertIn(project_id, app.tree.get_children())
        app.tree.selection_set(project_id)
        app.update()
        self.assertEqual(app.tree.selection(), (project_id,))

    def test_agent_start_immediately_enables_stop_action(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        with tempfile.TemporaryDirectory() as target:
            project = app.projects[0]
            project["path"] = target
            project_id = main_module.projects.ensure_project_id(project)
            app.agent = main_module.agents.new_agent(
                "test", "Test agent", sys.executable,
                args=["-c", "pass"])
            app._populate_trees()
            app.tree.selection_set(project_id)
            app._set_active("main")
            app.update()
            app._refresh_agent_status()

            process = mock.Mock()
            process.pid = 1234
            def start(run, **_kwargs):
                run["process_state"] = main_module.agents.RUNNING
                return process
            with mock.patch.object(
                    main_module.agents, "start_run", side_effect=start), \
                    mock.patch.object(app, "_observe_agent_run"):
                app._launch_agent()

            self.assertNotIn("disabled", app.agent_stop_btn.state())

    def test_healthy_summary_keeps_informational_warnings_secondary(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        timestamp = "2026-09-02T00:00:00Z"
        required = main_module.health.Finding(
            "required", rule="Required check", status=main_module.health.PASS,
            explanation="Required check passed.", timestamp=timestamp,
            importance=main_module.health.REQUIRED)
        informational = main_module.health.Finding(
            "docs", rule="Optional docs", status=main_module.health.WARN,
            explanation="Optional docs were not found.", timestamp=timestamp,
            importance=main_module.health.INFORMATIONAL)
        result = main_module.health.HealthResult(
            main_module.health.PASS, (required, informational), timestamp)

        with mock.patch.object(main_module.health, "evaluate_repository",
                               return_value=result), \
                mock.patch.object(app, "_show_provider"):
            app._show_health(app.projects[0])

        summary = app.d_health.cget("text")
        self.assertIn("No required or recommended action found", summary)
        self.assertIn("1 informational observation", summary)
        self.assertNotIn("WARN:", summary)

    def test_metadata_refresh_updates_selected_detail_health_and_worktrees(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        project["project_id"] = "refresh-project"
        project["remote"] = None
        project["status_available"] = True
        project["broken"] = False
        project["worktrees"] = [{"path": project["path"], "current": True}]
        app._populate_trees()
        row = main_module.project_row_id(project)

        initial = main_module.health.HealthResult(
            main_module.health.PASS,
            (main_module.health.Finding(
                "clean", rule="Working tree", status=main_module.health.PASS,
                explanation="clean", timestamp="initial"),),
            "initial")
        refreshed = main_module.health.HealthResult(
            main_module.health.WARN,
            (main_module.health.Finding(
                "dirty", rule="Working tree", status=main_module.health.WARN,
                explanation="dirty", timestamp="refreshed"),),
            "refreshed")

        app.tree.focus_set()
        with mock.patch.object(
                main_module.health, "evaluate_repository",
                side_effect=[initial, refreshed]) as evaluate, \
                mock.patch.object(app, "_show_provider"), \
                mock.patch.object(app, "_populate_launchers"):
            app.tree.selection_set(row)
            self.assertTrue(_pump_until(
                app, lambda: "Healthy" in app.d_health_status.cget("text")))
            self.assertEqual(
                app.d_worktrees.cget("text"),
                "Worktree: this checkout only")

            metadata = dict(project)
            metadata.update({
                "branch": "feature/refresh",
                "dirty": 1,
                "staged": 1,
                "unstaged": 0,
                "untracked": 0,
                "status_available": True,
                "worktrees": [
                    {"path": project["path"], "current": True},
                    {"path": project["path"] + "-linked",
                     "branch": "feature/refresh", "current": False},
                ],
            })
            app._metadata_gen = 1
            app._scanning = True
            app._scan_queue.put((
                "meta",
                [(project["project_id"], project["path"], metadata, None)],
                1))
            with mock.patch.object(main_module.store, "save_projects"):
                app._drain_scan_queue()
            self.assertTrue(_pump_until(
                app, lambda: "Needs attention" in app.d_health_status.cget("text")))

        values = app.tree.item(row, "values")
        self.assertEqual(values[3], "feature/refresh")
        self.assertEqual(values[4], "1")
        self.assertEqual(
            app.d_worktrees.cget("text"),
            "Worktrees: main + 1 linked (feature/refresh)")
        self.assertIn("Needs attention", app.d_health_status.cget("text"))
        self.assertIs(app._health_result, refreshed)
        self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(evaluate.call_args_list[1].args[1]["dirty"], 1)

    def test_filter_does_not_redefine_working_on_now_and_clears_stale_detail(self):
        app = _build_real_app(10)
        self.addCleanup(app.destroy)
        active = app.projects[0]
        selected = app.projects[1]
        app._set_active("main")
        app.tree.selection_set(selected["path"])
        app.update()
        self.assertIs(app._current, selected)

        app.filter_var.set(app.projects[2]["name"])
        app._populate_trees()
        app.update()

        self.assertIn(active["path"], app.now_tree.get_children())
        self.assertNotIn(selected["path"], app.tree.get_children())
        self.assertEqual(app.tree.selection(), ())
        self.assertIsNone(app._current)
        self.assertEqual(app.d_name.cget("text"), "")

    def test_filtered_out_active_project_remains_navigable_from_now(self):
        app = _build_real_app(10)
        self.addCleanup(app.destroy)
        active = app.projects[0]
        app.filter_var.set(app.projects[1]["name"])
        app._populate_trees()
        self.assertNotIn(active["path"], app.tree.get_children())
        self.assertIn(active["path"], app.now_tree.get_children())

        app._set_active("now")
        app.now_tree.selection_set(active["path"])
        app.update()
        self.assertIs(app._current, active)
        self.assertIs(app._selected_project(), active)

    def test_working_on_now_empty_state_names_only_active_membership(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        app.projects[0]["status"] = "idea"
        app.projects[0]["pinned"] = True
        app._populate_trees()
        text = app.now_tree.item("__won-empty__", "values")[0]
        self.assertIn("Active", text)
        self.assertNotIn("pin", text.casefold())

    def test_common_detail_actions_precede_technical_and_secondary_content(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        row = main_module.project_row_id(app.projects[0])
        app.tree.selection_set(row)
        app.update()

        self.assertEqual(app.d_project_id.winfo_manager(), "")
        self.assertEqual(app.d_classification.cget("text"),
                         "Type: Git Repository")
        section_order = [
            app.d_health_section.winfo_y(),
            app.d_launch_section.winfo_y(),
            app.d_notes_section.winfo_y(),
            app.d_provider_section.winfo_y(),
            app.d_export_section.winfo_y(),
        ]
        self.assertEqual(section_order, sorted(section_order))

        with mock.patch.object(app, "_open_text_dialog") as open_details:
            app._open_project_technical_details()
        title, content = open_details.call_args.args[:2]
        self.assertEqual(title, "Technical project details")
        self.assertIn("Project ID:", content)
        self.assertIn("Classification evidence:", content)

    def test_all_launchers_dialog_handles_structured_command_tuple(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        command = main_module.launchers.LauncherCandidate(
            label="Run", type="custom", cwd=tempfile.gettempdir(),
            command=("tool.exe", "--safe"), healthy=False,
            reason="tool unavailable")
        app._show_more_launchers([command])
        app.update()
        dialogs = [child for child in app.winfo_children()
                   if isinstance(child, tk.Toplevel)
                   and child.title() == "All launchers"]
        self.assertEqual(len(dialogs), 1)
        trees = [child for frame in dialogs[0].winfo_children()
                 for child in frame.winfo_children()
                 if isinstance(child, ttk.Treeview)]
        self.assertEqual(len(trees), 1)
        self.assertEqual(trees[0].cget("columns"),
                         ("name", "type", "command", "cwd", "source", "status"))
        self.assertEqual(trees[0].heading("name", "text"), "Name")
        self.assertTrue(trees[0].cget("xscrollcommand"))
        row = trees[0].get_children()[0]
        self.assertEqual(trees[0].item(row, "values")[0], "Run")
        self.assertEqual(trees[0].item(row, "values")[2], "tool.exe --safe")
        dialogs[0].destroy()

    def test_provider_summary_exposes_source_conflict_and_access_boundary(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        project = app.projects[0]
        project.update({
            "remote": "github.com/acme/demo", "remote_name": "origin",
            "remotes": ["github.com/acme/demo", "gitlab.com/acme/demo"],
        })
        correspondence = providers.provider_correspondence(project["remote"])
        lines = app._provider_local_lines(
            project, correspondence, online="Checking GitHub…")
        text = "\n".join(lines)
        self.assertIn("from origin", text)
        self.assertIn("Github (github.com)", text)
        self.assertIn("Gitlab (gitlab.com)", text)
        self.assertIn("Ownership / access: Not inferred", text)


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class AgentClosePolicyGuiTests(_StoreIsolationMixin, unittest.TestCase):
    class Process:
        pid = 4321

        def __init__(self):
            self.code = None
            self.terminated = False

        def poll(self):
            return self.code

        def terminate(self):
            self.terminated = True

    def _running_app(self):
        app = _build_real_app(1)
        run = {
            "run_id": "run-1", "agent_id": app.agent["agent_id"],
            "cwd": app.projects[0]["path"], "target": {}, "argv": [],
            "process_state": main_module.agents.RUNNING,
            "verification": main_module.agents.NOT_RUN,
        }
        process = self.Process()
        app.runs.append(run)
        app._agent_processes[run["run_id"]] = process
        return app, run, process

    def test_cancel_closing_leaves_running_agent_and_window_untouched(self):
        app, run, process = self._running_app()
        self.addCleanup(app.destroy)
        with mock.patch.object(
                app, "_confirm_stop_agents_and_close", return_value=False), \
                mock.patch.object(app, "_finish_close") as finish:
            app._on_close()
        finish.assert_not_called()
        self.assertFalse(process.terminated)
        self.assertEqual(run["process_state"], main_module.agents.RUNNING)

    def test_stop_and_close_waits_for_confirmed_process_exit(self):
        app, run, process = self._running_app()
        self.addCleanup(app.destroy)
        with mock.patch.object(
                app, "_confirm_stop_agents_and_close", return_value=True), \
                mock.patch.object(app, "_finish_close") as finish:
            app._on_close()
            self.assertTrue(process.terminated)
            self.assertEqual(run["process_state"],
                             main_module.agents.STOPPING)
            finish.assert_not_called()
            process.code = 0
            with mock.patch.object(
                    main_module.scanner, "collect_metadata",
                    return_value={"branch": "main", "head": "abc"}):
                app._observe_agent_run(run, process)
            finish.assert_called_once()
        self.assertEqual(run["process_state"],
                         main_module.agents.TERMINATED)
        self.assertEqual(run["verification"],
                         main_module.agents.TARGET_RECHECKED)
        self.assertIn("Target rechecked", app.run_status.cget("text"))

@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class GitClosePolicyGuiTests(_StoreIsolationMixin, unittest.TestCase):
    """the window must not close while a Git mutation is in flight."""

    def test_close_is_deferred_while_git_worker_is_active(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        delivered = []
        done = app._track_git_worker(
            lambda outcome, out: delivered.append(outcome))
        with mock.patch.object(app, "_finish_close") as finish, \
                mock.patch.object(app, "_schedule_after_idle",
                                  side_effect=lambda cb: cb() or "idle-0"):
            app._on_close()
            finish.assert_not_called()
            self.assertTrue(app._close_after_git)
            done(main_module.GIT_SUCCESS, "ok")  # terminal result arrives
        self.assertEqual(delivered, [main_module.GIT_SUCCESS])
        self.assertFalse(app._close_after_git)
        finish.assert_called_once()

    def test_thread_start_failure_still_resumes_deferred_close(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        with mock.patch.object(main_module.threading.Thread, "start",
                               side_effect=RuntimeError("no threads")):
            app._git_async(app.projects[0], lambda o, out: None, "status")
        kind, payload = app._scan_queue.get_nowait()
        self.assertEqual(kind, "done")
        done, outcome, _out = payload
        self.assertEqual(outcome, main_module.GIT_FAILED)
        with mock.patch.object(app, "_finish_close") as finish, \
                mock.patch.object(app, "_schedule_after_idle",
                                  side_effect=lambda cb: cb() or "idle-0"):
            app._on_close()
            finish.assert_not_called()  # worker token still outstanding
            done(main_module.GIT_FAILED, _out)
        finish.assert_called_once()


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class StubGenerationGuiTests(_StoreIsolationMixin, unittest.TestCase):
    """run.bat generation must not report success when no file exists."""

    def test_stub_generation_reports_when_file_was_not_created(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        proj = app.projects[0]
        with mock.patch.object(main_module.messagebox, "askyesno",
                               return_value=True), \
                mock.patch.object(main_module.launchers, "generate_stub_bat",
                                  return_value=False), \
                mock.patch.object(app, "_populate_launchers") as populate:
            app._generate_stub(proj)
        populate.assert_called_once_with(proj)
        self.assertIn("not created", app._status._var.get())


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class WorkspaceHeadingHardeningGuiTests(_StoreIsolationMixin,
                                        unittest.TestCase):
    """partial Workspace metadata must never render as READY."""

    def _selected_app(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        workspace = workspaces.new_workspace("Feature X")
        app._workspace_gen = 7
        app.workspace_var.set(workspace["name"])
        app._workspace_by_name = {workspace["name"]: workspace}
        return app, workspace

    def test_ready_is_demoted_when_member_dirty_is_partial(self):
        app, workspace = self._selected_app()
        app._apply_workspace_inspection(
            workspaces.workspace_id(workspace), {
                "status": "READY", "member_count": 1,
                "members": [{"state": "VALID", "dirty": "many"}],
                "counts": {"valid": 1, "missing": 0, "stale": 0},
            }, 7)
        text = app.workspace_status.cget("text")
        self.assertNotIn("READY", text)
        self.assertIn("UNKNOWN", text)

    def test_malformed_inspection_does_not_crash_heading(self):
        app, workspace = self._selected_app()
        app._apply_workspace_inspection(
            workspaces.workspace_id(workspace), None, 7)
        text = app.workspace_status.cget("text")
        self.assertIn("UNKNOWN", text)
        self.assertIn("0 members", text)

    def test_missing_and_untracked_counts_are_distinct(self):
        app, workspace = self._selected_app()
        app._apply_workspace_inspection(
            workspaces.workspace_id(workspace), {
                "status": "BLOCKED", "member_count": 5,
                "members": [
                    {"state": "MISSING", "dirty": 0},
                    {"state": "MISSING", "dirty": 0},
                    {"state": "UNTRACKED", "dirty": 0},
                    {"state": "UNTRACKED", "dirty": 0},
                    {"state": "UNTRACKED", "dirty": 0},
                ],
                "counts": {"valid": 0, "missing": 2, "untracked": 3,
                           "stale": 0},
            }, 7)
        text = app.workspace_status.cget("text")
        self.assertIn("missing 2", text)
        self.assertIn("untracked 3", text)


if __name__ == "__main__":
    unittest.main()
