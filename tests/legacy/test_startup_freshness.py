"""LEGACY / PARITY REFERENCE — NOT CURRENT UI PROOF."""
"""Startup freshness and rescan reliability (R2.2).

Covers the R2.1 root cause: populated-registry startup previously ran only a
metadata refresh (no discovery, no ``last_seen`` advance, no freshness signal)
and F5-during-scan was silently discarded. Tk-dependent cases are skipped when
no display is available.
"""
import json
import queue
import tempfile
import time
import unittest
import tkinter as tk
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from repo_manager import main as main_module
from repo_manager.main import format_inventory_age, parse_full_scan_at
from repo_manager import store
from tests.git_repository import create_repository, git as _git_repo


def _tk_available():
    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except (tk.TclError, OSError, AttributeError):
        return False


TK_AVAILABLE = _tk_available()


class _IsolatedStorePaths:
    """Redirect the app-data store paths to a temporary directory."""

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
    def setUp(self):
        self._store_iso = _IsolatedStorePaths()
        self.addCleanup(self._store_iso.close)


class FullScanTimestampTests(unittest.TestCase):
    """Pure parsing/formatting contract for the explicit freshness state."""

    def test_rejects_missing_and_malformed_values(self):
        for bad in (None, "", "  ", 123, "yesterday",
                    "2026-09-26 16:43:12", "2026-09-26T16:43:12",
                    "2026-13-40T99:99:99Z"):
            self.assertIsNone(parse_full_scan_at(bad), bad)

    def test_accepts_registry_timestamp_shape_as_utc(self):
        parsed = parse_full_scan_at("2026-09-26T16:43:12Z")
        self.assertEqual(parsed, datetime(2026, 9, 26, 16, 43, 12,
                                          tzinfo=timezone.utc))

    def test_unknown_without_timestamp(self):
        self.assertEqual(format_inventory_age(None), "Inventory scan: unknown")

    def test_buckets_are_coarse_and_deterministic(self):
        now = datetime(2026, 9, 26, 16, 43, 12, tzinfo=timezone.utc)
        cases = [
            (now, "Inventory scanned: just now"),
            (now - timedelta(seconds=59), "Inventory scanned: just now"),
            (now - timedelta(seconds=90), "Inventory scanned: 1 min ago"),
            (now - timedelta(minutes=59), "Inventory scanned: 59 min ago"),
            (now - timedelta(hours=5), "Inventory scanned: 5 hr ago"),
            (now - timedelta(days=3), "Inventory scanned: 3 days ago"),
            (now - timedelta(days=7), "Inventory scanned: 7 days ago"),
            (now + timedelta(seconds=30), "Inventory scanned: just now"),
        ]
        for scanned_at, expected in cases:
            with self.subTest(scanned_at=scanned_at):
                self.assertEqual(format_inventory_age(scanned_at, now),
                                 expected)


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class StartupDispatchTests(_StoreIsolationMixin, unittest.TestCase):
    """Populated-registry startup must schedule full discovery (R2.1)."""

    def _seed(self, projects):
        store.save_projects(projects)

    def _construct(self):
        with mock.patch.object(main_module.RepoManagerApp,
                               "_schedule_after", return_value=None), \
                mock.patch.object(main_module.RepoManagerApp,
                                  "start_scan") as scan, \
                mock.patch.object(main_module.RepoManagerApp,
                                  "_refresh_metadata_async") as refresh:
            app = main_module.RepoManagerApp()
        self.addCleanup(app.destroy)
        return app, scan, refresh

    def test_populated_startup_runs_full_discovery_not_metadata_only(self):
        root = Path(self._store_iso.app_dir)
        self._seed([{"project_id": "p1", "path": str(root),
                     "name": "cached"}])
        app, scan, refresh = self._construct()
        scan.assert_called_once_with()
        refresh.assert_not_called()

    def test_empty_startup_runs_full_discovery(self):
        app, scan, refresh = self._construct()
        scan.assert_called_once_with()
        refresh.assert_not_called()

    def test_cached_ui_renders_before_scan_with_unknown_freshness(self):
        root = Path(store.APP_DIR)
        self._seed([{"project_id": "p1", "path": str(root),
                     "name": "cached"}])
        app, scan, refresh = self._construct()
        self.assertGreater(len(app.tree.get_children()), 0)
        self.assertEqual(app.inventory_status.cget("text"),
                         "Inventory scan: unknown")

    def test_cached_ui_shows_stale_inventory_age(self):
        root = Path(store.APP_DIR)
        self._seed([{"project_id": "p1", "path": str(root),
                     "name": "cached"}])
        store.save_settings({**store.load_settings(),
                             "last_full_scan_at": "2026-09-19T09:15:12Z"})
        app, scan, refresh = self._construct()
        text = app.inventory_status.cget("text")
        self.assertTrue(text.startswith("Inventory scanned:"),
                        text)
        self.assertIn("days ago", text)


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class StartupDiscoveryIntegrationTests(_StoreIsolationMixin, unittest.TestCase):
    """End to end: normal startup discovers unknown repos without F5."""

    def test_startup_discovers_beta_and_advances_freshness(self):
        scan_root = Path(self._store_iso._tmp.name) / "repos"
        scan_root.mkdir()
        create_repository(scan_root / "alpha")
        create_repository(scan_root / "beta")
        store.save_settings({**store.load_settings(),
                             "roots": [str(scan_root)], "depth": 4})
        store.save_projects([{
            "project_id": "probe-alpha", "path": str(scan_root / "alpha"),
            "name": "alpha", "status": "active", "focus": "",
            "pinned": False, "head": "stale-head",
            "last_seen": "2026-09-19T09:15:12Z",
        }])
        # Advance alpha after seeding so cached git state is stale.
        (scan_root / "alpha" / "post.txt").write_text("v2\n", encoding="utf-8")
        _git_repo(scan_root / "alpha", "add", "-A")
        _git_repo(scan_root / "alpha", "commit", "-qm", "second")
        fresh_head = _git_repo(scan_root / "alpha", "rev-parse", "HEAD")

        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(main_module.messagebox, "showerror"))
            stack.enter_context(
                mock.patch.object(main_module.messagebox, "showwarning"))
            app = main_module.RepoManagerApp()
            self.addCleanup(app.destroy)
            deadline = time.time() + 60
            while (app._scanning or not app._scan_queue.empty()) \
                    and time.time() < deadline:
                app.update()
                app._drain_scan_queue()
                time.sleep(0.05)
            self.assertFalse(app._scanning, "startup scan did not settle")
            names = sorted(p.get("name") for p in app.projects)
            self.assertIn("beta", names)
            alpha_rec = next(p for p in app.projects
                             if p.get("name") == "alpha")
            self.assertNotEqual(alpha_rec.get("last_seen"),
                                "2026-09-19T09:15:12Z")
            self.assertEqual(alpha_rec.get("head"), fresh_head)
            beta_rec = next(p for p in app.projects
                            if p.get("name") == "beta")
            self.assertTrue(beta_rec.get("last_seen"))
            # Explicit global freshness advanced and persisted.
            self.assertIsNotNone(app.__dict__.get("_last_full_scan_at"))
            saved = json.loads(store.SETTINGS_FILE.read_text(
                encoding="utf-8"))
            self.assertTrue(saved.get("last_full_scan_at"))
            self.assertIn("Inventory scanned:",
                          app.inventory_status.cget("text"))
            app._closing = True

    def test_metadata_only_refresh_does_not_stamp_full_scan(self):
        root = Path(self._store_iso._tmp.name) / "solo"
        create_repository(root)
        project = {"project_id": "solo-1", "path": str(root),
                   "name": "solo", "status": "active", "focus": "",
                   "pinned": False}
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(main_module.messagebox, "showerror"))
            stack.enter_context(
                mock.patch.object(main_module.messagebox, "showwarning"))
            stack.enter_context(
                mock.patch.object(main_module.RepoManagerApp, "start_scan"))
            app = main_module.RepoManagerApp()
            self.addCleanup(app.destroy)
            app.projects = [dict(project)]
            app._scan_queue.put(("meta", [(
                "solo-1", str(root),
                {"path": str(root), "branch": "main", "dirty": 0}, None)],
                app._metadata_gen))
            app._drain_scan_queue()
            app.update()
            self.assertIsNone(app.__dict__.get("_last_full_scan_at"))
            if store.SETTINGS_FILE.exists():
                saved = json.loads(store.SETTINGS_FILE.read_text(
                    encoding="utf-8"))
                self.assertNotIn("last_full_scan_at", saved)


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class RescanCoalescingTests(_StoreIsolationMixin, unittest.TestCase):
    """F5 while busy queues exactly one trailing scan (R2.1)."""

    def test_request_while_busy_is_queued_not_started_or_lost(self):
        app = object.__new__(main_module.RepoManagerApp)
        app.__dict__["_closing"] = False
        app.__dict__["_registry_report"] = {"status": "valid"}
        app.__dict__["_scanning"] = True
        app.__dict__["_scan_gen"] = 3
        app._scan_queue = queue.Queue()
        with mock.patch.object(main_module.threading, "Thread",
                               side_effect=AssertionError("must not spawn")):
            app.start_scan()
            app.start_scan()
            app.start_scan()
        self.assertTrue(app.__dict__.get("_pending_rescan", False))
        self.assertEqual(app._scan_gen, 3)

    def test_closing_request_is_dropped_without_queue(self):
        app = object.__new__(main_module.RepoManagerApp)
        app.__dict__["_closing"] = True
        app.__dict__["_registry_report"] = {"status": "valid"}
        app.__dict__["_scanning"] = True
        app._scan_queue = queue.Queue()
        app.start_scan()
        self.assertFalse(app.__dict__.get("_pending_rescan", False))

    def test_drain_while_closing_starts_no_trailing_scan(self):
        app = object.__new__(main_module.RepoManagerApp)
        app.__dict__["_closing"] = True
        app.__dict__["_pending_rescan"] = True
        app._scan_queue = queue.Queue()
        app._scan_queue.put(("result", [], [], 1))
        with mock.patch.object(main_module.RepoManagerApp,
                               "start_scan",
                               side_effect=AssertionError("must not start")):
            app._drain_scan_queue()
        self.assertTrue(app._scan_queue.empty())

    def test_finish_close_cancels_pending_rescan(self):
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(main_module.messagebox, "showerror"))
            stack.enter_context(
                mock.patch.object(main_module.messagebox, "showwarning"))
            stack.enter_context(
                mock.patch.object(main_module.RepoManagerApp, "start_scan"))
            app = main_module.RepoManagerApp()
            app.__dict__["_pending_rescan"] = True
            app._finish_close()
            self.assertFalse(app.__dict__.get("_pending_rescan", True))
            self.assertTrue(app.__dict__.get("_closing", False))
            try:
                app.destroy()
            except tk.TclError:
                pass

    def test_failed_scan_records_no_successful_timestamp(self):
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(main_module.messagebox, "showerror"))
            stack.enter_context(
                mock.patch.object(main_module.messagebox, "showwarning"))
            stack.enter_context(
                mock.patch.object(main_module.RepoManagerApp, "start_scan"))
            app = main_module.RepoManagerApp()
            self.addCleanup(app.destroy)
            app._scan_queue.put(("error", {
                "message": "boom", "operation": "scan",
                "generation": app._scan_gen}))
            app._drain_scan_queue()
            app.update()
            self.assertFalse(app._scanning)
            self.assertIsNone(app.__dict__.get("_last_full_scan_at"))
            self.assertFalse(store.SETTINGS_FILE.exists())


if __name__ == "__main__":
    unittest.main()
