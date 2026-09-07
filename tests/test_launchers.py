import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import launchers
from tests.git_repository import canonical_path


def make(root: Path, *names, files=None):
    root.mkdir(parents=True, exist_ok=True)
    for n in names:
        (root / n).write_text("x", encoding="utf-8")
    for n, content in (files or {}).items():
        (root / n).write_text(content, encoding="utf-8")
    return root


class LauncherPresentationTests(unittest.TestCase):
    def test_display_name_removes_only_detected_play_marker(self):
        candidates = [
            {"label": "\u25b6 run"},
            {"label": "\u25b6 start"},
            {"label": "Custom: Dev"},
            {"label": "Run App"},
        ]
        before = [dict(candidate) for candidate in candidates]

        self.assertEqual(
            [launchers.launcher_display_name(candidate) for candidate in candidates],
            ["run", "start", "Custom: Dev", "Run App"],
        )
        self.assertEqual(candidates, before)

    def test_summary_uses_display_name_without_mutating_candidate(self):
        candidate = {"label": "\u25b6 run", "type": "bat", "file": "run.bat"}
        before = dict(candidate)

        self.assertEqual(launchers.launcher_summary(candidate)["name"], "run")
        self.assertEqual(candidate, before)


class DetectTests(unittest.TestCase):
    def test_bat_and_ps1(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "start.bat", "tool.ps1")
            cmds = launchers.detect_commands(r, {})
            types = {c["type"] for c in cmds}
            self.assertIn("bat", types)
            self.assertIn("ps1", types)

    def test_bat_ps1_twin_dedup_prefers_bat(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "start.bat", "start.ps1")
            cmds = launchers.detect_commands(r, {})
            bats = [c for c in cmds if c["type"] == "bat"]
            ps1s = [c for c in cmds if c["type"] == "ps1"]
            self.assertEqual(len(bats), 1)
            self.assertEqual(ps1s, [])  # twin suppressed

    def test_package_json_scripts_skip_pre_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), files={"package.json": json.dumps({
                "scripts": {"pretest": "x", "test": "jest",
                            "dev": "vite", "postbuild": "y"}})})
            cmds = launchers.detect_commands(r, {})
            labels = [c["label"] for c in cmds if c["type"] == "npm"]
            self.assertEqual(sorted(labels), ["npm run dev", "npm run test"])

    def test_godot_unavailable_exe_emits_unhealthy_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "project.godot")
            with mock.patch.object(launchers, "_godot_discovered", False), \
                 mock.patch.object(launchers.shutil, "which",
                                   return_value=None):
                cmds = launchers.detect_commands(r, {})
            self.assertEqual(len(cmds), 2)
            self.assertTrue(all(not c["healthy"] for c in cmds))
            self.assertTrue(all("Godot" in c["reason"] for c in cmds))
            self.assertIsNone(launchers.select_primary_command(cmds))

    def test_godot_configured_exe_healthy(self):
        exe = Path(tempfile.mkdtemp()) / "godot.exe"
        exe.write_text("x")
        self.addCleanup(lambda: exe.unlink(missing_ok=True))
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "project.godot")
            cmds = launchers.detect_commands(r, {"godot_exe": str(exe)})
            self.assertEqual({c["type"] for c in cmds},
                             {"godot_run", "godot_edit"})
            self.assertTrue(all(c["healthy"] and c["exe"] == str(exe)
                                for c in cmds))
            primary = launchers.select_primary_command(cmds)
            self.assertEqual(primary["type"], "godot_run")


