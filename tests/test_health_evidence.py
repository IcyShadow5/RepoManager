import tempfile
import unittest
from pathlib import Path

from repo_manager import health


class HealthEvidenceTests(unittest.TestCase):
    def test_finding_exposes_structured_evidence_and_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = health.evaluate_repository(tmp, {"dirty": 2, "broken": False})
            finding = next(item for item in result.findings if item.rule == "working_tree")
            self.assertEqual(finding.target, "repository")
            self.assertEqual(finding.freshness, health.STALE)
            self.assertEqual(finding.evidence[0].source, "Git")
            self.assertEqual(finding.evidence[0].timestamp, finding.timestamp)
            self.assertIn("Inspect", finding.remediation)

    def test_missing_git_state_is_unknown_and_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = health.evaluate_repository(tmp, {})
            finding = next(item for item in result.findings if item.rule == "working_tree")
            self.assertEqual(finding.status, health.UNKNOWN)
            self.assertEqual(finding.freshness, health.UNKNOWN_FRESHNESS)
            self.assertEqual(result.summary.unknown_count, 1)

    def test_prioritization_is_deterministic_not_numeric_scoring(self):
        findings = (
            health.Finding("a", "a", "repo", health.WARN, health.LOW, (), "a", "t"),
            health.Finding("b", "b", "repo", health.UNKNOWN, health.HIGH, (), "b", "t"),
        )
        result = health.HealthResult(health.UNKNOWN, findings, "t")
        self.assertEqual([f.finding_id for f in result.prioritized_findings()], ["b", "a"])
        self.assertFalse(hasattr(result, "attention_score"))

    def test_not_applicable_summary_preserves_domain_state(self):
        result = health.evaluate_repository(None)
        self.assertEqual(result.status, health.NOT_APPLICABLE)
        self.assertTrue(all(f.status == health.NOT_APPLICABLE for f in result.findings))
        self.assertEqual(result.summary.unknown_count, 0)

    def test_evaluation_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = sorted(root.rglob("*"))
            health.evaluate_repository(root, {"dirty": 1, "remote": "x"})
            self.assertEqual(before, sorted(root.rglob("*")))


if __name__ == "__main__":
    unittest.main()
