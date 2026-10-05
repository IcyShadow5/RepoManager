"""LEGACY / PARITY REFERENCE — NOT CURRENT UI PROOF."""
"""R2.3 fail-closed metadata observation regression tests (Temp-only)."""
import queue
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import scanner
from repo_manager.main import apply_metadata_refresh
from repo_manager import main as main_module


def make_repo(root: Path, name: str = "R") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=d, check=True)
    subprocess.run(["git", "-C", str(d), "config", "user.email", "t@t.t"],
                   check=True)
    subprocess.run(["git", "-C", str(d), "config", "user.name", "t"],
                   check=True)
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(d), "-c", "user.name=t",
         "-c", "user.email=t@t.t", "commit", "-qm", "init"],
        check=True)
    return d


def git_ok(repo: Path, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True)


def curated(old: dict) -> dict:
    out = dict(old)
    out.update({
        "project_id": "pid-1",
        "name": "Curated",
        "status": "active",
        "focus": "keep",
        "pinned": True,
        "added_at": "2026-01-01T00:00:00Z",
        "last_seen": "2026-01-01T00:00:00Z",
    })
    return out


class FailurePreservationTests(unittest.TestCase):
    def test_01_branch_transient_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = scanner.collect_metadata(str(repo))
            self.assertEqual(cached["branch"], "main")
            real_git = scanner._git

            def fail_branch(path, *args):
                if tuple(args) == ("branch", "--show-current"):
                    return None
                return real_git(path, *args)

            with mock.patch.object(scanner, "_git", side_effect=fail_branch):
                meta, observed = scanner.collect_metadata_observation(
                    str(repo))
            self.assertNotIn("branch", observed)
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertEqual(proj[0]["branch"], "main")

    def test_02_head_transient_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = scanner.collect_metadata(str(repo))
            real_result = scanner._git_result

            def fail_head(path, *args):
                if tuple(args) == ("rev-parse", "HEAD"):
                    return (1, "", "fatal: transient glitch")
                return real_result(path, *args)

            with mock.patch.object(scanner, "_git_result",
                                   side_effect=fail_head):
                meta, observed = scanner.collect_metadata_observation(
                    str(repo))
            self.assertNotIn("head", observed)
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertEqual(proj[0]["head"], cached["head"])
            self.assertIsNotNone(proj[0]["head"])

    def test_03_status_timeout_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = scanner.collect_metadata(str(repo))
            self.assertEqual(cached["dirty"], 0)
            real_git = scanner._git

            def fail_status(path, *args):
                if tuple(args) == ("status", "--porcelain"):
                    return None
                return real_git(path, *args)

            with mock.patch.object(scanner, "_git", side_effect=fail_status):
                meta, observed = scanner.collect_metadata_observation(
                    str(repo))
            self.assertNotIn("dirty", observed)
            self.assertNotIn("status_available", observed)
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertEqual(proj[0]["dirty"], 0)
            self.assertEqual(proj[0]["staged"], 0)
            self.assertTrue(proj[0]["status_available"])

    def test_04_upstream_generic_failure_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare = root / "remote.git"
            subprocess.run(["git", "init", "--bare", "-q", str(bare)],
                           check=True)
            local = make_repo(root, "local")
            git_ok(local, "remote", "add", "origin", str(bare))
            git_ok(local, "push", "-qu", "origin", "main")
            cached = scanner.collect_metadata(str(local))
            self.assertEqual(cached["upstream"], "origin/main")
            real_result = scanner._git_result

            def fail_upstream(path, *args):
                if tuple(args) == ("rev-parse", "--abbrev-ref",
                                        "@{upstream}"):
                    return (1, "", "fatal: transient glitch")
                return real_result(path, *args)

            with mock.patch.object(scanner, "_git_result",
                                   side_effect=fail_upstream):
                meta, observed = scanner.collect_metadata_observation(
                    str(local))
            self.assertNotIn("upstream", observed)
            self.assertNotIn("upstream_state", observed)
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertEqual(proj[0]["upstream"], "origin/main")
            self.assertEqual(proj[0]["upstream_state"], "TRACKED")

    def test_05_ahead_behind_failure_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare = root / "remote.git"
            subprocess.run(["git", "init", "--bare", "-q", str(bare)],
                           check=True)
            local = make_repo(root, "local")
            git_ok(local, "remote", "add", "origin", str(bare))
            git_ok(local, "push", "-qu", "origin", "main")
            (local / "f.txt").write_text("v2\n", encoding="utf-8")
            git_ok(local, "add", "-A")
            git_ok(local, "commit", "-qm", "ahead")
            cached = scanner.collect_metadata(str(local))
            self.assertEqual(cached["ahead"], 1)
            real_git = scanner._git

            def fail_counts(path, *args):
                if (len(args) >= 2 and args[0] == "rev-list"
                        and args[1] == "--count"):
                    return None
                return real_git(path, *args)

            with mock.patch.object(scanner, "_git", side_effect=fail_counts):
                meta, observed = scanner.collect_metadata_observation(
                    str(local))
            self.assertNotIn("ahead", observed)
            self.assertNotIn("behind", observed)
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertEqual(proj[0]["ahead"], 1)
            self.assertEqual(proj[0]["behind"], 0)
            self.assertTrue(proj[0]["sync_available"])
            # successful branch still observed in same record
            self.assertIn("branch", observed)
            self.assertEqual(meta["branch"], "main")

    def test_06_log_failure_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = scanner.collect_metadata(str(repo))
            real_result = scanner._git_result

            def fail_log(path, *args):
                if tuple(args)[:2] == ("log", "-1"):
                    return (1, "", "fatal: transient glitch")
                return real_result(path, *args)

            with mock.patch.object(scanner, "_git_result",
                                   side_effect=fail_log):
                meta, observed = scanner.collect_metadata_observation(
                    str(repo))
            self.assertNotIn("last_commit_date", observed)
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertEqual(proj[0]["last_commit_date"],
                             cached["last_commit_date"])
            self.assertEqual(proj[0]["last_commit_msg"],
                             cached["last_commit_msg"])

    def test_07_remote_failure_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            git_ok(repo, "remote", "add", "origin",
                   "git@github.com:owner/repo.git")
            cached = scanner.collect_metadata(str(repo))
            self.assertEqual(cached["remote"], "github.com/owner/repo")
            real_git = scanner._git

            def fail_remote(path, *args):
                if tuple(args) == ("remote", "-v"):
                    return None
                return real_git(path, *args)

            with mock.patch.object(scanner, "_git", side_effect=fail_remote):
                meta, observed = scanner.collect_metadata_observation(
                    str(repo))
            self.assertNotIn("remote", observed)
            self.assertNotIn("remotes", observed)
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertEqual(proj[0]["remote"], "github.com/owner/repo")
            self.assertEqual(proj[0]["remotes"], ["github.com/owner/repo"])
            self.assertEqual(proj[0]["fingerprint"]["remotes"],
                             ["github.com/owner/repo"])

    def test_08_worktree_failure_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = scanner.collect_metadata(str(repo))
            self.assertTrue(cached["worktrees_available"])
            with mock.patch.object(scanner, "_worktree_state",
                                   return_value=None):
                meta, observed = scanner.collect_metadata_observation(
                    str(repo))
            self.assertNotIn("worktrees", observed)
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertEqual(proj[0]["worktrees"], cached["worktrees"])
            self.assertTrue(proj[0]["worktrees_available"])

    def test_09_roots_failure_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = scanner.collect_metadata(str(repo))
            self.assertTrue(cached["fingerprint"]["root_commits"])
            real_result = scanner._git_result

            def fail_roots(path, *args):
                if (tuple(args)[:2] == ("rev-list", "--max-parents=0")):
                    return (1, "", "fatal: transient glitch")
                return real_result(path, *args)

            with mock.patch.object(scanner, "_git_result",
                                   side_effect=fail_roots):
                meta, observed = scanner.collect_metadata_observation(
                    str(repo))
            self.assertNotIn("fingerprint", observed)
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertEqual(proj[0]["fingerprint"]["root_commits"],
                             cached["fingerprint"]["root_commits"])

    def test_10_infrastructure_failure_not_broken(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = scanner.collect_metadata(str(repo))
            cached = curated(cached)
            cached["branch"] = "main"
            import subprocess as sp

            def always_timeout(*args, **kwargs):
                raise sp.TimeoutExpired(cmd=args, timeout=10)

            with mock.patch.object(scanner.subprocess, "run",
                                   side_effect=always_timeout):
                meta, observed = scanner.collect_metadata_observation(
                    str(repo))
            self.assertEqual(observed, frozenset())
            self.assertFalse(meta["broken"])
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertEqual(proj[0]["branch"], "main")
            self.assertEqual(proj[0]["head"], cached["head"])
            self.assertEqual(proj[0]["dirty"], cached["dirty"])
            self.assertFalse(proj[0]["broken"])


class LegitimateClearingTests(unittest.TestCase):
    def test_11_attached_to_detached_clears_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = scanner.collect_metadata(str(repo))
            self.assertEqual(cached["branch"], "main")
            git_ok(repo, "checkout", "--detach", "-q", "HEAD")
            meta, observed = scanner.collect_metadata_observation(str(repo))
            self.assertIn("branch", observed)
            self.assertIsNone(meta["branch"])
            self.assertIsNotNone(meta["head"])
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertIsNone(proj[0]["branch"])
            self.assertEqual(proj[0]["head"], meta["head"])

    def test_12_tracked_to_no_upstream_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare = root / "remote.git"
            subprocess.run(["git", "init", "--bare", "-q", str(bare)],
                           check=True)
            local = make_repo(root, "local")
            git_ok(local, "remote", "add", "origin", str(bare))
            git_ok(local, "push", "-qu", "origin", "main")
            cached = scanner.collect_metadata(str(local))
            self.assertEqual(cached["upstream_state"], "TRACKED")
            git_ok(local, "branch", "--unset-upstream")
            meta, observed = scanner.collect_metadata_observation(str(local))
            self.assertIn("upstream", observed)
            self.assertEqual(meta["upstream_state"], "NONE")
            self.assertIsNone(meta["upstream"])
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertIsNone(proj[0]["upstream"])
            self.assertEqual(proj[0]["upstream_state"], "NONE")
            self.assertIsNone(proj[0]["ahead"])
            self.assertIsNone(proj[0]["behind"])
            self.assertFalse(proj[0]["sync_available"])

    def test_13_remote_removal_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            git_ok(repo, "remote", "add", "origin",
                   "git@github.com:owner/repo.git")
            cached = scanner.collect_metadata(str(repo))
            self.assertEqual(cached["remote"], "github.com/owner/repo")
            git_ok(repo, "remote", "remove", "origin")
            meta, observed = scanner.collect_metadata_observation(str(repo))
            self.assertIn("remote", observed)
            self.assertIsNone(meta["remote"])
            self.assertEqual(meta["remotes"], [])
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertIsNone(proj[0]["remote"])
            self.assertEqual(proj[0]["remotes"], [])
            self.assertEqual(proj[0]["fingerprint"]["remotes"], [])

    def test_14_unborn_repo_legit_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty"
            empty.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=empty,
                           check=True)
            meta, observed = scanner.collect_metadata(str(empty)), None
            meta2, observed2 = scanner.collect_metadata_observation(
                str(empty))
            self.assertFalse(meta2["broken"])
            self.assertTrue(meta2["repository_observed"])
            self.assertIsNone(meta2["head"])
            self.assertIn("head", observed2)
            self.assertEqual(meta2["branch"], "main")
            # stale committed head must clear to None on genuine empty
            cached = {"path": meta2["path"], "branch": "main",
                      "head": "abc123" * 6 + "ab",
                      "last_commit_date": "2026-01-01",
                      "last_commit_msg": "old",
                      "fingerprint": {"remotes": [],
                                      "root_commits": ["abc"]}}
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta2], [observed2])
            self.assertIsNone(proj[0]["head"])
            self.assertIsNone(proj[0]["last_commit_date"])
            self.assertEqual(proj[0]["fingerprint"]["root_commits"], [])

    def test_15_new_head_and_log_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = scanner.collect_metadata(str(repo))
            (repo / "f.txt").write_text("v2\n", encoding="utf-8")
            git_ok(repo, "add", "-A")
            git_ok(repo, "commit", "-qm", "second commit message")
            meta, observed = scanner.collect_metadata_observation(str(repo))
            self.assertIn("head", observed)
            self.assertIn("last_commit_date", observed)
            self.assertNotEqual(meta["head"], cached["head"])
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertEqual(proj[0]["head"], meta["head"])
            self.assertEqual(proj[0]["last_commit_msg"], "second commit message")

    def test_16_missing_repo_can_mark_broken(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = curated(scanner.collect_metadata(str(repo)))
            missing = str(Path(tmp) / "gone")
            meta, observed = scanner.collect_metadata_observation(missing)
            self.assertTrue(meta["broken"])
            self.assertIn("broken", observed)
            proj = [dict(cached)]
            proj[0]["path"] = missing
            meta["path"] = missing
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertTrue(proj[0]["broken"])
            self.assertIsNone(proj[0]["branch"])




class PersistenceTests(unittest.TestCase):
    def test_20_queue_persist_preserves_failed_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            git_ok(repo, "remote", "add", "origin",
                   "git@github.com:owner/repo.git")
            cached = curated(scanner.collect_metadata(str(repo)))
            cached["dirty"] = 0
            real_git = scanner._git

            def fail_status_remote(path, *args):
                if tuple(args) in (("status", "--porcelain"),
                                        ("remote", "-v")):
                    return None
                return real_git(path, *args)

            with mock.patch.object(scanner, "_git",
                                   side_effect=fail_status_remote):
                meta, observed = scanner.collect_metadata_observation(
                    str(repo))
            app = object.__new__(main_module.RepoManagerApp)
            app.projects = [dict(cached)]
            app._scan_queue = queue.Queue()
            app._metadata_gen = 1
            app._scanning = True
            app.scan_btn = mock.Mock()
            app._populate_coalesced = mock.Mock()
            app._schedule_after = mock.Mock()
            pid = app.projects[0]["project_id"]
            app._scan_queue.put(("meta", [(pid, cached["path"], meta,
                                           observed, None)], 1))
            with mock.patch.object(main_module.store,
                                   "save_projects") as save:
                app._drain_scan_queue()
            self.assertEqual(app.projects[0]["dirty"], 0)
            self.assertEqual(app.projects[0]["remote"],
                             "github.com/owner/repo")
            saved = save.call_args[0][0]
            self.assertEqual(saved[0]["dirty"], 0)
            self.assertEqual(saved[0]["remote"], "github.com/owner/repo")
            # validity never persisted
            self.assertNotIn("_observed_fields", saved[0])
            self.assertNotIn("observed", saved[0])

    def test_dead_remote_reachable_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            meta, observed = scanner.collect_metadata_observation(str(repo))
            self.assertIsNone(meta["remote_reachable"])
            self.assertNotIn("remote_reachable", observed)
            cached = curated(scanner.collect_metadata(str(repo)))
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertIsNone(proj[0]["remote_reachable"])


class WholeRepoPositiveEvidenceTests(unittest.TestCase):
    """FIX-1 A: non-zero Git exit alone never condemns a checkout."""

    def test_21_permission_style_failure_preserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = curated(scanner.collect_metadata(str(repo)))
            self.assertEqual(
                scanner.repository_marker_evidence(str(repo)), "valid")
            real_result = scanner._git_result

            def dubious(path, *args):
                if tuple(args) == ("rev-parse", "--git-dir"):
                    return (128, "",
                            "fatal: detected dubious ownership in repository")
                return real_result(path, *args)

            with mock.patch.object(scanner, "_git_result",
                                   side_effect=dubious):
                meta, observed = scanner.collect_metadata_observation(
                    str(repo))
            self.assertFalse(meta["broken"])
            self.assertEqual(observed, frozenset())
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertEqual(proj[0]["branch"], cached["branch"])
            self.assertEqual(proj[0]["head"], cached["head"])
            self.assertEqual(proj[0]["dirty"], cached["dirty"])
            self.assertFalse(proj[0]["broken"])

    def test_22_translated_stderr_preserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = curated(scanner.collect_metadata(str(repo)))
            real_result = scanner._git_result

            def unknown_stderr(path, *args):
                if tuple(args) == ("rev-parse", "--git-dir"):
                    return (128, "", "schwerer unbekannter Fehler 0x5")
                return real_result(path, *args)

            with mock.patch.object(scanner, "_git_result",
                                   side_effect=unknown_stderr):
                meta, observed = scanner.collect_metadata_observation(
                    str(repo))
            self.assertFalse(meta["broken"])
            self.assertEqual(observed, frozenset())
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta], [observed])
            self.assertEqual(proj[0]["branch"], cached["branch"])
            self.assertFalse(proj[0]["broken"])

    def test_23_missing_path_positive_broken(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "gone")
            self.assertEqual(
                scanner.repository_marker_evidence(missing), "missing")
            meta, observed = scanner.collect_metadata_observation(missing)
            self.assertTrue(meta["broken"])
            self.assertIn("broken", observed)
            self.assertIn("branch", observed)

    def test_24_marker_absent_positive_broken(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain"
            plain.mkdir()
            self.assertEqual(
                scanner.repository_marker_evidence(str(plain)), "absent")
            meta, observed = scanner.collect_metadata_observation(str(plain))
            self.assertTrue(meta["broken"])
            self.assertIn("broken", observed)

    def test_25_corrupt_marker_invalid_broken_but_ambiguous_preserves(self):
        # Filesystem-proven corrupt markers still condemn (documents the
        # conservative boundary); unreadable/ambiguous markers preserve.
        with tempfile.TemporaryDirectory() as tmp:
            empty_git = Path(tmp) / "emptygit"
            empty_git.mkdir()
            (empty_git / ".git").mkdir()
            self.assertEqual(
                scanner.repository_marker_evidence(str(empty_git)),
                "invalid")
            meta, observed = scanner.collect_metadata_observation(
                str(empty_git))
            self.assertTrue(meta["broken"])

            garbage = Path(tmp) / "garbage"
            garbage.mkdir()
            (garbage / ".git").write_text("garbage\n", encoding="utf-8")
            self.assertEqual(
                scanner.repository_marker_evidence(str(garbage)), "invalid")
            meta_g, _ = scanner.collect_metadata_observation(str(garbage))
            self.assertTrue(meta_g["broken"])

            repo = make_repo(Path(tmp), "valid")
            cached = curated(scanner.collect_metadata(str(repo)))
            real_result = scanner._git_result

            def git_fails(path, *args):
                if tuple(args) == ("rev-parse", "--git-dir"):
                    return (128, "", "fatal: ambiguous failure")
                return real_result(path, *args)

            with mock.patch.object(scanner, "_git_result",
                                   side_effect=git_fails), \
                    mock.patch.object(scanner,
                                      "repository_marker_evidence",
                                      return_value="ambiguous"):
                meta_a, observed_a = scanner.collect_metadata_observation(
                    str(repo))
            self.assertFalse(meta_a["broken"])
            self.assertEqual(observed_a, frozenset())
            proj = [dict(cached)]
            apply_metadata_refresh(proj, [meta_a], [observed_a])
            self.assertEqual(proj[0]["branch"], cached["branch"])
            self.assertFalse(proj[0]["broken"])






class NoValidityPersistedTests(unittest.TestCase):
    """FIX-1: validity sets never reach durable records."""

    def test_32_queue_and_merge_persist_no_validity(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = curated(scanner.collect_metadata(str(repo)))
            meta, observed = scanner.collect_metadata_observation(str(repo))
            app = object.__new__(main_module.RepoManagerApp)
            app.projects = [dict(cached)]
            app._scan_queue = queue.Queue()
            app._metadata_gen = 1
            app._scanning = True
            app.scan_btn = mock.Mock()
            app._populate_coalesced = mock.Mock()
            app._schedule_after = mock.Mock()
            pid = app.projects[0]["project_id"]
            app._scan_queue.put(("meta", [(pid, cached["path"], meta,
                                           observed, None)], 1))
            with mock.patch.object(main_module.store,
                                   "save_projects") as save:
                app._drain_scan_queue()
            saved = save.call_args[0][0][0]
            self.assertNotIn("_observed_fields", saved)
            self.assertNotIn("observed", saved)
            self.assertNotIn("observed_fields", saved)
            merged, _ = scanner.merge_scan([dict(cached)], [str(repo)])
            self.assertNotIn("_observed_fields", merged[0])
            self.assertNotIn("observed", merged[0])
            self.assertNotIn("observed_fields", merged[0])


if __name__ == "__main__":
    unittest.main()