class PrimarySelectionTests(unittest.TestCase):
    @staticmethod
    def cmd(label, priority=50, healthy=True, ctype="x"):
        return {"label": label, "type": ctype, "priority": priority,
                "healthy": healthy}

    def test_empty_yields_none(self):
        self.assertIsNone(launchers.select_primary_command([]))

    def test_single_healthy_returned(self):
        c = self.cmd("only")
        self.assertIs(launchers.select_primary_command([c]), c)

    def test_all_unhealthy_yields_none(self):
        self.assertIsNone(launchers.select_primary_command(
            [self.cmd("a", healthy=False)]))

    def test_healthy_beats_better_priority_unhealthy(self):
        good = self.cmd("z build", priority=20)
        bad = self.cmd("a dev", priority=10, healthy=False)
        self.assertIs(launchers.select_primary_command([bad, good]), good)

    def test_npm_ladder_dev_beats_build_test_lint(self):
        cmds = [self.cmd(f"npm run {s}", priority=p, ctype="npm")
                for s, p in (("build", 20), ("dev", 10), ("lint", 24),
                             ("test", 24))]
        self.assertEqual(
            launchers.select_primary_command(cmds)["label"], "npm run dev")

    def test_build_selected_when_no_run_word(self):
        cmds = [self.cmd(f"npm run {s}", priority=p, ctype="npm")
                for s, p in (("build", 20), ("lint", 24), ("test", 24))]
        self.assertEqual(
            launchers.select_primary_command(cmds)["label"], "npm run build")

    def test_equal_priority_is_explicitly_ambiguous(self):
        a = self.cmd("alpha", priority=18)
        b = self.cmd("beta", priority=18)
        self.assertIsNone(launchers.select_primary_command([b, a]))

    def test_demoted_only_candidates_yield_no_primary(self):
        cmds = [self.cmd("\u25b6 cleanup-old", priority=30),
                self.cmd("\u25b6 organize-x", priority=30)]
        self.assertIsNone(launchers.select_primary_command(cmds))

    def test_demoted_single_candidate_also_yields_none(self):
        only = self.cmd("\u25b6 setup-tool", priority=30)
        self.assertIsNone(launchers.select_primary_command([only]))

    def test_large_script_list_selects_dev(self):
        names = ["runtime-check", "build", "check", "check:dependencies",
                 "dev", "format", "format:check", "lint", "test"]
        cmds = [self.cmd(f"npm run {n}",
                         priority=launchers.npm_script_priority(n),
                         ctype="npm") for n in names]
        self.assertEqual(
            launchers.select_primary_command(cmds)["label"], "npm run dev")


class NpmPriorityTests(unittest.TestCase):
    def test_run_words_lowest(self):
        for w in ("dev", "start", "serve", "preview"):
            self.assertEqual(launchers.npm_script_priority(w),
                             launchers.PRIORITY_NPM_RUN)

    def test_prefixed_run_variants_rank_as_run(self):
        self.assertEqual(launchers.npm_script_priority("dev:web"),
                         launchers.PRIORITY_NPM_RUN)
        self.assertEqual(launchers.npm_script_priority("start:server"),
                         launchers.PRIORITY_NPM_RUN)

    def test_tauri_is_app_runner(self):
        self.assertEqual(launchers.npm_script_priority("tauri"),
                         launchers.PRIORITY_NPM_APPRUN)

    def test_build_above_test_lint_format_deploy(self):
        p_build = launchers.npm_script_priority("build")
        for w in ("test", "lint", "format", "deploy", "check"):
            self.assertLess(p_build, launchers.npm_script_priority(w))

    def test_unknown_middle_tier(self):
        self.assertEqual(launchers.npm_script_priority("mappings:sync"),
                         launchers.PRIORITY_NPM_OTHER)


