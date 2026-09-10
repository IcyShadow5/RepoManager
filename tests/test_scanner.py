import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import health, projects, scanner
from tests.git_repository import canonical_path


def make_real_repo(root: Path, name: str) -> Path:
    """Create a real git repo with one commit so collect_metadata works."""
    d = root / name
    d.mkdir(parents=True)
    (d / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t.t",
         "commit", "-qm", "init"],
        check=True,
    )
    return d


def make_empty_git(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / ".git").mkdir()  # empty .git = invalid repo
    return d


class FindRepoDirsTests(unittest.TestCase):
    def test_finds_repos_and_respects_skip_and_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_empty_git(root, "projA")   # .git dir presence is enough for discovery
            deep = root / "projB" / "sub" / "deep" / "deeper"
            deep.mkdir(parents=True)
            (deep / ".git").mkdir()
            junk = root / "node_modules" / "pkg"
            junk.mkdir(parents=True)
            (junk / ".git").mkdir()

            found = scanner.find_repo_dirs([str(root)], depth=4,
                                           skip_dirs=["node_modules"])
            names = [Path(f).name for f in found]
            self.assertIn("projA", names)
            self.assertIn("deeper", names)
            self.assertNotIn("pkg", names)

    def test_relative_and_absolute_alias_roots_discover_one_canonical_path(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            repo = root / "repo"
            (repo / ".git").mkdir(parents=True)
            relative = os.path.relpath(root, Path.cwd())

            found = scanner.find_repo_dirs(
                [relative, str(root.resolve())], depth=2, skip_dirs=[])

            self.assertEqual([canonical_path(path) for path in found],
                             [canonical_path(repo)])


class MergeScanTests(unittest.TestCase):
    def test_registry_equivalent_path_variations_merge_into_one_project(self):
        existing = {
            "project_id": "coldline", "path": r"D:\Games\COLDLINE",
            "name": "COLDLINE", "status": "active", "focus": "keep",
            "last_seen": "2099-01-01T00:00:00Z",
        }
        variations = (
            "d:/games/coldline/.",
            r"d:\games\coldline",
            "D:/Games/COLDLINE/",
            r"D:\Games\Other\..\COLDLINE",
        )

        for scanned_path in variations:
            with self.subTest(scanned_path=scanned_path), mock.patch.object(
                    scanner, "collect_metadata",
                    side_effect=lambda path: scanner.empty_meta(path)):
                merged, _problems = scanner.merge_scan(
                    [dict(existing)], [scanned_path])

            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["project_id"], "coldline")
            self.assertEqual(merged[0]["path"], existing["path"])
            self.assertEqual(merged[0]["focus"], "keep")

    def test_distinct_canonical_paths_remain_distinct(self):
        existing = {
            "project_id": "repo", "path": r"D:\Repo", "name": "Repo",
            "last_seen": "2099-01-01T00:00:00Z",
        }
        with mock.patch.object(
                scanner, "collect_metadata",
                side_effect=lambda path: scanner.empty_meta(path)):
            merged, _problems = scanner.merge_scan(
                [existing], [r"D:\Repo-2"])

        self.assertEqual(len(merged), 2)
        self.assertEqual(len({record["project_id"] for record in merged}), 2)

    def test_folder_only_project_survives_repository_scan(self):
        project = {
            "name": "Planning",
            "folder_path": r"C:\\Projects\\Planning",
            "status": "active",
            "focus": "Keep this project",
        }

        merged, problems = scanner.merge_scan([project], [])

        self.assertEqual(problems, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["folder_path"], project["folder_path"])
        self.assertEqual(merged[0]["status"], "active")
        self.assertEqual(merged[0]["focus"], "Keep this project")
        self.assertTrue(merged[0]["project_id"])

    def test_new_project_gets_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_real_repo(Path(tmp), "Alpha")
            projects, problems = scanner.merge_scan([], [str(d)])
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["status"], "idea")
            self.assertFalse(projects[0]["pinned"])
            self.assertTrue(projects[0]["branch"])
            self.assertTrue(projects[0]["repository_observed"])
            self.assertEqual(projects[0]["last_commit_msg"], "init")
            self.assertEqual(problems, [])

    def test_nested_repository_uses_parent_project_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "PocketLedger"
            d = make_real_repo(project_root, "repository")

            projects, problems = scanner.merge_scan([], [str(d)])

            self.assertEqual(problems, [])
            self.assertEqual(projects[0]["name"], "PocketLedger")

    def test_user_data_preserved_on_rescan(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_real_repo(Path(tmp), "Alpha")
            old = [{
                "path": str(d), "name": "Curated Alpha", "branch": None,
                "dirty": 0, "ahead": 0, "behind": 0, "last_commit_date": None,
                "last_commit_msg": None, "remote": None, "broken": False,
                "added_at": "2026-01-01T00:00:00Z",
                "last_seen": "2026-01-01T00:00:00Z",
                "status": "active", "focus": "testing", "pinned": True,
            }]
            merged, _ = scanner.merge_scan(old, [str(d)])
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["status"], "active")
            self.assertEqual(merged[0]["focus"], "testing")
            self.assertTrue(merged[0]["pinned"])
            self.assertEqual(merged[0]["name"], "Curated Alpha")
            self.assertTrue(merged[0]["project_id"])

    def test_ignored_project_stays_ignored_when_rediscovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_real_repo(Path(tmp), "Ignored")
            old = [{
                "project_id": "ignored-project",
                "path": str(d),
                "name": "Curated ignored",
                "status": "active",
                "focus": "retain",
                "pinned": True,
                "ignored": True,
                "last_seen": "2020-01-01T00:00:00Z",
            }]

            merged, problems = scanner.merge_scan(old, [str(d)])

            self.assertEqual(problems, [])
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["project_id"], "ignored-project")
            self.assertTrue(merged[0]["ignored"])
            self.assertEqual(merged[0]["status"], "active")
            self.assertEqual(merged[0]["focus"], "retain")
            self.assertTrue(merged[0]["pinned"])

    def test_ignored_project_is_not_scanner_pruned_when_vanished(self):
        old = [{
            "project_id": "ignored-project",
            "path": r"C:\\gone\\Ignored",
            "name": "Ignored",
            "status": "active",
            "ignored": True,
            "last_seen": "2020-01-01T00:00:00Z",
        }]

        merged, problems = scanner.merge_scan(old, [])

        self.assertEqual(problems, [])
        self.assertEqual(merged, old)

    def test_ignored_and_normal_projects_remain_independently_addressable(self):
        with tempfile.TemporaryDirectory() as tmp:
            ignored_path = make_real_repo(Path(tmp), "Ignored")
            normal_path = make_real_repo(Path(tmp), "Normal")
            old = [
                {"project_id": "ignored-project", "path": str(ignored_path),
                 "name": "Ignored", "status": "active", "ignored": True},
                {"project_id": "normal-project", "path": str(normal_path),
                 "name": "Normal", "status": "active"},
            ]

            merged, problems = scanner.merge_scan(
                old, [str(ignored_path), str(normal_path)])

            self.assertEqual(problems, [])
            by_id = {item["project_id"]: item for item in merged}
            self.assertEqual(set(by_id), {"ignored-project", "normal-project"})
            self.assertTrue(by_id["ignored-project"]["ignored"])
            self.assertFalse(projects.is_ignored(by_id["normal-project"]))

    def test_legacy_repository_name_is_not_rewritten_on_rescan(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "PocketLedger"
            d = make_real_repo(project_root, "repository")
            old = [{
                "path": str(d),
                "name": "repository",
                "project_id": "stable-project-id",
                "status": "active",
                "focus": "preserve",
                "pinned": True,
                "broken": False,
            }]

            merged, problems = scanner.merge_scan(old, [str(d)])

            self.assertEqual(problems, [])
            self.assertEqual(merged[0]["name"], "repository")
            self.assertEqual(merged[0]["project_id"], "stable-project-id")
            self.assertEqual(merged[0]["focus"], "preserve")

    def test_ordinary_directory_without_git_is_not_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            ordinary = Path(tmp) / "ordinary-folder"
            ordinary.mkdir()
            found = scanner.find_repo_dirs([str(ordinary)], depth=2,
                                           skip_dirs=[])
            self.assertEqual(found, [])
            merged, problems = scanner.merge_scan([], list(found))
            self.assertEqual(merged, [])
            self.assertEqual(problems, [])

    def test_broken_repo_reported_as_repository_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_empty_git(Path(tmp), "Broken")
            projects, problems = scanner.merge_scan([], [str(d)])
        self.assertEqual(projects, [])
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["kind"], "repository")

    def test_persisted_ordinary_path_is_not_promoted_to_git_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            ordinary = Path(tmp) / "ordinary-folder"
            ordinary.mkdir()
            old = {
                "project_id": "ordinary-project",
                "name": "Ordinary folder",
                "path": str(ordinary),
                "broken": False,
                "status": "active",
                "last_seen": "2099-01-01T00:00:00Z",
            }
            merged, problems = scanner.merge_scan([old], [])
        self.assertEqual(problems, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(projects.classification_display(merged[0]), "Unknown")
        self.assertEqual(merged[0]["path"], str(ordinary))

    def test_vanished_project_kept_within_window(self):
        old = [{
            "path": r"C:\gone\Beta", "name": "Beta", "status": "paused",
            "focus": "", "pinned": False,
            "last_seen": "2099-01-01T00:00:00Z",
        }]
        merged, _ = scanner.merge_scan(old, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["status"], "paused")

    def test_vanished_project_without_age_evidence_is_not_pruned(self):
        old = [{
            "path": r"C:\gone\Legacy", "name": "Legacy",
            "status": "active", "focus": "preserve", "pinned": True,
        }]

        merged, _ = scanner.merge_scan(old, [])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["name"], "Legacy")
        self.assertEqual(merged[0]["focus"], "preserve")
        self.assertTrue(merged[0]["pinned"])

    def test_worktree_repo_is_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main_repo = make_real_repo(root, "Main")
            wt = root / "MyWorktree"
            subprocess.run(
                ["git", "-C", str(main_repo), "worktree", "add", str(wt), "HEAD"],
                check=True, capture_output=True,
            )
            # worktrees have `.git` as a FILE, not a directory
            self.assertTrue((wt / ".git").is_file())
            found = scanner.find_repo_dirs([str(root)], depth=4, skip_dirs=[])
            self.assertIn(
                canonical_path(wt),
                [canonical_path(path) for path in found],
            )
            projects, problems = scanner.merge_scan([], [str(wt)])
            self.assertEqual(len(projects), 1, f"problems={problems}")
            self.assertFalse(projects[0]["broken"])
            self.assertTrue(projects[0]["repository_observed"])

    def test_transient_failure_keeps_existing_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_empty_git(Path(tmp), "Known")  # unreadable right now
            old = [{
                "path": str(d), "name": "Known", "status": "active",
                "focus": "important work", "pinned": True,
                "branch": "main", "dirty": 0, "ahead": 0, "behind": 0,
                "last_commit_date": "2026-08-01", "last_commit_msg": "x",
                "remote": None, "broken": False,
                "added_at": "2026-01-01T00:00:00Z",
                "last_seen": "2026-01-01T00:00:00Z",
            }]
            projects, problems = scanner.merge_scan(old, [str(d)])
            # reported as a problem, but the stored project (with user data)
            # must NOT be dropped
            self.assertEqual(len(problems), 1)
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["status"], "active")
            self.assertEqual(projects[0]["focus"], "important work")
            self.assertTrue(projects[0]["pinned"])

    def test_broken_existing_repository_invalidates_old_git_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_real_repo(Path(tmp), "Known")
            clean = scanner.collect_metadata(repo)
            old = dict(clean)
            old.update({
                "project_id": "known-project",
                "name": "Curated Known",
                "status": "active",
                "focus": "important work",
                "pinned": True,
            })

            (repo / ".git").rename(repo / ".git-valid")
            (repo / ".git").mkdir()
            merged, problems = scanner.merge_scan([old], [str(repo)])

            self.assertEqual(len(problems), 1)
            self.assertEqual(len(merged), 1)
            project = merged[0]
            self.assertEqual(project["project_id"], "known-project")
            self.assertEqual(project["name"], "Curated Known")
            self.assertEqual(project["status"], "active")
            self.assertEqual(project["focus"], "important work")
            self.assertTrue(project["pinned"])
            self.assertTrue(project["broken"])
            self.assertIsNone(project["branch"])
            self.assertIsNone(project["dirty"])
            self.assertFalse(project["status_available"])
            self.assertFalse(project["worktrees_available"])
            self.assertEqual(
                projects.classification_display(project),
                "Unknown")

            result = health.evaluate_repository(repo, project)
            self.assertEqual(result.status, health.UNKNOWN)
            self.assertEqual(
                next(f for f in result.findings if f.rule == "git_metadata").status,
                health.UNKNOWN)
            self.assertEqual(
                next(f for f in result.findings if f.rule == "working_tree").status,
                health.UNKNOWN)


