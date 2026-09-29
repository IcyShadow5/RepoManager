import tempfile
import unittest
from pathlib import Path

from repo_manager import workspaces


class WorkspaceTests(unittest.TestCase):
    def test_identity_is_stable_and_path_independent(self):
        workspace = workspaces.new_workspace("Feature X")
        first = workspaces.workspace_id(workspace)
        workspace["members"] = [{"repository_id": "r", "path": r"C:\old"}]
        workspace["members"][0]["path"] = r"D:\new"
        self.assertEqual(workspaces.ensure_workspace_id(workspace), first)

    def test_add_remove_member_is_metadata_only(self):
        workspace = workspaces.new_workspace("Feature X")
        member = workspaces.add_member(workspace, "repo-1", "/tmp/repo", label="Main")
        self.assertEqual(member["label"], "Main")
        self.assertTrue(workspaces.remove_member(workspace, "/tmp/repo"))
        self.assertFalse(workspace["members"])

    def test_inspection_preserves_missing_stale_dirty_and_valid_states(self):
        workspace = workspaces.new_workspace("Feature X")
        workspaces.add_member(workspace, "valid", "valid", branch="main")
        workspaces.add_member(workspace, "stale", "stale", branch="main")
        workspaces.add_member(workspace, "missing", "missing")
        observations = {
            "valid": {"branch": "main", "head": "a", "dirty": 2, "worktrees": []},
            "stale": {"branch": "feature", "head": "b", "dirty": 0, "worktrees": []},
        }
        result = workspaces.inspect_workspace(
            workspace,
            observe=lambda path: observations.get(path),
            exists=lambda path: path != "missing",
            repository_ids={"valid", "stale", "missing"},
        )
        self.assertEqual(result["status"], "BLOCKED")
        states = [item["state"] for item in result["members"]]
        self.assertEqual(states, [workspaces.VALID, workspaces.STALE, workspaces.MISSING])
        self.assertEqual(result["members"][0]["dirty"], 2)
        self.assertEqual(result["counts"]["missing"], 1)
        self.assertEqual(result["counts"]["untracked"], 0)

    def test_invalid_git_observation_is_unavailable(self):
        workspace = workspaces.new_workspace("Unavailable")
        workspaces.add_member(workspace, "repo", "repo")
        result = workspaces.inspect_workspace(
            workspace, observe=lambda _path: {"broken": True}, exists=lambda _path: True)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["members"][0]["state"], workspaces.UNAVAILABLE)

    def test_unknown_repository_id_is_untracked_without_observation(self):
        workspace = workspaces.new_workspace("Unknown member")
        workspaces.add_member(workspace, "removed-project", "repo")
        calls = []
        result = workspaces.inspect_workspace(
            workspace, observe=lambda path: calls.append(("observe", path)),
            exists=lambda path: calls.append(("exists", path)),
            repository_ids={"current-project"})
        self.assertEqual(result["members"][0]["state"], workspaces.UNTRACKED)
        self.assertIn("not present in the Project registry",
                      result["members"][0]["evidence"][0])
        self.assertEqual(result["counts"]["missing"], 0)
        self.assertEqual(result["counts"]["untracked"], 1)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(calls, [])

    def test_workspace_validation_rejects_incomplete_members(self):
        workspace = workspaces.new_workspace("Broken")
        workspace["members"] = [{"path": "repo"}]
        issues = workspaces.validate_workspace(workspace)
        self.assertTrue(any("repository_id" in issue for issue in issues))


class WorkspaceObservationHardeningTests(unittest.TestCase):
    """partial Git observations must never surface as READY."""

    def _inspect(self, observed):
        workspace = workspaces.new_workspace("Partial")
        workspaces.add_member(workspace, "repo", "repo")
        return workspaces.inspect_workspace(
            workspace, observe=lambda _path: observed,
            exists=lambda _path: True)

    def test_string_dirty_is_unavailable_not_ready(self):
        result = self._inspect({"branch": "main", "head": "a",
                                "dirty": "many", "worktrees": []})
        self.assertEqual(result["members"][0]["state"], workspaces.UNAVAILABLE)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_bool_dirty_is_unavailable_not_ready(self):
        result = self._inspect({"branch": "main", "head": "a",
                                "dirty": True, "worktrees": []})
        self.assertEqual(result["members"][0]["state"], workspaces.UNAVAILABLE)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_negative_dirty_is_unavailable_not_ready(self):
        result = self._inspect({"branch": "main", "head": "a",
                                "dirty": -1, "worktrees": []})
        self.assertEqual(result["members"][0]["state"], workspaces.UNAVAILABLE)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_missing_git_status_is_unavailable_not_ready(self):
        result = self._inspect({"branch": "main", "head": "a", "dirty": 0,
                                "status_available": False, "worktrees": []})
        self.assertEqual(result["members"][0]["state"], workspaces.UNAVAILABLE)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_int_dirty_still_valid_and_ready(self):
        result = self._inspect({"branch": "main", "head": "a", "dirty": 3,
                                "worktrees": []})
        self.assertEqual(result["members"][0]["state"], workspaces.VALID)
        self.assertEqual(result["members"][0]["dirty"], 3)
        self.assertEqual(result["status"], "READY")


class WorkspaceExpectedHeadTests(unittest.TestCase):
    """A pinned expected_head must be compared against the observed HEAD.

    The branch-drift arm above is already covered; the HEAD-drift arm was
    reachable but unasserted, so disabling it left the whole suite green
    while a workspace sitting on the wrong commit reported READY.
    """

    def _inspect(self, *, pinned_head, observed_head, observed_branch="main"):
        workspace = workspaces.new_workspace("Pinned")
        workspaces.add_member(workspace, "repo-1", "repo",
                              branch="main", head=pinned_head)
        return workspaces.inspect_workspace(
            workspace,
            observe=lambda _path: {"branch": observed_branch,
                                   "head": observed_head,
                                   "dirty": 0, "worktrees": []},
            exists=lambda _path: True,
            repository_ids={"repo-1"},
        )

    def test_pinned_head_is_stored_on_the_member(self):
        workspace = workspaces.new_workspace("Pinned")
        member = workspaces.add_member(workspace, "repo-1", "repo",
                                       branch="main", head="aaaa111")
        self.assertEqual(member["expected_head"], "aaaa111")

    def test_diverged_head_is_stale_not_ready(self):
        result = self._inspect(pinned_head="aaaa111", observed_head="bbbb999")
        self.assertEqual(result["members"][0]["state"], workspaces.STALE)
        self.assertIn("expected HEAD differs from observed HEAD",
                      result["members"][0]["evidence"])
        self.assertEqual(result["status"], "STALE")

    def test_matching_head_is_valid_and_ready(self):
        result = self._inspect(pinned_head="aaaa111", observed_head="aaaa111")
        self.assertEqual(result["members"][0]["state"], workspaces.VALID)
        self.assertEqual(result["status"], "READY")

    def test_absent_observed_head_does_not_match_pinned_head(self):
        result = self._inspect(pinned_head="aaaa111", observed_head=None)
        self.assertEqual(result["members"][0]["state"], workspaces.STALE)
        self.assertEqual(result["status"], "STALE")

    def test_branch_drift_alone_is_stale(self):
        result = self._inspect(pinned_head="aaaa111", observed_head="aaaa111",
                               observed_branch="feature/x")
        self.assertEqual(result["members"][0]["state"], workspaces.STALE)
        self.assertEqual(result["status"], "STALE")


if __name__ == "__main__":
    unittest.main()
