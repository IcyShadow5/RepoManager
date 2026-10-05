"""UA-05 cleanup classifier and apply-gate tests.

All tests operate on temporary directories. Real user data is never touched;
the real-data guarantees are covered by tests/test_ua05_isolation.py and the
external pre/post full-suite hash probe.
"""
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from repo_manager import store
from repo_manager import ua05_cleanup as cleanup


def _fixture(i, **overrides):
    record = {"path": rf"C:\repos\repo{i:05d}", "name": f"repo{i:05d}",
              "status": "idea", "focus": "", "pinned": i % 5 == 0,
              "remote": None, "branch": None, "project_id": f"uuid-{i:05d}",
              "broken": True, "repository_observed": False,
              "custom_extra": {"keep": True}}
    record.update(overrides)
    return record


def _make_registry(tmp, *, extra_records=(), backup_extra=None):
    app = tmp / "app"
    app.mkdir(parents=True)
    doc = {"schema_version": 2,
           "projects": [_fixture(i) for i in range(40)] + list(extra_records),
           "workspaces": [{"workspace_id": "w-1", "members": []}],
           "meta_future": {"untouched": True}}
    write_registry(app / "repos.json", doc)
    write_registry(app / "repos.json.bak1", doc)
    return app, doc


def write_registry(path, doc):
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _snapshot(app_dir):
    """Byte snapshot of every existing registry artifact (read-only)."""
    return {name: (app_dir / name).read_bytes()
            for name in cleanup.ARTIFACTS if (app_dir / name).exists()}


def _hashes_of_existing(app_dir):
    return {name: cleanup._sha256_bytes((app_dir / name).read_bytes())
            for name in cleanup.ARTIFACTS if (app_dir / name).exists()}


class SyntheticClassifierTests(unittest.TestCase):
    def test_exact_fixture_record_is_confirmed(self):
        classification, evidence = cleanup.classify_synthetic_record(
            _fixture(7))
        self.assertEqual(classification, cleanup.CONFIRMED)
        self.assertTrue(evidence["name_pattern"])
        self.assertTrue(evidence["path_pattern"])
        self.assertTrue(evidence["suffix_agrees"])

    def test_near_match_legitimate_record_is_not_confirmed(self):
        # Fixture-like name at a real user location must never be removed.
        record = _fixture(3, path=r"C:\Users\u\Desktop\repo00003")
        classification, _ = cleanup.classify_synthetic_record(record)
        self.assertEqual(classification, cleanup.AMBIGUOUS)

    def test_mismatched_name_path_suffix_is_ambiguous(self):
        record = _fixture(1, path=r"C:\repos\repo00002")
        classification, evidence = cleanup.classify_synthetic_record(record)
        self.assertEqual(classification, cleanup.AMBIGUOUS)
        self.assertFalse(evidence["suffix_agrees"])

    def test_ordinary_repos_dir_record_survives(self):
        record = {"name": "real-project", "path": r"C:\repos\real-project",
                  "status": "active"}
        classification, _ = cleanup.classify_synthetic_record(record)
        self.assertEqual(classification, cleanup.NOT_SYNTHETIC)

    def test_fixture_name_at_non_fixture_path_survives(self):
        record = {"name": "repo12345", "path": r"C:\Users\u\code\repo12345"}
        classification, _ = cleanup.classify_synthetic_record(record)
        self.assertEqual(classification, cleanup.AMBIGUOUS)


