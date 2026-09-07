import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import launchers, store


class CustomLauncherTests(unittest.TestCase):
    """Custom launcher validation, persistence, and execution."""

    def _configure_store(self, tmp):
        original = (store.APP_DIR, store.REPOS_FILE,
                    store.SETTINGS_FILE, store.NOTES_DIR)
        store.APP_DIR = tmp / "app"
        store.REPOS_FILE = store.APP_DIR / "repos.json"
        store.SETTINGS_FILE = store.APP_DIR / "settings.json"
        store.NOTES_DIR = store.APP_DIR / "notes"
        store.ensure_dirs()
        return original

    def _restore_store(self, original):
        store.APP_DIR, store.REPOS_FILE, store.SETTINGS_FILE, store.NOTES_DIR = original

    def test_custom_launcher_roundtrip_and_malformed_record_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            original = self._configure_store(tmp)
            try:
                record = {"launcher_id": "l-1", "project_id": "p-1", "name": "Tool", "executable": "python", "args": ["-c", "print('ok')"], "cwd": str(tmp)}
                project = {"project_id": "p-1", "path": str(tmp), "name": "Project", "custom_launchers": [record, {"name": 3}]}
                store.save_projects([project])
                loaded = store.load_projects()[0]
                self.assertEqual(loaded["custom_launchers"], [record])
                self.assertEqual(launchers.validate_custom_launcher(loaded["custom_launchers"][0]), record)
                self.assertTrue(any("custom launcher" in reason for reason in store.read_registry()[1]["reasons"]))
            finally:
                self._restore_store(original)

    def test_custom_launcher_edit_and_remove_semantics_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            original = self._configure_store(tmp)
            try:
                first = {"launcher_id": "l-1", "project_id": "p-1", "name": "Old", "executable": "old.exe", "args": ["one"], "cwd": str(tmp)}
                second = {"launcher_id": "l-2", "project_id": "p-1", "name": "Other", "executable": "other.exe", "args": [], "cwd": str(tmp)}
                project = {"project_id": "p-1", "path": str(tmp), "name": "Project", "custom_launchers": [first, second]}
                store.save_projects([project])
                project["custom_launchers"][0] = {**first, "name": "New", "executable": "new.exe", "args": ["arg with spaces"], "cwd": str(tmp)}
                project["custom_launchers"] = [project["custom_launchers"][0]]
                store.save_projects([project])
                loaded = store.load_projects()[0]
                self.assertEqual(loaded["custom_launchers"], [project["custom_launchers"][0]])
            finally:
                self._restore_store(original)

    def test_custom_candidate_is_project_scoped_and_unavailable_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            record = {"launcher_id": "l-1", "project_id": "p-1", "name": "Missing", "executable": str(tmp / "missing tool.exe"), "args": ["hello world"], "cwd": str(tmp)}
            candidate = launchers.custom_launcher_candidate(record)
            self.assertEqual(candidate["type"], launchers.TYPE_CUSTOM)
            self.assertEqual(candidate["source"], "Custom")
            self.assertFalse(candidate["healthy"])
            self.assertIn("not found", candidate["reason"])
            self.assertEqual(launchers.detect_commands(tmp, {}, project_id="p-2", custom_launchers=[record]), [])

    def test_custom_launcher_executes_structured_argv_without_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            executable = Path(os.environ.get("PYTHON", os.sys.executable))
            record = {"launcher_id": "l-1", "project_id": "p-1", "name": "Probe", "executable": str(executable), "args": ["-c", "print('ok')", "unicode \u2713 and spaces"], "cwd": str(tmp)}
            candidate = launchers.custom_launcher_candidate(record)
            with mock.patch.object(launchers.subprocess, "Popen") as popen:
                launchers.run_command(candidate, {})
            args, kwargs = popen.call_args
            self.assertEqual(args[0], [str(executable), *record["args"]])
            self.assertEqual(kwargs["cwd"], str(tmp))
            self.assertFalse(kwargs["shell"])

    def test_custom_launcher_identity_survives_project_move_and_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            record = {"launcher_id": "l-1", "project_id": "stable", "name": "Tool", "executable": "tool.exe", "args": [], "cwd": str(tmp)}
            project = {"project_id": "stable", "path": str(tmp / "old"), "name": "Old", "custom_launchers": [record]}
            project["path"] = str(tmp / "new")
            project["name"] = "Renamed"
            self.assertEqual(project["custom_launchers"][0]["project_id"], "stable")
            other = {"project_id": "other", "path": str(tmp / "other"), "name": "Other"}
            self.assertEqual(launchers.detect_commands(Path(other["path"]), {}, project_id=other["project_id"], custom_launchers=project["custom_launchers"]), [])

    def test_custom_candidate_accepts_path_resolved_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            record = {"launcher_id": "l-1", "project_id": "p-1", "name": "Tool", "executable": "python", "args": [], "cwd": str(tmp)}
            with mock.patch.object(launchers.processes, "resolve_executable",
                                   return_value=str(tmp / "python.exe")):
                candidate = launchers.custom_launcher_candidate(record)
            self.assertEqual(candidate["type"], launchers.TYPE_CUSTOM)
            self.assertTrue(candidate["healthy"])
            self.assertEqual(candidate.get("reason", ""), "")
            self.assertEqual(candidate["executable"], "python")

    def test_custom_candidate_unhealthy_when_executable_not_resolvable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            record = {"launcher_id": "l-1", "project_id": "p-1", "name": "Tool", "executable": "absolutely-no-such-tool", "args": [], "cwd": str(tmp)}
            with mock.patch.object(launchers.processes, "resolve_executable",
                                   return_value=None):
                candidate = launchers.custom_launcher_candidate(record)
            self.assertFalse(candidate["healthy"])
            self.assertIn("PATH", candidate["reason"])

    def test_custom_launcher_run_accepts_path_resolved_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            record = {"launcher_id": "l-1", "project_id": "p-1", "name": "Tool", "executable": "python", "args": ["-c", "print('ok')"], "cwd": str(tmp)}
            candidate = launchers.custom_launcher_candidate(record)
            with mock.patch.object(launchers.processes, "resolve_executable",
                                   return_value=str(tmp / "python.exe")), \
                    mock.patch.object(launchers.processes, "spawn_structured") as spawn:
                launchers.run_command(candidate, {})
            args, kwargs = spawn.call_args
            self.assertEqual(args[0], "python")
            self.assertEqual(args[1], ["-c", "print('ok')"])
            self.assertEqual(kwargs["cwd"], str(tmp))
            self.assertEqual(kwargs["creationflags"], launchers.CREATE_NEW_CONSOLE)


if __name__ == "__main__":
    unittest.main()
