"""Git prerequisite detection and retry without changing the host installation."""
import subprocess
import ctypes
import os
from ctypes import wintypes
import unittest
from types import SimpleNamespace
from unittest import mock

from repo_manager import git_availability


class GitAvailabilityTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows error-mode contract")
    def test_probe_scopes_native_error_mode_and_restores_it_on_failure(self):
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetThreadErrorMode.restype = wintypes.DWORD
        api.SetThreadErrorMode.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        api.SetThreadErrorMode.restype = wintypes.BOOL
        original = api.GetThreadErrorMode()
        try:
            self.assertTrue(api.SetThreadErrorMode(0, None))
            for failed in (False, True):
                def run(*args, **kwargs):
                    self.assertEqual(api.GetThreadErrorMode(), 1)
                    if failed:
                        raise OSError("invalid executable")
                    return SimpleNamespace(returncode=0)
                with self.subTest(failed=failed):
                    result = git_availability.check_git(which=lambda _: "git.exe", run=run)
                    self.assertEqual(result.status, "launch_failed" if failed else "available")
                    self.assertEqual(api.GetThreadErrorMode(), 0)
        finally:
            self.assertTrue(api.SetThreadErrorMode(original, None))

    @unittest.skipUnless(os.name == "nt", "Windows error-mode contract")
    def test_native_error_mode_failure_is_reported_without_launching_git(self):
        run = mock.Mock()
        with mock.patch.object(ctypes, "WinDLL", side_effect=OSError("native error mode unavailable")):
            result = git_availability.check_git(which=lambda _: "git.exe", run=run)
        self.assertEqual(result.status, "launch_failed")
        self.assertIn("native error mode unavailable", result.detail)
        run.assert_not_called()

    def test_available_git(self):
        run = mock.Mock(return_value=SimpleNamespace(returncode=0))
        result = git_availability.check_git(which=lambda _: r"C:\Git\git.exe",
                                            run=run)
        self.assertTrue(result.available)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["git", "--version"])

    def test_git_not_on_path(self):
        run = mock.Mock()
        result = git_availability.check_git(which=lambda _: None, run=run)
        self.assertEqual(result.status, "not_found")
        run.assert_not_called()

    def test_git_launch_failure(self):
        run = mock.Mock(side_effect=PermissionError("access denied"))
        result = git_availability.check_git(which=lambda _: r"C:\Git\git.exe",
                                            run=run)
        self.assertEqual(result.status, "launch_failed")
        self.assertIn("access denied", result.detail)

    def test_git_timeout(self):
        run = mock.Mock(side_effect=subprocess.TimeoutExpired("git", 5))
        result = git_availability.check_git(which=lambda _: r"C:\Git\git.exe",
                                            run=run)
        self.assertEqual(result.status, "launch_failed")

    def test_git_version_error_is_unusable(self):
        run = mock.Mock(return_value=SimpleNamespace(
            returncode=1, stderr="version failed", stdout=""))
        result = git_availability.check_git(which=lambda _: r"C:\Git\git.exe",
                                            run=run)
        self.assertEqual(result.status, "unusable")

    def test_recheck_uses_fresh_detection(self):
        paths = iter((None, r"C:\Git\git.exe"))
        run = mock.Mock(return_value=SimpleNamespace(returncode=0))
        which = lambda _: next(paths)
        self.assertFalse(git_availability.check_git(which=which, run=run).available)
        self.assertTrue(git_availability.check_git(which=which, run=run).available)
        run.assert_called_once()




if __name__ == "__main__":
    unittest.main()