class CleanupManifestTests(unittest.TestCase):
    def test_analysis_confirms_exact_family_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_registry(Path(tmp))
            analysis = cleanup.analyze_artifact(app / "repos.json")
            self.assertEqual(analysis["status"], "valid")
            self.assertEqual(analysis["confirmed_count"], 40)
            self.assertTrue(analysis["family_matches_expected"])
            self.assertEqual(analysis["project_count"], 40)
            self.assertEqual(analysis["post_count"], 0)

    def test_ambiguous_and_normal_records_survive_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            extra = (
                {"name": "real-project", "path": r"C:\repos\real-project",
                 "status": "active"},
                {"name": "repo12345", "path": r"C:\Users\u\repo12345",
                 "status": "active"},
                {"name": "repo00039", "path": r"C:\repos\repo00040",
                 "status": "idea", "broken": True},
            )
            app, _ = _make_registry(Path(tmp), extra_records=extra)
            analysis = cleanup.analyze_artifact(app / "repos.json")
            self.assertEqual(analysis["status"], "valid")
            self.assertEqual(analysis["confirmed_count"], 40)
            self.assertEqual(analysis["ambiguous_count"], 2)
            expected = {
                name: cleanup._sha256_bytes((app / name).read_bytes())
                for name in cleanup.ARTIFACTS if (app / name).exists()}
            results, error = cleanup.cleanup_artifacts(app, expected)
            self.assertIsNone(error)
            doc = json.loads((app / "repos.json").read_text(encoding="utf-8"))
            names = [p["name"] for p in doc["projects"]]
            self.assertNotIn("repo00000", names)
            self.assertIn("real-project", names)
            self.assertIn("repo12345", names)
            self.assertIn("repo00039", names)

    def test_extra_family_member_blocks_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            extra = ({"name": "repo00040", "path": r"C:\repos\repo00040",
                      "status": "idea", "broken": True},)
            app, _ = _make_registry(Path(tmp), extra_records=extra)
            analysis = cleanup.analyze_artifact(app / "repos.json")
            self.assertEqual(analysis["status"], "family-mismatch")
            self.assertEqual(analysis["confirmed_count"], 41)
            expected = {
                "repos.json":
                    cleanup._sha256_bytes((app / "repos.json").read_bytes())}
            results, error = cleanup.cleanup_artifacts(app, expected)
            self.assertIsNotNone(error)
            self.assertIn("not cleanly analysable", error)
            doc = json.loads((app / "repos.json").read_text(encoding="utf-8"))
            self.assertEqual(len(doc["projects"]), 41)

    def test_dry_run_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_registry(Path(tmp))
            before = {p.name: p.read_bytes()
                      for p in app.glob("repos.json*")}
            cleanup.analyze_all(app)
            after = {p.name: p.read_bytes() for p in app.glob("repos.json*")}
            self.assertEqual(before, after)
            self.assertFalse(list(app.glob("ua05-preservation-*")))

    def test_apply_rejects_source_hash_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_registry(Path(tmp))
            sha = cleanup._sha256_bytes((app / "repos.json").read_bytes())
            # Mutate after the reviewed dry-run hash was captured.
            write_registry(app / "repos.json",
                           {"schema_version": 2, "projects": [], "workspaces": []})
            expected = {"repos.json": sha,
                        "repos.json.bak1":
                            cleanup._sha256_bytes(
                                (app / "repos.json.bak1").read_bytes())}
            results, error = cleanup.cleanup_artifacts(app, expected)
            self.assertIsNotNone(error)
            self.assertIn("ABORT - source changed", error)
            # primary must be untouched after abort
            self.assertEqual(len(json.loads(
                (app / "repos.json").read_text(encoding="utf-8"))["projects"]), 0)

    def test_apply_removes_only_manifest_ids_and_preserves_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            extra = (
                {"name": "real-project", "path": r"C:\repos\real-project",
                 "status": "active", "custom_key": "x"},
                {"name": "repo12345", "path": r"C:\Users\u\repo12345",
                 "status": "active"},
            )
            app, original = _make_registry(Path(tmp), extra_records=extra)
            expected = {
                name: cleanup._sha256_bytes((app / name).read_bytes())
                for name in cleanup.ARTIFACTS if (app / name).exists()}
            results, error = cleanup.cleanup_artifacts(app, expected)
            self.assertIsNone(error)
            cleaned = {r["artifact"]: r for r in results
                       if r.get("action") == "cleaned"}
            self.assertEqual(len(cleaned), 2)
            doc = json.loads((app / "repos.json").read_text(encoding="utf-8"))
            names = [p["name"] for p in doc["projects"]]
            self.assertNotIn("repo00000", names)
            self.assertNotIn("repo00039", names)
            self.assertIn("real-project", names)
            self.assertIn("repo12345", names)
            self.assertEqual(doc["workspaces"], original["workspaces"])
            self.assertEqual(doc["schema_version"], 2)
            self.assertEqual(doc["meta_future"], {"untouched": True})
            kept = {p["name"]: p for p in doc["projects"]}
            self.assertEqual(kept["real-project"]["custom_key"], "x")
            # original non-synthetic records are deep-equal
            original_kept = {p["name"]: p
                             for p in original["projects"]
                             if p["name"] in names}
            self.assertEqual(kept, original_kept)

    def test_apply_does_not_touch_invalid_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_registry(Path(tmp))
            broken = b"{not json"
            (app / "repos.json.bak2").write_bytes(broken)
            expected = {
                "repos.json":
                    cleanup._sha256_bytes((app / "repos.json").read_bytes()),
                "repos.json.bak1":
                    cleanup._sha256_bytes((app / "repos.json.bak1").read_bytes()),
                "repos.json.bak2":
                    cleanup._sha256_bytes(broken),
            }
            results, error = cleanup.cleanup_artifacts(app, expected)
            self.assertIsNone(error)
            self.assertEqual((app / "repos.json.bak2").read_bytes(), broken)
            actions = {r["artifact"]: r["action"] for r in results}
            self.assertEqual(actions["repos.json"], "cleaned")
            self.assertEqual(actions["repos.json.bak1"], "cleaned")
            self.assertEqual(actions["repos.json.bak2"], "left-untouched")

    def test_preservation_dir_hashes_match_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_registry(Path(tmp))
            (app / "settings.json").write_text(
                json.dumps({"roots": [r"C:\Users\u\Desktop"]}),
                encoding="utf-8")
            target, manifest = cleanup.preserve_originals(app)
            self.assertIsNotNone(target)
            for record in manifest["files"]:
                self.assertEqual(record["source_sha256"],
                                 record["copy_sha256"])
                self.assertTrue(Path(record["preserved"]).exists())


