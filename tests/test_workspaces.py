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
        )
        self.assertEqual(result["status"], "BLOCKED")
        states = [item["state"] for item in result["members"]]
        self.assertEqual(states, [workspaces.VALID, workspaces.STALE, workspaces.MISSING])
        self.assertEqual(result["members"][0]["dirty"], 2)
        self.assertEqual(result["counts"]["missing"], 1)

    def test_invalid_git_observation_is_unavailable(self):
        workspace = workspaces.new_workspace("Unavailable")
        workspaces.add_member(workspace, "repo", "repo")
        result = workspaces.inspect_workspace(
            workspace, observe=lambda _path: {"broken": True}, exists=lambda _path: True)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["members"][0]["state"], workspaces.UNAVAILABLE)

    def test_unknown_repository_id_is_missing_without_observation(self):
        workspace = workspaces.new_workspace("Unknown member")
        workspaces.add_member(workspace, "removed-project", "repo")
        calls = []
        result = workspaces.inspect_workspace(
            workspace, observe=lambda path: calls.append(path),
            repository_ids={"current-project"})
        self.assertEqual(result["members"][0]["state"], workspaces.MISSING)
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


if __name__ == "__main__":
    unittest.main()
