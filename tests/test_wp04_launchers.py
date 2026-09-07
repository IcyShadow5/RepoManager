import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import launchers


class Wp04LauncherTests(unittest.TestCase):
    def test_primary_is_none_when_best_candidates_are_equally_plausible(self):
        candidates = [
            {"label": "npm run dev:web", "type": "npm", "priority": 10, "healthy": True},
            {"label": "npm run dev:api", "type": "npm", "priority": 10, "healthy": True},
        ]
        self.assertIsNone(launchers.select_primary_command(candidates))

    def test_package_manager_lockfile_evidence(self):
        cases = {
            "package-lock.json": ("npm", "npm"),
            "pnpm-lock.yaml": ("pnpm", "pnpm"),
            "yarn.lock": ("yarn", "yarn"),
            "bun.lock": ("bun", "bun"),
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "package.json").write_text(json.dumps({"scripts": {"dev": "x"}}))
                (root / filename).write_text("")
                with mock.patch.object(launchers.shutil, "which", return_value="tool"):
                    cmd = launchers.detect_commands(root, {})[0]
                self.assertEqual(
                    (cmd["manager"], cmd["source"]),
                    (expected[0], filename if filename != "bun.lock" else "Bun lockfile"),
                )
                self.assertTrue(cmd["healthy"])

    def test_conflicting_lockfiles_are_explicit_and_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(json.dumps({"scripts": {"dev": "x"}}))
            (root / "package-lock.json").write_text("")
            (root / "pnpm-lock.yaml").write_text("")
            with mock.patch.object(launchers.shutil, "which", return_value="tool"):
                cmd = launchers.detect_commands(root, {})[0]
            self.assertEqual(cmd["manager"], "ambiguous package manager")
            self.assertIn("conflicting lockfiles", cmd["source"])
            self.assertFalse(cmd["healthy"])

    def test_missing_evidence_manager_remains_relevant_but_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(json.dumps({"scripts": {"dev": "x"}}))
            (root / "pnpm-lock.yaml").write_text("")
            with mock.patch.object(launchers.shutil, "which", return_value=None):
                cmd = launchers.detect_commands(root, {})[0]
            self.assertEqual(cmd["manager"], "pnpm")
            self.assertFalse(cmd["healthy"])
            self.assertIn("pnpm", cmd["reason"])

    def test_bounded_view_preserves_all_candidates(self):
        commands = [{"label": str(i), "type": "x", "priority": 20 + i, "healthy": True} for i in range(200)]
        primary, visible, hidden = launchers.bounded_launcher_view(commands, limit=4)
        self.assertIs(primary, commands[0])
        self.assertEqual(len(visible), 4)
        self.assertEqual(len(hidden), 195)
        self.assertEqual(
            {c["label"] for c in visible + hidden + [primary]},
            {str(i) for i in range(200)},
        )

    def test_run_bat_requires_confirmation_when_callback_supplied(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self.assertFalse(launchers.generate_stub_bat(tmp_path, lambda target: False))
            self.assertFalse((tmp_path / "run.bat").exists())
            self.assertTrue(launchers.generate_stub_bat(tmp_path, lambda target: True))
            self.assertTrue((tmp_path / "run.bat").exists())
            self.assertFalse(launchers.generate_stub_bat(tmp_path, lambda target: True))


if __name__ == "__main__":
    unittest.main()