class MergeScanHardeningTests(unittest.TestCase):
    def test_record_without_path_skipped_others_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_real_repo(Path(tmp), "Alpha")
            poisoned = [{"name": "no-path"}, {"path": None, "name": "x"},
                        {"path": 123, "name": "num"}]
            projects, problems = scanner.merge_scan(poisoned, [str(d)])
            self.assertEqual([p["name"] for p in projects], ["Alpha"])
            self.assertEqual(problems, [])

    def test_non_dict_entry_and_missing_name_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_real_repo(Path(tmp), "Alpha")
            poisoned = ["junk", {"path": r"C:\x"}, {"path": r"C:\y",
                                                    "name": ""}]
            projects, problems = scanner.merge_scan(poisoned, [str(d)])
            self.assertEqual([p["name"] for p in projects], ["Alpha"])

    def test_vanished_poisoned_records_do_not_crash(self):
        old = [{"path": r"C:\gone\A", "name": "A", "status": "paused",
                "focus": "", "pinned": False,
                "last_seen": "2099-01-01T00:00:00Z"},
               {"path": r"C:\bad"},  # no name: must be skipped
               ]
        projects, _problems = scanner.merge_scan(old, [])
        names = [p["name"] for p in projects]
        self.assertEqual(names, ["A"])

    def test_one_metadata_failure_does_not_abort_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = make_real_repo(Path(tmp), "A")
            b = make_real_repo(Path(tmp), "B")
            real = scanner.collect_metadata

            def flaky(path):
                if path.endswith("A"):
                    raise RuntimeError("simulated crash")
                return real(path)

            with mock.patch.object(scanner, "collect_metadata",
                                   side_effect=flaky):
                projects, problems = scanner.merge_scan([], [str(a), str(b)])
            # the failed repo degrades to a problem entry; B is unaffected
            self.assertEqual([p["path"] for p in problems], [str(a)])
            self.assertEqual([p["name"] for p in projects], ["B"])
            self.assertFalse(projects[0]["broken"])
            self.assertEqual(projects[0]["last_commit_msg"], "init")


