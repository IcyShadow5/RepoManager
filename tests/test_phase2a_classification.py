import unittest

from repo_manager.projects import (
    CLASS_ARCHIVED,
    CLASS_GIT_REPOSITORY,
    CLASS_NON_GIT_LOCATION,
    CLASS_UNKNOWN,
    classification_display,
    classification_summary,
    derived_classification,
)


class DerivedClassificationTests(unittest.TestCase):
    def test_git_repository_is_derived_from_scanner_evidence(self):
        result = derived_classification({"path": "repo", "broken": False,
                                         "repository_observed": True})
        self.assertEqual(result["value"], CLASS_GIT_REPOSITORY)
        self.assertEqual(result["source"], "scanner")
        self.assertEqual(result["confidence"], "observed")

    def test_broken_repository_does_not_become_authoritative_classification(self):
        result = derived_classification({"path": "repo", "broken": True})
        self.assertEqual(result["value"], CLASS_UNKNOWN)
        self.assertEqual(result["confidence"], "unknown")

    def test_path_without_current_scanner_evidence_is_unknown(self):
        result = derived_classification({"path": "ordinary-folder",
                                         "broken": False})
        self.assertEqual(result["value"], CLASS_UNKNOWN)
        self.assertEqual(result["source"], "scanner")
        self.assertIn("no current scanner evidence", result["evidence"])

    def test_folder_without_repository_is_non_git_location(self):
        result = derived_classification({"folder_path": "folder"})
        self.assertEqual(result["value"], CLASS_NON_GIT_LOCATION)
        self.assertEqual(result["source"], "project_metadata")

    def test_archived_is_project_status_not_repository_ownership(self):
        result = derived_classification({"path": "repo", "status": "archived"})
        self.assertEqual(result["value"], CLASS_ARCHIVED)
        self.assertEqual(result["source"], "project_metadata")

    def test_unknown_is_supported_without_inference(self):
        result = derived_classification({"name": "LooksExternal"})
        self.assertEqual(result["value"], CLASS_UNKNOWN)
        self.assertEqual(result["confidence"], "unknown")

    def test_classification_is_not_persisted_or_authorizing(self):
        project = {"path": "repo", "broken": False,
                   "repository_observed": True}
        before = dict(project)
        result = derived_classification(project)
        self.assertEqual(project, before)
        self.assertNotIn("ownership", project)
        self.assertNotIn("origin", project)
        self.assertNotIn("classification", project)
        self.assertNotIn("authorize", result)

    def test_display_label_is_stable_and_readable(self):
        self.assertEqual(
            classification_display({"path": "repo", "broken": False,
                                    "repository_observed": True}),
            "Git Repository",
        )

    def test_summary_exposes_source_and_evidence_without_persisting_result(self):
        project = {"path": "repo", "broken": False,
                   "repository_observed": True}
        summary = classification_summary(project)
        self.assertIn("Git Repository", summary)
        self.assertIn("source: scanner", summary)
        self.assertIn("scanner metadata", summary)
        self.assertNotIn("classification", project)


if __name__ == "__main__":
    unittest.main()
