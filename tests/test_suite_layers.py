"""A changed suite must not silently inherit current-product release status."""
import json
from pathlib import Path
import tempfile
import unittest
from tests import run_layers


class SuiteGateTests(unittest.TestCase):
    def test_unclassified_test_blocks_the_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tests").mkdir()
            (root / "tests/test_new.py").write_text("class NewTests:\n    def test_contract(self): pass\n", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1, "groups": []}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unclassified tests"):
                run_layers.catalog(root, manifest)

    def test_current_gate_excludes_all_legacy_and_auxiliary_groups(self):
        entries = run_layers.catalog()
        current = run_layers.select(entries, "current")
        self.assertTrue(current)
        self.assertTrue(all(group["category"].startswith("CURRENT_") and group["gate"] for group in current.values()))
        self.assertFalse(any(identity.startswith("tests.legacy.") for identity in current))
        self.assertFalse(set(current) & set(run_layers.select(entries, "auxiliary")))
        self.assertTrue(run_layers.select(entries, "legacy"))

    def test_deleted_or_duplicate_classification_blocks_the_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tests").mkdir()
            manifest = root / "manifest.json"
            group = {"module": "tests.test_missing", "class": "MissingTests", "category": "CURRENT_CORE", "tests": ["test_missing"]}
            for groups, error in (([group], "absent tests"), ([group, group], "duplicate classification")):
                with self.subTest(error=error):
                    manifest.write_text(json.dumps({"schema_version": 1, "groups": groups}), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, error):
                        run_layers.catalog(root, manifest)