class GitObservationAvailabilityTests(unittest.TestCase):
    def test_failed_status_is_unknown_not_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_real_repo(Path(tmp), "StatusUnknown")
            real_git = scanner._git

            def fail_status(path, *args):
                if args == ("status", "--porcelain"):
                    return None
                return real_git(path, *args)

            with mock.patch.object(scanner, "_git", side_effect=fail_status):
                metadata = scanner.collect_metadata(repo)
        self.assertFalse(metadata["status_available"])
        self.assertIsNone(metadata["dirty"])
        self.assertIsNone(metadata["staged"])

    def test_no_upstream_is_distinct_from_zero_divergence(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_real_repo(Path(tmp), "NoUpstream")
            metadata = scanner.collect_metadata(repo)
        self.assertEqual(metadata["upstream_state"], "NONE")
        self.assertFalse(metadata["sync_available"])
        self.assertIsNone(metadata["ahead"])
        self.assertIsNone(metadata["behind"])

    def test_worktree_remove_blocks_when_status_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "linked"
            record = {"path": str(target), "current": False}
            target.mkdir()
            with mock.patch.object(scanner, "list_worktrees",
                                   return_value=[record]), \
                    mock.patch.object(scanner, "_git", return_value=None), \
                    mock.patch.object(scanner, "_run_git") as run:
                result = scanner.remove_worktree(
                    Path(tmp), target, confirm=True)
        self.assertEqual(result["category"], scanner.WORKTREE_INSPECTION_UNAVAILABLE)
        self.assertFalse(result["executed"])
        run.assert_not_called()


class IdentityMatchingTests(unittest.TestCase):
    """Move matching is pure, evidence-based, and advisory."""

    @staticmethod
    def stale(path, name=None, remote=None, remotes=None, roots=None,
              **extra):
        entry = {"path": path, "name": name or Path(path).name,
                 "status": "idea", "focus": "", "pinned": False}
        entry.update(extra)
        if remote:
            entry["remote"] = remote
        fp = {}
        if remotes is not None:
            fp["remotes"] = remotes
        if roots is not None:
            fp["root_commits"] = roots
        if fp:
            entry["fingerprint"] = fp
        return entry

    def test_strong_remote_match_iron_style(self):
        old = self.stale(r"C:\Users\u\Desktop\Iron",
                         remote="github.com/o/Iron")
        out = scanner.match_move_candidates(
            [old], [(r"C:\Users\u\Desktop\Projekte\Iron",
                     {"remotes": ["github.com/o/Iron"],
                      "root_commits": ["a1"]})])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["category"], "strong")
        self.assertEqual(out[0]["new_path"],
                         r"C:\Users\u\Desktop\Projekte\Iron")
        self.assertTrue(any("remote" in e for e in out[0]["evidence"]))

    def test_case_folded_remote_matches(self):
        old = self.stale(r"C:\x\SpaceEx",
                         remote="github.com/o/SpaceEX")
        out = scanner.match_move_candidates(
            [old], [(r"C:\x2\spaceex",
                     {"remotes": ["github.com/O/spaceex"], })])
        self.assertEqual(out[0]["category"], "strong")

    def test_folder_rename_with_remote(self):
        old = self.stale(r"C:\p\Ember_space",
                         remote="github.com/o/ember-idle")
        out = scanner.match_move_candidates(
            [old], [(r"C:\p\ember-idle",
                     {"remotes": ["github.com/o/ember-idle"], })])
        self.assertEqual(len(out), 1)

    def test_legacy_entry_without_fingerprint_still_matches_by_remote(self):
        old = self.stale(r"C:\old\Repo", remote="github.com/o/repo")
        out = scanner.match_move_candidates(
            [old], [(r"C:\new\Repo2",
                     {"remotes": ["github.com/o/repo"], })])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["category"], "possible")

    def test_no_evidence_no_candidate(self):
        old = self.stale(r"C:\old\Mystery")
        out = scanner.match_move_candidates(
            [old], [(r"C:\new\Other", None)])
        self.assertEqual(out, [])

    def test_differing_remotes_disjoint_roots_no_candidate(self):
        old = self.stale(r"C:\old\A", remote="github.com/o/a",
                         remotes=["github.com/o/a"], roots=["aaa"])
        out = scanner.match_move_candidates(
            [old], [(r"C:\new\B",
                     {"remotes": ["github.com/o/b"],
                      "root_commits": ["bbb"]})])
        self.assertEqual(out, [])

    def test_same_remote_independent_clone_is_suggestion_not_migration(self):
        old = self.stale(r"C:\old\Alpha", remote="github.com/o/alpha",
                         status="active", focus="work", pinned=True)
        snapshot = json.loads(json.dumps(old))
        fresh_fp = {"remotes": ["github.com/o/alpha"],
                    "root_commits": ["a1", "a2"]}
        out = scanner.match_move_candidates([old],
                                            [(r"C:\new\AlphaCopy",
                                              fresh_fp)])
        self.assertEqual(len(out), 1)
        # absolute invariant: inputs untouched, nothing migrated
        self.assertEqual(json.loads(json.dumps(old)), snapshot)
        self.assertEqual(old["path"], r"C:\old\Alpha")

    def test_one_stale_two_fresh_is_ambiguous_grouped(self):
        old = self.stale(r"C:\old\RelicGuild",
                         remote="github.com/o/relicguild")
        out = scanner.match_move_candidates(
            [old],
            [(r"C:\new\RelicGuild",
              {"remotes": ["github.com/o/relicguild"]}),
             (r"C:\bak\dst-partial",
              {"remotes": ["github.com/o/relicguild"]})])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["category"], "ambiguous")
        self.assertEqual(sorted(out[0]["new_paths"]),
                         [r"C:\bak\dst-partial", r"C:\new\RelicGuild"])

    def test_two_stale_one_fresh_is_ambiguous_grouped(self):
        s1 = self.stale(r"C:\old\Foo", remote="github.com/o/foo")
        s2 = self.stale(r"C:\old2\Foo", remote="github.com/o/foo")
        out = scanner.match_move_candidates(
            [s1, s2],
            [(r"C:\new\Foo", {"remotes": ["github.com/o/foo"]})])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["category"], "ambiguous")
        self.assertIn(s2["path"], out[0]["old_paths"])

    def test_suppressed_pair_filtered(self):
        old = self.stale(r"C:\old\Iron", remote="github.com/o/Iron")
        out = scanner.match_move_candidates(
            [old],
            [(r"C:\new\Iron", {"remotes": ["github.com/o/Iron"]})],
            suppressed=[(r"c:\old\iron", r"c:\new\iron")])
        self.assertEqual(out, [])

    def test_deterministic_ordering(self):
        olds = [self.stale(rf"C:\o\repo{i}", remote=f"github.com/o/r{i}")
                for i in range(5)]
        fresh = [(rf"C:\n\repo{i}",
                  {"remotes": [f"github.com/o/r{i}"]}) for i in range(5)]
        a = scanner.match_move_candidates(olds, fresh)
        b = scanner.match_move_candidates(list(reversed(olds)),
                                          list(reversed(fresh)))
        self.assertEqual(a, b)

    def test_folder_only_match_is_possible_category(self):
        old = self.stale(r"C:\old\widget-toolkit")
        out = scanner.match_move_candidates(
            [old], [(r"C:\new\widget-toolkit", None)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["category"], "possible")

    def test_short_generic_folder_names_ignored(self):
        old = self.stale(r"C:\old\docs")
        out = scanner.match_move_candidates(
            [old], [(r"C:\new\docs", None)])
        self.assertEqual(out, [])


class FingerprintCaptureTests(unittest.TestCase):
    def make_repo_with_remotes(self, root, remotes, with_commit=True,
                               branch_first=True):
        d = root
        d.mkdir(parents=True)
        (d / "f.txt").write_text("x")
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        if with_commit:
            subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(d), "-c", "user.name=t",
                            "-c", "user.email=t@t.t", "commit", "-qm",
                            "init"], check=True)
        for i, url in enumerate(remotes):
            subprocess.run(["git", "-C", str(d), "remote", "add",
                            "origin" if i == 0 else f"up{i}", url],
                           check=True)
        return d

    def test_fingerprint_captures_remotes_and_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self.make_repo_with_remotes(
                Path(tmp) / "r", ["https://github.com/o/r.git"])
            meta = scanner.collect_metadata(str(d))
            self.assertEqual(meta["fingerprint"]["remotes"],
                             ["github.com/o/r"])
            self.assertEqual(len(meta["fingerprint"]["root_commits"]), 1)
            self.assertEqual(meta["remote"], "github.com/o/r")

    def test_multiple_remotes_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self.make_repo_with_remotes(
                Path(tmp) / "r", ["git@host:o/r.git", "https://h.org/x/y"])
            meta = scanner.collect_metadata(str(d))
            self.assertEqual(meta["fingerprint"]["remotes"],
                             sorted(["host/o/r", "h.org/x/y"]))
            self.assertEqual(meta["remote"], "host/o/r")  # origin preferred
            self.assertEqual(meta["remote_name"], "origin")

    def test_unborn_head_empty_root_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self.make_repo_with_remotes(Path(tmp) / "r",
                                            [], with_commit=False)
            meta = scanner.collect_metadata(str(d))
            self.assertEqual(meta["fingerprint"]["root_commits"], [])
            self.assertFalse(meta["broken"])


