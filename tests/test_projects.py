import unittest
from unittest import mock

from repo_manager.projects import (apply_curation, associate_repository,
                                   association_candidates, ensure_project_id,
                                   build_project_export,
                                   build_repository_report,
                                   display_worktree_records,
                                   is_visible, project_display_name, project_id,
                                   repository_default_name,
                                   sorted_projects, validate_association_target,
                                   working_on_now_rows,
                                   project_name_suggestion,
                                   project_secondary_identity)


class ProjectIdentityTests(unittest.TestCase):
    def test_id_is_created_once_and_is_path_independent(self):
        project = {"path": r"C:\old\Alpha", "name": "Alpha"}
        first = ensure_project_id(project)
        project["path"] = r"D:\new\Alpha"
        self.assertEqual(ensure_project_id(project), first)
        self.assertEqual(project_id(project), first)

    def test_existing_id_is_preserved_for_legacy_compatible_records(self):
        project = {"path": "repo", "name": "Repo", "project_id": "p-1"}
        self.assertEqual(ensure_project_id(project), "p-1")
        self.assertEqual(project, {"path": "repo", "name": "Repo",
                                   "project_id": "p-1"})

    def test_missing_id_is_reported_without_mutating_when_only_reading(self):
        self.assertIsNone(project_id({"path": "repo", "name": "Repo"}))

    def test_repository_layout_uses_parent_as_default_name(self):
        path = r"C:\Projects\PocketLedger\repository"
        self.assertEqual(repository_default_name(path), "PocketLedger")

    def test_legacy_scanner_name_gets_derived_display_without_mutation(self):
        project = {
            "path": r"C:\Projects\PocketLedger\repository",
            "name": "repository",
            "project_id": "p-1",
        }
        before = dict(project)

        self.assertEqual(project_display_name(project), "PocketLedger")
        self.assertEqual(project, before)

    def test_curated_name_wins_over_repository_layout(self):
        project = {
            "path": r"C:\Projects\PocketLedger\repository",
            "name": "Ledger App",
        }
        self.assertEqual(project_display_name(project), "Ledger App")

    def test_other_repository_folder_names_keep_existing_behavior(self):
        path = r"C:\Projects\PocketLedger\source"
        self.assertEqual(repository_default_name(path), "source")

    def test_normal_stored_name_casing_is_preserved(self):
        project = {"path": r"D:\new", "name": "New"}
        self.assertEqual(project_display_name(project), "New")

    def test_name_suggestion_is_separate_and_has_provenance(self):
        project = {"path": r"C:\OmniRouter", "name": "repo000123",
                   "remote": "github.com/example/OmniRouter.git"}
        suggestion = project_name_suggestion(project)
        self.assertEqual(suggestion, {"name": "OmniRouter", "source": "remote"})
        self.assertEqual(project["name"], "repo000123")

    def test_manifest_suggestion_can_replace_legacy_generated_name(self):
        project = {"path": r"C:\repo000123", "name": "repo000123",
                   "manifest_name": "omni-router"}
        suggestion = project_name_suggestion(project)
        self.assertEqual(suggestion, {"name": "omni-router", "source": "manifest"})
        self.assertEqual(project["name"], "repo000123")

    def test_custom_name_remains_untouched_even_if_evidence_differs(self):
        project = {"path": r"C:\repository", "name": "My Custom Name",
                   "remote": "github.com/example/OmniRouter.git"}
        before = dict(project)
        suggestion = project_name_suggestion(project)
        self.assertEqual(suggestion["name"], "OmniRouter")
        self.assertEqual(suggestion["source"], "remote")
        self.assertEqual(project, before)

    def test_equal_suggestion_is_not_repeated_case_insensitively(self):
        project = {"path": r"C:\OmniRouter", "name": "omnirouter",
                   "remote": "github.com/example/OmniRouter.git"}
        self.assertIsNone(project_name_suggestion(project))

    def test_evidence_changes_do_not_mutate_stored_name(self):
        project = {"path": r"C:\repo000123", "name": "repo000123",
                   "remote": "github.com/example/First.git"}
        first = project_name_suggestion(project)
        project["remote"] = "github.com/example/Second.git"
        second = project_name_suggestion(project)
        self.assertEqual(first["name"], "First")
        self.assertEqual(second["name"], "Second")
        self.assertEqual(project["name"], "repo000123")

    def test_curated_name_has_no_automatic_rename(self):
        project = {"path": r"C:\repo000123", "name": "My curated name",
                   "remote": "github.com/example/OmniRouter"}
        before = dict(project)
        suggestion = project_name_suggestion(project)
        self.assertEqual(suggestion, {"name": "OmniRouter", "source": "remote"})
        self.assertEqual(project, before)

    def test_duplicate_names_have_secondary_identity(self):
        self.assertEqual(
            project_secondary_identity({"name": "backend", "path": r"C:\one\backend"}),
            r"C:\one\backend")

    def test_move_keeps_project_id_and_stored_name(self):
        project = {"path": r"C:\old\repo000123", "name": "repo000123",
                   "project_id": "stable-id",
                   "remote": "github.com/example/OmniRouter.git"}
        before = project.copy()
        project["path"] = r"D:\new\OmniRouter"
        self.assertEqual(project["project_id"], before["project_id"])
        self.assertEqual(project["name"], before["name"])
        self.assertEqual(
            project_name_suggestion(project),
            {"name": "OmniRouter", "source": "remote"})

    def test_repository_report_uses_display_name_without_mutating_project(self):
        project = {
            "path": r"C:\Projects\PocketLedger\repository",
            "name": "repository",
        }
        before = dict(project)
        with mock.patch("repo_manager.projects.reports.repository_report",
                        side_effect=lambda value: value):
            report_project = build_repository_report(project)

        self.assertEqual(report_project["name"], "PocketLedger")
        self.assertEqual(project, before)

    def test_project_export_uses_display_name_without_mutating_project(self):
        project = {
            "path": r"C:\Projects\PocketLedger\repository",
            "name": "repository",
            "project_id": "stable-id",
        }
        before = dict(project)

        artifact = build_project_export(project)

        self.assertEqual(artifact["project"]["name"], "PocketLedger")
        self.assertEqual(artifact["project"]["project_id"], "stable-id")
        self.assertEqual(project, before)


