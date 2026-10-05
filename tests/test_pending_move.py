"""R2.6C: durable pending_move provenance across F5/restart.

The originating scan persists causal counterpart provenance on the OLD
Project; later scans rehydrate an actionable suggestion from the exact
project_id relation (never fingerprint heuristics). Transient uncertainty
defers, contradictions invalidate, curated targets conflict safely.
"""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from repo_manager import scanner
from repo_manager.relocation import (detach_pending_for_keep_both, move_outcome,
                               perform_confirmed_move)
from repo_manager import store
from repo_manager.projects import repository_path_key
from tests.git_repository import GIT_EXE, create_repository


NOW = "2026-09-28T12:00:00Z"
TS = "2026-09-28T12:00:00Z"


def _isolate_store(testcase, base):
    app_dir = Path(base) / "app"
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


def _valid_pending(new_id="target-id", new_path=r"C:\work\New",
                   remotes=None, roots=None, category="strong"):
    return {"new_project_id": new_id, "new_path": new_path,
            "identity": {"remotes": list(remotes or []),
                         "root_commits": list(roots or ["abc"])},
            "category": category, "detected_at": TS}


def _old_record(path=r"C:\work\Old", pending=None, **overrides):
    record = {"path": path, "name": Path(path).name, "status": "active",
              "focus": "keep", "pinned": True, "project_id": "old-stable-id",
              "added_at": "2026-01-01T00:00:00Z",
              "last_seen": "2026-09-27T00:00:00Z"}
    if pending is not None:
        record["pending_move"] = pending
    record.update(overrides)
    return record


def _pristine_target(path, target_id):
    return {"path": path, "name": Path(path).name,
            "status": "idea", "focus": "", "pinned": False,
            "project_id": target_id, "added_at": NOW, "last_seen": NOW}


def _live_meta(roots, remotes=None, broken=False):
    return {"fingerprint": {"remotes": list(remotes or []),
                            "root_commits": list(roots)},
            "broken": broken}


def _rehydrate(projects, scanned, metas, observeds, suppressions=None):
    keys = {repository_path_key(p) for p in scanned}
    meta_by_key = {repository_path_key(p): m
                   for p, m in zip(scanned, metas)}
    obs_by_key = {repository_path_key(p): o
                  for p, o in zip(scanned, observeds)}
    return scanner.rehydrate_pending_moves(
        projects, keys, meta_by_key, obs_by_key, suppressions)


class PendingValidationTests(unittest.TestCase):
    def _validate(self, record):
        payload = {"schema_version": store.SCHEMA_VERSION,
                   "projects": [record]}
        records, issues = store.validate_registry(payload)
        return records, issues

    def test_shape_round_trip_preserved_exactly(self):
        pending = _valid_pending(remotes=["GitHub.com/O/R"], roots=["b", "a"])
        record = _old_record(pending_move=copy.deepcopy(pending))
        records, issues = self._validate(record)
        self.assertEqual(issues, [])
        self.assertEqual(records[0]["pending_move"]["new_project_id"],
                         "target-id")
        # Normalized to the live-identity shape (sorted, casefolded remotes).
        self.assertEqual(records[0]["pending_move"]["identity"],
                         {"remotes": ["github.com/o/r"],
                          "root_commits": ["a", "b"]})

    def test_malformed_variants_fail_closed(self):
        base = _valid_pending()
        variants = {
            "non-object": "relocate",
            "list": [],
            "empty id": {**base, "new_project_id": "  "},
            "non-string id": {**base, "new_project_id": 7},
            "missing id": {k: v for k, v in base.items()
                           if k != "new_project_id"},
            "self id": {**base, "new_project_id": "old-stable-id"},
            "empty path": {**base, "new_path": ""},
            "non-string path": {**base, "new_path": 42},
            "missing path": {k: v for k, v in base.items()
                             if k != "new_path"},
            "self path": {**base, "new_path": r"C:\work\Old"},
            "missing identity": {k: v for k, v in base.items()
                                 if k != "identity"},
            "non-object identity": {**base, "identity": []},
            "non-list remotes": {**base, "identity": {
                "remotes": "x", "root_commits": []}},
            "non-string root": {**base, "identity": {
                "remotes": [], "root_commits": [None]}},
            "ambiguous category": {**base, "category": "ambiguous"},
            "unknown category": {**base, "category": "maybe"},
            "missing category": {k: v for k, v in base.items()
                                 if k != "category"},
            "missing detected": {k: v for k, v in base.items()
                                 if k != "detected_at"},
            "non-string detected": {**base, "detected_at": 20260928},
            "bad detected": {**base,
                             "detected_at": "2026-13-99T99:99:99Z"},
        }
        for label, pending in variants.items():
            with self.subTest(variant=label):
                record = _old_record(pending_move=pending)
                records, issues = self._validate(record)
                self.assertEqual(len(records), 1)
                self.assertNotIn("pending_move", records[0])
                self.assertTrue(
                    any("pending_move" in issue for issue in issues),
                    issues)
                # The owning record and its curation survive the strip.
                self.assertEqual(records[0]["status"], "active")
                self.assertEqual(records[0]["project_id"],
                                 "old-stable-id")

    def test_save_then_load_strips_malformed_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            _isolate_store(self, tmp)
            record = _old_record(
                pending_move={"new_project_id": "x"})  # malformed
            store.save_projects([record])
            loaded, report = store.read_registry()
            self.assertEqual(len(loaded), 1)
            self.assertNotIn("pending_move", loaded[0])
            self.assertTrue(any("pending_move" in reason
                                for reason in report["reasons"]))


