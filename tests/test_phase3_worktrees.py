import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import scanner


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True)


class WorktreeIntegrationTests(unittest.TestCase):
    def make_repo(self, root):
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        (repo / "tracked.txt").write_text("one", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-qm", "initial")
        return repo

    def test_single_worktree_has_branch_and_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(Path(tmp))
            records = scanner.list_worktrees(repo)
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0]["current"])
            self.assertTrue(records[0]["head"])
            self.assertTrue(records[0]["branch"])

    def test_multiple_worktrees_are_associated_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            git(repo, "branch", "feature")
            target = root / "feature-tree"
            git(repo, "worktree", "add", "-q", str(target), "feature")
            records = scanner.list_worktrees(repo)
            self.assertEqual(len(records), 2)
            by_path = {Path(item["path"]).resolve(): item for item in records}
            self.assertEqual(by_path[target.resolve()]["branch"], "feature")
            self.assertNotEqual(by_path[target.resolve()]["path"], str(repo))

    def test_inspection_failure_is_not_reported_as_empty(self):
        with mock.patch.object(scanner, "_git", return_value=None):
            self.assertIsNone(scanner.list_worktrees("missing"))

    def test_creation_fails_closed_when_postcondition_inspection_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            target = root / "new-tree"
            with mock.patch.object(scanner, "list_worktrees",
                                   side_effect=[[], None]), \
                    mock.patch.object(scanner, "_git", return_value="repo"):
                result = scanner.create_worktree(repo, target, "HEAD")
            self.assertFalse(result["ok"])
            self.assertEqual(result["category"],
                             scanner.WORKTREE_INSPECTION_UNAVAILABLE)
            self.assertFalse(result["verified"])

    def test_removal_fails_closed_when_postcondition_inspection_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            target = root / "tree"
            git(repo, "worktree", "add", "-q", str(target), "HEAD")
            with mock.patch.object(scanner, "list_worktrees",
                                   side_effect=[[{"path": str(target),
                                                  "current": False}], None]), \
                    mock.patch.object(scanner, "_git", return_value=""):
                result = scanner.remove_worktree(repo, target, confirm=True)
            self.assertFalse(result["ok"])
            self.assertEqual(result["category"],
                             scanner.WORKTREE_INSPECTION_UNAVAILABLE)
            self.assertFalse(result["verified"])

    def test_creation_checks_collision_and_verifies_postcondition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            collision = root / "existing"
            collision.mkdir()
            blocked = scanner.create_worktree(repo, collision, "HEAD")
            self.assertFalse(blocked["executed"])
            self.assertEqual(blocked["category"], "PATH_COLLISION")
            created = scanner.create_worktree(repo, root / "new-tree", "HEAD")
            self.assertTrue(created["ok"], created)
            self.assertTrue(created["verified"])

    def test_removal_requires_confirmation_and_blocks_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            target = root / "tree"
            git(repo, "worktree", "add", "-q", str(target), "HEAD")
            self.assertEqual(scanner.remove_worktree(repo, target)["category"],
                             "AUTHORIZATION_FAILURE")
            (target / "uncommitted.txt").write_text("keep", encoding="utf-8")
            result = scanner.remove_worktree(repo, target, confirm=True)
            self.assertEqual(result["category"], "DIRTY_STATE")
            self.assertTrue(target.exists())

    def test_removal_verifies_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.make_repo(root)
            target = root / "tree"
            git(repo, "worktree", "add", "-q", str(target), "HEAD")
            result = scanner.remove_worktree(repo, target, confirm=True)
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["verified"])
            self.assertFalse(target.exists())
            self.assertEqual(len(scanner.list_worktrees(repo)), 1)

    def test_metadata_marks_worktree_inspection_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(Path(tmp))
            with mock.patch.object(scanner, "_worktree_state", return_value=None):
                meta = scanner.collect_metadata(repo)
            self.assertEqual(meta["worktrees"], [])
            self.assertFalse(meta["worktrees_available"])

    def test_metadata_includes_worktrees_and_status_breakdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(Path(tmp))
            (repo / "tracked.txt").write_text("changed", encoding="utf-8")
            (repo / "new.txt").write_text("new", encoding="utf-8")
            git(repo, "add", "new.txt")
            meta = scanner.collect_metadata(repo)
            self.assertEqual(meta["staged"], 1)
            self.assertEqual(meta["unstaged"], 1)
            self.assertEqual(meta["untracked"], 0)
            self.assertEqual(meta["dirty"], 2)
            self.assertEqual(len(meta["worktrees"]), 1)
            self.assertTrue(meta["head"])


if __name__ == "__main__":
    unittest.main()
