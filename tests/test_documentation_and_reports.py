import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import intelligence, reports


class DocumentationAndReportsTests(unittest.TestCase):
    def test_documentation_reports_presence_without_claiming_correctness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("readme", encoding="utf-8")
            (root / "docs").mkdir()
            items = intelligence.inspect_documentation(root)
            readme = next(item for item in items if item.key == "README")
            security = next(item for item in items if item.key == "SECURITY")
            self.assertEqual(readme.status, intelligence.PRESENT)
            self.assertEqual(security.status, intelligence.MISSING)
            self.assertEqual(readme.freshness, "CURRENT")

    def test_stack_detection_uses_known_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for filename in ("pyproject.toml", "package.json", "pnpm-lock.yaml", "tsconfig.json", "next.config.ts"):
                (root / filename).write_text("", encoding="utf-8")
            result = intelligence.inspect_stack(root)
            self.assertIn("Python", result.languages)
            self.assertIn("JavaScript/TypeScript", result.languages)
            self.assertIn("TypeScript", result.languages)
            self.assertIn("Next.js", result.frameworks)
            self.assertIn("pnpm", result.package_managers)
            self.assertEqual(len(result.evidence), 5)

    def test_export_excludes_secret_fields_and_preserves_identity(self):
        artifact = reports.project_export({"project_id": "p1", "name": "Demo", "path": "C:/demo", "provider_token": "do-not-export", "nested": {"api_key": "secret"}})
        self.assertEqual(artifact["schema_version"], reports.SCHEMA_VERSION)
        self.assertEqual(artifact["project"]["project_id"], "p1")
        self.assertNotIn("provider_token", artifact["project"])
        self.assertNotIn("api_key", artifact["project"]["nested"])

    def test_repository_report_is_deterministic_except_generation_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("x", encoding="utf-8")
            project = {"project_id": "p1", "name": "Demo", "path": str(root), "broken": False}
            first = reports.repository_report(project, metadata=project)
            second = reports.repository_report(project, metadata=project)
            first["generated_at"] = second["generated_at"] = "fixed"
            self.assertEqual(reports.to_json(first), reports.to_json(second))
            self.assertEqual(json.loads(reports.to_json(first))["project"]["project_id"], "p1")
            self.assertIn("# RepoManager Repository Report", reports.to_markdown(first))

    def test_markdown_export_keeps_untrusted_values_on_their_lines(self):
        report = {
            "artifact": "Report\n# injected",
            "schema_version": 1,
            "generated_at": "now",
            "provenance": {"source": "local\n[link](javascript:bad)"},
            "project": {"name": "`name`\n## injected", "path": "C:/x|bad"},
            "stack": {"languages": ["Python\n# heading"]},
            "health": {"status": "PASS", "findings": [{
                "status": "WARN", "rule": "rule|cell",
                "explanation": "text\n- injected <script>",
            }]},
        }

        rendered = reports.to_markdown(report)

        self.assertNotIn("\n# injected", rendered)
        self.assertNotIn("\n## injected", rendered)
        self.assertNotIn("\n- injected", rendered)
        self.assertNotIn("[link](javascript:bad)", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("`` `name` ## injected ``", rendered)
        self.assertIn(r"rule\|cell", rendered)

    def test_export_handles_cycles_and_atomic_writer_writes_complete_text(self):
        cyclic = {}
        cyclic["self"] = cyclic
        self.assertEqual(json.loads(reports.to_json(cyclic))["self"], "[cycle]")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "report.md"
            reports.write_text_atomic(target, "complete\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "complete\n")

    def test_filesystem_observation_error_is_not_reported_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            real_is_file = Path.is_file

            def unavailable(path):
                if path.name == "README.md":
                    raise PermissionError("denied")
                return real_is_file(path)

            with mock.patch.object(Path, "is_file", unavailable):
                items = intelligence.inspect_documentation(tmp)
            readme = next(item for item in items if item.key == "README")
            self.assertEqual(readme.status, intelligence.UNKNOWN)
            self.assertEqual(readme.freshness, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
