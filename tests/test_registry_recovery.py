"""Failed registry reads must never authorize an empty replacement."""
import ctypes
import hashlib
import json
import os
import tempfile
import time
import unittest
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from unittest import mock

from repo_manager import store


@contextmanager
def deny_sharing(path):
    """Hold a real Windows handle that denies reads, writes and replacement."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


class RegistryFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="repomanager-recovery-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        patch = mock.patch.multiple(
            store, APP_DIR=self.base, REPOS_FILE=self.base / "repos.json",
            SETTINGS_FILE=self.base / "settings.json", NOTES_DIR=self.base / "notes",
        )
        patch.start()
        self.addCleanup(patch.stop)
        store.ensure_dirs()
        self.root = self.base / "scan-root"
        self.root.mkdir()
        store.save_settings({"roots": [str(self.root)]})
        self.project = {
            "project_id": "recovery-project", "folder_path": str(self.root),
            "name": "Known project", "status": "active", "pinned": True,
            "focus": "Keep this curation",
        }
        self.workspace = {"workspace_id": "recovery-workspace", "name": "Known group",
                          "members": []}
        self.payload = {"schema_version": store.SCHEMA_VERSION,
                        "projects": [self.project], "workspaces": [self.workspace]}
        self.raw = json.dumps(self.payload).encode("utf-8")

    def write_primary(self):
        store.REPOS_FILE.write_bytes(self.raw)

    def deny_read(self, exception=None):
        original = Path.read_bytes

        def read(path):
            if path == store.REPOS_FILE:
                raise exception or PermissionError("registry access denied")
            return original(path)

        return mock.patch.object(Path, "read_bytes", read)

    def assert_original(self):
        after = store.REPOS_FILE.read_bytes()
        self.assertEqual(after, self.raw)
        self.assertEqual(hashlib.sha256(after).hexdigest(),
                         hashlib.sha256(self.raw).hexdigest())
        self.assertFalse(store.REPOS_FILE.with_suffix(".tmp").exists())


class RegistryReadFailureTests(RegistryFixture):
    def test_absence_alone_authorizes_fresh_state_and_persistence(self):
        records, report = store.read_registry()
        self.assertEqual(records, [])
        self.assertEqual(report["status"], "fresh")
        self.assertFalse(report.get("write_blocked", False))
        store.save_projects([self.project])
        self.assertEqual(store.load_projects(), [self.project])

    def test_valid_primary_preserves_project_and_workspace_content(self):
        self.write_primary()
        records, report = store.read_registry()
        self.assertEqual(records, [self.project])
        self.assertEqual(report["workspaces"], [self.workspace])
        self.assertEqual(report["status"], "valid")
        self.assertFalse(report.get("write_blocked", False))
        self.assert_original()

    def test_permission_failure_is_unavailable_and_does_not_write(self):
        self.write_primary()
        with self.deny_read(), mock.patch.object(store, "save_projects") as save:
            records, report = store.read_registry()
        self.assertEqual(records, [])
        self.assertEqual(report["status"], "unavailable")
        self.assertTrue(report["write_blocked"])
        self.assertIsNone(report["quarantined"])
        self.assertIn("access denied", report["reasons"][0])
        save.assert_not_called()
        self.assert_original()

    def test_invalid_backups_do_not_make_permission_failure_fresh(self):
        self.write_primary()
        for backup in store._backup_paths():
            backup.write_bytes(b"invalid")
        with self.deny_read():
            _, report = store.read_registry()
        self.assertEqual(report["status"], "unavailable")
        self.assert_original()

    def test_unreadable_primary_uses_first_valid_backup_without_rotation(self):
        self.write_primary()
        b1, b2 = store._backup_paths()
        b1.write_bytes(b"invalid")
        b2.write_bytes(self.raw)
        with self.deny_read():
            records, report = store.read_registry()
        self.assertEqual(records, [self.project])
        self.assertEqual(report["status"], "recovered")
        self.assertEqual(report["source"], b2.name)
        self.assertTrue(report["write_blocked"])
        self.assertEqual(report["workspaces"], [self.workspace])
        self.assertEqual(b1.read_bytes(), b"invalid")
        self.assertEqual(b2.read_bytes(), self.raw)
        self.assert_original()

    def test_compatibility_loaders_cannot_hide_failed_read_as_empty(self):
        self.write_primary()
        with self.deny_read():
            for load in (store.load_projects, store.load_workspaces):
                with self.subTest(loader=load.__name__), self.assertRaises(OSError):
                    load()
        self.assert_original()

    def test_workspace_save_cannot_use_failed_implicit_project_load(self):
        self.write_primary()
        read = store.read_registry

        def transient_read():
            with self.deny_read():
                return read()

        with mock.patch.object(store, "read_registry", transient_read):
            with self.assertRaises(OSError):
                store.save_workspaces([])
        self.assert_original()

    def test_malformed_content_is_distinct_and_preserved_for_retry(self):
        store.REPOS_FILE.write_bytes(b"{malformed")
        records, report = store.read_registry()
        self.assertEqual(records, [])
        self.assertEqual(report["status"], "unrecoverable")
        self.assertTrue(report["write_blocked"])
        self.assertEqual(Path(report["quarantined"]).read_bytes(), b"{malformed")
        self.assertEqual(store.REPOS_FILE.read_bytes(), b"{malformed")

    def test_primary_read_does_not_rely_on_exists_permission_probe(self):
        self.write_primary()
        with mock.patch.object(Path, "exists", side_effect=PermissionError), \
                self.deny_read():
            _, report = store.read_registry()
        self.assertEqual(report["status"], "unavailable")
