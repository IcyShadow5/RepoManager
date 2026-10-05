"""Native portable identity and source execution boundaries."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from repo_manager import desktop_identity as identity


class DesktopIdentityTests(unittest.TestCase):
    def test_non_windows_identity_is_inapplicable(self):
        with mock.patch.object(identity.os, "name", "posix"):
            self.assertFalse(identity.set_windows_app_user_model_id())
            self.assertFalse(identity.set_windows_window_identity(0))

    def test_source_execution_does_not_register_python_as_portable_app(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertFalse(identity.set_windows_window_identity(0))

    @unittest.skipUnless(os.name == "nt", "Windows Shell properties")
    def test_portable_identity_reads_back_from_native_window(self):
        import importlib.util
        if not importlib.util.find_spec("PySide6"):
            self.skipTest("Qt development dependency required")
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-B", "-m", "tests.windows_identity_smoke"],
            cwd=root, env={**os.environ, "QT_QPA_PLATFORM": "windows"},
            capture_output=True, text=True, timeout=25, creationflags=0x08000000)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("properties=4 native_readback=PASS", result.stdout)