class ProjectAssociationTests(unittest.TestCase):
    def setUp(self):
        self.current = {"name": "Project", "path": "C:/project",
                         "project_id": "project-1", "status": "active",
                         "focus": "keep this", "pinned": True}
        self.other = {"name": "Other", "path": "D:/other",
                      "project_id": "project-2", "broken": False}

    def test_candidates_exclude_current_and_broken_records(self):
        broken = {"name": "Broken", "path": "E:/broken", "project_id": "p-3",
                  "broken": True}
        self.assertEqual(association_candidates([self.current, self.other, broken], self.current), [self.other])

    def test_candidates_exclude_ignored_repositories(self):
        ignored = dict(self.other, ignored=True)
        self.assertEqual(
            association_candidates([self.current, ignored], self.current), [])

    def test_valid_association_preserves_project_identity_and_metadata(self):
        associate_repository(self.current, self.other)
        self.assertEqual(self.current["project_id"], "project-1")
        self.assertEqual(self.current["path"], "D:/other")
        self.assertEqual(self.current["status"], "active")
        self.assertEqual(self.current["focus"], "keep this")
        self.assertTrue(self.current["pinned"])

    def test_invalid_association_is_blocked(self):
        valid, reason = validate_association_target([self.current], self.current, self.other)
        self.assertFalse(valid)
        self.assertTrue(reason)

    def test_broken_association_is_blocked(self):
        broken = {"name": "Broken", "path": "E:/broken", "project_id": "p-3", "broken": True}
        valid, reason = validate_association_target([self.current, broken], self.current, broken)
        self.assertFalse(valid)
        self.assertTrue(reason)

    def test_association_preserves_curated_name_and_clears_observations(self):
        self.current.update({"dirty": 3, "branch": "old", "remote": "old/repo"})
        associate_repository(self.current, self.other)
        self.assertEqual(self.current["name"], "Project")
        self.assertEqual(self.current["path"], "D:/other")
        self.assertNotIn("dirty", self.current)
        self.assertNotIn("branch", self.current)
        self.assertNotIn("remote", self.current)


class ProjectVisibilityTests(unittest.TestCase):
    def test_empty_filter_matches_every_project(self):
        self.assertTrue(is_visible({"name": "Alpha"}, ""))

    def test_filter_matches_name_path_and_focus_case_insensitively(self):
        project = {
            "name": "Alpha",
            "path": r"C:\repos\alpha",
            "focus": "Refactor district system",
        }
        self.assertTrue(is_visible(project, "ALPHA"))
        self.assertTrue(is_visible(project, r"c:\repos"))
        self.assertTrue(is_visible(project, "DISTRICT"))
        self.assertFalse(is_visible(project, "beta"))

    def test_filter_matches_derived_legacy_display_name(self):
        project = {
            "name": "repository",
            "path": r"C:\Projects\PocketLedger\repository",
        }
        self.assertTrue(is_visible(project, "pocketledger"))


