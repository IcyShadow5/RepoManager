"""Persistence, observation and target ownership shared with the Qt presentation."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import project_actions, projects, store
from repo_manager.repository_service import RepositorySession, ScanOutcome
from tests.git_repository import canonical_path, init_repository


class IsolatedSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        data = self.root / "app"
        patch = mock.patch.multiple(store, APP_DIR=data, REPOS_FILE=data / "repos.json",
                                    SETTINGS_FILE=data / "settings.json", NOTES_DIR=data / "notes")
        patch.start()
        self.addCleanup(patch.stop)
        self.session = RepositorySession()

    def seed(self, name="alpha", **fields):
        folder = self.root / name
        folder.mkdir(exist_ok=True)
        record = {"name": name, "path": str(folder), "status": "idea", **fields}
        projects.ensure_project_id(record)
        self.session.records.append(record)
        return record, project_actions.Target.capture(record)


class RepositoryServiceTests(IsolatedSessionTests):
    def test_scan_root_addition_is_validated_without_persistence(self):
        before = copy.deepcopy(self.session.settings)
        roots = [str(self.root)]
        child = self.root / "collection"
        child.mkdir()
        updated = self.session.add_scan_root(roots, f"  {child}  ")
        self.assertEqual(updated, [str(self.root), str(child)])
        self.assertEqual(roots, [str(self.root)])
        self.assertEqual(self.session.settings, before)
        self.assertFalse(store.SETTINGS_FILE.exists())

    def test_scan_root_addition_rejects_missing_and_duplicate_paths(self):
        for value in ("", str(self.root / "missing"), str(self.root / ".." / self.root.name)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.session.add_scan_root([str(self.root)], value)
        with mock.patch("repo_manager.repository_service.os.path.isdir", side_effect=OSError("denied")), self.assertRaises(ValueError):
            self.session.add_scan_root([], str(self.root))

    def test_scan_depth_is_relative_to_each_configured_folder(self):
        collection = self.root / "collection"
        nested = init_repository(collection / "group" / "project")
        deeper = init_repository(collection / "group" / "subgroup" / "deep")
        self.session.save_settings([str(collection)], 2, "opencode", "")
        self.assertEqual({canonical_path(item["path"]) for item in self.session.scan().records}, {canonical_path(nested)})
        self.session.save_settings([str(collection)], 3, "opencode", "")
        self.assertEqual({canonical_path(item["path"]) for item in self.session.scan().records}, {canonical_path(nested), canonical_path(deeper)})

    @unittest.skipUnless(os.name == "nt", "Windows drive-relative path semantics")
    def test_drive_relative_scan_root_is_not_a_whole_drive_search(self):
        for value in ("C:", "C:repositories"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "absolute folder path"):
                self.session.add_scan_root([], value)
        self.assertEqual(self.session.add_scan_root([], "C:\\"), ["C:\\"])

    def test_custom_launcher_crud_preserves_identity_and_notes(self):
        record, target = self.seed()
        self.session.save_note(target, "keep")
        value = {"name": "Check", "executable": "python", "args": ["-V"], "cwd": record["path"]}
        self.session.save_launcher(target, value)
        launcher = self.session.resolve(target)["custom_launchers"][0]
        self.assertEqual(launcher["project_id"], record["project_id"])
        self.session.save_launcher(target, {**launcher, "name": "Updated"})
        self.assertEqual(self.session.resolve(target)["custom_launchers"][0]["name"], "Updated")
        self.session.save_launcher(target, launcher, remove=True)
        self.assertEqual(self.session.resolve(target)["custom_launchers"], [])
        self.assertEqual(self.session.load_note(target), "keep")

    def test_launcher_write_failure_and_invalid_fields_preserve_records(self):
        record, target = self.seed()
        original = copy.deepcopy(self.session.records)
        with self.assertRaises(ValueError):
            self.session.save_launcher(target, {"name": "invalid"})
        with mock.patch.object(store, "save_projects", side_effect=OSError("locked")), self.assertRaises(OSError):
            self.session.save_launcher(target, {"name": "valid", "executable": "python", "args": [], "cwd": record["path"]})
        self.assertEqual(self.session.records, original)

    def test_ignore_restore_preserves_notes_and_folder(self):
        record, target = self.seed()
        self.session.save_note(target, "keep")
        self.session.set_ignored(target, True)
        self.assertEqual(self.session.visible(), [])
        self.assertTrue(Path(record["path"]).is_dir())
        self.session.set_ignored(target, False)
        self.assertEqual(len(self.session.visible()), 1)
        self.assertEqual(self.session.load_note(target), "keep")

    def test_missing_registry_after_blocked_load_never_becomes_fresh(self):
        self.session.report = {"status": "unavailable", "write_blocked": True}
        with mock.patch.object(store, "read_registry", return_value=([], {"status": "fresh"})):
            self.assertFalse(self.session.retry_registry())
        self.assertTrue(self.session.registry_blocked)

    def test_exports_use_existing_report_schemas(self):
        record, target = self.seed()
        destination = self.root / "project.json"
        self.session.export(target, destination, "project")
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), projects.build_project_export(record))
        self.session.export(target, self.root / "report.md", "report")
        self.assertIn("alpha", (self.root / "report.md").read_text(encoding="utf-8"))

    def test_stub_does_not_overwrite_existing_file(self):
        record, target = self.seed()
        self.assertTrue(self.session.generate_stub(target))
        stub = Path(record["path"]) / "run.bat"
        stub.write_text("owner contents", encoding="utf-8")
        self.assertFalse(self.session.generate_stub(target))
        self.assertEqual(stub.read_text(encoding="utf-8"), "owner contents")

    def test_real_scan_discovers_and_persists_git_observations(self):
        scan_root = self.root / "repos"
        scan_root.mkdir()
        init_repository(scan_root / "clean")
        dirty = init_repository(scan_root / "modified")
        (dirty / "new.txt").write_text("untracked\n", encoding="utf-8")
        self.session.save_settings([str(scan_root)], 4, "opencode", "")
        outcome = self.session.scan()
        self.assertEqual(len(outcome.records), 2)
        self.assertIsNone(self.session.accept_scan(outcome))
        self.assertEqual(self.session.counts(), {"repositories": 2, "clean": 1, "modified": 1, "unobserved": 0})
        loaded, report = store.read_registry()
        self.assertEqual(len(loaded), 2)
        self.assertEqual({item["branch"] for item in loaded}, {"main"})
        self.assertTrue(self.session.settings["last_full_scan_at"].endswith("Z"))

    def test_unknown_or_invalid_dirty_is_never_counted_clean(self):
        for index, dirty in enumerate((None, True, -1, "0")):
            self.seed(str(index), dirty=dirty, status_available=True)
        self.seed("unobserved", dirty=0, status_available=False)
        self.assertEqual(self.session.counts()["clean"], 0)
        self.assertEqual(self.session.counts()["unobserved"], 5)

    def test_curation_during_scan_survives_old_payload(self):
        record, target = self.seed(dirty=0)
        outcome = ScanOutcome(copy.deepcopy(self.session.records), [])
        outcome.records[0]["dirty"] = 2
        self.session.save_curation(target, "active", True, "next task")
        self.session.accept_scan(outcome)
        current = self.session.records[0]
        self.assertEqual((current["status"], current["pinned"], current["focus"], current["dirty"]),
                         ("active", True, "next task", 2))

    def test_failed_curation_does_not_change_live_or_persisted_state(self):
        record, target = self.seed()
        store.save_projects(self.session.records)
        before = store.REPOS_FILE.read_bytes()
        with mock.patch.object(store, "save_projects", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.session.save_curation(target, "active", True, "focus")
        self.assertEqual(record["status"], "idea")
        self.assertEqual(store.REPOS_FILE.read_bytes(), before)

    def test_failed_scan_save_retains_live_registry(self):
        record, target = self.seed()
        with mock.patch.object(store, "save_projects", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.session.accept_scan(ScanOutcome([], []))
        self.assertIs(self.session.records[0], record)

    def test_timestamp_failure_reports_saved_inventory_honestly(self):
        self.seed()
        with mock.patch.object(store, "save_settings", side_effect=OSError("disk full")):
            message = self.session.accept_scan(ScanOutcome(self.session.records, []))
        self.assertIn("Inventory saved", message)
        self.assertNotIn("last_full_scan_at", self.session.settings)
        self.assertEqual(len(json.loads(store.REPOS_FILE.read_text(encoding="utf-8"))["projects"]), 1)

    def test_stable_notes_follow_curation_not_row_position(self):
        record, target = self.seed()
        self.session.save_note(target, "owner notes")
        self.session.save_curation(target, "active", True, "focus")
        self.assertEqual(self.session.load_note(target), "owner notes")

    def test_notes_reject_reassociated_target(self):
        record, target = self.seed()
        record["path"] = str(self.root / "different")
        with self.assertRaises(ValueError):
            self.session.save_note(target, "must not be written")
        self.assertEqual(list(store.NOTES_DIR.iterdir()), [])

    def test_search_and_sort_reuse_display_rules(self):
        self.seed("zeta", focus="important task")
        self.seed("Alpha")
        self.assertEqual([item["name"] for item in self.session.visible("important")], ["zeta"])
        self.assertEqual([item["name"] for item in self.session.visible(sort_col="name")], ["Alpha", "zeta"])
        self.assertEqual([item["name"] for item in self.session.visible(sort_col="name", sort_desc=True)], ["zeta", "Alpha"])

    def test_working_view_preserves_activity_order_and_excludes_missing(self):
        self.seed("older", status="active", last_commit_date="2026-01-01")
        self.seed("newer", status="active", last_commit_date="2026-02-01")
        record, _ = self.seed("missing", status="active", last_commit_date="2026-03-01")
        record["path"] = str(self.root / "absent")
        self.assertEqual([item["name"] for item in self.session.visible(section="working")], ["newer", "older"])

    def test_settings_validation_preserves_unknown_fields_and_deduplicates_roots(self):
        self.session.settings["owner_setting"] = "keep"
        self.session.save_settings([str(self.root), str(self.root)], 3, "opencode", "")
        self.assertEqual(self.session.settings["roots"], [str(self.root)])
        self.assertEqual(self.session.settings["owner_setting"], "keep")
        with self.assertRaises(ValueError):
            self.session.save_settings([str(self.root / "missing")], 4, "opencode", "")
        self.assertEqual(self.session.settings["depth"], 3)

    def test_workspace_metadata_survives_scan_and_curation(self):
        record, target = self.seed()
        workspace = {"workspace_id": "preserved", "name": "Existing", "members": []}
        store.save_projects(self.session.records, workspaces=[workspace])
        self.session.save_curation(target, "paused", False, "")
        self.session.accept_scan(ScanOutcome(self.session.records, []))
        self.assertEqual(json.loads(store.REPOS_FILE.read_text(encoding="utf-8"))["workspaces"], [workspace])

    def test_registry_block_blocks_scan_curation_and_notes(self):
        record, target = self.seed()
        self.session.report["write_blocked"] = True
        for action in (self.session.scan,
                       lambda: self.session.save_curation(target, "active", True, ""),
                       lambda: self.session.save_note(target, "blocked")):
            with self.assertRaises(OSError):
                action()

    def test_stale_launcher_is_rejected_before_execution(self):
        record, target = self.seed()
        with mock.patch("repo_manager.launchers.detect_commands", return_value=[]), \
                mock.patch("repo_manager.launchers.run_command") as run:
            with self.assertRaises(ValueError):
                self.session.run_launcher(target, {"label": "old", "type": "bat"})
        run.assert_not_called()

    def test_unavailable_launcher_preserves_reason(self):
        record, target = self.seed()
        command = {"label": "test", "healthy": False, "reason": "missing runtime"}
        with mock.patch("repo_manager.launchers.detect_commands", return_value=[command]), \
                mock.patch("repo_manager.launchers.run_command") as run:
            with self.assertRaisesRegex(OSError, "missing runtime"):
                self.session.run_launcher(target, command)
        run.assert_not_called()

    def test_available_launcher_uses_existing_execution_boundary(self):
        record, target = self.seed()
        command = {"label": "test", "healthy": True}
        with mock.patch("repo_manager.launchers.detect_commands", return_value=[command]), \
                mock.patch("repo_manager.launchers.run_command") as run:
            self.session.run_launcher(target, command)
        run.assert_called_once_with(command, self.session.settings)


if __name__ == "__main__":
    unittest.main()