class PendingRehydrateUnitTests(unittest.TestCase):
    OLD = r"C:\work\Old"
    NEW = r"C:\work\New"

    def _projects(self, pending, target):
        return [_old_record(self.OLD, pending_move=copy.deepcopy(pending)),
                copy.deepcopy(target)]

    def test_unobserved_identity_defers_with_pending_retained(self):
        pending = _valid_pending(new_id="tid", new_path=self.NEW,
                                 roots=["r1"])
        projects = self._projects(
            pending, _pristine_target(self.NEW, "tid"))
        suggestions, conflicts = _rehydrate(
            projects, [self.NEW], [_live_meta(["r1"])],
            [frozenset({"branch"})])  # fingerprint unobserved
        self.assertEqual((suggestions, conflicts), ([], []))
        self.assertEqual(projects[0]["pending_move"]["new_project_id"],
                         "tid")

    def test_unscanned_target_defers_silently(self):
        pending = _valid_pending(new_id="tid", new_path=self.NEW,
                                 roots=["r1"])
        projects = self._projects(
            pending, _pristine_target(self.NEW, "tid"))
        suggestions, conflicts = _rehydrate(projects, [], [], [])
        self.assertEqual((suggestions, conflicts), ([], []))
        self.assertIn("pending_move", projects[0])

    def test_missing_target_with_scanned_path_invalidates(self):
        pending = _valid_pending(new_id="gone-id", new_path=self.NEW,
                                 roots=["r1"])
        projects = [_old_record(self.OLD,
                                pending_move=copy.deepcopy(pending)),
                    _pristine_target(self.NEW, "other-id")]
        suggestions, conflicts = _rehydrate(
            projects, [self.NEW], [_live_meta(["r1"])],
            [frozenset({"fingerprint", "remotes"})])
        self.assertEqual((suggestions, conflicts), ([], []))
        self.assertNotIn("pending_move", projects[0])
        self.assertEqual(len(projects), 2)

    def test_missing_target_unscanned_defers(self):
        pending = _valid_pending(new_id="gone-id", new_path=self.NEW,
                                 roots=["r1"])
        projects = [_old_record(self.OLD,
                                pending_move=copy.deepcopy(pending))]
        suggestions, conflicts = _rehydrate(projects, [], [], [])
        self.assertEqual((suggestions, conflicts), ([], []))
        self.assertIn("pending_move", projects[0])

    def test_target_moved_path_invalidates(self):
        pending = _valid_pending(new_id="tid", new_path=self.NEW,
                                 roots=["r1"])
        target = _pristine_target(r"C:\work\Elsewhere", "tid")
        projects = self._projects(pending, target)
        suggestions, conflicts = _rehydrate(
            projects, [r"C:\work\Elsewhere"], [_live_meta(["r1"])],
            [frozenset({"fingerprint", "remotes"})])
        self.assertEqual((suggestions, conflicts), ([], []))
        self.assertNotIn("pending_move", projects[0])

    def test_old_reappears_invalidates(self):
        pending = _valid_pending(new_id="tid", new_path=self.NEW,
                                 roots=["r1"])
        projects = self._projects(
            pending, _pristine_target(self.NEW, "tid"))
        suggestions, conflicts = _rehydrate(
            projects, [self.OLD, self.NEW],
            [_live_meta(["r1"]), _live_meta(["r1"])],
            [frozenset({"fingerprint", "remotes"})] * 2)
        self.assertEqual((suggestions, conflicts), ([], []))
        self.assertNotIn("pending_move", projects[0])
        self.assertEqual(len(projects), 2)

    def test_suppression_wins_over_pending(self):
        pending = _valid_pending(new_id="tid", new_path=self.NEW,
                                 roots=["r1"])
        projects = self._projects(
            pending, _pristine_target(self.NEW, "tid"))
        suggestions, conflicts = _rehydrate(
            projects, [self.NEW], [_live_meta(["r1"])],
            [frozenset({"fingerprint", "remotes"})],
            [{"old": self.OLD.lower(), "new": self.NEW.lower()}])
        self.assertEqual((suggestions, conflicts), ([], []))
        self.assertNotIn("pending_move", projects[0])

    def test_identity_conflict_invalidates(self):
        pending = _valid_pending(new_id="tid", new_path=self.NEW,
                                 roots=["r1"])
        projects = self._projects(
            pending, _pristine_target(self.NEW, "tid"))
        suggestions, conflicts = _rehydrate(
            projects, [self.NEW], [_live_meta(["changed"])],
            [frozenset({"fingerprint", "remotes"})])
        self.assertEqual((suggestions, conflicts), ([], []))
        self.assertNotIn("pending_move", projects[0])

    def test_curated_target_conflicts_and_retains(self):
        pending = _valid_pending(new_id="tid", new_path=self.NEW,
                                 roots=["r1"])
        target = _pristine_target(self.NEW, "tid")
        target["focus"] = "user curation"
        projects = self._projects(pending, target)
        suggestions, conflicts = _rehydrate(
            projects, [self.NEW], [_live_meta(["r1"])],
            [frozenset({"fingerprint", "remotes"})])
        self.assertEqual(suggestions, [])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["category"], "ambiguous")
        self.assertNotIn("new_project_id", conflicts[0])
        self.assertIn("pending_move", projects[0])
        self.assertEqual(projects[1]["focus"], "user curation")

    def test_legacy_fully_observed_counts_as_observed(self):
        pending = _valid_pending(new_id="tid", new_path=self.NEW,
                                 roots=["r1"])
        projects = self._projects(
            pending, _pristine_target(self.NEW, "tid"))
        suggestions, conflicts = _rehydrate(
            projects, [self.NEW], [_live_meta(["r1"])], [None])
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(conflicts, [])
        self.assertEqual(suggestions[0]["new_project_id"], "tid")
        self.assertEqual(suggestions[0]["identity"],
                         {"remotes": [], "root_commits": ["r1"]})

    def test_chain_never_collapses(self):
        a = _old_record(r"C:\r\A", pending_move=_valid_pending(
            new_id="id-b", new_path=r"C:\r\B", roots=["rB"]))
        b = _old_record(r"C:\r\B", pending_move=_valid_pending(
            new_id="id-c", new_path=r"C:\r\C", roots=["rC"]),
            project_id="id-b", status="idea", focus="", pinned=False,
            name="B")
        c = _pristine_target(r"C:\r\C", "id-c")
        projects = [a, b, c]
        suggestions, conflicts = _rehydrate(
            projects, [r"C:\r\C"], [_live_meta(["rC"])],
            [frozenset({"fingerprint", "remotes"})])
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["old_path"], r"C:\r\B")
        self.assertEqual(suggestions[0]["new_project_id"], "id-c")
        self.assertIn("pending_move", projects[0])  # A->B retained
        self.assertIn("pending_move", projects[1])  # B->C retained
        self.assertFalse(any(s.get("old_path") == r"C:\r\A"
                             for s in suggestions))


