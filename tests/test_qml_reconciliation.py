"""QTest events against shipped QML components in isolated source processes."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "Qt dependency required")
class CurrentQmlTests(unittest.TestCase):
    def run_scenario(self, scenario):
        result = subprocess.run([sys.executable, "-B", "-m", "tests.qt_reconciliation_smoke", scenario],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                                timeout=90, creationflags=0x08000000 if os.name == "nt" else 0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("QML warnings=0", result.stdout)

    def test_real_qml_diff_stage_unstage_and_explicit_local_commit(self):
        self.run_scenario("git")

    def test_real_qml_keyboard_selection_search_tab_and_help(self):
        self.run_scenario("keyboard")

    def test_filter_resets_scroll_resize_and_teardown_without_delegate_warning(self):
        self.run_scenario("filter")

    def test_real_qml_missing_git_install_destination_and_path_recheck(self):
        self.run_scenario("onboarding")