class GodotV2Tests(unittest.TestCase):
    def setUp(self):
        original = launchers._godot_discovered
        launchers._godot_discovered = None
        self.addCleanup(setattr, launchers, "_godot_discovered", original)

    @staticmethod
    def _fake_exe(tmp):
        exe = Path(tmp) / "fake-godot.exe"
        exe.write_text("x")
        return str(exe)

    def test_nested_one_level_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            game = r / "game"
            game.mkdir()
            (game / "project.godot").write_text("x")
            exe = self._fake_exe(tmp)
            cmds = launchers.detect_commands(r, {"godot_exe": exe})
            self.assertEqual(len(cmds), 2)
            self.assertTrue(cmds[0]["label"].endswith("[game]"))
            self.assertEqual(cmds[0]["cwd"], str(game))

    def test_root_project_outranks_nested(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            (r / "project.godot").write_text("x")
            sub = r / "game"
            sub.mkdir()
            (sub / "project.godot").write_text("x")
            exe = self._fake_exe(tmp)
            cmds = launchers.detect_commands(r, {"godot_exe": exe})
            self.assertFalse(cmds[0]["label"].endswith("[game]"))
            self.assertEqual(cmds[0]["cwd"], str(r))

    def test_nested_choice_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            for sub in ("zeta-game", "alpha-game"):
                (r / sub).mkdir()
                (r / sub / "project.godot").write_text("x")
            exe = self._fake_exe(tmp)
            cmds = launchers.detect_commands(r, {"godot_exe": exe})
            self.assertIn("[alpha-game]", cmds[0]["label"])

    def test_depth_two_not_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            deep = r / "wrapper" / "inner"
            deep.mkdir(parents=True)
            (deep / "project.godot").write_text("x")
            exe = self._fake_exe(tmp)
            self.assertEqual(
                launchers.detect_commands(r, {"godot_exe": exe}), [])

    def test_excluded_directories_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            junk = r / "node_modules" / "pkg"
            junk.mkdir(parents=True)
            (junk / "project.godot").write_text("x")
            exe = self._fake_exe(tmp)
            self.assertEqual(
                launchers.detect_commands(r, {"godot_exe": exe}), [])

    def test_auto_discovery_used_without_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = self._fake_exe(tmp)
            r = Path(tmp) / "repo"
            r.mkdir()
            (r / "project.godot").write_text("x")
            with mock.patch.object(launchers, "_discover_godot_exe",
                                   return_value=fake):
                cmds = launchers.detect_commands(r, {})
            self.assertTrue(cmds and cmds[0]["healthy"])
            self.assertEqual(cmds[0]["exe"], fake)

    def test_invalid_configured_falls_back_to_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = self._fake_exe(tmp)
            r = Path(tmp) / "repo"
            r.mkdir()
            (r / "project.godot").write_text("x")
            with mock.patch.object(launchers, "_discover_godot_exe",
                                   return_value=fake):
                cmds = launchers.detect_commands(
                    r, {"godot_exe": r"C:\missing\godot.exe"})
            self.assertTrue(cmds[0]["healthy"])
            self.assertEqual(cmds[0]["exe"], fake)


class PythonV2Tests(unittest.TestCase):
    @staticmethod
    def _venv(repo):
        scripts = repo / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").write_text("x")

    def test_src_main_py_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            src = r / "src"
            src.mkdir()
            (src / "main.py").write_text("x")
            self._venv(r)
            cmds = [c for c in launchers.detect_commands(r, {})
                    if c["type"] == "py"]
            self.assertEqual(len(cmds), 1)
            self.assertEqual(cmds[0]["label"], "Python: src/main.py")
            self.assertTrue(cmds[0]["healthy"])

    def test_all_four_src_entries_supported(self):
        for name in ("app.py", "run.py", "start.py"):
            with tempfile.TemporaryDirectory() as tmp:
                r = Path(tmp)
                src = r / "src"
                src.mkdir()
                (src / name).write_text("x")
                self._venv(r)
                cmds = [c for c in launchers.detect_commands(r, {})
                        if c["type"] == "py"]
                self.assertEqual(len(cmds), 1, name)
                self.assertEqual(cmds[0]["entry"], str(r / "src" / name))

    def test_missing_venv_yields_unhealthy_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            (r / "main.py").write_text("x")
            cmds = [c for c in launchers.detect_commands(r, {})
                    if c["type"] == "py"]
            self.assertEqual(len(cmds), 1)
            self.assertFalse(cmds[0]["healthy"])
            self.assertIn("venv", cmds[0]["reason"])

    def test_broken_venv_layout_yields_unhealthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            (r / "main.py").write_text("x")
            (r / ".venv").mkdir()
            cmds = [c for c in launchers.detect_commands(r, {})
                    if c["type"] == "py"]
            self.assertFalse(cmds[0]["healthy"])

    def test_app_main_py_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            app = r / "app"
            app.mkdir()
            (app / "main.py").write_text("x")
            self._venv(r)
            cmds = [c for c in launchers.detect_commands(r, {})
                    if c["type"] == "py"]
            self.assertEqual(len(cmds), 1)
            self.assertEqual(cmds[0]["label"], "Python: app/main.py")
            self.assertTrue(cmds[0]["healthy"])

    def test_arbitrary_python_file_not_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            (r / "tool.py").write_text("x")
            (r / "helper.py").write_text("x")
            self._venv(r)
            self.assertEqual([c for c in launchers.detect_commands(r, {})
                              if c["type"] == "py"], [])


class NestedNpmTests(unittest.TestCase):
    @staticmethod
    def pkg(directory, scripts=("dev", "build")):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "package.json").write_text(json.dumps(
            {"scripts": {s: "x" for s in scripts}}))

    def test_root_package_wins_over_nested(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            self.pkg(r, ("root-dev",))
            self.pkg(r / "frontend", ("nested-dev",))
            labels = [c["label"] for c in launchers.detect_commands(r, {})
                      if c["type"] == "npm"]
            self.assertIn("npm run root-dev", labels)
            self.assertNotIn("[frontend] npm run nested-dev", labels)

    def test_nested_detected_when_no_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            self.pkg(r / "frontend", ("dev", "build"))
            cmds = [c for c in launchers.detect_commands(r, {})
                    if c["type"] == "npm"]
            self.assertEqual(sorted(c["label"] for c in cmds),
                             ["[frontend] npm run build",
                              "[frontend] npm run dev"])
            self.assertTrue(all(c["cwd"] == str(r / "frontend")
                                for c in cmds))

    def test_node_modules_never_considered(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            self.pkg(r / "node_modules" / "thing", ("dev",))
            self.assertEqual([c for c in launchers.detect_commands(r, {})
                              if c["type"] == "npm"], [])

    def test_multiple_subdirs_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            self.pkg(r / "zeta-app", ("dev",))
            self.pkg(r / "alpha-app", ("dev",))
            labels = [c["label"] for c in launchers.detect_commands(r, {})
                      if c["type"] == "npm"]
            self.assertEqual(labels, ["[alpha-app] npm run dev"])


class HealthGatingTests(unittest.TestCase):
    def test_npm_absent_makes_all_npm_unhealthy(self):
        import shutil as _shutil
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            (r / "package.json").write_text(json.dumps(
                {"scripts": {"dev": "x"}}))
            real_which = _shutil.which
            with mock.patch.object(launchers.shutil, "which",
                                   side_effect=lambda n: real_which(n)
                                   if n != "npm" else None):
                cmds = launchers.detect_commands(r, {})
            npm_cmds = [c for c in cmds if c["type"] == "npm"]
            self.assertTrue(npm_cmds)
            self.assertTrue(all(not c["healthy"] for c in npm_cmds))
            self.assertIsNone(launchers.select_primary_command(cmds))

    def test_conflicting_lockfiles_explain_ambiguity_not_missing_npm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"scripts": {"dev": "x"}}), encoding="utf-8")
            (root / "package-lock.json").write_text("{}", encoding="utf-8")
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: 9")
            command = next(c for c in launchers.detect_commands(root, {})
                           if c["type"] == launchers.TYPE_NPM)
        self.assertFalse(command["healthy"])
        self.assertIn("conflicting lockfiles", command["reason"])
        self.assertIn("package-lock.json", command["reason"])
        self.assertNotIn("not found", command["reason"])

    def test_powershell_absent_makes_ps1_unhealthy(self):
        import shutil as _shutil
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            (r / "tool.ps1").write_text("x")
            real_which = _shutil.which
            with mock.patch.object(
                    launchers.shutil, "which",
                    side_effect=lambda n: real_which(n)
                    if n not in ("powershell", "pwsh") else None):
                cmds = launchers.detect_commands(r, {})
            ps1 = [c for c in cmds if c["type"] == "ps1"]
            self.assertTrue(ps1)
            self.assertFalse(ps1[0]["healthy"])

    def test_ps1_healthy_when_only_pwsh_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            (r / "tool.ps1").write_text("x")
            with mock.patch.object(
                    launchers.shutil, "which",
                    side_effect=lambda n: r"C:\ps7\pwsh.exe"
                    if n == "pwsh" else None):
                cmds = launchers.detect_commands(r, {})
            ps1 = [c for c in cmds if c["type"] == "ps1"]
            self.assertTrue(ps1)
            self.assertTrue(ps1[0]["healthy"])

    def test_resolve_powershell_exe_prefers_powershell_then_pwsh(self):
        import shutil as _shutil
        real_which = _shutil.which
        with mock.patch.object(
                launchers.shutil, "which",
                side_effect=lambda n: r"C:\ps\powershell.exe"
                if n == "powershell" else None):
            self.assertEqual(launchers.resolve_powershell_exe(),
                             r"C:\ps\powershell.exe")
        with mock.patch.object(
                launchers.shutil, "which",
                side_effect=lambda n: r"C:\ps7\pwsh.exe"
                if n == "pwsh" else None):
            self.assertEqual(launchers.resolve_powershell_exe(),
                             r"C:\ps7\pwsh.exe")
        with mock.patch.object(launchers.shutil, "which", return_value=None):
            self.assertIsNone(launchers.resolve_powershell_exe())

    def test_utility_named_scripts_demoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            (r / "organize-desktop.ps1").write_text("x")
            (r / "cleanup-old-remnants.ps1").write_text("x")
            (r / "Start-App.cmd").write_text("x")
            cmds = launchers.detect_commands(r, {})
            by_label = {c["label"]: c["priority"] for c in cmds}
            self.assertEqual(by_label["\u25b6 Start-App"],
                             launchers.PRIORITY_PROJECT_SCRIPT)
            for label, prio in by_label.items():
                if "organize" in label or "cleanup" in label:
                    self.assertEqual(prio, launchers.PRIORITY_DEMOTED_SCRIPT,
                                     label)

    def test_every_candidate_carries_priority_and_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            (r / "start.bat").write_text("x")
            (r / "package.json").write_text(json.dumps(
                {"scripts": {"dev": "x"}}))
            for c in launchers.detect_commands(r, {}):
                self.assertIn("priority", c)
                self.assertIn("healthy", c)

    def test_python_venv_with_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "main.py")
            (r / ".venv" / "Scripts").mkdir(parents=True)
            (r / ".venv" / "Scripts" / "python.exe").write_text("x")
            cmds = launchers.detect_commands(r, {})
            self.assertEqual(len(cmds), 1)
            self.assertEqual(cmds[0]["type"], "py")
            self.assertTrue(cmds[0]["exe"].endswith("python.exe"))

    def test_python_venv_without_entry_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp))
            (r / ".venv" / "Scripts").mkdir(parents=True)
            (r / ".venv" / "Scripts" / "python.exe").write_text("x")
            self.assertEqual(launchers.detect_commands(r, {}), [])

    def test_roblox_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "Game.rbxlx")
            cmds = launchers.detect_commands(r, {})
            self.assertEqual(cmds[0]["type"], "rbx")

    def test_no_descend_into_subdirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp))
            sub = r / "node_modules" / "thing"
            sub.mkdir(parents=True)
            (sub / "run.bat").write_text("x")
            self.assertEqual(launchers.detect_commands(r, {}), [])

    def test_generate_stub_bat(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(launchers.generate_stub_bat(tmp))
            self.assertFalse(launchers.generate_stub_bat(tmp))  # exists
            self.assertTrue((Path(tmp) / "run.bat").exists())
            cmds = launchers.detect_commands(Path(tmp), {})
            self.assertEqual(cmds[0]["type"], "bat")

    def test_generate_stub_bat_writes_crlf_not_crcrlf(self):
        """Preserve embedded CRLF bytes without doubling carriage returns."""
        with tempfile.TemporaryDirectory() as tmp:
            launchers.generate_stub_bat(tmp)
            data = (Path(tmp) / "run.bat").read_bytes()
            self.assertEqual(data.count(b"\r\r\n"), 0)
            self.assertGreater(data.count(b"\r\n"), 0)
            self.assertIn(b"@echo off\r\n", data)


class MixedCaseFilenameTests(unittest.TestCase):
    """Regression: detection crashed (KeyError) on uppercase script names."""

    def test_uppercase_bat_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "START.BAT")
            cmds = launchers.detect_commands(r, {})
            self.assertEqual([c["type"] for c in cmds], ["bat"])
            self.assertEqual(cmds[0]["file"], str(Path(tmp) / "START.BAT"))
            self.assertEqual(cmds[0]["label"], "\u25b6 START")

    def test_mixed_case_cmd_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "Run.CMD")
            cmds = launchers.detect_commands(r, {})
            self.assertEqual(cmds[0]["type"], "bat")  # .cmd shares bat type
            self.assertTrue(cmds[0]["file"].endswith("Run.CMD"))

    def test_mixed_case_ps1_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "Start-App.PS1")
            cmds = launchers.detect_commands(r, {})
            self.assertEqual([c["type"] for c in cmds], ["ps1"])
            self.assertEqual(cmds[0]["label"], "\u25b6 Start-App")

    def test_wild_case_bat_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "Start-App.BaT")
            cmds = [c for c in launchers.detect_commands(r, {})
                    if c["type"] == "bat"]
            self.assertEqual(len(cmds), 1)
            self.assertTrue(cmds[0]["file"].endswith("Start-App.BaT"))

    def test_original_casing_preserved_in_path_and_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "Verify-And-Run.Windows.cmd")
            cmd = launchers.detect_commands(r, {})[0]
            self.assertTrue(cmd["file"].endswith("Verify-And-Run.Windows.cmd"))
            self.assertIn("Verify-And-Run.Windows", cmd["label"])

    def test_mixed_case_twin_dedup_prefers_bat(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "Start.bat", "START.PS1")
            cmds = launchers.detect_commands(r, {})
            self.assertEqual(len(cmds), 1)
            self.assertEqual(cmds[0]["type"], "bat")

    def test_mixed_case_twin_dedup_both_bat_exts(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "RUN.BAT", "run.CMD")
            cmds = launchers.detect_commands(r, {})
            self.assertEqual(len(cmds), 1)
            self.assertEqual(cmds[0]["type"], "bat")
            self.assertTrue(cmds[0]["file"].endswith("RUN.BAT"))

    def test_distinct_stems_not_deduped_across_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "Build.bat", "DEPLOY.ps1")
            types = sorted(c["type"] for c in launchers.detect_commands(r, {}))
            self.assertEqual(types, ["bat", "ps1"])

    def test_lowercase_behavior_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "start.bat", "tool.ps1")
            cmds = launchers.detect_commands(r, {})
            self.assertEqual([(c["type"], c["label"]) for c in cmds],
                             [("bat", "\u25b6 start"),
                              ("ps1", "\u25b6 tool")])

    def test_spaces_and_unicode_in_repo_path_preserved(self):
        # Windows-realistic: repo inside a folder with spaces + Unicode
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp) / "Meine Projekte" / "Ünïcode Repo"
            r.mkdir(parents=True)
            (r / "Start Me.cmd").write_text("x", encoding="ascii")
            cmds = launchers.detect_commands(r, {})
            self.assertEqual(len(cmds), 1)
            self.assertEqual(cmds[0]["file"], str(r / "Start Me.cmd"))
            self.assertEqual(cmds[0]["label"], "\u25b6 Start Me")

    def test_lowercase_bat_cmd_twin_prefers_bat_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "start.cmd", "start.bat")
            cmds = launchers.detect_commands(r, {})
            self.assertEqual(len(cmds), 1)
            self.assertEqual(cmds[0]["type"], "bat")
            self.assertTrue(cmds[0]["file"].endswith("start.bat"))



