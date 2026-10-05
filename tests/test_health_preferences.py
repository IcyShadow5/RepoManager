"""Advisory suppression must preserve evidence, integrity and stable identity."""
import copy
from dataclasses import replace
import unittest
from unittest import mock

from repo_manager import health as h, health_preferences as prefs, health_presentation as ui, projects, store
from repo_manager.repository_service import RepositorySession
from tests.test_repository_service import IsolatedSessionTests


def finding(rule="documentation_architecture", **values):
    return h.Finding(rule, rule=rule, status=h.WARN, importance=h.INFORMATIONAL,
                     severity=h.LOW, **values)


class HealthPreferenceTests(unittest.TestCase):
    def test_ignored_is_explicit_and_preserves_observation(self):
        raw = h.HealthResult(h.UNKNOWN, (finding(freshness=h.STALE),), "now")
        settings = {prefs.SETTINGS_KEY: {"repositories": {"stable": [raw.findings[0].rule]}}}
        result = prefs.apply(raw, settings, "stable")
        item = result.findings[0]
        self.assertEqual((item.status, item.observed_status, item.suppression_scope), (h.IGNORED, h.WARN, "repository"))
        self.assertEqual(item.evidence, raw.findings[0].evidence)
        self.assertEqual(result.status, h.NOT_APPLICABLE)
        self.assertIsNone(ui.repository_health_score(result))
        self.assertEqual((result.summary.active_count, result.summary.ignored_count, result.summary.stale_count), (0, 1, 0))
        self.assertEqual(ui.health_finding_group(item), "Ignored")
        self.assertEqual(ui.health_dashboard_counts(result)["ignored"], 1)

    def test_active_score_excludes_ignored_penalty_without_adding_a_pass(self):
        good = h.Finding("git_metadata", rule="git_metadata", status=h.PASS)
        advisory = finding()
        raw = h.HealthResult(h.PASS, (good, advisory), "now")
        self.assertEqual(ui.repository_health_score(raw), 99)
        result = prefs.apply(raw, {prefs.SETTINGS_KEY: {"global": [advisory.rule]}}, "stable")
        self.assertEqual(ui.repository_health_score(result), 100)
        counts = ui.health_dashboard_counts(result)
        self.assertEqual((counts["passed"], counts["informational"], counts["ignored"]), (1, 0, 1))
        self.assertIn("1 active", ui.health_detail_summary(result)[1])

    def test_integrity_policy_and_serious_failures_cannot_be_hidden(self):
        for item in (finding("working_tree"), replace(finding(), status=h.FAIL),
                     replace(finding(), severity=h.HIGH), replace(finding(), policy_relevant=True)):
            with self.subTest(rule=item.rule, status=item.status, severity=item.severity):
                result = prefs.apply(h.HealthResult(item.status, (item,), "now"),
                                     {prefs.SETTINGS_KEY: {"global": [item.rule]}}, "stable")
                self.assertFalse(prefs.eligible(item))
                self.assertNotEqual(result.findings[0].status, h.IGNORED)

    def test_restoration_reapplies_original_status_after_background_inspection(self):
        raw = h.HealthResult(h.UNKNOWN, (replace(finding(), status=h.UNKNOWN),), "now")
        ignored = prefs.apply(raw, {prefs.SETTINGS_KEY: {"global": [raw.findings[0].rule]}}, "stable")
        restored = prefs.apply(ignored, {}, "stable")
        self.assertEqual(restored, raw)

    def test_unknown_and_malformed_preferences_are_not_suppression_authority(self):
        for value in (None, [], {"global": "working_tree", "repositories": []},
                      {"global": ["working_tree", "invented"], "repositories": {"stable": "readme_presence"}}):
            with self.subTest(value=value):
                self.assertEqual(prefs.preferences({prefs.SETTINGS_KEY: value}), {"global": [], "repositories": {}})

    def test_global_and_repository_preferences_remain_independent(self):
        item = finding()
        raw = h.HealthResult(h.PASS, (item,), "now")
        settings = {prefs.SETTINGS_KEY: {"global": [item.rule], "repositories": {"stable": [item.rule]}}}
        self.assertEqual(prefs.apply(raw, settings, "stable").findings[0].suppression_scope, "global")
        settings[prefs.SETTINGS_KEY]["global"] = []
        self.assertEqual(prefs.apply(raw, settings, "stable").findings[0].suppression_scope, "repository")
        self.assertEqual(prefs.apply(raw, settings, "other"), raw)


class HealthPreferencePersistenceTests(IsolatedSessionTests):
    def setUp(self):
        super().setUp()
        self.record, self.target = self.seed()
        store.save_projects(self.session.records)
        raw = h.HealthResult(h.PASS, (finding(),), "now")
        patch = mock.patch.object(h, "evaluate_repository", return_value=raw)
        patch.start()
        self.addCleanup(patch.stop)

    def test_restart_rename_and_restore_use_project_id(self):
        self.session.set_health_ignored(self.target, "documentation_architecture", True)
        self.record["name"] = "renamed"
        store.save_projects(self.session.records)
        restarted = RepositorySession()
        result, _ = restarted.inspect(self.target)
        self.assertEqual(result.summary.ignored_count, 1)
        self.assertIn(projects.project_id(self.record), restarted.settings[prefs.SETTINGS_KEY]["repositories"])
        restarted.set_health_ignored(self.target, "documentation_architecture", False)
        self.assertEqual(RepositorySession().inspect(self.target)[0].summary.ignored_count, 0)

    def test_write_failure_preserves_preferences_in_memory_and_on_disk(self):
        self.session.set_health_ignored(self.target, "documentation_architecture", True)
        before = copy.deepcopy(self.session.settings)
        contents = store.SETTINGS_FILE.read_bytes()
        with mock.patch.object(store, "save_settings", side_effect=OSError("locked")), self.assertRaises(OSError):
            self.session.set_health_ignored(self.target, "documentation_architecture", False)
        self.assertEqual(self.session.settings, before)
        self.assertEqual(store.SETTINGS_FILE.read_bytes(), contents)

    def test_changed_association_and_protected_rule_are_rejected(self):
        with self.assertRaises(ValueError):
            self.session.set_health_ignored(self.target, "working_tree", True)
        self.record["path"] = str(self.root)
        with self.assertRaises(ValueError):
            self.session.set_health_ignored(self.target, "documentation_architecture", True)

    def test_global_save_persists_without_overwriting_local_preference(self):
        self.session.set_health_ignored(self.target, "documentation_architecture", True)
        self.session.save_settings([str(self.root)], 3, "opencode", "", ["readme_presence"])
        value = prefs.preferences(RepositorySession().settings)
        self.assertEqual(value["global"], ["readme_presence"])
        self.assertEqual(value["repositories"][self.target.project_id], ["documentation_architecture"])