class TwoPhasePreflightTests(unittest.TestCase):
    """UA-05 hardening: every gate fires before any registry write.

    These are the residuals that failed independent review: a later
    source-hash/family/validation failure must never leave an earlier
    artifact already modified. Each preflight-abort test therefore proves all
    three registry artifacts stay byte-identical.
    """

    def test_later_hash_mismatch_causes_zero_registry_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, doc = _make_registry(Path(tmp))
            # Three valid contaminated artifacts; bak2 mirrors the primary.
            write_registry(app / "repos.json.bak2", doc)
            shas = {name: cleanup._sha256_bytes((app / name).read_bytes())
                    for name in cleanup.ARTIFACTS}
            # Correct hash for primary + bak2, incorrect hash for bak1.
            wrong = ("0" if shas["repos.json.bak1"][0] != "0" else "1") \
                + shas["repos.json.bak1"][1:]
            self.assertNotEqual(wrong, shas["repos.json.bak1"])
            expected = {"repos.json": shas["repos.json"],
                        "repos.json.bak1": wrong,
                        "repos.json.bak2": shas["repos.json.bak2"]}
            before = _snapshot(app)
            results, error = cleanup.cleanup_artifacts(app, expected)
            self.assertIsNotNone(error)
            self.assertIn("PRECHECK FAILURE", error)
            self.assertIn("source changed", error)
            self.assertIn("repos.json.bak1", error)
            self.assertEqual(results, [])
            # The earlier-verified primary must not have been rewritten.
            self.assertEqual(_snapshot(app), before)
            primary = json.loads(
                (app / "repos.json").read_text(encoding="utf-8"))
            self.assertEqual(len(primary["projects"]), 40)

    def test_later_family_mismatch_causes_zero_registry_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, doc = _make_registry(Path(tmp))
            # Primary: valid exact family. bak1: extra repo00040 member.
            extra = dict(doc)
            extra["projects"] = [_fixture(i) for i in range(40)] + [_fixture(40)]
            write_registry(app / "repos.json.bak1", extra)
            write_registry(app / "repos.json.bak2", doc)
            expected = _hashes_of_existing(app)
            before = _snapshot(app)
            results, error = cleanup.cleanup_artifacts(app, expected)
            self.assertIsNotNone(error)
            self.assertIn("PRECHECK FAILURE", error)
            self.assertIn("repos.json.bak1", error)
            self.assertIn("not cleanly analysable", error)
            self.assertEqual(results, [])
            self.assertEqual(_snapshot(app), before)
            primary = json.loads(
                (app / "repos.json").read_text(encoding="utf-8"))
            self.assertEqual(len(primary["projects"]), 40)

    def test_missing_expected_hash_aborts_before_any_write(self):
        # Hash supplied only for the primary while a valid contaminated
        # backup exists: the backup must not be silently skipped.
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_registry(Path(tmp))
            expected = {"repos.json": cleanup._sha256_bytes(
                (app / "repos.json").read_bytes())}
            before = _snapshot(app)
            results, error = cleanup.cleanup_artifacts(app, expected)
            self.assertIsNotNone(error)
            self.assertIn("PRECHECK FAILURE", error)
            self.assertIn("missing expected hash", error)
            self.assertIn("repos.json.bak1", error)
            self.assertEqual(results, [])
            self.assertEqual(_snapshot(app), before)
            primary = json.loads(
                (app / "repos.json").read_text(encoding="utf-8"))
            self.assertEqual(len(primary["projects"]), 40)

    def test_invalid_backup_preserved_and_never_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_registry(Path(tmp))
            broken = b"{ definitely not json"
            (app / "repos.json.bak2").write_bytes(broken)
            expected = {"repos.json": cleanup._sha256_bytes(
                            (app / "repos.json").read_bytes()),
                        "repos.json.bak1": cleanup._sha256_bytes(
                            (app / "repos.json.bak1").read_bytes()),
                        "repos.json.bak2": cleanup._sha256_bytes(broken)}
            results, error = cleanup.cleanup_artifacts(app, expected)
            self.assertIsNone(error)
            actions = {r["artifact"]: r["action"] for r in results}
            self.assertEqual(actions, {"repos.json": "cleaned",
                                       "repos.json.bak1": "cleaned",
                                       "repos.json.bak2": "left-untouched"})
            # Malformed backup preserved byte-for-byte, never sanitized.
            self.assertEqual((app / "repos.json.bak2").read_bytes(), broken)
            with self.assertRaises(ValueError):
                json.loads((app / "repos.json.bak2").read_text(
                    encoding="utf-8"))
            cleaned = json.loads(
                (app / "repos.json").read_text(encoding="utf-8"))
            self.assertEqual(cleaned["projects"], [])

    def test_unrelated_malformed_record_aborts_instead_of_normalizing(self):
        # A non-UA-05 record that the registry validator would repair must
        # abort the cleanup rather than being silently normalized or dropped.
        with tempfile.TemporaryDirectory() as tmp:
            malformed = {"name": "user-project",
                         "path": r"C:\Users\u\work\user-project",
                         "status": "not-a-status", "pinned": "yes"}
            app, _ = _make_registry(Path(tmp), extra_records=(malformed,))
            expected = _hashes_of_existing(app)
            before = _snapshot(app)
            results, error = cleanup.cleanup_artifacts(app, expected)
            self.assertIsNotNone(error)
            self.assertIn("PRECHECK FAILURE", error)
            self.assertIn("unrelated", error)
            self.assertEqual(results, [])
            self.assertEqual(_snapshot(app), before)
            kept = json.loads(
                (app / "repos.json").read_text(encoding="utf-8"))["projects"]
            self.assertIn(malformed, kept)