class NpmNameValidationTests(unittest.TestCase):
    VALID = ("dev", "start", "build:prod", "test:unit", "@scope/gen",
             "lint_fix", "a" * 64)
    HOSTILE = ('x" & echo hi & rem',   # quote and command-separator payload
               'a"b',                  # embedded quote
               "a^b",                  # caret stripped by cmd
               "a%PATH%",              # environment expansion
               "a&echo x",             # ampersand
               "a|x",                  # pipe
               "a>b", "<b",            # redirection
               "a b",                  # space
               "",                     # empty
               "a" * 65,               # too long
               "skripté",              # unicode
               )

    def test_valid_names_accepted(self):
        for name in self.VALID:
            self.assertTrue(launchers.NPM_SCRIPT_RE.match(name), name)

    def test_hostile_names_rejected(self):
        for name in self.HOSTILE:
            self.assertIsNone(launchers.NPM_SCRIPT_RE.match(name), name)

    def test_detection_hides_invalid_keeps_valid_and_logs(self):
        scripts = {"dev": "vite", 'x" & calc': "evil", "a^b": "caret"}
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), files={
                "package.json": json.dumps({"scripts": scripts})})
            with self.assertLogs(launchers.log, level="INFO") as captured:
                cmds = launchers.detect_commands(r, {})
        labels = [c["label"] for c in cmds if c["type"] == "npm"]
        self.assertEqual(labels, ["npm run dev"])
        self.assertTrue(any("unsafe name" in line for line in captured.output))


