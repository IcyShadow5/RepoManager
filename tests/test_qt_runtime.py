"""The real QML entry point must render and close at supported QA scales."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "Qt development dependency required")
class QtRuntimeTests(unittest.TestCase):
    def test_qml_root_load_failure_reports_error_and_preserves_nonzero_exit(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-B', '-m', 'tests.qt_load_failure_smoke'],
            cwd=root, capture_output=True, text=True, timeout=30,
            creationflags=0x08000000 if os.name == 'nt' else 0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('exit=1 dialog=1 diagnostic_log=PASS', result.stdout)

    def test_qml_project_menu_and_settings_use_real_control_events(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-B", "-m", "tests.qt_interaction_smoke"],
            cwd=root, capture_output=True, text=True, timeout=45,
            creationflags=0x08000000 if os.name == "nt" else 0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("QML warnings=0", result.stdout)

    def test_initial_window_fits_common_logical_work_areas(self):
        from PySide6.QtCore import QRect
        from run_qt import initial_window_size
        for width, height in ((1920, 1032), (1536, 824), (1280, 688)):
            with self.subTest(work_area=(width, height)):
                size = initial_window_size(QRect(0, 0, width, height))
                self.assertLessEqual(size["width"], width - 40)
                self.assertLessEqual(size["height"], height - 48)
                self.assertGreaterEqual(size["width"], 1120)
                self.assertGreaterEqual(size["height"], 640)

    def test_qml_startup_at_three_scales_without_tkinter_or_qml_errors(self):
        root = Path(__file__).resolve().parents[1]
        for scale in ("1", "1.25", "1.5"):
            with self.subTest(scale=scale):
                environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
                               "QT_QUICK_BACKEND": "software", "QT_SCALE_FACTOR": scale}
                result = subprocess.run([sys.executable, "-B", "-m", "tests.qt_startup_smoke"],
                    cwd=root, env=environment, capture_output=True, text=True,
                    timeout=35, creationflags=0x08000000 if os.name == "nt" else 0)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("QML warnings=0 tkinter_loaded=False", result.stdout)