class MoveAwareMergeTests(unittest.TestCase):
    def _real_repo(self, root, name, remote=None):
        d = root / name
        d.mkdir(parents=True)
        (d / "f.txt").write_text("x")
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(d), "-c", "user.name=t",
                        "-c", "user.email=t@t.t", "commit", "-qm", "init"],
                       check=True)
        if remote:
            subprocess.run(["git", "-C", str(d), "remote", "add",
                            "origin", remote], check=True)
        return d

    def test_vanished_plus_matching_discovery_yields_suggestion_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            newd = self._real_repo(Path(tmp), "Iron",
                                   "https://github.com/o/Iron.git")
            old_entry = {
                "path": r"C:\Users\u\Desktop\Iron", "name": "Iron",
                "branch": "master", "dirty": 0, "ahead": 0, "behind": 0,
                "last_commit_date": "2026-01-01", "last_commit_msg": "x",
                "remote": "github.com/o/Iron", "broken": False,
                "added_at": "2026-01-01T00:00:00Z",
                "last_seen": "2099-01-01T00:00:00Z",
                "status": "active", "focus": "district refactor",
                "pinned": True,
            }
            before = json.loads(json.dumps(old_entry))
            projects, problems = scanner.merge_scan(
                [old_entry], [str(newd)])
            moves = [p for p in problems if p.get("kind") == "move"]
            self.assertEqual(len(moves), 1)
            self.assertEqual(moves[0]["category"], "strong")
            # CRITICAL SAFETY: no automatic migration happened
            still_old = next(p for p in projects
                             if p["name"] == "Iron" and
                             p.get("status") == "active")
            self.assertEqual(still_old["path"], r"C:\Users\u\Desktop\Iron")
            self.assertNotEqual(still_old["path"], str(newd))
            self.assertEqual(still_old["focus"], "district refactor")
            self.assertTrue(still_old.get("project_id"))
            # the new discovery also exists as its own default entry
            fresh = [p for p in projects
                     if p["path"] == str(newd)]
            self.assertEqual(len(fresh), 1)
            self.assertEqual(fresh[0]["status"], "idea")

    def test_suppressed_pair_produces_no_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            newd = self._real_repo(Path(tmp), "Iron",
                                   "https://github.com/o/Iron.git")
            old_entry = {
                "path": r"C:\Users\u\Desktop\Iron", "name": "Iron",
                "status": "idea", "focus": "", "pinned": False,
                "remote": "github.com/o/Iron", "broken": False,
                "last_seen": "2099-01-01T00:00:00Z",
            }
            projects, problems = scanner.merge_scan(
                [old_entry], [str(newd)],
                move_suppressions=[{"old": r"c:\users\u\desktop\iron",
                                    "new": str(newd).lower()}])
            self.assertEqual([p for p in problems
                              if p.get("kind") == "move"], [])

    def test_exact_path_match_refreshes_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._real_repo(Path(tmp), "Stay",
                                "https://github.com/o/stay.git")
            old_entry = {"path": str(d), "name": "Stay", "status": "active",
                         "focus": "keep", "pinned": True, "broken": False,
                         "last_seen": "2099-01-01T00:00:00Z"}
            projects, _ = scanner.merge_scan([old_entry], [str(d)])
            stay = next(p for p in projects if p["name"] == "Stay")
            self.assertEqual(stay["status"], "active")
            self.assertEqual(stay["fingerprint"]["remotes"],
                             ["github.com/o/stay"])

    def test_same_remote_at_two_paths_remains_two_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._real_repo(
                root, "First", "https://github.com/o/shared.git")
            second = self._real_repo(
                root, "Second", "https://github.com/o/shared.git")

            projects, _problems = scanner.merge_scan(
                [], [str(first), str(second)])

            self.assertEqual({record["path"] for record in projects},
                             {str(first), str(second)})
            self.assertEqual(len({record["project_id"] for record in projects}),
                             2)
            self.assertEqual({record["remote"] for record in projects},
                             {"github.com/o/shared"})