class ProjectOrderingTests(unittest.TestCase):
    def test_default_order_is_pinned_then_active_then_name(self):
        projects = [
            {"name": "zeta", "status": "idea", "pinned": False},
            {"name": "beta", "status": "active", "pinned": False},
            {"name": "alpha", "status": "idea", "pinned": True},
        ]
        self.assertEqual(
            [p["name"] for p in sorted_projects(projects)],
            ["alpha", "beta", "zeta"],
        )

    def test_name_sort_is_case_insensitive_in_both_directions(self):
        projects = [{"name": "B"}, {"name": "a"}, {"name": "C"}]
        self.assertEqual(
            [p["name"] for p in sorted_projects(projects, "name")],
            ["a", "B", "C"],
        )
        self.assertEqual(
            [p["name"] for p in sorted_projects(projects, "name", True)],
            ["C", "B", "a"],
        )

    def test_sync_sort_uses_ahead_plus_behind(self):
        projects = [{"name": "A", "ahead": 1},
                    {"name": "B", "behind": 3}]
        self.assertEqual(
            sorted_projects(projects, "sync")[0]["name"], "A")
        self.assertEqual(
            sorted_projects(projects, "sync", True)[0]["name"], "B")

    def test_name_sort_uses_derived_legacy_display_name(self):
        projects = [
            {"name": "repository",
             "path": r"C:\Projects\Zulu\repository"},
            {"name": "repository",
             "path": r"C:\Projects\Alpha\repository"},
        ]
        ordered = sorted_projects(projects, "name")
        self.assertEqual(
            [project_display_name(project) for project in ordered],
            ["Alpha", "Zulu"],
        )

    def test_every_visible_column_sorts_by_its_displayed_value(self):
        project_a = {
            "project_id": "a", "name": "Zulu", "path": r"C:\b",
            "status": "idea", "pinned": False, "branch": "Zulu",
            "dirty": 2, "ahead": 2, "behind": 1,
            "worktrees": [
                {"path": r"C:\b", "current": True, "branch": "main"},
                {"path": r"C:\b-linked", "current": False,
                 "branch": "topic"},
            ], "last_commit_date": "2026-02-01",
        }
        project_b = {
            "project_id": "b", "name": "alpha", "folder_path": r"C:\a",
            "status": "active", "pinned": True, "branch": "alpha",
            "dirty": 1, "ahead": 0, "behind": 1,
            "worktrees": [
                {"path": r"C:\a", "current": True, "branch": "main"},
            ], "last_commit_date": "2026-01-01",
        }
        expected_first = {
            "name": "b", "classification": "a", "status": "a",
            "branch": "b", "dirty": "b", "sync": "b",
            "worktrees": "b", "last_commit": "b", "path": "b",
        }
        for column, project_id in expected_first.items():
            with self.subTest(column=column):
                ordered = sorted_projects([project_a, project_b], column)
                self.assertEqual(ordered[0]["project_id"], project_id)

    def test_worktree_projection_rejects_malformed_collections(self):
        for value in (None, 7, "invalid-entry", {"path": "invalid"}, ()):
            with self.subTest(value=value):
                self.assertEqual(display_worktree_records(value), [])

    def test_worktree_projection_preserves_only_scanner_compatible_records(self):
        normal = {"path": r"C:\main", "current": True, "branch": "main",
                  "head": "abc", "locked": False, "prunable": False}
        detached = {"path": r"C:\detached", "current": False,
                    "branch": None, "head": "def"}
        malformed = ["invalid-entry", None, 7, [], {},
                     {"path": "", "current": False, "branch": "topic"},
                     {"path": r"C:\missing-current", "branch": "topic"},
                     {"path": r"C:\bad-current", "current": 1,
                      "branch": "topic"},
                     {"path": r"C:\bad-branch", "current": False,
                      "branch": 7}]
        value = [normal, *malformed, detached]
        before = list(value)

        self.assertEqual(display_worktree_records(value), [normal, detached])
        self.assertEqual(value, before)

    def test_worktree_sort_uses_projected_count_without_mutation(self):
        one = {"project_id": "one", "name": "One", "worktrees": [
            {"path": r"C:\one", "current": True, "branch": "main"},
        ]}
        mixed = {"project_id": "mixed", "name": "Mixed", "worktrees": [
            {"path": r"C:\mixed", "current": True, "branch": None},
            "invalid-entry",
        ]}
        two = {"project_id": "two", "name": "Two", "worktrees": [
            {"path": r"C:\two", "current": True, "branch": "main"},
            {"path": r"C:\two-linked", "current": False,
             "branch": "topic"},
        ]}
        malformed = [
            {"project_id": "int", "name": "Integer", "worktrees": 7},
            {"project_id": "string", "name": "String",
             "worktrees": "invalid-entry"},
        ]
        records = [two, one, *malformed, mixed]
        before = [dict(record) for record in records]

        self.assertEqual(len(display_worktree_records(mixed["worktrees"])), 1)
        self.assertEqual(
            [record["project_id"]
             for record in sorted_projects(records, "worktrees")],
            ["int", "string", "mixed", "one", "two"],
        )
        self.assertEqual(
            [record["project_id"]
             for record in sorted_projects(records, "worktrees", True)],
            ["two", "one", "mixed", "string", "int"],
        )
        self.assertEqual(records, before)

    def test_duplicate_names_use_visible_path_then_stable_id(self):
        records = [
            {"project_id": "z", "name": "Same", "path": r"C:\Beta"},
            {"project_id": "a", "name": "same", "path": r"C:\Alpha"},
            {"project_id": "b", "name": "SAME", "path": r"C:\Alpha"},
        ]
        ordered = sorted_projects(records, "name")
        self.assertEqual([item["project_id"] for item in ordered],
                         ["a", "b", "z"])

    def test_unicode_casefold_is_used_for_filter_and_sort(self):
        records = [
            {"project_id": "2", "name": "Straße", "path": "b"},
            {"project_id": "1", "name": "STRASSE", "path": "a"},
        ]
        self.assertTrue(is_visible(records[0], "STRASSE"))
        ordered = sorted_projects(records, "name")
        self.assertEqual([item["project_id"] for item in ordered], ["1", "2"])


