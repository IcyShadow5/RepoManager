"""Classic association adapter reference; neutral store contracts run separately."""
import json
import unittest
from pathlib import Path
from repo_manager import main as main_module, store
from tests import test_store as fixtures

class LegacyStoreAssociationTests(unittest.TestCase):
    setUp = fixtures.StoreTests.setUp
    tearDown = fixtures.StoreTests.tearDown

    def test_association_survives_save_reload_and_preserves_metadata(self):
        current = {"path": r"C:\\old", "name": "Old", "project_id": "p-1",
                   "status": "active", "focus": "keep", "pinned": True,
                   "ignored": False, "custom": {"keep": True}}
        other = {"path": r"E:\\other", "name": "Other",
                 "project_id": "p-2", "focus": "unchanged"}
        target = {"path": r"D:\\new", "name": "New", "broken": False}
        before_other = dict(other)
        app = object.__new__(main_module.RepoManagerApp)
        app.projects = [current, other]
        app._registry_report = {"status": "valid", "write_blocked": False}
        app._closing = False
        store.save_note("Old", current["path"], "stable note", "p-1")

        app.associate_repository(current, target)
        app._persist_projects(workspaces=[])

        loaded_records, report = store.read_registry()
        loaded = next(record for record in loaded_records
                      if record["project_id"] == "p-1")
        loaded_other = next(record for record in loaded_records
                            if record["project_id"] == "p-2")
        self.assertEqual(report["status"], "valid")
        self.assertEqual(loaded["path"], target["path"])
        self.assertEqual(loaded["name"], "Old")
        self.assertEqual(loaded["project_id"], "p-1")
        self.assertEqual(loaded["status"], "active")
        self.assertEqual(loaded["focus"], "keep")
        self.assertTrue(loaded["pinned"])
        self.assertIs(loaded["ignored"], False)
        self.assertEqual(loaded["custom"], {"keep": True})
        self.assertEqual(loaded_other, before_other)
        self.assertEqual(store.load_note("Old", target["path"], "p-1"),
                         "stable note")
        self.assertEqual(json.loads(Path(store.REPOS_FILE).read_text())["schema_version"], 2)

    def test_canonical_association_collision_preserves_identities_and_notes(self):
        current = {
            "path": r"C:\old", "name": "Old", "project_id": "p-1",
            "status": "active", "focus": "keep", "pinned": True,
        }
        owner = {
            "path": r"D:\new", "name": "New", "project_id": "p-2",
            "status": "idea", "focus": "target", "pinned": False,
        }
        target = {"path": r"d:\new\.", "name": "Equivalent", "broken": False}
        records = [owner, current]
        before_records = json.loads(json.dumps(records))
        store.save_note("Old", r"C:\old", "old note", "p-1")
        store.save_note("New", r"D:\new", "new note", "p-2")
        store.save_projects(records)
        before_disk = Path(store.REPOS_FILE).read_bytes()

        app = object.__new__(main_module.RepoManagerApp)
        app.projects = records
        with self.assertRaisesRegex(ValueError, "another Project"):
            app.associate_repository(current, target)

        self.assertEqual(records, before_records)
        self.assertEqual(Path(store.REPOS_FILE).read_bytes(), before_disk)
        loaded, report = store.read_registry()
        by_id = {record["project_id"]: record for record in loaded}
        self.assertEqual(report["status"], "valid")
        self.assertEqual(set(by_id), {"p-1", "p-2"})
        self.assertEqual(by_id["p-1"]["path"], r"C:\old")
        self.assertEqual(by_id["p-1"]["focus"], "keep")
        self.assertTrue(by_id["p-1"]["pinned"])
        self.assertEqual(by_id["p-2"]["path"], r"D:\new")
        self.assertEqual(by_id["p-2"]["focus"], "target")
        self.assertEqual(store.load_note("Old", r"C:\old", "p-1"),
                         "old note")
        self.assertEqual(store.load_note("New", r"D:\new", "p-2"),
                         "new note")
