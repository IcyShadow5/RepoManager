import copy
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from repo_manager import scanner, relocation as move_review, relocation as main, store


class ManualMoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old = str(self.root / "old" / "example")
        self.new = str(self.root / "new" / "example")
        self.other = str(self.root / "other" / "example")
        self.identity = {"remotes": ["host/repo"], "root_commits": ["root"]}
        self.source = {"project_id": "source", "path": self.old, "name": "example", "focus": "mine"}
        self.target = {"project_id": "fresh", "path": self.new, "name": "example",
                       "status": "idea", "focus": "", "pinned": False}
        self.records = [self.source, self.target]
        self.groups = scanner.match_move_candidates(
            [dict(self.source, fingerprint=self.identity)],
            [(self.new, self.identity), (self.other, self.identity)])
        scanner.annotate_counterpart_provenance(self.groups, {main.repository_path_key(self.new): "fresh"})
        # Isolate note lookup even when no persistence operation is exercised.
        patches = [mock.patch.object(store, "NOTES_DIR", self.root / "notes")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_group_stays_ambiguous_but_preserves_proven_pair(self):
        group = self.groups[0]
        self.assertEqual(group["category"], "ambiguous")
        self.assertNotIn("new_project_id", group)
        pair = next(index for index, candidate in enumerate(group["candidates"]) if candidate["new_path"] == self.new)
        approved = move_review.approve_candidate(group, pair, self.records)
        self.assertEqual(approved["new_project_id"], "fresh")
        self.assertEqual(approved["old_project_id"], "source")
        before = copy.deepcopy(self.records)
        self.assertEqual(self.records, before)
        outcome, entry = main.perform_confirmed_move(self.records, approved, "now", lambda: None,
                                                     lambda *args: "none", path_exists=lambda path: path != self.old,
                                                     target_identity=lambda path: self.identity)
        self.assertEqual(outcome, main.MOVE_OK)
        self.assertEqual(entry["project_id"], "source")
        self.assertEqual(entry["focus"], "mine")

    def test_missing_pair_provenance_never_infers_occupant_id(self):
        group = copy.deepcopy(self.groups[0])
        for pair in group["candidates"]:
            pair.pop("new_project_id", None)
        index = next(index for index, pair in enumerate(group["candidates"]) if pair["new_path"] == self.new)
        with self.assertRaises(ValueError):
            move_review.approve_candidate(group, index, self.records)
        with self.assertRaises(ValueError):
            move_review.approve_candidate({"category": "ambiguous"}, 0, self.records)

    def test_source_replacement_identity_blocks_transaction(self):
        group = self.groups[0]
        index = next(index for index, pair in enumerate(group["candidates"]) if pair["new_path"] == self.new)
        approved = move_review.approve_candidate(group, index, self.records)
        self.source["project_id"] = "replacement"
        outcome, _ = main.perform_confirmed_move(self.records, approved, "now", lambda: None,
                                                 lambda *args: "none", path_exists=lambda path: path != self.old)
        self.assertEqual(outcome, main.MOVE_STALE_TARGET)

    def test_curated_counterpart_is_never_absorbed(self):
        index = next(index for index, pair in enumerate(self.groups[0]["candidates"]) if pair["new_path"] == self.new)
        approved = move_review.approve_candidate(self.groups[0], index, self.records)
        self.target["focus"] = "owned"
        outcome, _ = main.perform_confirmed_move(self.records, approved, "now", lambda: None,
                                                 lambda *args: "none", path_exists=lambda path: path != self.old)
        self.assertEqual(outcome, main.MOVE_DUPLICATE)
        self.assertEqual(len(self.records), 2)

    def test_retirement_keeps_unresolved_contested_sources(self):
        pair_a = {"old_path": "old-a", "new_path": "new"}
        pair_b = {"old_path": "old-b", "new_path": "new"}
        groups = [{"category": "ambiguous", "candidates": [pair_a, pair_b]}]
        remaining = move_review.retire_accepted_pair(groups, pair_a)
        self.assertEqual(remaining[0]["candidates"], [pair_b])
        self.assertEqual(remaining[0]["category"], "ambiguous")


if __name__ == "__main__":
    unittest.main()
