import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import launchers, main, processes


def vscode_app(path):
    app = types.SimpleNamespace(
        _selected_project=lambda: {"path": str(path)},
        _status=mock.Mock(),
    )
    app._launch = lambda cmd: main.RepoManagerApp._launch(app, cmd)
    return app


class VSCodeActionTests(unittest.TestCase):
    def test_missing_editor_reports_error_without_starting_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(main.shutil, "which", return_value=None), \
                    mock.patch.object(Path, "home", return_value=Path(tmp)), \
                    mock.patch.object(main.subprocess, "Popen") as popen, \
                    mock.patch.object(main.messagebox, "showerror") as error:
                main.RepoManagerApp.open_vscode(vscode_app(tmp))
            popen.assert_not_called()
            error.assert_called_once_with(
                "RepoManager", "VS Code not found on PATH.")

    def test_unavailable_project_does_not_start_editor(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = vscode_app(Path(tmp) / "missing")
            with mock.patch.object(main.subprocess, "Popen") as popen:
                main.RepoManagerApp.open_vscode(app)
            popen.assert_not_called()
            app._status.set.assert_called_once_with(
                "Project folder is unavailable", important=True)

    def test_disappeared_editor_reports_launch_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "missing-code.exe")
            with mock.patch.object(main.shutil, "which", return_value=missing), \
                    mock.patch.object(main.subprocess, "Popen",
                                      side_effect=OSError("editor unavailable")), \
                    mock.patch.object(main.messagebox, "showerror") as error:
                main.RepoManagerApp.open_vscode(vscode_app(tmp))
            error.assert_called_once()
            self.assertIn("Launch failed", error.call_args.args[1])

    def test_native_executable_receives_project_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(main.shutil, "which",
                                   return_value=sys.executable), \
                    mock.patch.object(main.subprocess, "Popen") as popen:
                main.RepoManagerApp.open_vscode(vscode_app(tmp))
            self.assertEqual(popen.call_args.args[0], [sys.executable, tmp])
            self.assertEqual(popen.call_args.kwargs["creationflags"],
                             main.CREATE_NO_WINDOW)

    def test_rejected_batch_argument_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(main.shutil, "which",
                                   return_value=sys.executable), \
                    mock.patch.object(main.processes, "spawn_structured",
                                      side_effect=ValueError("unsafe argument")), \
                    mock.patch.object(main.messagebox, "showerror") as error:
                main.RepoManagerApp.open_vscode(vscode_app(tmp))
            error.assert_called_once()
            self.assertIn("Launch failed", error.call_args.args[1])
            self.assertIn("unsafe argument", error.call_args.args[1])

    @unittest.skipUnless(os.name == "nt", "Windows batch runtime only")
    def test_cmd_action_preserves_paths_without_command_interpretation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "capture.py"
            helper.write_text(
                "import json,sys; print(json.dumps(sys.argv[1:]))\n",
                encoding="ascii")
            shim_dir = root / "Microsoft VS Code"
            shim_dir.mkdir()
            shim = shim_dir / "code.cmd"
            shim.write_text(
                f'@echo off\r\n"{sys.executable}" "{helper}" %*\r\n',
                encoding="utf-8", newline="")
            real_popen = subprocess.Popen
            real_which = main.shutil.which
            targets = []
            for name in ("normal", "space project", "Ünicode", "R&D",
                         "R&ver&rem", "%USERNAME%", "!USERNAME!",
                         "R&echo MARKER&rem"):
                target = root / name
                target.mkdir()
                targets.append(str(target))
            targets.extend((str(root) + "\\", str(root) + "\\\\", root.anchor))
            for target in targets:
                with self.subTest(path=target):
                    children = []

                    def capture(*args, **kwargs):
                        child = real_popen(
                            *args, **kwargs, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
                        children.append(child)
                        return child

                    with mock.patch.object(
                            main.shutil, "which",
                            side_effect=lambda value: str(shim) if value == "code"
                            else real_which(value)), \
                            mock.patch.object(main.subprocess, "Popen",
                                              side_effect=capture), \
                            mock.patch.object(main.messagebox, "showerror") as error:
                        main.RepoManagerApp.open_vscode(vscode_app(target))
                    self.assertEqual(len(children), 1)
                    child = children[0]
                    with child:
                        stdout, stderr = child.communicate(timeout=10)
                    error.assert_not_called()
                    self.assertEqual(child.returncode, 0, stderr)
                    self.assertEqual(stderr, "")
                    self.assertEqual(stdout.strip(), json.dumps([str(target)]))


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
                            text=True), creationflags=main.CREATE_NO_WINDOW)
                    with child:
                        stdout, stderr = child.communicate(timeout=10)
                    self.assertEqual(child.returncode, 0, stderr)
                    self.assertEqual(stderr, "")
                    observed = [line[4:] for line in stdout.splitlines()
                                if line.startswith("CWD=")]
                    self.assertEqual(observed, [json.dumps(str(target))])
                    self.assertNotIn("STUB_MARKER", stdout.splitlines())
