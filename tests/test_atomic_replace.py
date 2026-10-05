"""R2.4 atomic-replace resilience regression tests (Temp-only)."""
import json
import os
import queue
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import store


def isolated(target):
    """Redirect store paths of `target` test into a fresh temp app dir."""
    tmp = tempfile.TemporaryDirectory()
    base = Path(tmp.name) / "app"
    patcher = mock.patch.multiple(
        store,
        APP_DIR=base,
        REPOS_FILE=base / "repos.json",
        SETTINGS_FILE=base / "settings.json",
        NOTES_DIR=base / "notes",
    )
    patcher.start()
    store.ensure_dirs()
    target.addCleanup(patcher.stop)
    target.addCleanup(tmp.cleanup)
    return base


def win5(msg="denied"):
    # Real Windows failures carry winerror (not errno); constructing via
    # errno (PermissionError(5, ...)) would set winerror=None instead.
    exc = PermissionError(f"[WinError 5] {msg}")
    exc.winerror = 5
    return exc


def win32(msg="sharing"):
    exc = OSError(f"[WinError 32] {msg}")
    exc.winerror = 32
    return exc


class NoteRetryTests(unittest.TestCase):
    def setUp(self):
        isolated(self)

    def test_01_note_win5_then_success(self):
        calls = {"n": 0}
        real_replace = os.replace

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                raise win5()
            return real_replace(src, dst)

        with mock.patch.object(store.os, "replace", side_effect=flaky):
            store.save_note("P", r"C:\x\P", "v2")
        self.assertGreater(calls["n"], 1)
        p = store.note_path_for("P", r"C:\x\P")
        self.assertEqual(p.read_text(encoding="utf-8"), "v2")
        self.assertFalse(p.with_suffix(".tmp").exists())

    def test_02_note_win32_then_success(self):
        calls = {"n": 0}
        real_replace = os.replace

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise win32()
            return real_replace(src, dst)

        with mock.patch.object(store.os, "replace", side_effect=flaky):
            store.save_note("P", r"C:\x\P", "v2")
        self.assertEqual(calls["n"], 3)
        p = store.note_path_for("P", r"C:\x\P")
        self.assertEqual(p.read_text(encoding="utf-8"), "v2")
        self.assertFalse(p.with_suffix(".tmp").exists())


class RegistrySettingsRetryTests(unittest.TestCase):
    def setUp(self):
        isolated(self)

    def test_03_registry_transient_then_success_with_backup(self):
        store.save_projects([{"path": r"C:\x", "name": "x"}])
        calls = {"n": 0}
        real_replace = os.replace

        def flaky(src, dst):
            calls["n"] += 1
            if str(dst).endswith("repos.json") and calls["n"] == 1:
                raise win5()
            return real_replace(src, dst)

        with mock.patch.object(store.os, "replace", side_effect=flaky):
            store.save_projects([{"path": r"C:\y", "name": "y"}])
        loaded = store.load_projects()
        self.assertEqual([p["path"] for p in loaded], [r"C:\y"])
        # backup rotation ran only after the successful authoritative replace
        bak1 = Path(str(store.REPOS_FILE) + ".bak1")
        self.assertTrue(bak1.exists())
        first = __import__("json").loads(bak1.read_text(encoding="utf-8"))
        self.assertEqual([p["path"] for p in first["projects"]], [r"C:\y"])

    def test_04_settings_transient_then_success(self):
        settings = store.load_settings()
        settings["depth"] = 6
        calls = {"n": 0}
        real_replace = os.replace

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                raise win32()
            return real_replace(src, dst)

        with mock.patch.object(store.os, "replace", side_effect=flaky):
            store.save_settings(settings)
        self.assertGreater(calls["n"], 1)
        self.assertEqual(store.load_settings()["depth"], 6)


