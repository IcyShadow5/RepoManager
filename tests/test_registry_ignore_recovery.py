"""A committed Ignore must survive Registry recovery.

Backup generations follow the authoritative registry replacement, so recovery
must not resurrect a previous Project state.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import main, store


class RegistryIgnoreRecoveryTests(unittest.TestCase):
    """recovery must preserve the latest committed Project state."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="repomanager-ignore-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        app_dir = self.base / "app"
        patcher = mock.patch.multiple(
            store, APP_DIR=app_dir, REPOS_FILE=app_dir / "repos.json",
            SETTINGS_FILE=app_dir / "settings.json",
            NOTES_DIR=app_dir / "notes",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        store.ensure_dirs()
        self.project = {
            "project_id": "victim-1", "path": r"C:\v\ictim",
            "name": "Victim", "status": "active",
        }

    def _persist_committed_ignore(self):
        """Commit an explicit Ignore through the real Remove-button path."""
        result = main.persist_project_ignore(
            [self.project], "victim-1", store.save_projects)
        self.assertIsNotNone(result)
        self.assertTrue(result["ignored"])
        persisted = json.loads(
            store.REPOS_FILE.read_text(encoding="utf-8"))["projects"]
        self.assertIs(persisted[0]["ignored"], True)
        return persisted

    def _assert_recovered_ignore_preserved(self, report, records):
        """Recovered registry stays valid and never resurrects Active."""
        self.assertEqual(report["status"], "recovered")
        self.assertEqual(report["source"], "repos.json.bak1")
        self.assertEqual(len(records), 1)
        self.assertIs(records[0]["ignored"], True)
        self.assertEqual(records[0]["project_id"], "victim-1")
        # Recovery re-persist must carry the committed state, not the older
        # pre-ignore generation.
        persisted = json.loads(
            store.REPOS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(persisted["schema_version"], store.SCHEMA_VERSION)
        self.assertIsInstance(persisted["projects"], list)
        self.assertIs(persisted["projects"][0]["ignored"], True)

    # ------------------------------------------------- A: missing primary

    def test_committed_ignore_survives_missing_primary_recovery(self):
        self._persist_committed_ignore()
        b1, _b2 = store._backup_paths()
        self.assertIs(
            json.loads(b1.read_text(encoding="utf-8"))["projects"][0]
            ["ignored"], True)  # newest backup mirrors the commit

        store.REPOS_FILE.unlink()
        records, report = store.read_registry()

        self._assert_recovered_ignore_preserved(report, records)

    # ------------------------------------------------- B: corrupt primary

    def test_committed_ignore_survives_corrupt_primary_recovery(self):
        self._persist_committed_ignore()
        store.REPOS_FILE.write_text("{broken", encoding="utf-8")

        records, report = store.read_registry()

        self._assert_recovered_ignore_preserved(report, records)
        self.assertTrue(report["quarantined"])

    # --------------------------------- D: no second-round resurrection

    def test_recovered_ignore_state_is_not_resurrected_again(self):
        self._persist_committed_ignore()
        store.REPOS_FILE.unlink()
        records, report = store.read_registry()
        self._assert_recovered_ignore_preserved(report, records)

        # A subsequent reload reads the re-persisted primary: the previously
        # Active state must never return through recovery or backfill.
        records2, report2 = store.read_registry()
        self.assertEqual(report2["status"], "valid")
        self.assertEqual(report2["source"], "primary")
        self.assertIs(records2[0]["ignored"], True)

    # ----------------------------- E: generations and recovery behavior

    def test_backup_generations_mirror_committed_history(self):
        store.save_projects([{"path": r"C:\one", "name": "one"}])
        store.save_projects([{"path": r"C:\two", "name": "two"}])
        b1, b2 = store._backup_paths()
        self.assertEqual(
            json.loads(b1.read_text(encoding="utf-8"))["projects"][0]["name"],
            "two")  # bak1 mirrors the latest committed registry
        self.assertEqual(
            json.loads(b2.read_text(encoding="utf-8"))["projects"][0]["name"],
            "one")  # bak2 retains the previous committed generation
        store.save_projects([{"path": r"C:\three", "name": "three"}])
        self.assertEqual(
            json.loads(b1.read_text(encoding="utf-8"))["projects"][0]["name"],
            "three")
        self.assertEqual(
            json.loads(b2.read_text(encoding="utf-8"))["projects"][0]["name"],
            "two")

    def test_invalid_backups_do_not_recover_corrupt_primary(self):
        store.save_projects([{"path": r"C:\one", "name": "one"}])
        for backup in store._backup_paths():
            backup.write_text("{bad", encoding="utf-8")
        store.REPOS_FILE.write_text("{broken", encoding="utf-8")

        records, report = store.read_registry()

        self.assertEqual(records, [])
        self.assertEqual(report["status"], "unrecoverable")
        self.assertTrue(report["write_blocked"])

    # ----------------------------- F: pre-commit failure keeps old state

    def test_pre_commit_failure_preserves_previous_registry(self):
        good = [{"project_id": "keeper", "path": r"C:\good",
                 "name": "Good", "status": "active"}]
        store.save_projects(good)
        before_primary = store.REPOS_FILE.read_bytes()
        before_b1 = store._backup_paths()[0].read_bytes()
        real_open = open

        def failing_open(file, *args, **kwargs):
            if str(file).endswith(".tmp"):
                raise OSError("disk full")
            return real_open(file, *args, **kwargs)

        with mock.patch("builtins.open", failing_open):
            with self.assertRaises(OSError):
                store.save_projects(
                    [{"path": r"C:\other", "name": "other"}])

        self.assertEqual(store.REPOS_FILE.read_bytes(), before_primary)
        self.assertEqual(store._backup_paths()[0].read_bytes(), before_b1)
        records, report = store.read_registry()
        self.assertEqual(report["status"], "valid")
        self.assertEqual([r["name"] for r in records], ["Good"])

    # ------------------- G: post-commit aux failure keeps commit durable

    def test_post_commit_rotation_failure_does_not_invalidate_commit(self):
        good = [{"project_id": "keeper", "path": r"C:\good",
                 "name": "Good", "status": "active"}]
        store.save_projects(good)

        with mock.patch.object(store, "_rotate_backups",
                               side_effect=OSError("rotation broken")), \
                self.assertLogs("repo_manager.store", level="ERROR") as logs:
            store.save_projects(
                [{"project_id": "next", "path": r"C:\next",
                  "name": "Next", "status": "active"}])

        self.assertTrue(any("backup rotation failed" in message
                            for message in logs.output))
        records, report = store.read_registry()
        self.assertEqual(report["status"], "valid")
        self.assertEqual([r["name"] for r in records], ["Next"])

    def test_corrupt_primary_recovery_cannot_undo_committed_ignore(self):
        # The authoritative save succeeded; a later corrupt-primary read
        # must not turn that commit into an apparent failure.
        with mock.patch.object(store, "save_projects",
                               wraps=store.save_projects) as save:
            self._persist_committed_ignore()
        save.assert_called_once()

        store.REPOS_FILE.write_text("{broken", encoding="utf-8")
        records, report = store.read_registry()

        self._assert_recovered_ignore_preserved(report, records)


if __name__ == "__main__":
    unittest.main()
