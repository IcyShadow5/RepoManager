"""Cancellation never changes inventory or leaves scan workers running."""
import copy
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import git_observation, scanner, store
from repo_manager.scan_control import ScanControl, ScanCancelled, active_scan, checkpoint
from tests.test_repository_service import IsolatedSessionTests


class ScanControlTests(unittest.TestCase):
    def test_progress_and_cancel_are_explicit(self):
        control = ScanControl()
        control.update(path="folder", directories=3, repositories=1)
        self.assertEqual(control.snapshot()["directories"], 3)
        control.cancel()
        self.assertTrue(control.snapshot()["cancelling"])
        with self.assertRaises(ScanCancelled):
            control.check()

    def test_context_does_not_leak_into_other_work(self):
        control = ScanControl()
        with active_scan(control):
            control.cancel()
            with self.assertRaises(ScanCancelled):
                checkpoint()
        checkpoint()

    def test_cancel_stops_scheduling_and_joins_all_metadata_threads(self):
        control, full, release = ScanControl(), threading.Event(), threading.Event()
        started, errors, worker_ids = [], [], set()
        lock = threading.Lock()
        before = {thread.ident for thread in threading.enumerate()}

        def collect(path):
            with lock:
                started.append(path)
                worker_ids.add(threading.get_ident())
                if len(started) == scanner.CONTROLLED_SCAN_WORKERS:
                    full.set()
            release.wait(3)
            checkpoint()
            return scanner.empty_meta(path), frozenset()

        def scan():
            try:
                with active_scan(control):
                    scanner.merge_scan([], ["repo-" + str(i) for i in range(100)])
            except ScanCancelled as exc:
                errors.append(exc)

        with mock.patch.object(scanner, "collect_metadata_observation", side_effect=collect):
            thread = threading.Thread(target=scan)
            thread.start()
            try:
                self.assertTrue(full.wait(3))
                control.cancel()
            finally:
                release.set()
                thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(started), scanner.CONTROLLED_SCAN_WORKERS)
        self.assertEqual(len(errors), 1)
        self.assertFalse(worker_ids & ({t.ident for t in threading.enumerate()} - before))

    def test_current_git_timeout_reaps_process_before_cancel_is_reported(self):
        control, children = ScanControl(), []
        original = git_observation.WindowsProcess

        def spawn(*args, **kwargs):
            child = original(sys.executable, subprocess.list2cmdline(
                [sys.executable, "-c", "import time; time.sleep(30)"]), **kwargs)
            children.append(child)
            control.cancel()
            return child

        with mock.patch.object(scanner, "GIT_TIMEOUT", .15), mock.patch.object(git_observation, "WindowsProcess", side_effect=spawn):
            started = time.monotonic()
            with active_scan(control), self.assertRaises(ScanCancelled):
                scanner._git_result(Path.cwd(), "status")
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_unborn_failure_handler_does_not_swallow_cancellation(self):
        with mock.patch.object(scanner, "_git_result", side_effect=ScanCancelled()):
            with self.assertRaises(ScanCancelled):
                scanner._unborn_repo_is_confirmed("repo", {})


class ScanPersistenceTests(IsolatedSessionTests):
    def test_cancelled_discovery_preserves_registry_and_timestamp(self):
        self.seed()
        store.save_projects(self.session.records)
        self.session.settings["last_full_scan_at"] = "previous"
        store.save_settings(self.session.settings)
        before = (store.REPOS_FILE.read_bytes(), store.SETTINGS_FILE.read_bytes(), copy.deepcopy(self.session.records))
        control = ScanControl()
        control.cancel()
        with self.assertRaises(ScanCancelled):
            self.session.scan(control)
        self.assertEqual((store.REPOS_FILE.read_bytes(), store.SETTINGS_FILE.read_bytes(), self.session.records), before)

    def test_cancel_during_real_discovery_is_not_returned_as_empty_inventory(self):
        self.seed()
        self.session.settings["roots"] = [str(self.root)]
        control = ScanControl()
        original = scanner.os.scandir

        def cancel_on_read(path):
            control.cancel()
            return original(path)

        with mock.patch.object(scanner.os, "scandir", side_effect=cancel_on_read), self.assertRaises(ScanCancelled):
            self.session.scan(control)
        self.assertEqual(len(self.session.records), 1)