class TerminalFailureTests(unittest.TestCase):
    def setUp(self):
        isolated(self)

    def test_05_permanent_win5_bounded_and_safe(self):
        store.save_note("P", r"C:\x\P", "v1")
        p = store.note_path_for("P", r"C:\x\P")
        calls = {"n": 0}
        sleeps = []

        def always_fail(src, dst):
            calls["n"] += 1
            raise win5()

        # Injected sleeps: no real ~2 s wait; attempt cap bounds the loop.
        real_helper = store._replace_with_retry

        def helper_no_wait(src, dst):
            return real_helper(src, dst, sleep=sleeps.append)

        with mock.patch.object(store.os, "replace", side_effect=always_fail), \
                mock.patch.object(store, "_replace_with_retry",
                                  side_effect=helper_no_wait):
            with self.assertRaises(PermissionError) as ctx:
                store.save_note("P", r"C:\x\P", "v2")
        # bounded: attempt cap enforced
        self.assertLessEqual(calls["n"], store._REPLACE_MAX_ATTEMPTS)
        self.assertGreater(calls["n"], 1)
        # original PermissionError surfaces unchanged
        self.assertEqual(ctx.exception.winerror, 5)
        # destination intact and readable
        self.assertEqual(p.read_text(encoding="utf-8"), "v1")
        # tmp best-effort cleaned
        self.assertFalse(p.with_suffix(".tmp").exists())
        # budget shape: at least one backoff, all capped
        self.assertGreater(len(sleeps), 0)
        self.assertLessEqual(len(sleeps), store._REPLACE_MAX_ATTEMPTS)
        self.assertTrue(all(s <= store._REPLACE_MAX_DELAY_S for s in sleeps))
        self.assertGreaterEqual(
            sum(sleeps), store._REPLACE_INITIAL_DELAY_S)

    def test_06_non_retryable_single_attempt(self):
        calls = {"n": 0}

        def boom(src, dst):
            calls["n"] += 1
            raise FileNotFoundError(2, "gone")

        with mock.patch.object(store.os, "replace", side_effect=boom):
            with self.assertRaises(FileNotFoundError):
                store.save_note("P", r"C:\x\P", "v2")
        self.assertEqual(calls["n"], 1)
        # other winerror codes are not retried either
        calls["n"] = 0

        def other(src, dst):
            calls["n"] += 1
            raise OSError(999, "weird")

        with mock.patch.object(store.os, "replace", side_effect=other):
            with self.assertRaises(OSError):
                store.save_note("P", r"C:\x\P", "v2")
        self.assertEqual(calls["n"], 1)


class SharingWindowTests(unittest.TestCase):
    def setUp(self):
        isolated(self)

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_07_reader_released_within_budget_succeeds(self):
        store.save_note("P", r"C:\x\P", "v1")
        p = store.note_path_for("P", r"C:\x\P")
        holder = open(p, "r", encoding="utf-8")
        release_at = time.monotonic() + 0.3

        real_replace = os.replace

        def gated_replace(src, dst):
            # Fail while the reader is held only if the deadline hasn't
            # passed for the test's own release schedule.
            if not holder.closed and time.monotonic() < release_at + 5:
                try:
                    return real_replace(src, dst)
                except OSError as exc:
                    if getattr(exc, "winerror", None) in (5, 32):
                        raise
                    raise
            return real_replace(src, dst)

        def releaser():
            time.sleep(0.3)
            holder.close()

        thread = threading.Thread(target=releaser)
        thread.start()
        try:
            with mock.patch.object(store.os, "replace",
                                   side_effect=gated_replace):
                store.save_note("P", r"C:\x\P", "v2")
        finally:
            thread.join()
            if not holder.closed:
                holder.close()
        self.assertEqual(p.read_text(encoding="utf-8"), "v2")
        self.assertFalse(p.with_suffix(".tmp").exists())

    def test_07b_injected_reader_contention_then_success(self):
        # Deterministic everywhere: first attempts see a sharing violation,
        # emulating a held destination handle; later attempt succeeds.
        store.save_note("P", r"C:\x\P", "v1")
        p = store.note_path_for("P", r"C:\x\P")
        calls = {"n": 0}
        real_replace = os.replace

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise win32()
            return real_replace(src, dst)

        with mock.patch.object(store.os, "replace", side_effect=flaky):
            store.save_note("P", r"C:\x\P", "v2")
        # prior content existed until the successful replace
        self.assertEqual(p.read_text(encoding="utf-8"), "v2")

    def test_08_terminal_failure_keeps_destination(self):
        store.save_note("P", r"C:\x\P", "v1")
        p = store.note_path_for("P", r"C:\x\P")

        def always_fail(src, dst):
            raise win5()

        with mock.patch.object(store.os, "replace", side_effect=always_fail):
            with self.assertRaises(PermissionError):
                store.save_note("P", r"C:\x\P", "v2")
        self.assertEqual(p.read_text(encoding="utf-8"), "v1")

    def test_09_tmp_cleanup_and_original_exception(self):
        store.save_note("P", r"C:\x\P", "v1")
        p = store.note_path_for("P", r"C:\x\P")
        tmp = p.with_suffix(".tmp")

        def always_fail(src, dst):
            raise win5()

        with mock.patch.object(store.os, "replace", side_effect=always_fail):
            with self.assertRaises(PermissionError) as ctx:
                store.save_note("P", r"C:\x\P", "v2")
        self.assertEqual(ctx.exception.winerror, 5)
        self.assertFalse(tmp.exists())

        # cleanup failure must not mask the replace exception
        def always_fail2(src, dst):
            raise win5()

        with mock.patch.object(store.os, "replace", side_effect=always_fail2), \
                mock.patch.object(store.Path, "unlink",
                                  side_effect=OSError("cleanup boom")):
            with self.assertRaises(PermissionError) as ctx2:
                # unlink patched globally would break write path; scope it:
                store.save_note("P", r"C:\x\P", "v3")
        self.assertEqual(ctx2.exception.winerror, 5)


