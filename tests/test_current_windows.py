"""Current Windows service and structured process boundaries; no Tk widgets."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from repo_manager import launchers, processes, repository_service
from tests.test_repository_service import IsolatedSessionTests

@unittest.skipUnless(os.name == "nt", "Windows service boundary")
class FolderActionTests(IsolatedSessionTests):
    def test_explorer_and_terminal_receive_exact_target_and_working_directory(self):
        record, target = self.seed("space & ü")
        for action, command, args in (("explorer", "explorer.exe", (record["path"],)),
                                      ("terminal", "wt.exe", ("-d", record["path"]))):
            with self.subTest(action=action), mock.patch.object(processes, "spawn_structured") as spawn:
                self.session.open_folder(target, action)
                self.assertEqual(spawn.call_args.args, (command, args))
                self.assertEqual(spawn.call_args.kwargs["cwd"], record["path"])
                self.assertEqual(spawn.call_args.kwargs["creationflags"], 0x08000000)

    def test_vscode_executable_receives_exact_path_without_shell(self):
        record, target = self.seed("space & ü")
        with mock.patch.object(repository_service.shutil, "which", return_value=sys.executable), \
                mock.patch.object(processes, "spawn_structured") as spawn:
            self.session.open_folder(target, "vscode")
        self.assertEqual(spawn.call_args.args, (sys.executable, (record["path"],)))
        self.assertEqual(spawn.call_args.kwargs["cwd"], record["path"])
        self.assertEqual(spawn.call_args.kwargs["creationflags"], 0x08000000)

    def test_missing_editor_unavailable_folder_and_stale_target_never_launch(self):
        record, target = self.seed()
        with mock.patch.object(repository_service.shutil, "which", return_value=None), \
                mock.patch.object(Path, "home", return_value=self.root), \
                mock.patch.object(processes, "spawn_structured") as spawn:
            with self.assertRaisesRegex(OSError, "VS Code not found"):
                self.session.open_folder(target, "vscode")
            Path(record["path"]).rmdir()
            with self.assertRaisesRegex(OSError, "unavailable"):
                self.session.open_folder(target, "explorer")
            record["path"] = str(self.root)
            with self.assertRaises((ValueError, OSError)):
                self.session.open_folder(target, "terminal")
            spawn.assert_not_called()

    def test_launch_failures_remain_visible(self):
        _, target = self.seed()
        with mock.patch.object(processes, "spawn_structured", side_effect=OSError("missing wt.exe")):
            with self.assertRaisesRegex(OSError, "missing wt.exe"):
                self.session.open_folder(target, "terminal")

    def test_missing_terminal_explains_requirement_without_launching_a_shell(self):
        _, target = self.seed()
        with mock.patch.object(processes, "resolve_executable", return_value=None), \
                mock.patch.object(repository_service.subprocess, "Popen") as launched:
            with self.assertRaisesRegex(OSError, "Windows Terminal.*PowerShell 7 is not required"):
                self.session.open_folder(target, "terminal")
            launched.assert_not_called()

    def test_current_vscode_batch_preserves_special_paths_in_real_child(self):
        helper = self.root / "capture.py"
        helper.write_text("import json,os,sys; print(json.dumps([os.getcwd(),sys.argv[1:]]))\n", encoding="ascii")
        shim = self.root / "code.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{helper}" %*\r\n', encoding="utf-8", newline="")
        real_popen = subprocess.Popen
        for name in ("space project", "Ünicode", "R&D", "%USERNAME%", "!USERNAME!"):
            with self.subTest(name=name):
                record, target = self.seed(name)
                children = []
                def capture(*args, **kwargs):
                    child = real_popen(*args, **kwargs, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    children.append(child)
                    return child
                with mock.patch.object(repository_service.shutil, "which", return_value=str(shim)), \
                        mock.patch.object(repository_service.subprocess, "Popen", side_effect=capture):
                    self.session.open_folder(target, "vscode")
                self.assertEqual(len(children), 1)
                with children[0] as child:
                    stdout, stderr = child.communicate(timeout=10)
                self.assertEqual(child.returncode, 0, stderr)
                self.assertEqual(stderr, "")
                self.assertEqual(json.loads(stdout), [record["path"], [record["path"]]])

class GeneratedStarterBoundaryTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows batch runtime only")
    def test_generated_starter_changes_directory_without_interpreting_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "cwd.py"
            helper.write_text(
                "import json,os; print('CWD=' + json.dumps(os.getcwd()))\n",
                encoding="ascii")
            for name in ("normal", "space project", "Ünicode", "R&D",
                         "R&echo STUB_MARKER&rem", "%USERNAME%", "!USERNAME!"):
                with self.subTest(name=name):
                    target = root / name
                    target.mkdir()
                    self.assertTrue(launchers.generate_stub_bat(target))
                    script = target / "run.bat"
                    original = script.read_bytes()
                    self.assertNotIn(b"\r\r\n", original)
                    self.assertNotIn(b"\n", original.replace(b"\r\n", b""))
                    self.assertFalse(launchers.generate_stub_bat(target))
                    self.assertEqual(script.read_bytes(), original)
                    # Observe cwd after executing the complete generated template.
                    with script.open("a", encoding="utf-8", newline="") as out:
                        out.write(f'"{sys.executable}" "{helper}"\r\n')
                    child = processes.spawn_structured(
                        str(script), cwd=str(root),
                        popen=lambda *a, **kw: subprocess.Popen(
                            *a, **kw, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True), creationflags=0x08000000)
                    with child:
                        stdout, stderr = child.communicate(timeout=10)
                    self.assertEqual(child.returncode, 0, stderr)
                    self.assertEqual(stderr, "")
                    observed = [line[4:] for line in stdout.splitlines()
                                if line.startswith("CWD=")]
                    self.assertEqual(observed, [json.dumps(str(target))])
                    self.assertNotIn("STUB_MARKER", stdout.splitlines())
