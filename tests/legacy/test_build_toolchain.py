"""Legacy Tk distribution license collector; not the portable Qt release gate."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[2]

class RuntimeLicenseTests(unittest.TestCase):
    def test_missing_python_notice_fails_before_package_is_modified(self):
        spec = importlib.util.spec_from_file_location(
            "runtime_licenses", ROOT / "packaging" / "runtime_licenses.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with mock.patch.object(module.sys, "base_prefix", str(base)), \
                    mock.patch.object(module.tkinter, "Tk") as tk:
                with self.assertRaises(FileNotFoundError):
                    module.collect(base / "bundle", base / "metadata.json")
            tk.assert_not_called()
            self.assertEqual(list(base.iterdir()), [])