class ShLauncherTests(unittest.TestCase):
    def test_hidden_when_no_executor(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "start.sh")
            with mock.patch.object(launchers, "resolve_sh_executor",
                                   return_value=None):
                cmds = launchers.detect_commands(r, {})
            self.assertEqual([c for c in cmds if c["type"] == "sh"], [])

    def test_wsl_executor_labeled(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "start.sh")
            with mock.patch.object(launchers, "resolve_sh_executor",
                                   return_value="wsl"):
                cmds = launchers.detect_commands(r, {})
            sh = [c for c in cmds if c["type"] == "sh"]
            self.assertEqual(len(sh), 1)
            self.assertEqual(sh[0]["executor"], "wsl")
            self.assertIn("(WSL)", sh[0]["label"])

    def test_git_bash_executor_labeled(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "start.sh")
            with mock.patch.object(launchers, "resolve_sh_executor",
                                   return_value="git-bash"):
                cmds = launchers.detect_commands(r, {})
            sh = [c for c in cmds if c["type"] == "sh"]
            self.assertEqual(sh[0]["executor"], "git-bash")
            self.assertIn("(Git Bash)", sh[0]["label"])

    def test_run_command_wsl_argv(self):
        cmd = {"type": "sh", "file": r"C:\repo\start.sh", "executor": "wsl"}
        with mock.patch.object(launchers.shutil, "which", return_value="wsl"), \
                mock.patch.object(launchers.processes, "spawn_structured") as spawn:
            launchers.run_command(cmd, {})
        args, kwargs = spawn.call_args
        self.assertEqual(args[:2], ("wsl", ("./start.sh",)))
        self.assertEqual(kwargs["cwd"], r"C:\repo")
        self.assertEqual(kwargs["creationflags"], launchers.CREATE_NEW_CONSOLE)

    def test_run_command_git_bash_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            git_fake = Path(tmp) / "cmd" / "git.exe"
            bash_fake = Path(tmp) / "bin" / "bash.exe"
            bash_fake.parent.mkdir(parents=True)
            git_fake.parent.mkdir(parents=True)
            git_fake.write_text("")
            bash_fake.write_text("")
            which = lambda name: (str(git_fake) if name == "git"
                                  else None)  # noqa: E731
            cmd = {"type": "sh", "file": str(Path(tmp) / "start.sh"),
                   "executor": "git-bash"}
            with mock.patch.object(launchers.shutil, "which", which), \
                 mock.patch.object(launchers.processes, "spawn_structured") as spawn:
                launchers.run_command(cmd, {})
            args, _kwargs = spawn.call_args
            self.assertEqual(canonical_path(args[0]), canonical_path(bash_fake))
            self.assertEqual(canonical_path(args[1][0]),
                             canonical_path(Path(tmp) / "start.sh"))

    def test_run_command_without_executor_is_defensive_error(self):
        with self.assertRaises(ValueError):
            launchers.run_command({"type": "sh"}, {})

    def test_run_command_missing_executor_runtime_raises(self):
        cmd = {"type": "sh", "file": r"C:\repo\start.sh",
               "executor": "git-bash"}
        with mock.patch.object(launchers.shutil, "which", return_value=None):
            with self.assertRaises(OSError):
                launchers.run_command(cmd, {})


