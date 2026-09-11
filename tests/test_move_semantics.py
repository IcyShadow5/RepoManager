import unittest

from repo_manager.main import (
    ERROR_IDENTITY_MISMATCH,
    ERROR_STALE_TARGET,
    MOVE_OK,
    MOVE_STALE_TARGET,
    move_outcome,
)


class ConfirmedMoveSemanticTests(unittest.TestCase):
    OLD = r"C:\\old\\Repo"
    NEW = r"C:\\new\\Repo"

    def projects(self):
        return [{"path": self.OLD, "name": "Repo", "status": "active"}]

    def test_revalidation_blocks_changed_filesystem_target(self):
        result = move_outcome(
            self.projects(),
            {"old_path": self.OLD, "new_path": self.NEW},
            "2026-08-30T00:00:00Z",
            lambda: self.fail("registry must not be saved"),
            lambda *args: self.fail("note must not move"),
            path_exists=lambda path: path == self.OLD,
        )
        self.assertEqual(result.status, MOVE_STALE_TARGET)
        self.assertEqual(result.error_category, ERROR_STALE_TARGET)

    def test_revalidation_blocks_identity_change(self):
        result = move_outcome(
            self.projects(),
            {"old_path": self.OLD, "new_path": self.NEW, "identity": "repo-1"},
            "2026-08-30T00:00:00Z",
            lambda: self.fail("registry must not be saved"),
            lambda *args: self.fail("note must not move"),
            path_exists=lambda path: path == self.NEW,
            target_identity=lambda _path: "repo-2",
        )
        self.assertEqual(result.status, MOVE_STALE_TARGET)
        self.assertEqual(result.error_category, ERROR_IDENTITY_MISMATCH)

    def test_structured_success_preserves_existing_transaction(self):
        projects = self.projects()
        result = move_outcome(
            projects,
            {"old_path": self.OLD, "new_path": self.NEW},
            "2026-08-30T00:00:00Z",
            lambda: None,
            lambda *args: "absent",
            path_exists=lambda path: path == self.NEW,
        )
        self.assertEqual(result.status, MOVE_OK)
        self.assertIsNone(result.error_category)
        self.assertEqual(result.entry["path"], self.NEW)


if __name__ == "__main__":
    unittest.main()
