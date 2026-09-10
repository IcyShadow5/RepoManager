import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import main as main_module
from repo_manager import projects as project_domain
from repo_manager import store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        app_dir = self.base / "app"
        patcher = mock.patch.multiple(
            store,
            APP_DIR=app_dir,
            REPOS_FILE=app_dir / "repos.json",
            SETTINGS_FILE=app_dir / "settings.json",
            NOTES_DIR=app_dir / "notes",
        )
        patcher.start()
        store.ensure_dirs()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    # ------------------------------------------------ persistence basics

    def test_projects_roundtrip(self):
        self.assertEqual(store.load_projects(), [])
        projects = [{"path": r"C:\x", "name": "x", "status": "active"}]
        store.save_projects(projects)
        loaded = store.load_projects()
        self.assertEqual(loaded[0]["path"], projects[0]["path"])
        self.assertEqual(loaded[0]["name"], projects[0]["name"])
        self.assertTrue(loaded[0]["project_id"])

    def test_ignored_project_roundtrips_without_losing_identity_or_note(self):
        record = {
            "project_id": "ignored-project",
            "path": r"C:\ignored",
            "name": "Ignored project",
            "status": "active",
            "focus": "retain this curation",
            "pinned": True,
            "ignored": True,
        }
        store.save_note(record["name"], record["path"],
                        "keep this note", record["project_id"])
        store.save_projects([record])

        loaded, report = store.read_registry()

        self.assertEqual(report["status"], "valid")
        self.assertEqual(loaded, [record])
        self.assertEqual(loaded[0]["project_id"], "ignored-project")
        self.assertTrue(project_domain.is_ignored(loaded[0]))
        self.assertEqual(
            store.load_note(record["name"], record["path"],
                            record["project_id"]),
            "keep this note")

    def test_valid_false_ignored_project_remains_active(self):
        record = {
            "project_id": "active-project",
            "path": r"C:\active",
            "name": "Active project",
            "status": "active",
            "ignored": False,
        }
        store.save_projects([record])

        loaded, report = store.read_registry()

        self.assertEqual(report["status"], "valid")
        self.assertEqual(loaded, [record])
        self.assertIs(loaded[0]["ignored"], False)
        self.assertFalse(project_domain.is_ignored(loaded[0]))

    def test_malformed_ignored_values_fail_closed_with_validation_evidence(self):
        for malformed in ("true", 1, {}, None):
            with self.subTest(malformed=malformed):
                payload = {"projects": [{
                    "project_id": "uncertain-project",
                    "path": r"C:\uncertain",
                    "name": "Uncertain project",
                    "status": "active",
                    "ignored": malformed,
                }]}
                self._write_raw(json.dumps(payload))

                loaded, report = store.read_registry()

                self.assertEqual(report["status"], "valid")
                self.assertTrue(any("invalid 'ignored'" in reason
                                    for reason in report["reasons"]))
                self.assertIs(loaded[0]["ignored"], True)
                self.assertTrue(project_domain.is_ignored(loaded[0]))
                self.assertEqual(
                    project_domain.working_on_now_rows(
                        loaded, lambda _path: True), [])

    def test_legacy_project_defaults_to_not_ignored(self):
        payload = {"projects": [{
            "project_id": "legacy-project",
            "path": r"C:\legacy",
            "name": "Legacy",
            "status": "active",
        }]}
        self._write_raw(json.dumps(payload))

        loaded, report = store.read_registry()

        self.assertEqual(report["status"], "valid")
        self.assertFalse(project_domain.is_ignored(loaded[0]))
        self.assertNotIn("ignored", loaded[0])

    def test_archived_and_ignored_are_distinct_persisted_states(self):
        records = [
            {"project_id": "archived-project", "path": r"C:\archived",
             "name": "Archived", "status": "archived"},
            {"project_id": "ignored-project", "path": r"C:\ignored",
             "name": "Ignored", "status": "active", "ignored": True},
        ]
        store.save_projects(records)

        loaded = store.load_projects()

        archived = next(item for item in loaded
                        if item["project_id"] == "archived-project")
        ignored = next(item for item in loaded
                       if item["project_id"] == "ignored-project")
        self.assertEqual(archived["status"], "archived")
        self.assertFalse(project_domain.is_ignored(archived))
        self.assertEqual(ignored["status"], "active")
        self.assertTrue(project_domain.is_ignored(ignored))

    def test_workspace_metadata_roundtrip_and_legacy_compatibility(self):
        workspace = {"workspace_id": "w-1", "name": "Feature X", "members": []}
        store.save_projects([], workspaces=[workspace])
        self.assertEqual(store.load_workspaces(), [workspace])
        store.save_projects([{"path": "C:\\repo", "name": "repo"}], workspaces=[])
        self.assertEqual(store.load_workspaces(), [])

    def test_corrupt_registry_recovers_workspaces_from_backup(self):
        workspace = {"workspace_id": "w-1", "name": "Feature X", "members": []}
        store.save_projects([], workspaces=[workspace])
        store.save_projects([], workspaces=[workspace])
        Path(store.REPOS_FILE).write_text("{broken", encoding="utf-8")
        self.assertEqual(store.load_workspaces(), [workspace])

    # ------------------------------------------- bare-save preservation

    def test_workspaces_survive_ordinary_bare_save_paths(self):
        """The app's bare save_projects convention must never drop Workspaces.

        Scan persistence, curation flush, move persistence, and close-time
        flush all call ``save_projects(projects)`` without ``workspaces=``;
        the previously persisted Workspace metadata must carry over.
        """
        workspace = {"workspace_id": "w-1", "name": "Feature X", "members": []}
        store.save_projects([], workspaces=[workspace])          # UI New-Workspace flow
        store.save_projects([{"path": r"C:\repo", "name": "r"}])  # bare scan save
        self.assertEqual(store.load_workspaces(), [workspace])
        store.save_projects([{"path": r"C:\repo", "name": "r",
                              "status": "active"}])            # curation flush
        self.assertEqual(store.load_workspaces(), [workspace])
        store.save_projects([{"path": r"C:\moved", "name": "moved"}])  # move save
        self.assertEqual(store.load_workspaces(), [workspace])
        store.save_projects([])                                  # close-time flush
        self.assertEqual(store.load_workspaces(), [workspace])

    def test_explicit_empty_workspaces_still_clears(self):
        """Passing workspaces=[] explicitly must still clear the list."""
        workspace = {"workspace_id": "w-1", "name": "Feature X", "members": []}
        store.save_projects([], workspaces=[workspace])
        store.save_projects([], workspaces=[])
        self.assertEqual(store.load_workspaces(), [])

    def test_workspace_save_uses_live_projects_when_provided(self):
        workspace = {"workspace_id": "w-1", "name": "Feature X",
                     "members": []}
        store.save_projects([{"path": r"C:\old", "name": "old"}])
        live = [{"path": r"C:\live", "name": "live", "focus": "new"}]

        store.save_workspaces([workspace], projects=live)

        persisted = json.loads(store.REPOS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(persisted["projects"], live)
        self.assertEqual(persisted["workspaces"], [workspace])

    def test_unreadable_primary_aborts_bare_save_and_preserves_bytes(self):
        workspace = {"workspace_id": "w-1", "name": "Feature X",
                     "members": []}
        store.save_projects([{"path": r"C:\old", "name": "old"}],
                            workspaces=[workspace])
        before = store.REPOS_FILE.read_bytes()
        real_read_text = Path.read_text

        def unreadable(path, *args, **kwargs):
            if path == store.REPOS_FILE:
                raise PermissionError("sharing violation")
            return real_read_text(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", unreadable):
            with self.assertRaises(PermissionError):
                store.save_projects([{"path": r"C:\new", "name": "new"}])

        self.assertEqual(store.REPOS_FILE.read_bytes(), before)

    def test_recovered_registry_keeps_workspaces_with_legacy_ids(self):
        """Recovery backfill re-persist must not drop Workspace metadata.

        Legacy records (no project_id) recovered from a backup trigger
        _backfill_and_persist, whose re-persist must carry the recovered
        workspaces even though the primary on disk is corrupt.
        """
        workspace = {"workspace_id": "w-1", "name": "Feature X", "members": []}
        payload = {"schema_version": store.SCHEMA_VERSION,
                   "projects": [{"path": r"C:\old", "name": "old"}],
                   "workspaces": [workspace]}
        b1, _b2 = store._backup_paths()
        b1.write_text(json.dumps(payload), encoding="utf-8")
        self._write_raw("{broken")  # malformed primary before recovery
        projects, report = store.read_registry()
        self.assertEqual(report["status"], "recovered")
        self.assertEqual(report["workspaces"], [workspace])
        self.assertTrue(projects[0]["project_id"])  # legacy ID backfilled
        persisted = json.loads(Path(store.REPOS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(persisted.get("workspaces"), [workspace])

    def test_missing_primary_recovery_repersists_workspaces(self):
        """Recovering from a backup when the primary is gone keeps workspaces."""
        workspace = {"workspace_id": "w-1", "name": "Feature X", "members": []}
        payload = {"schema_version": store.SCHEMA_VERSION,
                   "projects": [{"path": r"C:\old", "name": "old"}],
                   "workspaces": [workspace]}
        b1, _b2 = store._backup_paths()
        b1.write_text(json.dumps(payload), encoding="utf-8")
        projects, report = store.read_registry()  # recover without an initial primary
        self.assertEqual(report["status"], "recovered")
        self.assertEqual(report["workspaces"], [workspace])
        self.assertTrue(projects[0]["project_id"])
        persisted = json.loads(Path(store.REPOS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(persisted.get("workspaces"), [workspace])

    def test_invalid_workspace_is_ignored_without_dropping_projects(self):
        payload = {"schema_version": store.SCHEMA_VERSION,
                   "projects": [{"path": "C:\\repo", "name": "repo"}],
                   "workspaces": [{"name": "invalid", "members": []}]}
        self._write_raw(json.dumps(payload))
        projects, report = store.read_registry()
        self.assertEqual(len(projects), 1)
        self.assertEqual(report["workspaces"], [])
        self.assertTrue(any("workspace [0]" in reason for reason in report["reasons"]))

    def test_settings_defaults_and_persistence(self):
        s = store.load_settings()
        self.assertIn("roots", s)
        self.assertGreaterEqual(len(s["roots"]), 1)
        s["depth"] = 6
        store.save_settings(s)
        self.assertEqual(store.load_settings()["depth"], 6)

    def test_notes_roundtrip(self):
        store.save_note("My Proj", r"C:\p\My Proj", "# hello")
        self.assertEqual(store.load_note("My Proj", r"C:\p\My Proj"), "# hello")

    def test_concurrent_note_writes_are_not_corrupted(self):
        """Concurrent note saves leave complete UTF-8 documents, not torn files."""
        errors = []
        barrier = threading.Barrier(8)
        bodies = [f"note-{index}-" + ("x" * 256) for index in range(8)]

        def writer(index):
            try:
                barrier.wait()
                for _ in range(25):
                    store.save_note("Concurrent", r"C:\\notes\\Concurrent",
                                    bodies[index])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(index,))
                   for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertIn(store.load_note("Concurrent", r"C:\\notes\\Concurrent"),
                      bodies)

    def test_note_collision_gets_unique_files(self):
        store.save_note("Same", r"C:\a\Same", "A")
        store.save_note("Same", r"C:\b\Same", "B")
        a = store.load_note("Same", r"C:\a\Same")
        b = store.load_note("Same", r"C:\b\Same")
        self.assertEqual(a, "A")
        self.assertEqual(b, "B")

    def test_project_id_note_key_survives_rename_move_and_reassociation(self):
        first = store.note_path_for("Old", r"C:\old", "project-1")
        changed = store.note_path_for("Renamed", r"D:\new", "project-1")
        other = store.note_path_for("Old", r"C:\old", "project-2")
        self.assertEqual(first, changed)
        self.assertNotEqual(first, other)
        self.assertEqual(len(first.stem.removeprefix("project-")), 64)

    def test_legacy_six_hex_note_remains_readable_after_key_upgrade(self):
        name, path = "Legacy", r"C:\legacy"
        legacy = store._legacy_note_path_for(name, path)
        legacy.write_text("legacy body", encoding="utf-8")
        self.assertEqual(store.load_note(name, path), "legacy body")

    # -------------------------------------------------- registry hardening

    def _write_raw(self, text):
        Path(store.REPOS_FILE).write_text(text, encoding="utf-8")

    def test_save_stamps_schema_version(self):
        store.save_projects([{"path": r"C:\a", "name": "a"}])
        data = json.loads(Path(store.REPOS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], store.SCHEMA_VERSION)

    def test_legacy_file_without_version_is_valid(self):
        payload = {"projects": [{"path": r"C:\old", "name": "old"}]}
        self._write_raw(json.dumps(payload))
        projects, report = store.read_registry()
        self.assertEqual(projects[0]["path"], payload["projects"][0]["path"])
        self.assertEqual(projects[0]["name"], payload["projects"][0]["name"])
        self.assertTrue(projects[0]["project_id"])
        self.assertEqual(report["status"], "valid")

    def test_legacy_ids_are_durable_without_subsequent_save(self):
        payload = {"schema_version": store.SCHEMA_VERSION, "projects": [
            {"path": r"C:\\one", "name": "one"},
            {"path": r"D:\\two", "name": "two"},
        ]}
        self._write_raw(json.dumps(payload))

        first, _report = store.read_registry()
        persisted = json.loads(Path(store.REPOS_FILE).read_text(encoding="utf-8"))
        disk_ids = [record["project_id"] for record in persisted["projects"]]
        second, _report = store.read_registry()

        self.assertEqual(disk_ids, [record["project_id"] for record in first])
        self.assertEqual([record["project_id"] for record in second], disk_ids)
        self.assertEqual(len(set(disk_ids)), 2)

    def test_legacy_backfill_save_failure_does_not_report_durable_identity(self):
        payload = {"projects": [{"path": r"C:\\old", "name": "old"}]}
        self._write_raw(json.dumps(payload))
        before = Path(store.REPOS_FILE).read_bytes()

        with mock.patch.object(store, "save_projects", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.read_registry()

        self.assertEqual(Path(store.REPOS_FILE).read_bytes(), before)

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

    def test_save_rejects_equivalent_duplicate_paths_before_replace(self):
        original = [{"project_id": "p-1", "path": r"C:\original",
                     "name": "Original"}]
        store.save_projects(original)
        before = Path(store.REPOS_FILE).read_bytes()
        conflicting = [
            {"project_id": "p-2", "path": r"D:\Repo", "name": "First"},
            {"project_id": "p-3", "path": r"d:\repo\.", "name": "Second"},
        ]

        with self.assertRaisesRegex(store.RegistryCorrupt,
                                    "duplicate Project path"):
            store.save_projects(conflicting)

        self.assertEqual(Path(store.REPOS_FILE).read_bytes(), before)
        self.assertEqual(store.load_projects(), original)

    def test_project_id_is_persisted_without_schema_migration(self):
        record = {"path": r"C:\old", "name": "old", "project_id": "p-1"}
        self._write_raw(json.dumps({"projects": [record]}))
        projects, report = store.read_registry()
        self.assertEqual(projects, [record])
        self.assertEqual(report["status"], "valid")

    def _assert_corrupt(self, text, expect_quarantine=True):
        self._write_raw(text)
        projects, report = store.read_registry()
        self.assertEqual(projects, [])
        self.assertEqual(report["status"], "unrecoverable")
        if expect_quarantine:
            self.assertIsNotNone(report["quarantined"])
            self.assertTrue(Path(report["quarantined"]).exists())

    def test_malformed_json_is_corruption_with_recovery(self):
        good = [{"path": r"C:\good", "name": "good"}]
        store.save_projects(good)          # primary=good
        store.save_projects(good + [])     # bak1=good (rotated)
        self._write_raw('{"projects": [ {"path": ')
        projects, report = store.read_registry()
        self.assertEqual(report["status"], "recovered")
        self.assertEqual(report["source"], "repos.json.bak1")
        self.assertEqual(projects[0]["path"], good[0]["path"])
        self.assertTrue(projects[0]["project_id"])
        self.assertTrue(Path(report["quarantined"]).exists())
        quarantined = Path(report["quarantined"]).read_text(encoding="utf-8")
        self.assertEqual(quarantined, '{"projects": [ {"path": ')

    def test_truncated_json_is_corruption(self):
        valid = json.dumps({"projects": [{"path": r"C:\a", "name": "a"}]})
        self._assert_corrupt(valid[:len(valid) - 12])

    def test_empty_file_is_corruption_not_fresh(self):
        self._assert_corrupt("")

    def test_wrong_top_level_type_list_is_corruption(self):
        self._assert_corrupt('[{"path": "C:\\\\a"}]')

    def test_wrong_top_level_type_string_is_corruption(self):
        self._assert_corrupt('"hello"')

    def test_missing_projects_key_is_corruption(self):
        self._assert_corrupt('{"schema_version": 1}')

    def test_projects_null_is_corruption_not_fresh(self):
        self._assert_corrupt('{"projects": null}')

    def test_projects_string_is_corruption(self):
        self._assert_corrupt('{"projects": "notalist"}')

    def test_projects_object_is_corruption(self):
        self._assert_corrupt('{"projects": {"path": "C:\\\\a"}}')

    def test_unknown_schema_version_is_corruption(self):
        self._assert_corrupt('{"schema_version": 99, "projects": []}')

    def test_malformed_record_dropped_valid_kept(self):
        payload = {"projects": [
            {"name": "no-path"},
            {"path": "", "name": "empty-path"},
            {"path": r"C:\ok", "name": "ok", "status": "active"},
            "not-a-dict",
            {"path": r"C:\no-name"},
        ]}
        records, issues = store.validate_registry(payload)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["name"], "ok")
        self.assertEqual(len(issues), 4)

    def test_read_registry_reports_dropped_records_on_valid_file(self):
        payload = {"projects": [
            {"path": r"C:\ok", "name": "ok"},
            {"name": "broken"},
        ]}
        self._write_raw(json.dumps(payload))
        projects, report = store.read_registry()
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["dropped"], 1)
        self.assertEqual(len(projects), 1)

    def test_unknown_fields_preserved(self):
        rec = {"path": r"C:\a", "name": "a", "future_field": {"x": 1}}
        store.save_projects([rec])
        loaded = store.load_projects()
        self.assertEqual(loaded[0]["future_field"], {"x": 1})

    def test_backup_rotation_order(self):
        store.save_projects([{"path": "C:\\1", "name": "1"}])
        store.save_projects([{"path": "C:\\2", "name": "2"}])
        b1, b2 = store._backup_paths()
        self.assertFalse(b2.exists())
        self.assertEqual(
            json.loads(b1.read_text(encoding="utf-8"))["projects"][0]["name"],
            "1")
        store.save_projects([{"path": "C:\\3", "name": "3"}])
        self.assertEqual(
            json.loads(b1.read_text(encoding="utf-8"))["projects"][0]["name"],
            "2")
        self.assertEqual(
            json.loads(b2.read_text(encoding="utf-8"))["projects"][0]["name"],
            "1")

    def test_semantic_noop_preserves_existing_backup_history(self):
        first = [{"path": r"C:\first", "name": "first"}]
        second = [{"path": r"C:\second", "name": "second"}]
        store.save_projects(first)
        store.save_projects(second)
        b1, b2 = store._backup_paths()
        before_primary = store.REPOS_FILE.read_bytes()
        before_b1 = b1.read_bytes()
        self.assertFalse(b2.exists())

        store.save_projects(second)

        self.assertEqual(store.REPOS_FILE.read_bytes(), before_primary)
        self.assertEqual(b1.read_bytes(), before_b1)
        self.assertFalse(b2.exists())

    def test_matching_backup_never_suppresses_stale_primary_repair(self):
        desired = {"schema_version": store.SCHEMA_VERSION,
                   "projects": [{"path": r"C:\desired", "name": "desired"}],
                   "workspaces": []}
        stale = {"schema_version": store.SCHEMA_VERSION,
                 "projects": [{"path": r"C:\stale", "name": "stale"}],
                 "workspaces": []}
        store.REPOS_FILE.write_text(json.dumps(stale), encoding="utf-8")
        b1, _b2 = store._backup_paths()
        b1.write_text(json.dumps(desired), encoding="utf-8")

        store.save_projects(desired["projects"], workspaces=[])

        current = json.loads(store.REPOS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(current, desired)

    # ---------------------------------------------- atomic-save ordering

    def test_concurrent_registry_writes_are_serialized(self):
        """Concurrent callers never collide on the shared transaction temp path."""
        errors = []
        barrier = threading.Barrier(8)

        def writer(index):
            try:
                barrier.wait()
                for revision in range(25):
                    store.save_projects([{
                        "path": f"C:/repo/{index}-{revision}",
                        "name": f"{index}-{revision}",
                    }])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(index,))
                   for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        primary = json.loads(store.REPOS_FILE.read_text(encoding="utf-8"))
        self.assertIsInstance(primary["projects"], list)
        for backup in store._backup_paths():
            if backup.exists():
                payload = json.loads(backup.read_text(encoding="utf-8"))
                self.assertIsInstance(payload["projects"], list)
        self.assertFalse(Path(store.REPOS_FILE).with_suffix(".tmp").exists())

    def test_failed_write_keeps_previous_primary(self):
        good = [{"path": r"C:\good", "name": "good", "status": "active"}]
        store.save_projects(good)   # valid primary
        real_open = open

        def failing_open(file, *a, **k):
            if str(file).endswith(".tmp"):
                raise OSError("disk full")
            return real_open(file, *a, **k)

        with mock.patch("builtins.open", failing_open):
            with self.assertRaises(OSError):
                store.save_projects([{"path": r"C:\other",
                                      "name": "other"}])
        # the previous valid primary is untouched and still readable
        self.assertTrue(store.REPOS_FILE.exists())
        projects, report = store.read_registry()
        self.assertEqual(report["status"], "valid")
        self.assertEqual([p["name"] for p in projects], ["good"])

    def test_post_replace_cache_failure_keeps_committed_save_successful(self):
        desired = [{"project_id": "target-id", "path": r"C:\target",
                    "name": "Target", "ignored": True}]

        with mock.patch.object(
                store, "_cache_recovered_workspaces",
                side_effect=OSError("post-replace cache failure")) as cache, \
                self.assertLogs("repo_manager.store", level="ERROR") as logs:
            store.save_projects(desired)

        cache.assert_called_once_with([])
        persisted = json.loads(store.REPOS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(persisted["projects"], desired)
        self.assertFalse(store.REPOS_FILE.with_suffix(".tmp").exists())
        self.assertTrue(any(
            "registry committed but recovered Workspace cache update failed"
            in message for message in logs.output))

    def test_successful_save_keeps_primary_valid_and_rotates(self):
        store.save_projects([{"path": r"C:\1", "name": "1"}])
        store.save_projects([{"path": r"C:\2", "name": "2"}])
        projects, report = store.read_registry()
        self.assertEqual(report["status"], "valid")
        self.assertEqual([p["name"] for p in projects], ["2"])
        b1, _b2 = store._backup_paths()
        self.assertEqual(
            json.loads(b1.read_text(encoding="utf-8"))["projects"][0]["name"],
            "2")

    def test_missing_primary_recovers_from_valid_backup(self):
        good = [{"path": r"C:\good", "name": "good", "status": "active"}]
        store.save_projects(good)
        store.save_projects(good)          # bak1 = good
        os.remove(store.REPOS_FILE)        # primary missing (lost state)
        projects, report = store.read_registry()
        self.assertEqual(report["status"], "recovered")
        self.assertEqual([p["name"] for p in projects], ["good"])

    def test_recovery_prefers_bak1_then_bak2(self):
        store.save_projects([{"path": "C:\\one", "name": "one"}])
        store.save_projects([{"path": "C:\\two", "name": "two"}])
        self._write_raw("{garbage")
        projects, report = store.read_registry()
        self.assertEqual(report["status"], "recovered")
        self.assertEqual([p["name"] for p in projects], ["one"])

    def test_invalid_backups_ignored_unrecoverable(self):
        b1, b2 = store._backup_paths()
        b1.write_text("{bad", encoding="utf-8")
        b2.write_text("[wrong]", encoding="utf-8")
        self._assert_corrupt('{"projects": "x"}')

    def test_quarantine_names_unique(self):
        self._assert_corrupt("{bad one")
        first = sorted(Path(store.APP_DIR).glob("repos.json.corrupt-*"))
        self.assertEqual(len(first), 1)
        self._write_raw("{different garbage")
        store.read_registry()
        second = sorted(Path(store.APP_DIR).glob("repos.json.corrupt-*"))
        self.assertEqual(len(second), 2)
        self.assertNotEqual(first[0], second[-1])

    def test_stale_tmp_ignored_by_load(self):
        store.save_projects([{"path": "C:\\good", "name": "good"}])
        Path(store.REPOS_FILE).with_suffix(".tmp").write_text(
            '{"pro', encoding="utf-8")
        projects, report = store.read_registry()
        self.assertEqual([p["name"] for p in projects], ["good"])
        self.assertEqual(report["status"], "valid")

    def test_notes_unaffected_by_registry_corruption(self):
        store.save_note("P", r"C:\x\P", "note-body")
        self._assert_corrupt("{corrupt")
        self.assertEqual(store.load_note("P", r"C:\x\P"), "note-body")

    def test_missing_file_reported_as_fresh(self):
        projects, report = store.read_registry()
        self.assertEqual(projects, [])
        self.assertEqual(report["status"], "fresh")
        self.assertIsNone(report["quarantined"])

    def test_settings_coercion_resets_invalid_values(self):
        bad = {"roots": "notalist", "depth": 99,
               "skip_dirs": {"not": "alist"}, "agent_cmd": "opencode",
               "theme": "ultraviolet", "sort": ["name", "no"],
               "column_widths": {"name": 200, "path": True,
                                 "status": "wide"}}
        store.save_settings(bad)
        s = store.load_settings()
        self.assertIsInstance(s["roots"], list)
        self.assertEqual(s["depth"], store.DEFAULT_SETTINGS["depth"])
        self.assertEqual(s["skip_dirs"], store.DEFAULT_SETTINGS["skip_dirs"])
        self.assertEqual(s["agent_cmd"], "opencode")
        self.assertEqual(s["theme"], "dark")
        self.assertNotIn("sort", s)
        self.assertEqual(s["column_widths"], {"name": 200})

    def test_unreadable_primary_recovers_from_backup(self):
        good = [{"path": r"C:\good", "name": "good", "status": "active"}]
        store.save_projects(good)
        store.save_projects(good)  # bak1 = good
        # simulate a lock/sync-placeholder: primary is a DIRECTORY,
        # so read_bytes raises PermissionError instead of parse errors
        import os
        os.remove(store.REPOS_FILE)
        store.REPOS_FILE.mkdir(parents=True)
        projects, report = store.read_registry()
        self.assertEqual(report["status"], "recovered")
        self.assertEqual([p["name"] for p in projects], ["good"])
        self.assertTrue(projects[0]["project_id"])
        self.assertIsNone(report["quarantined"])  # nothing to quarantine

    def test_unreadable_primary_without_backup_is_unavailable(self):
        import os
        store.REPOS_FILE.mkdir(parents=True)  # unreadable as a file
        projects, report = store.read_registry()
        self.assertEqual(projects, [])
        self.assertEqual(report["status"], "unavailable")
        self.assertTrue(report["write_blocked"])
        self.assertTrue(any("unreadable" in r for r in report["reasons"]))

    def test_settings_valid_json_wrong_top_type_falls_back(self):
        Path(store.SETTINGS_FILE).write_text('[1, 2, 3]', encoding="utf-8")
        s = store.load_settings()
        self.assertIn("roots", s)          # defaults restored
        self.assertIsInstance(s["depth"], int)

    def test_note_roundtrip_unicode_and_spaces(self):
        name = "Ünïcode Pröj (draft)"
        path = r"C:\Users\Üser\My Projects\Ünïcode Pröj (draft)"
        store.save_note(name, path, "# äöü — ✅")
        self.assertEqual(store.load_note(name, path), "# äöü — ✅")
        self.assertIn("Ünïcode-Pröj", store.note_path_for(name, path).name)

    def test_move_suppressions_roundtrip_and_sanitizing(self):
        s = store.load_settings()
        s["move_suppressions"] = [
            {"old": r"C:\a", "new": r"C:\b"},
            {"bad": "shape"},
            "junk",
        ]
        store.save_settings(s)
        loaded = store.load_settings()
        self.assertEqual(loaded["move_suppressions"],
                         [{"old": r"C:\a", "new": r"C:\b"}])

    def test_fingerprint_sanitize_valid_with_unknown_fields(self):
        fp = {"remotes": ["github.com/a/b"], "root_commits": ["abc"],
              "future_key": 1}
        out = store.sanitize_fingerprint(fp)
        self.assertEqual(out["remotes"], ["github.com/a/b"])
        self.assertEqual(out["future_key"], 1)

    def test_fingerprint_sanitize_malformed(self):
        self.assertIsNone(store.sanitize_fingerprint("junk"))
        self.assertIsNone(store.sanitize_fingerprint(42))
        out = store.sanitize_fingerprint({"remotes": "nope",
                                          "root_commits": [1, None, "abc"]})
        self.assertEqual(out["remotes"], [])
        self.assertEqual(out["root_commits"], ["abc"])

    def test_validation_sanitizes_record_fingerprint_not_rejects(self):
        payload = {"projects": [{"path": r"C:\a", "name": "a",
                                 "fingerprint": {"remotes": "bad"}}]}
        records, issues = store.validate_registry(payload)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["fingerprint"]["remotes"], [])
        self.assertEqual(issues, [])

    def test_registry_drops_duplicate_identity_and_equivalent_paths(self):
        payload = {"projects": [
            {"project_id": "p-1", "path": r"C:\Repo", "name": "first"},
            {"project_id": "p-1", "path": r"D:\Other", "name": "duplicate id"},
            {"project_id": "p-3", "path": r"c:\repo\.", "name": "duplicate path"},
        ]}
        records, issues = store.validate_registry(payload)
        self.assertEqual([record["name"] for record in records], ["first"])
        self.assertTrue(any("duplicate 'project_id'" in issue for issue in issues))
        self.assertTrue(any("duplicate Project path" in issue for issue in issues))

    def test_registry_sanitizes_operational_types_and_launcher_ownership(self):
        payload = {"projects": [{
            "project_id": "p-1", "path": r"C:\Repo", "name": "first",
            "status": "invented", "pinned": "yes", "dirty": -1,
            "custom_launchers": [{
                "project_id": "p-other", "name": "wrong owner",
                "executable": "tool.exe", "args": [], "cwd": r"C:\Repo",
            }],
        }]}
        records, issues = store.validate_registry(payload)
        self.assertEqual(records[0]["status"], "idea")
        self.assertFalse(records[0]["pinned"])
        self.assertIsNone(records[0]["dirty"])
        self.assertEqual(records[0]["custom_launchers"], [])
        self.assertGreaterEqual(len(issues), 4)

    def test_duplicate_workspace_id_is_not_loaded(self):
        workspace = {"workspace_id": "w-1", "name": "one", "members": []}
        self._write_raw(json.dumps({
            "projects": [], "workspaces": [workspace, {**workspace, "name": "two"}],
        }))
        _projects, report = store.read_registry()
        self.assertEqual(report["workspaces"], [workspace])
        self.assertTrue(any("duplicate workspace_id" in issue
                            for issue in report["reasons"]))

    def test_registry_size_limit_is_explicit_corruption(self):
        self._write_raw('{"projects": []} ')
        with mock.patch.object(store, "MAX_REGISTRY_BYTES", 8):
            projects, report = store.read_registry()
        self.assertEqual(projects, [])
        self.assertEqual(report["status"], "unrecoverable")
        self.assertIn("registry exceeds size limit", report["reasons"][0])

    def test_registry_count_limits_are_explicit_corruption(self):
        payload = {"projects": [
            {"path": r"C:\one", "name": "one"},
            {"path": r"C:\two", "name": "two"},
        ]}
        with mock.patch.object(store, "MAX_PROJECTS", 1):
            with self.assertRaises(store.RegistryCorrupt):
                store.validate_registry(payload)

    def test_moved_from_invalid_ignored(self):
        payload = {"projects": [{"path": r"C:\a", "name": "a",
                                 "moved_from": 123}]}
        records, issues = store.validate_registry(payload)
        self.assertNotIn("moved_from", records[0])
        self.assertEqual(len(issues), 1)

    def test_move_note_absent_source_is_noop(self):
        self.assertEqual(
            store.move_note("A", r"C:\old", "A", r"C:\new"), "absent")

    def test_move_note_moves_byte_identical(self):
        store.save_note("Proj", r"C:\old\Proj", "line1\r\nline2")
        src = store.note_path_for("Proj", r"C:\old\Proj")
        before = src.read_bytes()
        result = store.move_note("Proj", r"C:\old\Proj",
                                 "Proj", r"D:\moved\Proj")
        self.assertEqual(result, "moved")
        self.assertFalse(src.exists())
        dst = store.note_path_for("Proj", r"D:\moved\Proj")
        self.assertEqual(dst.read_bytes(), before)

    def test_move_note_migrates_legacy_note_to_stable_project_key(self):
        name, old, new = "Proj", r"C:\old\Proj", r"D:\new\Proj"
        legacy = store._legacy_note_path_for(name, old)
        legacy.write_text("stable body", encoding="utf-8")

        result = store.move_note(name, old, name, new, "project-1")

        self.assertEqual(result, "moved")
        self.assertFalse(legacy.exists())
        self.assertEqual(
            store.load_note(name, new, "project-1"), "stable body")

    def test_move_note_collision_never_overwrites(self):
        store.save_note("Same", r"C:\old\Same", "OLD-NOTE")
        store.save_note("Same", r"C:\new\Same", "NEW-NOTE")
        result = store.move_note("Same", r"C:\old\Same",
                                 "Same", r"C:\new\Same")
        self.assertEqual(result, "collision")
        self.assertEqual(store.load_note("Same", r"C:\new\Same"),
                         "NEW-NOTE")
        sidecars = list(Path(store.NOTES_DIR).glob("*moved*.md"))
        self.assertEqual(len(sidecars), 1)
        self.assertEqual(sidecars[0].read_text(encoding="utf-8"),
                         "OLD-NOTE")


class RecoveryIdentityTests(unittest.TestCase):
    """recovery must never rotate Project IDs between reads."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        app_dir = self.base / "app"
        patcher = mock.patch.multiple(
            store,
            APP_DIR=app_dir,
            REPOS_FILE=app_dir / "repos.json",
            SETTINGS_FILE=app_dir / "settings.json",
            NOTES_DIR=app_dir / "notes",
        )
        patcher.start()
        store.ensure_dirs()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _write_raw(self, text):
        Path(store.REPOS_FILE).write_text(text, encoding="utf-8")

    def test_missing_primary_recovery_derives_stable_legacy_ids(self):
        payload = {"schema_version": store.SCHEMA_VERSION,
                   "projects": [{"path": r"C:\old", "name": "old"}]}
        b1, _b2 = store._backup_paths()
        b1.write_text(json.dumps(payload), encoding="utf-8")
        # primary never existed: recovery must come from the backup

        projects, report = store.read_registry()
        self.assertEqual(report["status"], "recovered")
        self.assertEqual(report["identity_source"], "derived")
        self.assertTrue(report["identity_durable"])  # re-persisted
        first = [p["project_id"] for p in projects]
        self.assertTrue(all(str(i).startswith("legacy-") for i in first))

        # A second recovery must produce identical IDs (path-derived digest,
        # never a fresh UUID) and the primary must carry them.
        Path(store.REPOS_FILE).unlink()
        projects2, _ = store.read_registry()
        self.assertEqual([p["project_id"] for p in projects2], first)
        persisted = json.loads(
            Path(store.REPOS_FILE).read_text(encoding="utf-8"))
        self.assertEqual([p["project_id"] for p in persisted["projects"]],
                         first)

    def test_unreadable_primary_derives_ids_in_memory_without_persisting(self):
        payload = {"schema_version": store.SCHEMA_VERSION,
                   "projects": [{"path": r"C:\old", "name": "old"}]}
        b1, _b2 = store._backup_paths()
        b1.write_text(json.dumps(payload), encoding="utf-8")
        self._write_raw(json.dumps({"schema_version": 2, "projects": []}))
        before = Path(store.REPOS_FILE).read_bytes()
        real_read_bytes = Path.read_bytes

        def unreadable_primary(path, *args, **kwargs):
            if path == store.REPOS_FILE:
                raise PermissionError("sharing violation")
            return real_read_bytes(path, *args, **kwargs)

        with mock.patch.object(Path, "read_bytes", unreadable_primary):
            projects, report = store.read_registry()

        self.assertEqual(report["status"], "recovered")
        self.assertEqual(report["identity_source"], "derived")
        self.assertFalse(report["identity_durable"])  # in-memory only
        self.assertTrue(projects[0]["project_id"].startswith("legacy-"))
        self.assertEqual(Path(store.REPOS_FILE).read_bytes(), before)


class SettingsQuarantineTests(unittest.TestCase):
    """malformed settings must be preserved, never silently replaced."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        app_dir = self.base / "app"
        patcher = mock.patch.multiple(
            store,
            APP_DIR=app_dir,
            REPOS_FILE=app_dir / "repos.json",
            SETTINGS_FILE=app_dir / "settings.json",
            NOTES_DIR=app_dir / "notes",
        )
        patcher.start()
        store.ensure_dirs()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_malformed_settings_load_defaults_and_are_quarantined(self):
        broken = b"{not json"
        Path(store.SETTINGS_FILE).write_bytes(broken)

        loaded = store.load_settings()
        self.assertEqual(loaded, store._sanitize_settings({}))
        report = store.settings_recovery_report()
        self.assertEqual(report["status"], "recovered")
        self.assertFalse(report["preservation_failed"])
        quarantined = Path(report["quarantined"])
        self.assertTrue(quarantined.exists())
        self.assertEqual(quarantined.read_bytes(), broken)

    def test_save_preserves_malformed_source_before_replacing(self):
        broken = b"{not json"
        Path(store.SETTINGS_FILE).write_bytes(broken)

        store.save_settings({"theme": "dark"})

        sidecars = sorted(Path(store.APP_DIR).glob("settings.json.corrupt-*"))
        self.assertEqual(len(sidecars), 1)
        self.assertEqual(sidecars[0].read_bytes(), broken)
        valid = json.loads(
            Path(store.SETTINGS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(valid["theme"], "dark")

    def test_unpreservable_malformed_settings_block_the_save(self):
        Path(store.SETTINGS_FILE).write_bytes(b"{broken")
        with mock.patch.object(store, "_quarantine_settings",
                               return_value=None):
            with self.assertRaises(OSError):
                store.save_settings({"theme": "dark"})


class BackupWorkspacePreservationTests(unittest.TestCase):
    """backup-only recovery must survive the next bare save."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        app_dir = self.base / "app"
        patcher = mock.patch.multiple(
            store,
            APP_DIR=app_dir,
            REPOS_FILE=app_dir / "repos.json",
            SETTINGS_FILE=app_dir / "settings.json",
            NOTES_DIR=app_dir / "notes",
        )
        patcher.start()
        store.ensure_dirs()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_workspaces_survive_bare_save_while_primary_is_missing(self):
        workspace = {"workspace_id": "w-1", "name": "Feature X",
                     "members": []}
        payload = {"schema_version": store.SCHEMA_VERSION,
                   "projects": [{"path": r"C:\old", "name": "old"}],
                   "workspaces": [workspace]}
        b1, _b2 = store._backup_paths()
        b1.write_text(json.dumps(payload), encoding="utf-8")
        # primary never existed: recovery must come from the backup

        _projects, report = store.read_registry()  # recovery populates cache
        self.assertEqual(report["workspaces"], [workspace])

        # Simulate the primary vanishing again before an ordinary bare save:
        # the recovery cache, not an empty list, must feed the next save.
        Path(store.REPOS_FILE).unlink()
        store.save_projects([{"path": r"C:\old", "name": "old"}])
        persisted = json.loads(
            Path(store.REPOS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(persisted.get("workspaces"), [workspace])


if __name__ == "__main__":
    unittest.main()