class AtomicReplacementTests(unittest.TestCase):
    """UA-05 hardening: same-directory os.replace without delete-then-write."""

    def test_atomic_write_replaces_via_same_directory_os_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            target = directory / "target.json"
            target.write_text("old", encoding="utf-8")
            with mock.patch("os.replace", wraps=os.replace) as replace:
                cleanup.atomic_write(target, "new content")
            self.assertEqual(target.read_text(encoding="utf-8"),
                             "new content")
            self.assertEqual(replace.call_count, 1)
            src, dst = replace.call_args[0]
            self.assertEqual(Path(dst), target)
            # Temporary file lived in the same directory (same filesystem).
            self.assertEqual(Path(src).parent, target.parent)
            # No temp litter and no delete-then-write gap: only target left.
            self.assertEqual([p.name for p in directory.iterdir()],
                             ["target.json"])

    def test_atomic_write_cleans_up_temp_file_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            target = directory / "target.json"
            target.write_text("old", encoding="utf-8")
            with mock.patch("os.replace",
                            side_effect=OSError("simulated disk failure")):
                with self.assertRaises(OSError):
                    cleanup.atomic_write(target, "new")
            # Target untouched and the temp file removed when safely possible.
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual([p.name for p in directory.iterdir()],
                             ["target.json"])

    def test_successful_apply_writes_through_atomic_write_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = _make_registry(Path(tmp))
            expected = _hashes_of_existing(app)
            with mock.patch.object(cleanup, "atomic_write",
                                   wraps=cleanup.atomic_write) as writer:
                results, error = cleanup.cleanup_artifacts(app, expected)
            self.assertIsNone(error)
            self.assertEqual(writer.call_count, 2)
            written = {Path(call.args[0]).name for call in writer.call_args_list}
            self.assertEqual(written, {"repos.json", "repos.json.bak1"})


