"""LEGACY / PARITY REFERENCE — NOT CURRENT UI PROOF."""
import ctypes
import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import tkinter as tk
from repo_manager import main, store
from tests.test_registry_recovery import RegistryFixture, deny_sharing

class RegistryLifecycleTests(RegistryFixture):
    def setUp(self):
        super().setUp()
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        root.withdraw()
        root.destroy()
        self.apps = []
        self.addCleanup(self.close_apps)
        for name in ("showwarning", "showinfo", "showerror"):
            patch = mock.patch.object(main.messagebox, name)
            setattr(self, name, patch.start())
            self.addCleanup(patch.stop)

    def close_apps(self):
        for app in self.apps:
            if not app.__dict__.get("_closing", False):
                app._closing = True
                app._cancel_after_jobs()
                app.destroy()

    def app(self):
        app = main.RepoManagerApp.__new__(main.RepoManagerApp)
        self.apps.append(app)
        app.__init__()
        app.withdraw()
        return app

    def drain(self, app):
        deadline = time.monotonic() + 5
        while app._scanning and time.monotonic() < deadline:
            app.update()
            time.sleep(.01)
        self.assertFalse(app._scanning, "scan did not finish")

    def assert_blocked(self, app):
        self.assertTrue(app._registry_blocked())
        self.assertFalse(app._scanning)
        self.assertIn("Retry registry", app.scan_btn.cget("text"))
        with mock.patch.object(store, "save_projects") as save, \
                mock.patch.object(main.threading, "Thread") as thread:
            app._schedule_project_save()
            self.assertIsNone(app._save_job)
            app._flush_project_save()
            app._refresh_metadata_async()
            with self.assertRaises(OSError):
                app._persist_projects([])
            with self.assertRaises(OSError):
                app._persist_projects([], workspaces=[])
            app._scan_queue.put(("result", [], [], app._scan_gen))
            app._scan_queue.put(("meta", [], app._metadata_gen))
            app._drain_scan_queue()
        save.assert_not_called()
        thread.assert_not_called()

    def test_failed_read_stays_blocked_after_access_returns_until_f5_reload(self):
        self.write_primary()
        with self.deny_read():
            app = self.app()
        self.assertEqual(app.projects, [])
        self.assert_blocked(app)
        self.assert_original()
        app.scan_btn.invoke()
        self.assertFalse(app._registry_blocked())
        self.assertEqual(app.projects, [self.project])
        self.assertEqual(app.workspaces, [self.workspace])
        self.assert_original()
        app.start_scan()
        self.drain(app)
        self.assertEqual(app.projects[0]["focus"], self.project["focus"])
        self.assertEqual(len(app.projects), 1)
        app._on_close()
        self.assertTrue(app._closing)
        self.assertEqual(store.load_projects(), app.projects)

    def test_retry_failure_never_unblocks_or_starts_scan(self):
        self.write_primary()
        with self.deny_read():
            app = self.app()
            app.start_scan()
            self.assert_blocked(app)
            app._on_close()
        self.assertTrue(app._closing)
        self.assert_original()
        self.showwarning.assert_called()
        self.showerror.assert_not_called()

    def test_backup_appearing_during_blocked_session_becomes_visible_on_retry(self):
        self.write_primary()
        with self.deny_read():
            app = self.app()
            store._backup_paths()[0].write_bytes(self.raw)
            app.start_scan()
            self.assert_blocked(app)
            self.assertEqual(app.projects, [self.project])
            self.assertEqual(app.workspaces, [self.workspace])
        self.assert_original()

    def test_delayed_save_callback_uses_reloaded_projects_after_retry(self):
        self.write_primary()
        with self.deny_read():
            app = self.app()
        callback = app._flush_project_save
        app.start_scan()
        callback()
        self.assertEqual(store.load_projects(), [self.project])
        self.assertEqual(store.load_workspaces(), [self.workspace])

    def test_empty_backup_under_lock_does_not_trigger_startup_scan(self):
        self.write_primary()
        store._backup_paths()[0].write_text('{"projects": []}', encoding="utf-8")
        with self.deny_read():
            app = self.app()
        self.assert_blocked(app)
        self.assertEqual(app.projects, [])
        self.assertIn("Registry unavailable", app.empty_lbl.cget("text"))
        app._on_close()
        self.assert_original()

    def test_file_disappearing_after_failed_read_does_not_authorize_empty_save(self):
        self.write_primary()
        with self.deny_read():
            app = self.app()
        store.REPOS_FILE.unlink()
        app.start_scan()
        self.assert_blocked(app)
        app._on_close()
        self.assertFalse(store.REPOS_FILE.exists())

    def test_backup_is_readable_but_curation_and_writes_wait_for_reload(self):
        self.write_primary()
        backup = store._backup_paths()[0]
        backup.write_bytes(self.raw)
        with self.deny_read():
            app = self.app()
        self.assertEqual(app.projects, [self.project])
        self.assert_blocked(app)
        app._current = app.projects[0]
        app.d_focus.delete(0, "end")
        app.d_focus.insert(0, "must not replace recovered curation")
        app._on_focus_changed()
        app._save_detail()
        self.assertEqual(app.projects, [self.project])
        app._on_close()
        self.assertTrue(app._closing)
        self.assert_original()
        self.assertEqual(backup.read_bytes(), self.raw)

    def test_retry_uses_readable_primary_instead_of_older_backup(self):
        self.write_primary()
        old = {**self.payload, "projects": [{**self.project, "focus": "old"}]}
        store._backup_paths()[0].write_text(json.dumps(old), encoding="utf-8")
        with self.deny_read():
            app = self.app()
        self.assertEqual(app.projects[0]["focus"], "old")
        app.start_scan()
        self.assertEqual(app.projects, [self.project])
        self.assertFalse(app._registry_blocked())
        self.assert_original()

    def test_malformed_primary_waits_for_repair_and_reloads(self):
        store.REPOS_FILE.write_bytes(b"{malformed")
        app = self.app()
        self.assert_blocked(app)
        self.assertEqual(store.REPOS_FILE.read_bytes(), b"{malformed")
        self.write_primary()
        app.start_scan()
        self.assertFalse(app._registry_blocked())
        self.assertEqual(app.projects, [self.project])

    def test_first_run_scans_saves_and_closes_without_recovery_warning(self):
        app = self.app()
        self.assertEqual(app._registry_report["status"], "fresh")
        self.drain(app)
        self.assertFalse(app._registry_blocked())
        self.assertEqual(store.load_projects(), [])
        app._on_close()
        self.assertTrue(app._closing)
        self.showwarning.assert_not_called()
        self.showerror.assert_not_called()

    def test_valid_primary_startup_scan_and_shutdown_preserve_content(self):
        self.write_primary()
        app = self.app()
        self.drain(app)
        app.start_scan()
        self.drain(app)
        self.assertFalse(app._registry_blocked())
        self.assertEqual(app.projects[0]["focus"], self.project["focus"])
        app._on_close()
        self.assertTrue(app._closing)
        self.assertEqual(len(store.load_projects()), 1)
        self.assertEqual(store.load_workspaces(), [self.workspace])
        self.showwarning.assert_not_called()
        self.showerror.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows sharing denial only")
    def test_real_persistent_lock_startup_retry_and_shutdown_preserve_bytes(self):
        self.write_primary()
        with deny_sharing(store.REPOS_FILE):
            with self.assertRaises(PermissionError):
                store.REPOS_FILE.read_bytes()
            app = self.app()
            self.assertEqual(app._registry_report["status"], "unavailable")
            self.assert_blocked(app)
            app.start_scan()
            self.assertTrue(app._registry_blocked())
            app._on_close()
            self.assertTrue(app._closing)
            self.assertFalse(store.REPOS_FILE.with_suffix(".tmp").exists())
            self.assertFalse(any(path.exists() for path in store._backup_paths()))
        self.assert_original()
        self.showerror.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows sharing denial only")
    def test_real_transient_lock_never_saves_empty_and_f5_recovers_original(self):
        self.write_primary()
        with deny_sharing(store.REPOS_FILE):
            app = self.app()
        self.assert_blocked(app)
        self.assert_original()
        app.scan_btn.invoke()
        self.assertFalse(app._registry_blocked())
        self.assertEqual(app.projects, [self.project])
        self.assertEqual(app.workspaces, [self.workspace])
        self.assert_original()
        app._on_close()
        self.assertEqual(store.load_projects(), [self.project])
        self.assertEqual(store.load_workspaces(), [self.workspace])
        self.assertTrue(app._closing)

    @unittest.skipUnless(os.name == "nt", "Windows sharing denial only")
    def test_real_lock_with_backup_can_close_without_writing_primary(self):
        self.write_primary()
        store._backup_paths()[0].write_bytes(self.raw)
        with deny_sharing(store.REPOS_FILE):
            app = self.app()
            self.assertEqual(app._registry_report["status"], "recovered")
            self.assertEqual(app.projects, [self.project])
            self.assert_blocked(app)
            app._on_close()
            self.assertTrue(app._closing)
        self.assert_original()
        self.assertEqual(store._backup_paths()[0].read_bytes(), self.raw)