@unittest.skipUnless(GIT_EXE, "Git executable is not available")
class PendingLiveFlowTests(unittest.TestCase):
    """Temp-only real-Git flows for F5/restart/identity/offline semantics."""

    def _originating(self, tmp, old_name="OldRepo", new_name="NewRepo",
                     roots=("r",)):
        old_dir = str(Path(tmp) / roots[0] / old_name)
        new_dir = str(Path(tmp) / roots[-1] / new_name)
        for parent in {str(Path(old_dir).parent),
                       str(Path(new_dir).parent)}:
            os.makedirs(parent, exist_ok=True)
        _isolate_store(self, str(Path(tmp) / "store-iso"))
        create_repository(Path(old_dir))
        projects, _ = scanner.merge_scan([], [old_dir], None)
        old = next(p for p in projects if p["path"] == old_dir)
        old.update({"status": "active", "focus": "keep", "pinned": True,
                    "name": Path(old_dir).name})
        old_id = old["project_id"]
        os.rename(old_dir, new_dir)
        projects2, problems = scanner.merge_scan(projects, [new_dir], None)
        return projects2, problems, old_id, old_dir, new_dir

    def _actionable(self, problems):
        return [s for s in problems if s.get("kind") == "move"
                and s.get("category") in ("strong", "possible")
                and "new_path" in s and "new_project_id" in s]

    def test_f5_before_accept_rehydrates_and_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects2, _, old_id, old_dir, new_dir = self._originating(tmp)
            old2 = next(p for p in projects2 if p["project_id"] == old_id)
            self.assertIn("pending_move", old2)
            # F5: scan again with identical inputs.
            projects3, problems3 = scanner.merge_scan(projects2, [new_dir],
                                                      None)
            sug = self._actionable(problems3)
            self.assertEqual(len(sug), 1)
            self.assertEqual(sug[0]["new_project_id"],
                             old2["pending_move"]["new_project_id"])
            saves = []
            result = move_outcome(
                projects3, sug[0], NOW, lambda: saves.append(1),
                store.move_note, path_exists=os.path.exists,
                target_identity=scanner.move_target_identity)
            self.assertEqual(result.status, "migrated")
            self.assertEqual([p["project_id"] for p in projects3], [old_id])
            self.assertNotIn("pending_move", result.entry)

    def test_restart_before_accept_rehydrates_and_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects2, _, old_id, old_dir, new_dir = self._originating(tmp)
            store.save_projects(projects2)
            loaded, report = store.read_registry()
            self.assertEqual(report["status"], "valid")
            reloaded_old = next(p for p in loaded
                                if p["project_id"] == old_id)
            self.assertIn("pending_move", reloaded_old)
            # Restart: fresh process state, same durable registry + scan.
            projects3, problems3 = scanner.merge_scan(loaded, [new_dir],
                                                      None)
            sug = self._actionable(problems3)
            self.assertEqual(len(sug), 1)
            saves = []
            result = move_outcome(
                projects3, sug[0], NOW, lambda: saves.append(1),
                store.move_note, path_exists=os.path.exists,
                target_identity=scanner.move_target_identity)
            self.assertEqual(result.status, "migrated")
            self.assertEqual([p["project_id"] for p in projects3], [old_id])
            self.assertNotIn("pending_move", result.entry)
            # Durable post-success state carries no pending relation.
            store.save_projects(projects3)
            reloaded, _ = store.read_registry()
            self.assertEqual(len(reloaded), 1)
            self.assertNotIn("pending_move", reloaded[0])

    def test_cross_root_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects2, _, old_id, old_dir, new_dir = self._originating(
                tmp, roots=("root-a", "root-b"))
            store.save_projects(projects2)
            loaded, _ = store.read_registry()
            projects3, problems3 = scanner.merge_scan(loaded, [new_dir],
                                                      None)
            sug = self._actionable(problems3)
            self.assertEqual(len(sug), 1)
            result = move_outcome(
                projects3, sug[0], NOW, lambda: None,
                store.move_note, path_exists=os.path.exists,
                target_identity=scanner.move_target_identity)
            self.assertEqual(result.status, "migrated")
            self.assertEqual(result.entry["project_id"], old_id)

    def test_offline_second_root_defers(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects2, _, old_id, old_dir, new_dir = self._originating(tmp)
            third = Path(tmp) / "other" / "Third"
            create_repository(third)
            # Only an unrelated root is scanned: both sides unscanned.
            projects3, problems3 = scanner.merge_scan(
                projects2, [str(third)], None)
            moves = [p for p in problems3 if p.get("kind") == "move"]
            self.assertEqual(moves, [])
            kept = next(p for p in projects3 if p["project_id"] == old_id)
            self.assertIn("pending_move", kept)

    def test_old_reappears_invalidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects2, _, old_id, old_dir, new_dir = self._originating(tmp)
            create_repository(Path(old_dir))
            projects3, problems3 = scanner.merge_scan(
                projects2, [old_dir, new_dir], None)
            moves = [p for p in problems3 if p.get("kind") == "move"]
            self.assertEqual(moves, [])
            kept = next(p for p in projects3 if p["project_id"] == old_id)
            self.assertNotIn("pending_move", kept)
            self.assertEqual(len(projects3), 2)

    def test_target_replaced_invalidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects2, _, old_id, old_dir, new_dir = self._originating(tmp)
            os.rename(new_dir, new_dir + "-aside")
            create_repository(Path(new_dir))
            projects3, problems3 = scanner.merge_scan(
                projects2, [new_dir], None)
            moves = [p for p in problems3 if p.get("kind") == "move"]
            self.assertEqual(moves, [])
            kept = next(p for p in projects3 if p["project_id"] == old_id)
            self.assertNotIn("pending_move", kept)

    def test_pristine_clone_gets_no_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "FirstRepo"
            create_repository(first)
            import subprocess
            subprocess.run(
                ["git", "clone", "-q", str(first),
                 str(Path(tmp) / "Clone")],
                check=True, capture_output=True, timeout=30,
                stdin=subprocess.DEVNULL)
            projects, _ = scanner.merge_scan(
                [], [str(first), str(Path(tmp) / "Clone")], None)
            self.assertEqual(len(projects), 2)
            for record in projects:
                self.assertNotIn("pending_move", record)

    def test_backup_recovery_preserves_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            _isolate_store(self, tmp)
            old = _old_record(r"C:\work\Old",
                              pending_move=_valid_pending())
            target = _pristine_target(r"C:\work\New", "target-id")
            store.save_projects([old, target])
            store.save_projects([old, target])  # bak1 = valid state
            store.REPOS_FILE.write_bytes(b"{corrupt")
            loaded, report = store.read_registry()
            self.assertEqual(report["status"], "recovered")
            reloaded_old = next(p for p in loaded
                                if p["project_id"] == "old-stable-id")
            self.assertEqual(reloaded_old["pending_move"]["new_project_id"],
                             "target-id")






class ChainSafetyTests(unittest.TestCase):
    def test_existing_different_pending_never_overwritten(self):
        old = _old_record(r"C:\work\A", pending_move=_valid_pending(
            new_id="id-b", new_path=r"C:\work\B"))
        target = _pristine_target(r"C:\work\C", "id-c")
        projects = [old, target]
        by_path = {repository_path_key(p["path"]): p for p in projects}
        scanner.persist_pending_moves(
            by_path,
            [{"kind": "move", "category": "strong",
              "old_path": r"C:\work\A", "new_path": r"C:\work\C",
              "name": "A", "evidence": [],
              "identity": {"remotes": [], "root_commits": ["r"]},
              "new_project_id": "id-c"}],
            {repository_path_key(r"C:\work\C"): frozenset(
                {"fingerprint", "remotes"})},
            NOW)
        self.assertEqual(old["pending_move"]["new_project_id"], "id-b")

    def test_chain_old_reappears(self):
        a = _old_record(r"C:\r\A", pending_move=_valid_pending(
            new_id="id-b", new_path=r"C:\r\B", roots=["rB"]))
        b = _old_record(r"C:\r\B", project_id="id-b",
                        status="idea", focus="", pinned=False, name="B")
        projects = [a, b]
        # A reappears: scan A only; B unscanned and present by id.
        suggestions, conflicts = _rehydrate(
            projects, [r"C:\r\A"], [_live_meta(["rB"])],
            [frozenset({"fingerprint", "remotes"})])
        self.assertEqual((suggestions, conflicts), ([], []))
        self.assertNotIn("pending_move", projects[0])
        self.assertEqual(len(projects), 2)


if __name__ == "__main__":
    unittest.main()