class WritePhaseReportingTests(unittest.TestCase):
    """UA-05 hardening: replacement is tracked the moment os.replace lands.

    Once ``atomic_write`` succeeds, the current artifact must appear under
    "artifacts already replaced" in every later error - even when its
    post-write re-read or validation fails - while a failure inside
    ``atomic_write`` must not report it as replaced at all.
    """

    def _registry_app(self):
        """A tmp app dir with valid contaminated repos.json + bak1."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app, _ = _make_registry(Path(tmp.name))
        return app, _hashes_of_existing(app)

    def test_post_write_reread_failure_reports_current_artifact_replaced(self):
        app, expected = self._registry_app()
        original_read_bytes = Path.read_bytes
        replaced_landed = {"done": False}

        def fake_atomic_write(_path, _text):
            # os.replace completed (mocked); everything after this point is
            # the post-write phase for repos.json.
            replaced_landed["done"] = True

        def flaky_read(self):
            # Only the re-read that follows the successful replacement fails.
            if self.name == "repos.json" and replaced_landed["done"]:
                raise OSError("simulated post-write re-read failure")
            return original_read_bytes(self)

        with mock.patch.object(cleanup, "atomic_write",
                               side_effect=fake_atomic_write), \
                mock.patch.object(Path, "read_bytes", flaky_read):
            results, error = cleanup.cleanup_artifacts(app, expected)
        self.assertIsNotNone(error)
        self.assertIn("WRITE PHASE FAILURE", error)
        self.assertIn("cannot re-read rewritten repos.json", error)
        # os.replace completed, so repos.json must be reported as replaced
        # even though the follow-up re-read failed.
        self.assertIn("artifacts already replaced: repos.json", error)
        self.assertEqual(results, [])

    def test_post_write_validation_failure_reports_current_artifact_replaced(self):
        app, expected = self._registry_app()
        with mock.patch.object(cleanup, "atomic_write",
                               side_effect=lambda _path, _text: None):
            results, error = cleanup.cleanup_artifacts(app, expected)
        self.assertIsNotNone(error)
        self.assertIn("failed validation", error)
        # Replacement completed; repos.json must be reported even though its
        # rewritten content did not validate.
        self.assertIn("artifacts already replaced: repos.json", error)
        # ... but it must not be mislabelled as cleaned/validated.
        self.assertEqual([r for r in results if r.get("action") == "cleaned"],
                         [])

    def test_atomic_write_failure_before_replacement_reports_none(self):
        app, expected = self._registry_app()
        with mock.patch.object(cleanup, "atomic_write",
                               side_effect=OSError("simulated replace failure")):
            results, error = cleanup.cleanup_artifacts(app, expected)
        self.assertIsNotNone(error)
        self.assertIn("WRITE PHASE FAILURE - replacing repos.json failed",
                      error)
        # The replacement never happened: repos.json must NOT be reported.
        self.assertIn("artifacts already replaced: none", error)
        self.assertNotIn("already replaced: repos.json", error)
        self.assertEqual(results, [])


class RegistryValidationTests(unittest.TestCase):
    """UA-05 hardening: prepared and rewritten results pass the real validator."""

    def test_cleaned_result_accepted_by_repo_manager_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "app"
            app.mkdir(parents=True)
            legit = {"path": r"C:\Users\u\Documents\research",
                     "name": "research", "status": "active",
                     "pinned": False, "focus": "",
                     "project_id": "legit-1",
                     "custom_field": {"keep": 1}}
            doc = {"schema_version": 2,
                   "projects": [_fixture(i) for i in range(40)] + [legit],
                   "workspaces": [{"workspace_id": "w-1",
                                    "name": "Default", "members": []}],
                   "meta_future": {"untouched": True}}
            write_registry(app / "repos.json", doc)
            write_registry(app / "repos.json.bak1", doc)
            expected = _hashes_of_existing(app)
            results, error = cleanup.cleanup_artifacts(app, expected)
            self.assertIsNone(error)
            for name in ("repos.json", "repos.json.bak1"):
                parsed = json.loads((app / name).read_text(encoding="utf-8"))
                records, issues = store.validate_registry(parsed)
                self.assertEqual(issues, [])
                self.assertEqual(records, [legit])
                self.assertEqual(parsed["projects"], [legit])

    def test_successful_cleanup_semantics_unchanged(self):
        # Exactly repo00000..repo00039 removed; every legitimate record,
        # Workspace, unknown top-level key, unknown record field and the
        # settings file survive untouched.
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "app"
            app.mkdir(parents=True)
            legit = {"path": r"C:\Users\u\code\app", "name": "app",
                     "status": "active", "pinned": False, "focus": "",
                     "project_id": "real-1",
                     "launcher_extra": {"x": 1}, "notes": "keep"}
            original = {"schema_version": 2,
                        "projects": [_fixture(i) for i in range(40)] + [legit],
                        "workspaces": [{"workspace_id": "w-1",
                                         "name": "Default",
                                         "members": []}],
                        "meta_future": {"untouched": True}}
            write_registry(app / "repos.json", original)
            write_registry(app / "repos.json.bak1", original)
            settings = {"roots": [r"C:\Users\u\code"], "theme": "dark",
                        "extra_setting": 3}
            settings_path = app / "settings.json"
            settings_path.write_text(json.dumps(settings, indent=2),
                                     encoding="utf-8")
            settings_before = settings_path.read_bytes()
            expected = _hashes_of_existing(app)
            results, error = cleanup.cleanup_artifacts(app, expected)
            self.assertIsNone(error)
            cleaned = {r["artifact"]: r for r in results
                       if r.get("action") == "cleaned"}
            self.assertEqual(set(cleaned), {"repos.json", "repos.json.bak1"})
            for name in ("repos.json", "repos.json.bak1"):
                parsed = json.loads((app / name).read_text(encoding="utf-8"))
                # The full synthetic family is gone, the rest is untouched.
                self.assertEqual(parsed["projects"], [legit])
                self.assertTrue(
                    {p["name"] for p in parsed["projects"]}.isdisjoint(
                        cleanup.EXPECTED_FAMILY))
                self.assertEqual(
                    {k: v for k, v in parsed.items() if k != "projects"},
                    {k: v for k, v in original.items() if k != "projects"})
            self.assertEqual(settings_path.read_bytes(), settings_before)
            for result in cleaned.values():
                self.assertEqual(result["removed_count"], 40)
                self.assertEqual(result["project_count_before"], 41)
                self.assertEqual(result["project_count_after"], 1)


if __name__ == "__main__":
    unittest.main()