class PruneGuardTests(unittest.TestCase):
    """Open move suggestions protect entries from pruning."""

    def test_spaces_in_repo_path_discovered_and_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            spaced = Path(tmp) / "ordner mit leerzeichen"
            d = self._real_repo(spaced, "Space Proj",
                                "https://github.com/o/sp.git")
            projects, problems = scanner.merge_scan([], [str(d)])
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["path"], str(d))
            self.assertEqual(projects[0]["remote"], "github.com/o/sp")
            self.assertEqual(problems, [])

    def _real_repo(self, root, name, remote=None):
        d = root / name
        d.mkdir(parents=True)
        (d / "f.txt").write_text("x")
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(d), "-c", "user.name=t",
                        "-c", "user.email=t@t.t", "commit", "-qm", "init"],
                       check=True)
        if remote:
            subprocess.run(["git", "-C", str(d), "remote", "add",
                            "origin", remote], check=True)
        return d

    @staticmethod
    def _ancient_entry(path, name, remote=None):
        e = {"path": path, "name": name, "status": "idea", "focus": "",
             "pinned": False, "broken": False,
             "last_seen": "2020-01-01T00:00:00Z"}
        if remote:
            e["remote"] = remote
        return e

    def test_suggested_entry_survives_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            newd = self._real_repo(Path(tmp), "Iron",
                                   "https://github.com/o/Iron.git")
            ancient = self._ancient_entry(r"C:\Users\u\Desktop\Iron",
                                          "Iron", "github.com/o/Iron")
            projects, problems = scanner.merge_scan([ancient], [str(newd)])
            moves = [p for p in problems if p.get("kind") == "move"]
            self.assertEqual(len(moves), 1)
            self.assertTrue(any(p["path"] == r"C:\Users\u\Desktop\Iron"
                                for p in projects),
                            "suggested entry must not be pruned")

    def test_unsuggested_ancient_entry_still_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            newd = self._real_repo(Path(tmp), "Other")
            ancient = self._ancient_entry(r"C:\gone\Zombie", "Zombie")
            projects, problems = scanner.merge_scan([ancient], [str(newd)])
            self.assertFalse(any(p["path"] == r"C:\gone\Zombie"
                                 for p in projects))
            self.assertEqual([p for p in problems
                              if p.get("kind") == "move"], [])

    def test_explicit_protection_keeps_unrelated_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            newd = self._real_repo(Path(tmp), "Other")
            ancient = self._ancient_entry(r"C:\gone\Pinned", "Pinned",
                                          "github.com/o/x")
            projects, problems = scanner.merge_scan(
                [ancient], [str(newd)],
                protected_paths=[r"c:\gone\pinned"])
            self.assertTrue(any(p["path"] == r"C:\gone\Pinned"
                                for p in projects))

    def test_registered_new_location_no_longer_produces_move_suggestion(self):
        """A registered new location no longer pairs with the replaced entry."""
        with tempfile.TemporaryDirectory() as tmp:
            newd = self._real_repo(Path(tmp), "Iron",
                                   "https://github.com/o/Iron.git")
            ancient = self._ancient_entry(r"C:\Users\u\Desktop\Iron",
                                          "Iron", "github.com/o/Iron")
            # round 1: protected because suggested
            projects, problems = scanner.merge_scan([ancient], [str(newd)])
            self.assertTrue(any(p.get("kind") == "move"
                                for p in problems))
            # round 2: user accepted elsewhere; new path now registered,
            # no vanished entry remains to produce a move suggestion
            registered = [{**ancient, "path": str(newd)}]
            projects2, problems2 = scanner.merge_scan(registered,
                                                      [str(newd)])
            self.assertFalse(any(p.get("kind") == "move"
                                 for p in problems2))



    def test_case_insensitive_path_identity_in_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._real_repo(Path(tmp), "Iron",
                                "https://github.com/o/Iron.git")
            old_entry = {
                "path": str(d).upper(), "name": "Iron",
                "status": "active", "focus": "kept", "pinned": True,
                "broken": False, "last_seen": "2099-01-01T00:00:00Z",
            }
            projects, _ = scanner.merge_scan([old_entry], [str(d)])
            iron = [p for p in projects if p["name"] == "Iron"]
            self.assertEqual(len(iron), 1)          # no duplicate row
            self.assertEqual(iron[0]["path"], str(d).upper())  # casing kept
            self.assertEqual(iron[0]["status"], "active")