class WorkingOnNowTests(unittest.TestCase):
    def test_ignored_active_project_is_not_in_working_on_now_projection(self):
        ignored = {"name": "Ignored", "path": "ignored",
                   "status": "active", "ignored": True}

        self.assertEqual(
            working_on_now_rows([ignored], lambda _path: True), [])

    def test_only_explicitly_active_projects_are_kept_including_folder_only(self):
        projects = [
            {"name": "active", "path": "active", "status": "active"},
            {"name": "pinned", "path": "pinned", "pinned": True},
            {"name": "folder", "folder_path": "folder", "status": "active"},
            {"name": "idea", "path": "idea", "status": "idea"},
            {"name": "archived", "path": "archived", "status": "archived",
             "pinned": True},
            {"name": "gone", "path": "gone", "status": "active"},
        ]
        self.assertEqual(
            [p["name"] for p in working_on_now_rows(
                projects, lambda path: path not in {"gone", "archived"})],
            ["active", "folder"],
        )

    def test_rows_are_newest_first_and_capped(self):
        projects = [
            {"name": f"p{i}", "path": str(i), "status": "active",
             "last_commit_date": f"2026-08-{i + 1:02d}"}
            for i in range(25)
        ]
        rows = working_on_now_rows(projects, lambda _path: True, limit=20)
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0]["name"], "p24")
        self.assertEqual(rows[-1]["name"], "p5")

    def test_pinned_only_is_not_working_on_now(self):
        project = {"name": "Pinned", "path": "pinned", "pinned": True}
        self.assertEqual(working_on_now_rows([project], lambda _path: True), [])

    def test_working_on_now_does_not_mutate_project_records(self):
        project = {"name": "Alpha", "path": "alpha",
                   "status": "active", "last_commit_date": "2026-08-01"}
        before = dict(project)
        working_on_now_rows([project], lambda _path: True)
        self.assertEqual(project, before)


class ProjectCurationTests(unittest.TestCase):
    def test_apply_curation_preserves_record_and_normalizes_existing_fields(self):
        project = {"path": "x", "name": "X", "status": "idea",
                   "pinned": False, "focus": "old"}
        result = apply_curation(
            project, status="active", pinned=1, focus="  new focus  ")
        self.assertIs(result, project)
        self.assertEqual(project["status"], "active")
        self.assertTrue(project["pinned"])
        self.assertEqual(project["focus"], "new focus")
        self.assertEqual(project["path"], "x")
        self.assertEqual(project["name"], "X")

    def test_empty_status_keeps_existing_ui_default(self):
        project = {"status": "active", "pinned": True, "focus": "old"}
        apply_curation(project, status="", pinned=False, focus="")
        self.assertEqual(project["status"], "idea")
        self.assertFalse(project["pinned"])
        self.assertEqual(project["focus"], "")


if __name__ == "__main__":
    unittest.main()
