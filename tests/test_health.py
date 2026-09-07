import json
import tempfile
import unittest
from pathlib import Path

from repo_manager import health
from repo_manager import main


class HealthTests(unittest.TestCase):
    def test_complete_repository_passes_presence_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("README.md", "LICENSE", ".gitignore", ".gitattributes"):
                (root / name).write_text(name, encoding="utf-8")
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "ci.yml").write_text("name: test", encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs" / "README.md").write_text("docs", encoding="utf-8")
            result = health.evaluate_repository(
                root, {"branch": "main", "dirty": 0, "remote": "github.com/o/r",
                       "broken": False})
            self.assertEqual(result.status, health.PASS)
            self.assertTrue(all(f.status == health.PASS for f in result.findings
                                if f.rule.endswith("presence")))

    def test_missing_optional_files_are_informational_and_do_not_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = health.evaluate_repository(
                tmp, {"branch": "main", "dirty": 0, "remote": None,
                      "broken": False})
            self.assertEqual(result.status, health.PASS)
            readme = next(f for f in result.findings if f.rule == "readme_presence")
            self.assertEqual(readme.status, health.WARN)
            self.assertEqual(readme.importance, health.INFORMATIONAL)
            self.assertFalse(any(f.status == health.FAIL for f in result.findings))
            self.assertFalse(any(f.status == health.WARN and f.importance != health.INFORMATIONAL
                                  for f in result.findings))

    def test_material_fail_cannot_coexist_with_pass(self):
        finding = health.Finding(
            "failure", "failure", "repository", health.FAIL, health.HIGH,
            "bad state", "A material failure", "now",
            importance=health.REQUIRED)
        result = health.HealthResult(health.PASS, (finding,), "now")
        self.assertEqual(result.status, health.FAIL)

    def test_material_unknown_cannot_coexist_with_pass(self):
        finding = health.Finding(
            "unknown", "unknown", "repository", health.UNKNOWN, health.HIGH,
            "unavailable", "Evidence unavailable", "now",
            importance=health.REQUIRED)
        result = health.HealthResult(health.PASS, (finding,), "now")
        self.assertEqual(result.status, health.UNKNOWN)

    def test_disabled_rule_is_excluded_from_aggregation(self):
        finding = health.Finding(
            "disabled", "disabled", "repository", health.FAIL, health.HIGH,
            "excluded", "Disabled finding", "now",
            importance=health.DISABLED)
        result = health.HealthResult(health.FAIL, (finding,), "now")
        self.assertEqual(result.status, health.NOT_APPLICABLE)

    def test_warning_material_evidence_cannot_coexist_with_pass(self):
        finding = health.Finding(
            "upstream", "upstream", "repository", health.WARN, health.MEDIUM,
            "No upstream branch configured", "Action needed", "now",
            importance=health.REQUIRED)
        result = health.HealthResult(health.PASS, (finding,), "now")
        self.assertEqual(result.status, health.WARN)

    def test_unknown_inaccessible_repository(self):
        result = health.evaluate_repository("/definitely/not/a/repository", {})
        self.assertEqual(result.status, health.UNKNOWN)
        self.assertEqual(result.findings[0].status, health.UNKNOWN)

    def test_broken_git_metadata_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = health.evaluate_repository(tmp, {"broken": True})
            finding = next(f for f in result.findings if f.rule == "git_metadata")
            self.assertEqual(finding.status, health.UNKNOWN)
            self.assertEqual(result.status, health.UNKNOWN)

    def test_dirty_tree_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = health.evaluate_repository(tmp, {"dirty": 2, "broken": False})
            finding = next(f for f in result.findings if f.rule == "working_tree")
            self.assertEqual(finding.status, health.WARN)
            self.assertEqual(finding.severity, health.LOW)

    def test_explicit_status_unavailable_is_unknown_not_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = health.evaluate_repository(
                tmp, {"dirty": None, "status_available": False,
                      "broken": False})
        finding = next(
            f for f in result.findings if f.rule == "working_tree")
        self.assertEqual(finding.status, health.UNKNOWN)
        self.assertEqual(result.status, health.UNKNOWN)

    def test_findings_have_required_evidence_fields_and_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = health.evaluate_repository(tmp, {})
            self.assertTrue(result.evaluated_at)
            for finding in result.findings:
                self.assertTrue(finding.rule)
                self.assertTrue(finding.status)
                self.assertTrue(finding.severity)
                self.assertTrue(finding.evidence)
                self.assertTrue(finding.explanation)
                self.assertEqual(finding.timestamp, result.evaluated_at)

    def test_evaluation_does_not_mutate_files_or_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "marker.txt"
            marker.write_text("before", encoding="utf-8")
            metadata = {"dirty": 0, "broken": False, "remote": None}
            before_metadata = json.dumps(metadata, sort_keys=True)
            before_files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
            health.evaluate_repository(root, metadata)
            after_files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
            self.assertEqual(marker.read_text(encoding="utf-8"), "before")
            self.assertEqual(before_files, after_files)
            self.assertEqual(json.dumps(metadata, sort_keys=True), before_metadata)

    def test_snapshot_findings_disclose_metadata_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = health.evaluate_repository(
                tmp, {"branch": "main", "dirty": 2, "remote": "origin"})
            for rule in ("git_metadata", "working_tree", "remote_presence"):
                finding = next(f for f in result.findings if f.rule == rule)
                self.assertIn("metadata snapshot", finding.explanation)
                self.assertIn("stale", finding.explanation)

    def test_legacy_six_argument_constructor_maps_all_fields(self):
        # Legacy callers passed rule, status, severity, evidence, explanation,
        # timestamp positionally; the post-init shim must recover every field
        # exactly, not only `status`.
        finding = health.Finding("example", health.WARN, health.MEDIUM,
                                 "some evidence", "A clear explanation.",
                                 "2026-01-01T00:00:00Z")
        self.assertEqual(finding.finding_id, "example")
        self.assertEqual(finding.rule, "example")
        self.assertEqual(finding.status, health.WARN)
        self.assertEqual(finding.severity, health.MEDIUM)
        self.assertEqual(finding.evidence[0].observation, "some evidence")
        self.assertEqual(finding.evidence[0].timestamp, "2026-01-01T00:00:00Z")
        self.assertEqual(finding.explanation, "A clear explanation.")
        self.assertEqual(finding.timestamp, "2026-01-01T00:00:00Z")

    def test_all_not_applicable_is_not_reported_as_pass(self):
        result = health.HealthResult(
            health.NOT_APPLICABLE,
            (health.Finding("x", "x", "repository", health.NOT_APPLICABLE,
                            health.INFO, "not applicable", "Rule does not apply.", "now"),),
            "now")
        self.assertEqual(result.status, health.NOT_APPLICABLE)

    def test_not_applicable_is_supported_as_domain_state(self):
        finding = health.Finding("example", health.NOT_APPLICABLE, health.INFO,
                                "not applicable", "Rule does not apply.", "now")
        self.assertEqual(finding.status, health.NOT_APPLICABLE)

    def test_modern_constructor_is_unchanged(self):
        evidence = health.Evidence("git", ".", "dirty", "2026-01-01T00:00:00Z")
        finding = health.Finding("id-1", "some_rule", "repository",
                                 health.WARN, health.HIGH, (evidence,),
                                 "explanation", "ts")
        self.assertEqual(finding.finding_id, "id-1")
        self.assertEqual(finding.rule, "some_rule")
        self.assertEqual(finding.target, "repository")
        self.assertEqual(finding.status, health.WARN)
        self.assertEqual(finding.severity, health.HIGH)
        self.assertEqual(finding.evidence, (evidence,))
        self.assertEqual(finding.explanation, "explanation")
        self.assertEqual(finding.timestamp, "ts")
        self.assertEqual(finding.freshness, health.CURRENT)


class HealthDetailsPresentationTests(unittest.TestCase):
    """presentation helpers for the structured Health Details surface."""

    @staticmethod
    def _finding(rule, status, importance, severity=health.LOW,
                 freshness=health.CURRENT):
        evidence = (health.Evidence("filesystem", rule, "evidence", "now",
                                    freshness),)
        return health.Finding(rule, rule, rule, status, severity, evidence,
                              "Explanation for " + rule, "now", freshness,
                              remediation="Fix " + rule,
                              importance=importance)

    def test_group_classification_covers_all_statuses_and_importances(self):
        cases = (
            (health.FAIL, health.REQUIRED, "Action Needed"),
            (health.UNKNOWN, health.REQUIRED, "Action Needed"),
            (health.WARN, health.REQUIRED, "Needs Attention"),
            (health.WARN, health.RECOMMENDED, "Needs Attention"),
            (health.WARN, health.INFORMATIONAL, "Informational"),
            (health.UNKNOWN, health.INFORMATIONAL, "Informational"),
            (health.PASS, health.REQUIRED, "Passed Checks"),
            (health.NOT_APPLICABLE, health.REQUIRED, "Not Applicable"),
            (health.FAIL, health.DISABLED, "Disabled"),
        )
        for status, importance, expected in cases:
            with self.subTest(status=status, importance=importance):
                finding = self._finding("rule", status, importance)
                self.assertEqual(
                    main.health_finding_group(finding), expected)

    def test_required_warn_is_never_omitted(self):
        finding = self._finding("working_tree", health.WARN,
                                health.REQUIRED)
        group = main.health_finding_group(finding)
        self.assertEqual(group, "Needs Attention")
        self.assertEqual(finding.status, health.WARN)
        self.assertEqual(finding.importance, health.REQUIRED)

    def test_every_finding_maps_to_exactly_one_group(self):
        statuses = (health.PASS, health.WARN, health.FAIL, health.UNKNOWN,
                    health.NOT_APPLICABLE)
        importances = (health.REQUIRED, health.RECOMMENDED,
                       health.INFORMATIONAL, health.DISABLED)
        findings = [self._finding(f"rule-{s}-{i}", s, i)
                    for s in statuses for i in importances]
        for finding in findings:
            self.assertIn(main.health_finding_group(finding),
                          main.HEALTH_GROUP_ORDER)

    def test_display_name_is_presentation_only(self):
        finding = self._finding("working_tree", health.WARN,
                                health.REQUIRED)
        original = finding.rule
        self.assertEqual(main.health_rule_display_name(finding.rule),
                         "Working tree")
        self.assertEqual(main.health_rule_display_name("remote_presence"),
                         "Remote presence")
        self.assertEqual(main.health_rule_display_name("documentation_presence"),
                         "Documentation presence")
        self.assertEqual(finding.rule, original)

    def test_detail_summary_counts_required_warn_as_warning(self):
        findings = (self._finding("working_tree", health.WARN,
                                  health.REQUIRED),
                    self._finding("readme_presence", health.WARN,
                                  health.INFORMATIONAL),
                    self._finding("git_metadata", health.PASS,
                                  health.REQUIRED, freshness=health.STALE))
        result = health.HealthResult(health.WARN, findings, "2026-01-01T00:00:00Z")
        headline, counts, evaluated = main.health_detail_summary(result)
        self.assertEqual(headline, main.health_headline(health.WARN))
        self.assertIn("3 checks", counts)
        self.assertIn("1 warning", counts)
        self.assertIn("1 stale", counts)
        self.assertIn("Evaluated 2026-01-01T00:00:00Z", evaluated)


class HealthScorePresentationTests(unittest.TestCase):
    """presentation-only 0-100 score, stale cap and counters."""

    @staticmethod
    def _finding(rule, status, importance, freshness=health.CURRENT):
        evidence = (health.Evidence("filesystem", rule, "evidence", "now",
                                    freshness),)
        return health.Finding(rule, rule, "repository", status, health.LOW,
                              evidence, "Explanation for " + rule, "now",
                              freshness, remediation="Fix " + rule,
                              importance=importance)

    def test_clean_pass_scores_in_band_preferably_near_100(self):
        result = health.HealthResult(health.PASS, (
            self._finding("git_metadata", health.PASS, health.REQUIRED),
            self._finding("working_tree", health.PASS, health.REQUIRED),
            self._finding("ci_presence", health.PASS,
                          health.RECOMMENDED),
        ), "ts")
        score = main.repository_health_score(result)
        self.assertIsNotNone(score)
        self.assertEqual(score, 100)
        self.assertTrue(80 <= score <= 100)

    def test_required_warn_decreases_score_and_stays_in_warn_band(self):
        clean = health.HealthResult(health.PASS, (
            self._finding("pass_rule", health.PASS, health.REQUIRED),), "ts")
        warned = health.HealthResult(health.WARN, (
            self._finding("working_tree", health.WARN, health.REQUIRED),
            self._finding("pass_rule", health.PASS, health.REQUIRED),
        ), "ts")
        clean_score = main.repository_health_score(clean)
        warn_score = main.repository_health_score(warned)
        self.assertLess(warn_score, clean_score)
        self.assertTrue(60 <= warn_score <= 79)

    def test_required_fail_is_at_most_39(self):
        result = health.HealthResult(health.FAIL, (
            self._finding("repo_access", health.FAIL, health.REQUIRED),
            self._finding("pass_rule", health.PASS, health.REQUIRED),
        ), "ts")
        score = main.repository_health_score(result)
        self.assertIsNotNone(score)
        self.assertLessEqual(score, 39)

    def test_unknown_status_stays_in_40_to_59_band(self):
        result = health.HealthResult(health.UNKNOWN, (
            self._finding("git_metadata", health.UNKNOWN, health.REQUIRED),
            self._finding("pass_rule", health.PASS, health.REQUIRED),
        ), "ts")
        score = main.repository_health_score(result)
        self.assertIsNotNone(score)
        self.assertTrue(40 <= score <= 59)

    def test_informational_warn_only_slightly_reduces_pass_score(self):
        result = health.HealthResult(health.PASS, (
            self._finding("pass_rule", health.PASS, health.REQUIRED),
            self._finding("readme_presence", health.WARN,
                          health.INFORMATIONAL),
        ), "ts")
        score = main.repository_health_score(result)
        self.assertIsNotNone(score)
        self.assertEqual(score, 99)
        self.assertTrue(80 <= score <= 100)

    def test_unknown_future_importance_falls_back_to_required(self):
        # Documented fallback: an unknown future importance is penalized
        # conservatively like REQUIRED through its status.
        for status, expected in ((health.WARN, 10), (health.UNKNOWN, 18),
                                 (health.FAIL, 30)):
            finding = self._finding("future_rule", status, "FUTURE_IMPORTANCE")
            with self.subTest(status=status):
                self.assertEqual(main.health_finding_penalty(finding),
                                 expected)
        # PASS/NA and DISABLED remain zero under the fallback.
        self.assertEqual(
            main.health_finding_penalty(
                self._finding("pass_rule", health.PASS, "FUTURE_IMPORTANCE")),
            0)
        self.assertEqual(
            main.health_finding_penalty(
                self._finding("na_rule", health.NOT_APPLICABLE,
                              "FUTURE_IMPORTANCE")),
            0)
        self.assertEqual(
            main.health_finding_penalty(
                self._finding("disabled_rule", health.FAIL, health.DISABLED)),
            0)

    def test_known_importance_penalties_are_unchanged(self):
        cases = ((health.REQUIRED, health.WARN, 10),
                 (health.REQUIRED, health.UNKNOWN, 18),
                 (health.REQUIRED, health.FAIL, 30),
                 (health.RECOMMENDED, health.WARN, 7),
                 (health.RECOMMENDED, health.UNKNOWN, 12),
                 (health.RECOMMENDED, health.FAIL, 18),
                 (health.INFORMATIONAL, health.WARN, 1),
                 (health.INFORMATIONAL, health.UNKNOWN, 2),
                 (health.INFORMATIONAL, health.FAIL, 4))
        for importance, status, expected in cases:
            finding = self._finding("rule", status, importance)
            with self.subTest(importance=importance, status=status):
                self.assertEqual(main.health_finding_penalty(finding),
                                 expected)

    def test_stale_penalty_applies_and_is_capped_at_five(self):
        findings = tuple(
            self._finding(f"pass_{i}", health.PASS, health.REQUIRED,
                          freshness=health.STALE) for i in range(8))
        self.assertEqual(main.health_stale_penalty(findings), 5)
        informational = tuple(
            self._finding(f"info_{i}", health.PASS, health.INFORMATIONAL,
                          freshness=health.STALE) for i in range(4))
        self.assertEqual(main.health_stale_penalty(informational), 0)

    def test_stale_evidence_lowers_score_without_dominating(self):
        current = health.HealthResult(health.UNKNOWN, tuple(
            self._finding(f"unknown_{i}", health.UNKNOWN, health.REQUIRED)
            for i in range(3)), "ts")
        stale = health.HealthResult(health.UNKNOWN, tuple(
            self._finding(f"unknown_{i}", health.UNKNOWN, health.REQUIRED,
                          freshness=health.STALE) for i in range(3)), "ts")
        self.assertEqual(main.repository_health_score(current), 46)
        self.assertEqual(main.repository_health_score(stale), 43)

    def test_not_enough_applicable_evidence_returns_none(self):
        only_na = health.HealthResult(health.NOT_APPLICABLE, (
            self._finding("windows_only", health.NOT_APPLICABLE,
                          health.REQUIRED),
        ), "ts")
        disabled = health.HealthResult(health.NOT_APPLICABLE, (
            self._finding("disabled_check", health.FAIL, health.DISABLED),
        ), "ts")
        empty = health.HealthResult(health.PASS, (), "ts")
        for result in (only_na, disabled, empty):
            with self.subTest(status=result.status):
                self.assertIsNone(main.repository_health_score(result))
                self.assertEqual(
                    main.repository_health_band_label(result),
                    "Not enough applicable evidence")

    def test_score_is_deterministic(self):
        findings = (
            self._finding("repo_access", health.FAIL, health.REQUIRED),
            self._finding("working_tree", health.WARN, health.REQUIRED,
                          freshness=health.STALE),
            self._finding("readme_presence", health.WARN,
                          health.INFORMATIONAL),
        )
        first = health.HealthResult(health.FAIL, findings, "2026-01-01")
        second = health.HealthResult(health.FAIL, findings, "2026-02-02")
        self.assertEqual(main.repository_health_score(first),
                         main.repository_health_score(second))
        self.assertEqual(main.repository_health_score(first),
                         main.repository_health_score(first))

    def test_score_language_is_restrained(self):
        mapping = (
            (health.PASS, "Healthy"),
            (health.WARN, "Needs attention"),
            (health.UNKNOWN, "Evidence incomplete"),
            (health.FAIL, "Problems found"),
        )
        for status, expected in mapping:
            result = health.HealthResult(status, (
                self._finding("rule", status, health.REQUIRED),
            ), "ts")
            with self.subTest(status=status):
                self.assertEqual(
                    main.repository_health_band_label(result), expected)
        banned = ("Secure", "Safe", "Perfect", "Malware-free",
                  "Threat-free")
        for label in main._HEALTH_SCORE_LABELS.values():
            for word in banned:
                self.assertNotIn(word, label)

    def test_dashboard_counts_follow_documented_rule(self):
        result = health.HealthResult(health.FAIL, (
            self._finding("git_metadata", health.PASS, health.REQUIRED),
            self._finding("working_tree", health.PASS, health.REQUIRED,
                          freshness=health.STALE),
            self._finding("upstream", health.WARN, health.RECOMMENDED),
            self._finding("repo_access", health.FAIL, health.REQUIRED),
            self._finding("remote_state", health.UNKNOWN,
                          health.REQUIRED),
            self._finding("readme_presence", health.WARN,
                          health.INFORMATIONAL),
            self._finding("docs_presence", health.PASS,
                          health.INFORMATIONAL),
            self._finding("platform_rule", health.NOT_APPLICABLE,
                          health.REQUIRED),
            self._finding("old_check", health.FAIL, health.DISABLED),
        ), "ts")
        counts = main.health_dashboard_counts(result)
        self.assertEqual(counts["passed"], 2)  # NA + informational excluded
        self.assertEqual(counts["warnings"], 1)
        self.assertEqual(counts["problems"], 1)
        self.assertEqual(counts["unknown"], 1)
        self.assertEqual(counts["stale"], 1)   # disabled excluded
        self.assertEqual(counts["informational"], 2)


if __name__ == "__main__":
    unittest.main()
