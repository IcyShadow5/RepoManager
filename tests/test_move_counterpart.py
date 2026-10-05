"""R2.6B: confirmed moves absorb the scan-created counterpart.

A filesystem rename leaves the curated Project stale while the scan creates
a fresh default record at the new path plus an advisory suggestion. Accepting
the suggestion must migrate the ORIGINAL logical Project (stable ID and
curation) and remove only the proven-disposable counterpart. Foreign or
curated occupants stay protected behind MOVE_DUPLICATE.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import scanner
from repo_manager.relocation import move_outcome, perform_confirmed_move
from repo_manager import store
from repo_manager.projects import repository_path_key
from tests.git_repository import GIT_EXE, create_repository


NOW = "2026-09-27T12:00:00Z"


def _isolate_store(testcase, base):
    app_dir = base / "app"
    patcher = mock.patch.multiple(
        store,
        APP_DIR=app_dir,
        REPOS_FILE=app_dir / "repos.json",
        SETTINGS_FILE=app_dir / "settings.json",
        NOTES_DIR=app_dir / "notes",
    )
    patcher.start()
    store.ensure_dirs()
    testcase.addCleanup(patcher.stop)


def _curated_old(path, name="OldRepo"):
    return {"path": path, "name": name, "status": "active",
            "focus": "my focus", "pinned": True,
            "project_id": "original-stable-id",
            "added_at": "2026-01-01T00:00:00Z",
            "last_seen": "2026-09-26T00:00:00Z"}


def _fresh_counterpart(path, counterpart_id, name=None):
    return {"path": path,
            "name": name or Path(path).name,
            "status": "idea", "focus": "", "pinned": False,
            "project_id": counterpart_id,
            "added_at": NOW, "last_seen": NOW}


def _suggestion(old_path, new_path, new_project_id=None):
    suggestion = {"kind": "move", "category": "strong",
                  "old_path": old_path, "new_path": new_path,
                  "name": Path(old_path).name,
                  "evidence": ["Root history matches (1)"],
                  "identity": {"remotes": [], "root_commits": ["abc"]}}
    if new_project_id is not None:
        suggestion["new_project_id"] = new_project_id
    return suggestion


@unittest.skipUnless(GIT_EXE, "Git executable is not available")
class CounterpartProvenanceTests(unittest.TestCase):
    """merge_scan annotates only unambiguous suggestions with provenance."""

    def test_rename_produces_provenance_matching_counterpart(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "OldRepo"
            create_repository(old)
            projects, _ = scanner.merge_scan([], [str(old)], None)
            old_id = projects[0]["project_id"]
            new = Path(tmp) / "NewRepo"
            os.rename(old, new)
            projects2, problems = scanner.merge_scan(
                projects, [str(new)], None)
            sugs = [p for p in problems if p.get("kind") == "move"
                    and p.get("category") in ("strong", "possible")
                    and "new_path" in p]
            self.assertEqual(len(sugs), 1)
            counterpart = next(
                p for p in projects2 if p["path"] == str(new))
            self.assertEqual(sugs[0]["new_project_id"],
                             counterpart["project_id"])
            self.assertNotEqual(counterpart["project_id"], old_id)

    def test_ambiguous_group_gains_no_counterpart_identity(self):
        strong = {"kind": "move", "category": "strong",
                  "old_path": r"C:\work\OldA", "new_path": r"C:\work\New",
                  "name": "OldA", "evidence": []}
        possible = {"kind": "move", "category": "possible",
                    "old_path": r"C:\work\OldB", "new_path": r"C:\work\New",
                    "name": "OldB", "evidence": []}
        ambiguous_new = {"kind": "move", "category": "ambiguous",
                         "old_paths": [r"C:\work\OldA", r"C:\work\OldB"],
                         "new_path": r"C:\work\New",
                         "name": "New",
                         "evidence": ["Matches multiple vanished entries"]}
        ambiguous_old = {"kind": "move", "category": "ambiguous",
                         "old_path": r"C:\work\OldA",
                         "new_paths": [r"C:\work\New1", r"C:\work\New2"],
                         "name": "OldA",
                         "evidence": ["Multiple candidate locations"]}
        unknown = {"kind": "move", "category": "strong",
                   "old_path": r"C:\work\OldC", "new_path": r"C:\work\Else",
                   "name": "OldC", "evidence": []}
        suggestions = [strong, possible, ambiguous_new, ambiguous_old,
                       unknown, None, "not-a-dict"]
        created = {repository_path_key(r"C:\work\New"): "cp-id"}
        scanner.annotate_counterpart_provenance(suggestions, created)
        self.assertEqual(strong["new_project_id"], "cp-id")
        self.assertEqual(possible["new_project_id"], "cp-id")
        self.assertNotIn("new_project_id", ambiguous_new)
        self.assertNotIn("new_project_id", ambiguous_old)
        self.assertNotIn("new_project_id", unknown)

    def test_second_scan_rehydrates_provenance(self):
        # R2.6C: once the counterpart is durable, a later scan matches by
        # path and creates nothing, but the durable pending relation is
        # rehydrated into exactly one actionable suggestion (no duplicates).
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "OldRepo"
            create_repository(old)
            projects, _ = scanner.merge_scan([], [str(old)], None)
            new = Path(tmp) / "NewRepo"
            os.rename(old, new)
            projects2, _ = scanner.merge_scan(projects, [str(new)], None)
            old2 = next(p for p in projects2 if p["path"] == str(old))
            self.assertIn("pending_move", old2)
            projects3, problems3 = scanner.merge_scan(
                projects2, [str(new)], None)
            moves = [p for p in problems3 if p.get("kind") == "move"
                     and p.get("category") in ("strong", "possible")]
            self.assertEqual(len(moves), 1)
            self.assertEqual(moves[0]["new_project_id"],
                             old2["pending_move"]["new_project_id"])
            self.assertEqual(moves[0]["old_path"], str(old))
            self.assertEqual(moves[0]["new_path"], str(new))
            self.assertEqual(len(projects3), 2)


class CounterpartAbsorptionTests(unittest.TestCase):
    OLD = r"C:\work\OldRepo"
    NEW = r"C:\work\NewRepo"

    def test_foreign_occupant_without_provenance_is_duplicate(self):
        projects = [_curated_old(self.OLD),
                    _fresh_counterpart(self.NEW, "foreign-id")]
        before = [dict(p) for p in projects]
        outcome, entry = perform_confirmed_move(
            projects, _suggestion(self.OLD, self.NEW), NOW,
            save_projects=self._fail, move_note=self._fail)
        self.assertEqual(outcome, "duplicate")
        self.assertIsNone(entry)
        self.assertEqual(projects, before)

    def test_foreign_occupant_with_mismatched_provenance_is_duplicate(self):
        projects = [_curated_old(self.OLD),
                    _fresh_counterpart(self.NEW, "foreign-id")]
        before = [dict(p) for p in projects]
        outcome, entry = perform_confirmed_move(
            projects,
            _suggestion(self.OLD, self.NEW, new_project_id="other-id"), NOW,
            save_projects=self._fail, move_note=self._fail)
        self.assertEqual(outcome, "duplicate")
        self.assertIsNone(entry)
        self.assertEqual(projects, before)

    def test_legacy_occupant_without_id_is_duplicate(self):
        projects = [_curated_old(self.OLD),
                    {"path": self.NEW, "name": "NewRepo"}]
        outcome, entry = perform_confirmed_move(
            projects,
            _suggestion(self.OLD, self.NEW, new_project_id="some-id"), NOW,
            save_projects=self._fail, move_note=self._fail)
        self.assertEqual(outcome, "duplicate")
        self.assertIsNone(entry)
        self.assertEqual(len(projects), 2)

    def test_curated_counterpart_is_duplicate_and_preserved(self):
        counterpart = _fresh_counterpart(self.NEW, "cp-id")
        counterpart["status"] = "active"
        projects = [_curated_old(self.OLD), counterpart]
        outcome, entry = perform_confirmed_move(
            projects,
            _suggestion(self.OLD, self.NEW, new_project_id="cp-id"), NOW,
            save_projects=self._fail, move_note=self._fail)
        self.assertEqual(outcome, "duplicate")
        self.assertIsNone(entry)
        self.assertEqual(len(projects), 2)
        self.assertEqual(projects[1]["status"], "active")

    def test_pinned_counterpart_is_duplicate_and_preserved(self):
        counterpart = _fresh_counterpart(self.NEW, "cp-id")
        counterpart["pinned"] = True
        projects = [_curated_old(self.OLD), counterpart]
        outcome, _ = perform_confirmed_move(
            projects,
            _suggestion(self.OLD, self.NEW, new_project_id="cp-id"), NOW,
            save_projects=self._fail, move_note=self._fail)
        self.assertEqual(outcome, "duplicate")
        self.assertEqual(len(projects), 2)

    def test_identity_revalidation_failure_preserves_both(self):
        projects = [_curated_old(self.OLD),
                    _fresh_counterpart(self.NEW, "cp-id")]
        outcome, entry = perform_confirmed_move(
            projects,
            _suggestion(self.OLD, self.NEW, new_project_id="cp-id"), NOW,
            save_projects=self._fail, move_note=self._fail,
            path_exists=lambda path: True,
            target_identity=lambda path: {"remotes": ["changed"],
                                          "root_commits": ["zzz"]})
        self.assertEqual(outcome, "stale_target")
        self.assertIsNone(entry)
        self.assertEqual(len(projects), 2)

    def test_absorb_happy_path_keeps_original_identity(self):
        projects = [_curated_old(self.OLD),
                    _fresh_counterpart(self.NEW, "cp-id")]
        calls = {"save": 0}
        outcome, entry = perform_confirmed_move(
            projects,
            _suggestion(self.OLD, self.NEW, new_project_id="cp-id"), NOW,
            save_projects=lambda: calls.__setitem__(
                "save", calls["save"] + 1),
            move_note=lambda *a: "absent")
        self.assertEqual(outcome, "migrated")
        self.assertEqual(len(projects), 1)
        self.assertEqual(entry["path"], self.NEW)
        self.assertEqual(entry["project_id"], "original-stable-id")
        self.assertEqual(entry["status"], "active")
        self.assertEqual(entry["focus"], "my focus")
        self.assertTrue(entry["pinned"])
        self.assertEqual(entry["moved_from"], self.OLD)
        self.assertEqual(entry["last_seen"], NOW)
        self.assertFalse(any(p.get("project_id") == "cp-id"
                             for p in projects))

    def test_save_failure_restores_both_records(self):
        old = _curated_old(self.OLD)
        counterpart = _fresh_counterpart(self.NEW, "cp-id")
        projects = [old, counterpart]
        old_snapshot = dict(old)

        def failing_save():
            raise OSError("disk full")

        outcome, entry = perform_confirmed_move(
            projects,
            _suggestion(self.OLD, self.NEW, new_project_id="cp-id"), NOW,
            save_projects=failing_save, move_note=lambda *a: "absent")
        self.assertEqual(outcome, "rolled_back_registry")
        self.assertEqual(len(projects), 2)
        self.assertEqual(projects[0], old_snapshot)
        self.assertEqual(projects[1], counterpart)
        self.assertEqual(projects[1]["project_id"], "cp-id")

    def test_stable_note_failure_restores_counterpart(self):
        old = _curated_old(self.OLD)
        counterpart = _fresh_counterpart(self.NEW, "cp-id")
        projects = [old, counterpart]
        saves = []

        def failing_note(*args):
            raise OSError("rename locked")

        outcome, _ = perform_confirmed_move(
            projects,
            _suggestion(self.OLD, self.NEW, new_project_id="cp-id"), NOW,
            save_projects=lambda: saves.append(1),
            move_note=failing_note)
        self.assertEqual(outcome, "rolled_back_note")
        self.assertEqual(saves, [])
        self.assertEqual(len(projects), 2)
        self.assertEqual(projects[0]["path"], self.OLD)
        self.assertEqual(projects[1]["project_id"], "cp-id")

    def test_nonstable_note_failure_restores_counterpart(self):
        old = _curated_old(self.OLD)
        del old["project_id"]
        counterpart = _fresh_counterpart(self.NEW, "cp-id")
        projects = [old, counterpart]
        saves = []

        def failing_note(*args):
            raise OSError("rename locked")

        outcome, _ = perform_confirmed_move(
            projects,
            _suggestion(self.OLD, self.NEW, new_project_id="cp-id"), NOW,
            save_projects=lambda: saves.append(1),
            move_note=failing_note)
        self.assertEqual(outcome, "rolled_back_note")
        self.assertEqual(len(saves), 2)  # commit attempt + compensation
        self.assertEqual(len(projects), 2)
        self.assertEqual(projects[0]["path"], self.OLD)
        self.assertEqual(projects[1]["project_id"], "cp-id")

    def test_counterpart_note_blocks_absorption(self):
        with tempfile.TemporaryDirectory() as tmp:
            _isolate_store(self, Path(tmp))
            projects = [_curated_old(self.OLD),
                        _fresh_counterpart(self.NEW, "cp-id")]
            store.save_note("NewRepo", self.NEW, "counterpart note", "cp-id")
            outcome, entry = perform_confirmed_move(
                projects,
                _suggestion(self.OLD, self.NEW, new_project_id="cp-id"),
                NOW, save_projects=self._fail, move_note=self._fail)
            self.assertEqual(outcome, "duplicate")
            self.assertIsNone(entry)
            self.assertEqual(len(projects), 2)

    def _fail(self, *a, **k):
        raise OSError("injected failure")


@unittest.skipUnless(GIT_EXE, "Git executable is not available")
class CounterpartEndToEndTests(unittest.TestCase):
    """A. simple rename and B. cross-root move on real Temp repositories."""

    def _rename_flow(self, old_dir, new_dir):
        base = Path(old_dir).parent
        _isolate_store(self, base / "store-iso")
        create_repository(Path(old_dir))
        projects, _ = scanner.merge_scan([], [str(old_dir)], None)
        old = next(p for p in projects if p["path"] == str(old_dir))
        old.update({"status": "active", "focus": "my focus", "pinned": True,
                    "name": Path(old_dir).name})
        old_id = old["project_id"]
        store.save_note(old["name"], str(old_dir), "keep me", old_id)
        os.rename(old_dir, new_dir)
        projects2, problems = scanner.merge_scan(projects, [str(new_dir)],
                                                 None)
        sug = next(p for p in problems
                   if p.get("kind") == "move"
                   and p.get("category") in ("strong", "possible")
                   and "new_path" in p)
        return projects2, sug, old_id

    def _accept_and_check(self, projects, suggestion, old_id, old_dir,
                          new_dir):
        saves = []
        result = move_outcome(
            projects, suggestion, NOW, lambda: saves.append(1),
            store.move_note, path_exists=os.path.exists,
            target_identity=scanner.move_target_identity)
        self.assertEqual(result.status, "migrated")
        entry = result.entry
        self.assertEqual(len(projects), 1)
        self.assertEqual(entry["path"], str(new_dir))
        self.assertEqual(entry["project_id"], old_id)
        self.assertEqual(entry["status"], "active")
        self.assertEqual(entry["focus"], "my focus")
        self.assertTrue(entry["pinned"])
        self.assertEqual(entry["moved_from"], str(old_dir))
        self.assertEqual(
            [p["project_id"] for p in projects], [old_id])
        self.assertEqual(
            store.load_note(entry["name"], str(new_dir), old_id), "keep me")
        return entry

    def test_simple_rename_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = str(Path(tmp) / "OldRepo")
            new_dir = str(Path(tmp) / "NewRepo")
            projects, sug, old_id = self._rename_flow(old_dir, new_dir)
            self.assertIn("new_project_id", sug)
            self._accept_and_check(projects, sug, old_id, old_dir, new_dir)

    def test_cross_root_move_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = str(Path(tmp) / "root-a" / "OldRepo")
            new_dir = str(Path(tmp) / "root-b" / "OldRepo")
            os.makedirs(Path(old_dir).parent, exist_ok=True)
            os.makedirs(Path(new_dir).parent, exist_ok=True)
            projects, sug, old_id = self._rename_flow(old_dir, new_dir)
            self.assertIn("new_project_id", sug)
            self._accept_and_check(projects, sug, old_id, old_dir, new_dir)


@unittest.skipUnless(GIT_EXE, "Git executable is not available")
class CloneSafetyTests(unittest.TestCase):
    """H. legitimate clones never auto-merge and never absorb."""

    def test_two_clones_produce_no_move_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "FirstRepo"
            create_repository(first)
            import subprocess
            subprocess.run(
                ["git", "clone", "-q", str(first), str(Path(tmp) / "Clone")],
                check=True, capture_output=True, timeout=30,
                stdin=subprocess.DEVNULL)
            projects, problems = scanner.merge_scan(
                [], [str(first), str(Path(tmp) / "Clone")], None)
            self.assertEqual(len(projects), 2)
            moves = [p for p in problems if p.get("kind") == "move"]
            self.assertEqual(moves, [])

    def test_clone_occupant_without_provenance_is_duplicate(self):
        old = _curated_old(r"C:\work\OldRepo")
        clone = _curated_old(r"C:\work\CloneRepo")
        clone["project_id"] = "clone-stable-id"
        projects = [old, clone]
        before = [dict(p) for p in projects]
        outcome, entry = perform_confirmed_move(
            projects, _suggestion(r"C:\work\OldRepo", r"C:\work\CloneRepo"),
            NOW, save_projects=self._fail, move_note=self._fail)
        self.assertEqual(outcome, "duplicate")
        self.assertIsNone(entry)
        self.assertEqual(projects, before)

    def _fail(self, *a, **k):
        raise OSError("injected failure")


if __name__ == "__main__":
    unittest.main()