class TimestampFormatTests(unittest.TestCase):
    """Registry timestamps must keep the exact legacy Z-suffixed shape:
    prune comparisons are plain string comparisons against persisted
    values, so a format drift would silently corrupt retention logic."""

    def test_format_matches_legacy_contract(self):
        import re
        stamp = scanner.utc_now_iso()
        self.assertRegex(stamp,
                         r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_compares_correctly_against_persisted_values(self):
        now = scanner.utc_now_iso()
        self.assertLess("2020-01-01T00:00:00Z", now)   # ancient < now
        self.assertLess(now, "2099-01-01T00:00:00Z")   # now < far future


class NormalizeRemoteTests(unittest.TestCase):
    CASES = {
        "https://github.com/Org/Repo.git": "github.com/Org/Repo",
        "https://github.com/o/r": "github.com/o/r",
        "https://user:token@github.com/o/r.git": "github.com/o/r",
        "git@github.com:Owner/Repository.git": "github.com/Owner/Repository",
        "ssh://git@github.com/Owner/Repository.git":
            "github.com/Owner/Repository",
        "ssh://git@gitlab.com:2222/o/r.git": "gitlab.com:2222/o/r",
        "https://gitlab.com/group/sub/proj.git":
            "gitlab.com/group/sub/proj",
    }

    def test_known_forms(self):
        for raw, expected in self.CASES.items():
            self.assertEqual(scanner.normalize_remote(raw), expected, raw)

    def test_unrepresentable_returns_none(self):
        for raw in ("", r"C:\data\repo", "file:///srv/git/repo.git"):
            self.assertIsNone(scanner.normalize_remote(raw), raw)

    def test_invalid_ports_and_percent_encoded_paths_are_rejected(self):
        for raw in (
                "https://example.com:0/o/r",
                "https://example.com:65536/o/r",
                "https://example.com/o/%2e%2e/r"):
            self.assertIsNone(scanner.normalize_remote(raw), raw)

    def test_collect_metadata_normalizes_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_real_repo(Path(tmp), "WithRemote")
            subprocess.run(["git", "-C", str(d), "remote", "add", "origin",
                            "git@github.com:o/r.git"], check=True)
            projects, _ = scanner.merge_scan([], [str(d)])
            self.assertEqual(projects[0]["remote"], "github.com/o/r")


class ScanRootAvailabilityTests(unittest.TestCase):
    """an unavailable scan root must never read as a healthy empty root.

    Discovery must retain per-root completeness problems so merge_scan keeps
    projects below unavailable roots instead of pruning them as vanished.
    """

    def test_missing_root_is_reported_as_scan_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "gone"
            found, problems = scanner.find_repo_dirs_with_status(
                [str(missing)], depth=4, skip_dirs=[])
            self.assertEqual(found, [])
            self.assertEqual(len(problems), 1)
            self.assertEqual(problems[0]["kind"], "scan_root")
            self.assertEqual(problems[0]["status"], "UNAVAILABLE")
            self.assertFalse(problems[0]["complete"])
            self.assertIn("does not exist", problems[0]["reason"])

    def test_find_repo_dirs_carries_root_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "ok"
            (repo / ".git").mkdir(parents=True)
            result = scanner.find_repo_dirs(
                [str(root / "gone"), str(root)], depth=4, skip_dirs=[])
            self.assertEqual([canonical_path(path) for path in result],
                             [canonical_path(repo)])
            self.assertEqual(len(result.problems), 1)
            self.assertEqual(result.problems[0]["kind"], "scan_root")
            self.assertEqual(canonical_path(result.problems[0]["path"]),
                             canonical_path(root / "gone"))

    def test_merge_keeps_project_below_unavailable_root(self):
        ancient = {"path": r"C:\Users\u\Desktop\Iron", "name": "Iron",
                   "status": "idea", "focus": "", "pinned": False,
                   "broken": False, "last_seen": "2020-01-01T00:00:00Z"}
        projects, problems = scanner.merge_scan(
            [ancient], [], failed_roots=[
                {"root": r"C:\Users\u\Desktop",
                 "path": r"C:\Users\u\Desktop",
                 "reason": "scan root incomplete: injected"}])
        self.assertTrue(any(p["path"] == ancient["path"]
                            for p in projects),
                        "project under an unavailable root must not be pruned")
        self.assertFalse(any(p.get("kind") == "move" for p in problems))

    def test_merge_still_prunes_stale_project_outside_failed_root(self):
        ancient = {"path": r"C:\gone\Zombie", "name": "Zombie",
                   "status": "idea", "focus": "", "pinned": False,
                   "broken": False, "last_seen": "2020-01-01T00:00:00Z"}
        projects, problems = scanner.merge_scan(
            [ancient], [], failed_roots=[
                {"root": r"C:\Users\u\Desktop",
                 "path": r"C:\Users\u\Desktop",
                 "reason": "unavailable"}])
        self.assertFalse(any(p["path"] == ancient["path"]
                             for p in projects))


if __name__ == "__main__":
    unittest.main()
