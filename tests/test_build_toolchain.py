"""Release preflight must fail before altering an incompatible environment."""
import importlib.util
import os
import platform
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "build_windows.ps1"


@unittest.skipUnless(
    os.name == "nt" and shutil.which("pwsh"),
    "Windows release preflight requires PowerShell 7 (pwsh)",
)
class BuildPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def preflight(self, environment, python=None):
        return subprocess.run(
            ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(SCRIPT), "-Python", str(python or sys.executable),
             "-BuildVenv", str(environment), "-OutputRoot", str(self.base / "output"),
             "-CheckOnly"], capture_output=True, text=True, timeout=30,
        )

    def assert_official_runtime_result(self, result):
        if platform.python_version() != "3.14.7":
            self.skipTest("requires the official CPython 3.14.7 build runtime")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RequestedPython=", result.stdout)

    def test_unsupported_official_build_runtimes_are_rejected_without_creation(self):
        probe = self.base / "runtime.cmd"
        baseline = {
            "version": "3.14.7", "implementation": "CPython", "bits": 64,
            "base": sys.executable, "free_threaded": False,
        }
        for invalid in ({"version": "3.11.9"}, {"bits": 32},
                        {"free_threaded": True}, {"implementation": "PyPy"}):
            with self.subTest(runtime=invalid):
                payload = json.dumps({**baseline, **invalid})
                probe.write_text("@echo off\n" + "echo " + payload + "\n",
                                 encoding="ascii")
                result = self.preflight(self.base / "new-env", python=probe)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("require", result.stderr)
                self.assertFalse((self.base / "new-env").exists())
                self.assertFalse((self.base / "output").exists())

    def test_check_only_does_not_create_environment_or_outputs(self):
        result = self.preflight(self.base / "new-env")
        self.assert_official_runtime_result(result)
        self.assertEqual(list(self.base.iterdir()), [])

    def test_incomplete_existing_directory_is_not_repurposed(self):
        environment = self.base / "existing"
        environment.mkdir()
        sentinel = environment / "protected.txt"
        sentinel.write_bytes(b"preserve")
        result = self.preflight(environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Select an unused -BuildVenv", result.stderr)
        self.assertEqual(sentinel.read_bytes(), b"preserve")
        self.assertEqual(list(environment.iterdir()), [sentinel])
        self.assertFalse((self.base / "output").exists())

    def test_matching_real_environment_is_preserved_without_installing(self):
        environment = self.base / "matching"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)],
                       check=True, capture_output=True, timeout=30)
        before = {str(path.relative_to(environment)): path.read_bytes()
                  for path in environment.rglob("*") if path.is_file()}
        result = self.preflight(environment)
        self.assert_official_runtime_result(result)
        after = {str(path.relative_to(environment)): path.read_bytes()
                 for path in environment.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.base / "output").exists())


class ZipEntryStreamOwnershipTests(unittest.TestCase):
    """ZIP entry streams must each own a disposal boundary as they are acquired."""

    INPUT_GUARD = (
        "            $inputStream = $_.OpenRead()\n"
        "            try {\n"
        "                $outputStream = $entry.Open()\n"
        "                try {\n"
        "                    $inputStream.CopyTo($outputStream)\n"
        "                }\n"
        "                finally {\n"
        "                    $outputStream.Dispose()\n"
        "                }\n"
        "            }\n"
        "            finally {\n"
        "                $inputStream.Dispose()\n"
        "            }\n"
    )

    def setUp(self):
        self.script = SCRIPT.read_text(encoding="utf-8-sig")

    def guarded_region_end(self):
        start = self.script.find(self.INPUT_GUARD)
        if start == -1:
            self.fail("build_windows.ps1 lost the nested per-entry stream ownership structure")
        return start + len(self.INPUT_GUARD)

    def test_entry_open_is_guarded_by_the_input_stream_try(self):
        end = self.guarded_region_end()
        self.assertEqual(self.script.count("$entry.Open()"), 1)
        open_call = self.script.find("$entry.Open()")
        self.assertGreater(open_call, end - len(self.INPUT_GUARD))
        self.assertLess(open_call, end)

    def test_output_disposal_cannot_mask_input_disposal(self):
        end = self.guarded_region_end()
        region = self.script[end - len(self.INPUT_GUARD):end]
        self.assertGreaterEqual(region.find("$outputStream.Dispose()"), 0)
        self.assertGreater(region.find("$inputStream.Dispose()"),
                           region.find("$outputStream.Dispose()"))


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
