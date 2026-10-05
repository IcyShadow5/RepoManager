import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import ua05_diagnostics as diagnostic


class Ua05DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app_dir = self.root / "RepoManager"
        self.source_root = self.root / "source"
        self.app_dir.mkdir()
        (self.source_root / "tests").mkdir(parents=True)
        (self.source_root / "repo_manager").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_report_finds_persisted_synthetic_records_without_mutating_files(self):
        registry = self.app_dir / "repos.json"
        settings = self.app_dir / "settings.json"
        payload = {
            "schema_version": 2,
            "projects": [
                {"name": "repo000000", "path": r"C:\repos\repo000000"},
                {"name": "Real", "path": r"C:\work\Real"},
            ],
        }
        self._write_json(registry, payload)
        self._write_json(settings, {"roots": [r"C:\work"]})
        before_registry = registry.read_bytes()
        before_settings = settings.read_bytes()

        report = diagnostic.build_report(self.app_dir, source_root=self.source_root)

        registry_summary = report["artifacts"]["registry"][0]
        self.assertEqual(registry_summary["project_count"], 2)
        self.assertEqual(len(registry_summary["synthetic_records"]), 1)
        self.assertEqual(
            report["conclusions"][0]["status"], "present")
        self.assertEqual(registry.read_bytes(), before_registry)
        self.assertEqual(settings.read_bytes(), before_settings)
        self.assertTrue(report["read_only"])

    def test_source_fixture_is_test_provenance_not_production_provenance(self):
        fixture = self.source_root / "tests" / "test_gui_scaling.py"
        fixture.write_text(
            'def _mk_project(i):\n'
            '    store.APP_DIR = isolated_app_dir\n'
            '    store.save_projects([])\n'
            '    return {"path": rf"C:\\\\repos\\\\repo{i:05d}"}\n',
            encoding="utf-8",
        )
        (self.source_root / "repo_manager" / "production.py").write_text(
            "def normal():\n    return 'repository'\n", encoding="utf-8")

        report = diagnostic.build_report(self.app_dir, source_root=self.source_root)
        provenance = report["source_provenance"]

        self.assertTrue(provenance["test_hits"])
        self.assertEqual(provenance["production_hits"], [])
        isolation = next(item for item in provenance["isolation_review"]
                         if item["file"].replace("\\", "/")
                         == "tests/test_gui_scaling.py")
        self.assertEqual(isolation["status"], "path-isolated")
        conclusion = next(item for item in report["conclusions"]
                           if item["code"] == "current_source_provenance")
        self.assertEqual(
            conclusion["status"], "no-production-match-test-fixture-present")
        self.assertEqual(conclusion["confidence"], "high")

    def test_generic_production_store_reference_is_not_synthetic_provenance(self):
        (self.source_root / "repo_manager" / "storage.py").write_text(
            'def paths():\n'
            '    return store.APP_DIR / "repo_manager.log", store.APP_DIR / "repo_manager.lock"\n',
            encoding="utf-8")

        report = diagnostic.build_report(self.app_dir, source_root=self.source_root)

        self.assertEqual(report["source_provenance"]["production_hits"], [])
        self.assertTrue(report["source_provenance"]["isolation_hits"])
        conclusion = next(item for item in report["conclusions"]
                          if item["code"] == "current_source_provenance")
        self.assertEqual(conclusion["status"], "no-source-match")

    def test_actual_production_synthetic_generator_is_detected(self):
        (self.source_root / "repo_manager" / "fixture_generator.py").write_text(
            'def make_project(index):\n'
            '    return {"name": f"repo{index:05d}", "path": f"C:\\\\repos\\\\repo{index:05d}"}\n',
            encoding="utf-8")

        report = diagnostic.build_report(self.app_dir, source_root=self.source_root)

        production_hits = report["source_provenance"]["production_hits"]
        self.assertTrue(production_hits)
        self.assertTrue(any(
            hit["file"].replace("\\", "/") == "repo_manager/fixture_generator.py"
            for hit in production_hits))
        conclusion = next(item for item in report["conclusions"]
                          if item["code"] == "current_source_provenance")
        self.assertEqual(conclusion["status"], "production-source-match")

    def test_source_directory_exclusions_skip_environment_fixtures(self):
        excluded = (
            ".venv", "venv", "node_modules", ".tox", ".pytest_cache",
            ".mypy_cache", ".ruff_cache", "__pycache__",
        )
        for directory in excluded:
            path = self.source_root / directory / "fixture.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                'return f"repo{index:05d}"\n', encoding="utf-8")
        visible = self.source_root / "repo_manager" / "VISIBLE.py"
        visible.write_text(
            'return f"repo{index:05d}"\n', encoding="utf-8")

        report = diagnostic.build_report(self.app_dir, source_root=self.source_root)

        files = {
            hit["file"].replace("\\", "/")
            for hit in report["source_provenance"]["test_hits"]
            + report["source_provenance"]["production_hits"]
        }
        self.assertEqual(files, {"repo_manager/VISIBLE.py"})
        self.assertLessEqual(report["source_provenance"]["files_considered"], 1)

    def test_configured_synthetic_root_and_log_are_reported(self):
        self._write_json(
            self.app_dir / "settings.json",
            {"roots": [r"C:\repos", r"C:\Users\u\Projects"]})
        (self.app_dir / "repo_manager.log").write_text(
            "created C:\\repos\\repo000001\n", encoding="utf-8")

        report = diagnostic.build_report(self.app_dir, source_root=self.source_root)
        settings = report["artifacts"]["settings"][0]
        self.assertEqual(settings["synthetic_roots"], [r"C:\repos"])
        self.assertEqual(report["artifacts"]["log"]["match_count"], 1)
        historical = next(item for item in report["conclusions"]
                          if item["code"] == "historical_log_provenance")
        self.assertEqual(historical["status"], "matches-found")

    def test_backups_and_quarantine_are_inspected_but_not_recovered_or_changed(self):
        primary = self.app_dir / "repos.json"
        backup = self.app_dir / "repos.json.bak1"
        quarantine = self.app_dir / "repos.json.corrupt-1"
        primary.write_text("{broken", encoding="utf-8")
        self._write_json(backup, {
            "projects": [{"name": "repo000002", "path": r"C:\repos\repo000002"}]
        })
        quarantine.write_text("historical bytes", encoding="utf-8")
        before = {
            path: path.read_bytes() for path in (primary, backup, quarantine)
        }

        report = diagnostic.build_report(self.app_dir, source_root=self.source_root)
        artifacts = report["artifacts"]["registry"]
        by_name = {Path(item["path"]).name: item for item in artifacts}
        self.assertEqual(by_name["repos.json"]["json_status"], "invalid")
        self.assertEqual(len(by_name["repos.json.bak1"]["synthetic_records"]), 1)
        self.assertEqual(by_name["repos.json.corrupt-1"]["json_status"], "invalid")
        self.assertEqual(
            report["conclusions"][0]["status"], "indeterminate")
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_missing_and_malformed_files_are_nonfatal_and_reported(self):
        self._write_json(self.app_dir / "settings.json", ["not an object"])
        report = diagnostic.build_report(self.app_dir, source_root=self.source_root)

        self.assertFalse(report["artifacts"]["registry"][0]["exists"])
        settings = report["artifacts"]["settings"][0]
        self.assertEqual(settings["json_status"], "valid")
        self.assertEqual(settings["shape"], "valid-json-non-object")
        self.assertEqual(settings.get("synthetic_roots"), None)
        self.assertEqual(report["artifacts"]["log"]["matches"], [])

    def test_cli_json_and_text_outputs_are_read_only(self):
        registry = self.app_dir / "repos.json"
        self._write_json(registry, {"projects": []})
        before = registry.read_bytes()
        with mock.patch("builtins.print") as printed:
            self.assertEqual(diagnostic.main([
                "--app-dir", str(self.app_dir),
                "--source-root", str(self.source_root),
                "--format", "json",
            ]), 0)
            self.assertTrue(printed.call_args.args[0].startswith("{"))
        with mock.patch("builtins.print") as printed:
            self.assertEqual(diagnostic.main([
                "--app-dir", str(self.app_dir),
                "--source-root", str(self.source_root),
            ]), 0)
            self.assertIn("read-only diagnostic", printed.call_args.args[0])
        self.assertEqual(registry.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
