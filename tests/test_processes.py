import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import processes


class StructuredInvocationTests(unittest.TestCase):
    def test_direct_executable_remains_structured(self):
        argv, env = processes.structured_invocation(
            sys.executable, ["-V"])
        self.assertEqual(argv, [sys.executable, "-V"])
        self.assertIsNone(env)

    def test_batch_values_are_not_embedded_in_cmd_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "Start & Verify.cmd"
            script.write_text("@echo off\r\n", encoding="ascii")
            hostile = "hello & whoami | not-a-command"
            argv, env = processes.structured_invocation(
                str(script), [hostile], platform="nt")
        self.assertEqual(Path(argv[0]).name.casefold(), "cmd.exe")
        self.assertEqual(argv[1:5], ["/d", "/v:off", "/s", "/c"])
        self.assertNotIn(str(script), argv[-1])
        self.assertNotIn(hostile, argv[-1])
        self.assertEqual(env["REPOMANAGER_EXECUTABLE"], str(script))
        self.assertEqual(env["REPOMANAGER_ARG_0"], hostile)

    def test_batch_boundary_rejects_quote_or_line_break_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "run.cmd"
            script.write_text("@echo off\r\n", encoding="ascii")
            for value in ('bad"arg', "bad\rarg", "bad\narg"):
                with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                    processes.structured_invocation(
                        str(script), [value], platform="nt")

    def test_batch_boundary_does_not_trust_comspec(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "run.cmd"
            script.write_text("@echo off\r\n", encoding="ascii")
            trusted = r"C:\Windows\System32\cmd.exe"
            with mock.patch.object(
                    processes, "_system_cmd_executable",
                    return_value=trusted):
                argv, _env = processes.structured_invocation(
                    str(script), environ={"COMSPEC": r"C:\attacker\cmd.exe"},
                    platform="nt")
        self.assertEqual(argv[0], trusted)

    @unittest.skipUnless(os.name == "nt", "Windows command runtime only")
    def test_cmd_and_bat_run_with_spaces_unicode_and_structured_argv(self):
        body = (
            "@echo off\r\n"
            '"%~1" -c "import pathlib,sys; '
            "pathlib.Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')\""
            ' "%~2" "%~3"\r\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Meine Projekte" / "Ünicode Repo"
            root.mkdir(parents=True)
            for suffix in (".cmd", ".bat"):
                with self.subTest(suffix=suffix):
                    script = root / f"Start App{suffix}"
                    output = root / f"result {suffix[1:]}.txt"
                    value = (f"Grüße {suffix} & echo INJECTED > "
                             "injected.txt | rem %PATH%")
                    script.write_text(body, encoding="ascii")
                    process = processes.spawn_structured(
                        str(script), [sys.executable, str(output), value],
                        cwd=str(root))
                    self.assertEqual(process.wait(timeout=10), 0)
                    self.assertEqual(output.read_text(encoding="utf-8"), value)
                    self.assertFalse((root / "injected.txt").exists())


if __name__ == "__main__":
    unittest.main()