class SettingsSerializationTests(unittest.TestCase):
    """R2.4-FIX-1: one settings transaction owns settings.tmp at a time."""

    def setUp(self):
        isolated(self)

    def test_10_concurrent_settings_serialized(self):
        inside = {"n": 0, "max": 0}
        guard = threading.Lock()
        real_write_json = store._write_json

        def guarded(path, data):
            with guard:
                inside["n"] += 1
                inside["max"] = max(inside["max"], inside["n"])
            try:
                time.sleep(0.05)
                return real_write_json(path, data)
            finally:
                with guard:
                    inside["n"] -= 1

        barrier = threading.Barrier(2)
        errors = []

        def saver(depth):
            try:
                barrier.wait(timeout=10)
                settings = store.load_settings()
                settings["depth"] = depth
                store.save_settings(settings)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        with mock.patch.object(store, "_write_json", side_effect=guarded):
            threads = [threading.Thread(target=saver, args=(depth,))
                       for depth in (3, 7)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        # The temp-ownership critical section never overlapped.
        self.assertEqual(inside["max"], 1)
        final = store.load_settings()
        # One complete valid writer result won; never mixed/corrupt JSON.
        self.assertIn(final["depth"], (3, 7))
        raw = store.SETTINGS_FILE.read_text(encoding="utf-8")
        self.assertEqual(json.loads(raw)["depth"], final["depth"])

    def test_11_terminal_failure_waiting_writer(self):
        # A owns the settings lock and fails terminally while B waits on the
        # lock; A's tmp cleanup therefore cannot delete B's future temp.
        a_ident = {}
        a_waiting = threading.Event()
        b_started = threading.Event()
        first_sleep = {"done": False}
        real_replace = os.replace
        real_retry = store._replace_with_retry

        def fail_for_a(src, dst):
            if threading.get_ident() == a_ident.get("id"):
                raise win5()
            return real_replace(src, dst)

        def sleep_until_b(delay):
            if not first_sleep["done"]:
                first_sleep["done"] = True
                a_waiting.set()
                b_started.wait(timeout=10)
            return None

        def retry_no_wait(src, dst):
            return real_retry(src, dst, sleep=sleep_until_b)

        results = {}

        def writer_a():
            a_ident["id"] = threading.get_ident()
            try:
                settings = store.load_settings()
                settings["depth"] = 3
                store.save_settings(settings)
                results["a"] = "unexpected-success"
            except PermissionError as exc:
                results["a"] = exc

        def writer_b():
            b_started.set()
            settings = store.load_settings()
            settings["depth"] = 7
            store.save_settings(settings)
            results["b"] = "ok"

        with mock.patch.object(store.os, "replace",
                               side_effect=fail_for_a), \
                mock.patch.object(store, "_replace_with_retry",
                                  side_effect=retry_no_wait):
            thread_a = threading.Thread(target=writer_a)
            thread_a.start()
            # A is now parked inside its retry loop holding the lock.
            self.assertTrue(a_waiting.wait(timeout=10))
            thread_b = threading.Thread(target=writer_b)
            thread_b.start()
            thread_a.join(timeout=30)
            thread_b.join(timeout=30)
        self.assertFalse(thread_a.is_alive())
        self.assertFalse(thread_b.is_alive())
        # A surfaces its original replace exception ...
        self.assertIsInstance(results.get("a"), PermissionError)
        self.assertEqual(results["a"].winerror, 5)
        # ... and B succeeds afterwards with its complete payload.
        self.assertEqual(results.get("b"), "ok")
        final = store.load_settings()
        self.assertEqual(final["depth"], 7)
        self.assertFalse(
            store.SETTINGS_FILE.with_suffix(".tmp").exists())
        self.assertEqual(
            store.settings_recovery_report()["status"], "valid")

    def test_12_report_consistency(self):
        settings = store.load_settings()
        settings["depth"] = 4
        store.save_settings(settings)
        self.assertEqual(
            store.settings_recovery_report()["status"], "valid")

        def always_fail(src, dst):
            raise win5()

        with mock.patch.object(store.os, "replace", side_effect=always_fail):
            with self.assertRaises(PermissionError):
                store.save_settings({**store.load_settings(), "depth": 5})
        # A failed transaction leaves the prior authoritative report intact.
        report = store.settings_recovery_report()
        self.assertEqual(report["status"], "valid")
        self.assertIn("reasons", report)

        settings = store.load_settings()
        settings["depth"] = 7
        store.save_settings(settings)
        self.assertEqual(
            store.settings_recovery_report()["status"], "valid")
        self.assertEqual(store.load_settings()["depth"], 7)


if __name__ == "__main__":
    unittest.main()