class WslCapabilityTests(unittest.TestCase):
    def setUp(self):
        original = launchers._wsl_usable
        launchers._wsl_usable = None  # tri-state reset (test-only)
        self.addCleanup(setattr, launchers, "_wsl_usable", original)

    def test_probe_success_means_usable(self):
        with mock.patch.object(launchers.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0)
            self.assertTrue(launchers.is_wsl_usable())
        run.assert_called_once()

    def test_probe_nonzero_exit_means_unusable(self):
        with mock.patch.object(launchers.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=1)
            self.assertFalse(launchers.is_wsl_usable())

    def test_probe_timeout_means_unusable(self):
        with mock.patch.object(launchers.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired(
                                   cmd="wsl", timeout=10)):
            self.assertFalse(launchers.is_wsl_usable())

    def test_probe_missing_binary_means_unusable(self):
        with mock.patch.object(launchers.subprocess, "run",
                               side_effect=FileNotFoundError("no wsl")):
            self.assertFalse(launchers.is_wsl_usable())

    def test_probe_executed_only_once_per_process(self):
        with mock.patch.object(launchers.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0)
            for _ in range(3):
                self.assertTrue(launchers.is_wsl_usable())
        self.assertEqual(run.call_count, 1)

    def test_stub_on_path_without_distro_is_not_wsl(self):
        # wsl.exe exists but cannot execute Linux commands
        with mock.patch.object(launchers.shutil, "which", return_value="stub"), \
             mock.patch.object(launchers, "is_wsl_usable", return_value=False):
            self.assertIsNone(launchers.resolve_sh_executor())

    def test_usable_wsl_selected(self):
        with mock.patch.object(launchers.shutil, "which", return_value="stub"), \
             mock.patch.object(launchers, "is_wsl_usable", return_value=True):
            self.assertEqual(launchers.resolve_sh_executor(), "wsl")

    def test_git_bash_fallback_when_wsl_unusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            git_fake = Path(tmp) / "cmd" / "git.exe"
            bash_fake = Path(tmp) / "bin" / "bash.exe"
            git_fake.parent.mkdir(parents=True)
            bash_fake.parent.mkdir(parents=True)
            git_fake.write_text("")
            bash_fake.write_text("")
            which = lambda name: (str(git_fake) if name == "git" else
                                  "stub-wsl" if name == "wsl" else
                                  None)  # noqa: E731
            with mock.patch.object(launchers.shutil, "which", which), \
                 mock.patch.object(launchers, "is_wsl_usable",
                                   return_value=False):
                self.assertEqual(launchers.resolve_sh_executor(), "git-bash")

    def test_neither_available_yields_none(self):
        with mock.patch.object(launchers.shutil, "which", return_value=None), \
             mock.patch.object(launchers, "is_wsl_usable", return_value=False):
            self.assertIsNone(launchers.resolve_sh_executor())

    def test_detection_probes_only_once_across_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "start.sh")
            which = lambda name: "stub-wsl" if name == "wsl" else None  # noqa: E731
            with mock.patch.object(launchers.subprocess, "run") as run, \
                 mock.patch.object(launchers.shutil, "which", which):
                run.return_value = mock.Mock(returncode=0)
                launchers.detect_commands(r, {})
                launchers.detect_commands(r, {})
                launchers.detect_commands(r, {})
            self.assertEqual(run.call_count, 1)

    def test_detection_without_sh_files_never_probes(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = make(Path(tmp), "start.bat")
            with mock.patch.object(launchers.subprocess, "run") as run:
                launchers.detect_commands(r, {})
            run.assert_not_called()


class RunCommandGoldenTests(unittest.TestCase):
    def _popen(self):
        return mock.patch.object(launchers.subprocess, "Popen")

    def test_bat_golden(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "start.bat"
            script.write_text("@echo off\r\n", encoding="ascii")
            cmd = {"type": "bat", "file": str(script)}
            with self._popen() as popen:
                launchers.run_command(cmd, {})
            args, kwargs = popen.call_args
        self.assertIn("cmd.exe", args[0].casefold())
        self.assertIn("/d /v:off /s /c", args[0].casefold())
        self.assertNotIn(str(script), args[0])
        self.assertEqual(kwargs["env"]["REPOMANAGER_EXECUTABLE"], str(script))
        self.assertEqual(kwargs["cwd"], str(script.parent))
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["creationflags"], launchers.CREATE_NEW_CONSOLE)

    def test_ps1_golden(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "tool.ps1"
            script.write_text("Write-Output ok", encoding="utf-8")
            cmd = {"type": "ps1", "file": str(script)}
            ps_exe = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
            with mock.patch.object(
                    launchers, "resolve_powershell_exe", return_value=ps_exe), \
                    mock.patch.object(
                        launchers.processes, "spawn_structured") as spawn:
                launchers.run_command(cmd, {})
        args, kwargs = spawn.call_args
        self.assertEqual(
            args[:2],
            (ps_exe, ("-NoProfile", "-File", str(script))))
        self.assertNotIn("ExecutionPolicy", args[1])
        self.assertEqual(kwargs["creationflags"], launchers.CREATE_NEW_CONSOLE)

    def test_ps1_runs_with_pwsh_when_windows_powershell_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "tool.ps1"
            script.write_text("Write-Output ok", encoding="utf-8")
            cmd = {"type": "ps1", "file": str(script)}
            ps_exe = r"C:\Program Files\PowerShell\7\pwsh.exe"
            with mock.patch.object(
                    launchers, "resolve_powershell_exe", return_value=ps_exe), \
                    mock.patch.object(
                        launchers.processes, "spawn_structured") as spawn:
                launchers.run_command(cmd, {})
        args, kwargs = spawn.call_args
        self.assertEqual(
            args[:2], (ps_exe, ("-NoProfile", "-File", str(script))))
        self.assertNotIn("ExecutionPolicy", args[1])
        self.assertEqual(kwargs["creationflags"], launchers.CREATE_NEW_CONSOLE)

    def test_npm_golden(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = Path(tmp) / "npm.cmd"
            manager.write_text("@echo off\r\n", encoding="ascii")
            cmd = {"type": "npm", "script": "dev", "cwd": tmp}
            with mock.patch.object(
                    launchers.processes, "resolve_executable",
                    return_value=str(manager)), self._popen() as popen:
                launchers.run_command(cmd, {})
            args, kwargs = popen.call_args
        self.assertIn("cmd.exe", args[0].casefold())
        self.assertEqual(kwargs["env"]["REPOMANAGER_EXECUTABLE"], str(manager))
        self.assertEqual(kwargs["env"]["REPOMANAGER_ARG_0"], "run")
        self.assertEqual(kwargs["env"]["REPOMANAGER_ARG_1"], "dev")
        self.assertEqual(kwargs["cwd"], tmp)
        self.assertFalse(kwargs["shell"])

    def test_py_golden(self):
        cmd = {"type": "py", "exe": r"C:\repo\.venv\Scripts\python.exe",
               "entry": r"C:\repo\main.py", "cwd": r"C:\repo"}
        with mock.patch.object(launchers.processes, "spawn_structured") as spawn:
            launchers.run_command(cmd, {})
        args, kwargs = spawn.call_args
        self.assertEqual(args[:2], (cmd["exe"], (cmd["entry"],)))
        self.assertEqual(kwargs["cwd"], r"C:\repo")
        self.assertEqual(kwargs["creationflags"], launchers.CREATE_NEW_CONSOLE)

    def test_godot_golden(self):
        run_cmd = {"type": "godot_run", "cwd": r"C:\repo"}
        edit_cmd = {"type": "godot_edit", "cwd": r"C:\repo"}
        settings = {"godot_exe": r"C:\godot.exe"}
        with mock.patch.object(launchers.processes, "spawn_structured") as spawn:
            launchers.run_command(run_cmd, settings)
            launchers.run_command(edit_cmd, settings)
        first_args, first_kw = spawn.call_args_list[0]
        second_args, second_kw = spawn.call_args_list[1]
        self.assertEqual(first_args[:2],
                         (r"C:\godot.exe", ["--path", r"C:\repo"]))
        self.assertEqual(second_args[:2],
                         (r"C:\godot.exe", ["-e", "--path", r"C:\repo"]))
        self.assertNotIn("creationflags", first_kw)  # unchanged GUI delegation

    def test_rbx_uses_startfile(self):
        cmd = {"type": "rbx", "file": r"C:\repo\Game.rbxlx"}
        with mock.patch.object(launchers.os, "startfile") as startfile:
            launchers.run_command(cmd, {})
        startfile.assert_called_once_with(r"C:\repo\Game.rbxlx")

    def test_spawn_failure_propagates_for_dialog(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "start.bat"
            script.write_text("@echo off\r\n", encoding="ascii")
            cmd = {"type": "bat", "file": str(script)}
            with self._popen() as popen:
                popen.side_effect = OSError("[WinError 2] not found")
                with self.assertRaises(OSError):
                    launchers.run_command(cmd, {})

    def test_unknown_type_defensive_error(self):
        with self.assertRaises(ValueError):
            launchers.run_command({"type": "mystery"}, {})


if __name__ == "__main__":
    unittest.main()
