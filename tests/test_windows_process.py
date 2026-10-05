"""Actual Windows containment, including descendants of command shims."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from repo_manager import processes


@unittest.skipUnless(os.name == "nt", "Windows job objects only")
class WindowsProcessTests(unittest.TestCase):
    def test_failed_job_assignment_never_runs_the_suspended_program(self):
        from repo_manager import windows_process
        import ctypes
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "must-not-exist.txt"
            api = mock.Mock(wraps=windows_process.native_api())
            def fail_assignment(*args):
                ctypes.set_last_error(5)
                return False
            api.AssignProcessToJobObject.side_effect = fail_assignment
            with mock.patch.object(windows_process, "native_api", return_value=api), self.assertRaises(OSError):
                processes.spawn_agent(sys.executable, ["-c", "from pathlib import Path; Path('must-not-exist.txt').write_text('ran')"], cwd=folder, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertFalse(marker.exists())
            api.ResumeThread.assert_not_called()

    def wait_for_file(self, path, process):
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail("Agent exited before creating its readiness file")
            time.sleep(0.01)
        self.assertTrue(path.is_file())

    def test_cmd_stop_terminates_child_and_releases_its_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "Ünicode & repo"
            root.mkdir()
            ready = root / "ready.txt"
            child = root / "child.py"
            child.write_text("import pathlib,time\nf=open('ready.txt','w'); f.write('ready'); f.flush()\ntime.sleep(30)\n", encoding="utf-8")
            script = root / "Agent Start.cmd"
            script.write_text('@echo off\n"%~1" "%~2"\n', encoding="ascii")
            process = processes.spawn_agent(str(script), [sys.executable, str(child)], cwd=str(root), creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                self.wait_for_file(ready, process)
                self.assertIsNone(process.poll())
                process.terminate()
                self.assertNotEqual(process.wait(timeout=5), 0)
                # Windows denies unlink while the child's open file survives.
                ready.unlink()
                self.assertFalse(ready.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    def test_parent_exit_does_not_report_success_while_child_survives(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ready = root / "ready.txt"
            child = root / "child.py"
            child.write_text("import time\nf=open('ready.txt','w'); f.write('ready'); f.flush()\ntime.sleep(30)\n", encoding="utf-8")
            program = "import subprocess,sys; subprocess.Popen([sys.executable, 'child.py'])"
            process = processes.spawn_agent(sys.executable, ["-c", program], cwd=str(root), creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                self.wait_for_file(ready, process)
                time.sleep(0.1)
                self.assertIsNone(process.poll())
                with self.assertRaises(subprocess.TimeoutExpired):
                    process.wait(timeout=0.05)
                process.terminate()
                self.assertEqual(process.wait(timeout=5), 0)
                ready.unlink()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    def test_normal_exit_and_launch_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            process = processes.spawn_agent(sys.executable, ["-c", "raise SystemExit(7)"], cwd=folder, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(process.wait(timeout=5), 7)
            self.assertEqual(process.poll(), 7)
            with self.assertRaises(OSError):
                processes.spawn_agent(str(Path(folder) / "missing.exe"), [], cwd=folder)


if __name__ == "__main__":
    unittest.main()
