"""Release preflight must fail before altering an incompatible environment."""
import importlib.util
import os
import platform
import json
import shutil
import subprocess
import sys
import tempfile
import time
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
    # R2.5A: CheckOnly-only watchdog. Read-only audit confirmed CheckOnly
    # performs no Git/network/venv work, yet bare pwsh startup spans
    # 2.20-14.47 s locally (CheckOnly p95 14.02 s, max 17.32 s, stress max
    # 32.67 s). 60 s clears the observed tail while still catching a hang.
    CHECK_ONLY_TIMEOUT = 60

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def preflight(self, environment, python=None):
        cmd = ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(SCRIPT), "-Python", str(python or sys.executable),
               "-BuildVenv", str(environment), "-OutputRoot", str(self.base / "output"),
               "-CheckOnly"]
        self._last_preflight_cmd = list(cmd)
        self._last_preflight_pwsh = shutil.which("pwsh")
        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=self.CHECK_ONLY_TIMEOUT,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - start
            self._last_preflight_elapsed = elapsed
            partial_out = getattr(exc, "stdout", getattr(exc, "output", None))
            partial_err = getattr(exc, "stderr", None)
            detail = (
                f"pwsh={self._last_preflight_pwsh} "
                f"elapsed={elapsed:.2f}s "
                f"timeout={self.CHECK_ONLY_TIMEOUT}s "
                f"cmd={cmd} "
                f"stdout={partial_out!r} stderr={partial_err!r}"
            )
            try:
                exc.add_note(detail)
            except Exception:
                pass
            raise
        self._last_preflight_elapsed = time.monotonic() - start
        return result

    def preflight_diagnostics(self, result):
        return (
            f"pwsh={getattr(self, '_last_preflight_pwsh', None)} "
            f"elapsed={getattr(self, '_last_preflight_elapsed', None)} "
            f"cmd={getattr(self, '_last_preflight_cmd', None)} "
            f"returncode={getattr(result, 'returncode', None)} "
            f"stdout={getattr(result, 'stdout', None)!r} "
            f"stderr={getattr(result, 'stderr', None)!r}"
        )

    def assert_official_runtime_result(self, result):
        if platform.python_version() != "3.14.7":
            self.skipTest("requires the official CPython 3.14.7 build runtime")
        self.assertEqual(result.returncode, 0, self.preflight_diagnostics(result))
        self.assertIn("RequestedPython=", result.stdout,
                      self.preflight_diagnostics(result))

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


class CheckOnlyHarnessStabilityTests(unittest.TestCase):
    """R2.5A: prove the CheckOnly harness budget/diagnostics without pwsh."""

    def make_case(self):
        case = object.__new__(BuildPreflightTests)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        case.base = Path(temporary.name)
        return case

    def test_check_only_uses_sixty_second_budget_with_noninteractive_stdin(self):
        case = self.make_case()
        with mock.patch.object(subprocess, "run",
                               return_value=mock.sentinel.result) as run:
            result = case.preflight(case.base / "new-env")
        self.assertIs(result, mock.sentinel.result)
        self.assertEqual(run.call_count, 1)
        cmd = run.call_args[0][0]
        kwargs = run.call_args[1]
        self.assertEqual(kwargs.get("timeout"), 60)
        self.assertEqual(BuildPreflightTests.CHECK_ONLY_TIMEOUT, 60)
        self.assertIs(kwargs.get("stdin"), subprocess.DEVNULL)
        self.assertTrue(kwargs.get("capture_output"))
        self.assertTrue(kwargs.get("text"))
        self.assertIn("-CheckOnly", cmd)
        self.assertIn("-File", cmd)
        self.assertIn(str(SCRIPT), cmd)

    def test_timeout_propagates_and_retains_partial_output(self):
        case = self.make_case()
        expired = subprocess.TimeoutExpired(
            ["pwsh", "-CheckOnly"], 60,
            output="partial-out", stderr="partial-err")
        with mock.patch.object(subprocess, "run", side_effect=expired):
            with self.assertRaises(subprocess.TimeoutExpired) as ctx:
                case.preflight(case.base / "new-env")
        exc = ctx.exception
        self.assertIs(exc, expired)
        self.assertEqual(getattr(exc, "stdout", getattr(exc, "output", None)),
                         "partial-out")
        self.assertEqual(exc.stderr, "partial-err")
        notes = getattr(exc, "__notes__", [])
        self.assertTrue(notes, "timeout must carry preflight diagnostics")
        self.assertIn("partial-out", notes[0])
        self.assertIn("partial-err", notes[0])
        self.assertIn("elapsed=", notes[0])

    def test_failure_diagnostics_report_context_without_secrets(self):
        case = self.make_case()
        result = subprocess.CompletedProcess(
            args=["pwsh", "-CheckOnly"], returncode=1,
            stdout="some-stdout", stderr="some-stderr")
        case._last_preflight_cmd = ["pwsh", "-CheckOnly"]
        case._last_preflight_pwsh = "C:\\fake\\pwsh.exe"
        case._last_preflight_elapsed = 4.25
        detail = case.preflight_diagnostics(result)
        self.assertIn("pwsh=", detail)
        self.assertIn("elapsed=", detail)
        self.assertIn("returncode=1", detail)
        self.assertIn("some-stdout", detail)
        self.assertIn("some-stderr", detail)


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
