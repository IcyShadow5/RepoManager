import tempfile
import unittest
from pathlib import Path

from repo_manager import scanner
from tests.git_repository import (
    GIT_EXE,
    add_remote,
    canonical_path,
    add_worktree,
    checkout,
    commit,
    create_file,
    create_repository,
    delete_file,
    detach_head,
    git,
    init_repository,
    modify_file,
    stage,
)


# Test-scope metadata, not a support claim: V0.1.0 supports Windows only.
ALL = frozenset({"WINDOWS", "LINUX", "MACOS"})


@unittest.skipUnless(GIT_EXE, "Git executable is not available")
class RepositoryCompatibilityLabTests(unittest.TestCase):
    platforms = ALL

    def test_discovery_contract_for_direct_nested_bare_and_non_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = create_repository(root / "direct")
            nested = create_repository(root / "container" / "nested")
            non_repository = root / "ordinary"
            non_repository.mkdir()
            bare = init_repository(root / "reference.git", bare=True)

            found = set(scanner.find_repo_dirs([root], depth=3, skip_dirs=[]))

            found_keys = {canonical_path(path) for path in found}
            self.assertIn(canonical_path(direct), found_keys)
            self.assertIn(canonical_path(nested), found_keys)
            self.assertNotIn(canonical_path(non_repository), found_keys)
            self.assertNotIn(canonical_path(bare), found_keys)
            self.assertFalse(scanner.collect_metadata(bare)["broken"])

    def test_linked_worktree_git_file_is_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary = create_repository(root / "primary")
            linked = add_worktree(primary, root / "linked", branch="feature")

            self.assertTrue((linked / ".git").is_file())
            found = scanner.find_repo_dirs([root], depth=2, skip_dirs=[])
            self.assertIn(canonical_path(linked),
                          {canonical_path(path) for path in found})
            self.assertFalse(scanner.collect_metadata(linked)["broken"])

    def test_attached_detached_and_unborn_head_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attached = create_repository(root / "attached")
            detached = create_repository(root / "detached")
            detach_head(detached)
            unborn = init_repository(root / "unborn")

            attached_meta = scanner.collect_metadata(attached)
            detached_meta = scanner.collect_metadata(detached)
            unborn_meta = scanner.collect_metadata(unborn)

            self.assertEqual(attached_meta["branch"], "main")
            self.assertIsNotNone(attached_meta["head"])
            self.assertIsNone(detached_meta["branch"])
            self.assertIsNotNone(detached_meta["head"])
            self.assertEqual(unborn_meta["branch"], "main")
            self.assertIsNone(unborn_meta["head"])
            self.assertFalse(unborn_meta["broken"])

    def test_working_tree_status_counts_meaningful_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = create_repository(Path(tmp) / "repo")
            self.assertEqual(scanner.collect_metadata(repo)["dirty"], 0)

            modify_file(repo, "unstaged\n")
            self.assertEqual(scanner.collect_metadata(repo)["unstaged"], 1)

            stage(repo)
            self.assertEqual(scanner.collect_metadata(repo)["staged"], 1)

            modify_file(repo, "staged and unstaged\n")
            both = scanner.collect_metadata(repo)
            self.assertEqual((both["staged"], both["unstaged"]), (1, 1))

            create_file(repo, "untracked.txt", "new\n")
            self.assertEqual(scanner.collect_metadata(repo)["untracked"], 1)

            git(repo, "reset", "--hard", "-q", "HEAD")
            (repo / "untracked.txt").unlink()
            delete_file(repo)
            deleted = scanner.collect_metadata(repo)
            self.assertEqual(deleted["unstaged"], 1)
            self.assertEqual(deleted["dirty"], 1)

    def test_local_remote_names_are_observed_without_identity_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = create_repository(root / "repo")
            one = init_repository(root / "one.git", bare=True)
            two = init_repository(root / "two.git", bare=True)

            none = scanner.collect_metadata(repo)
            self.assertEqual(none["remote_names"], [])
            self.assertFalse(none["broken"])

            add_remote(repo, "backup", one)
            alternate = scanner.collect_metadata(repo)
            self.assertEqual(alternate["remote_names"], ["backup"])
            self.assertIsNone(alternate["remote"])

            add_remote(repo, "origin", two)
            multiple = scanner.collect_metadata(repo)
            self.assertEqual(multiple["remote_names"], ["backup", "origin"])
            self.assertEqual(multiple["remotes"], [])

    def test_upstream_ahead_behind_and_diverged_against_local_bare_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = init_repository(root / "remote.git", bare=True)
            local = create_repository(root / "local")
            add_remote(local, "upstream", remote)
            git(local, "push", "-qu", "upstream", "main")

            configured = scanner.collect_metadata(local)
            self.assertEqual(configured["upstream"], "upstream/main")
            self.assertEqual((configured["ahead"], configured["behind"]), (0, 0))

            commit(local, "local ahead", content="ahead\n")
            ahead = scanner.collect_metadata(local)
            self.assertEqual((ahead["ahead"], ahead["behind"]), (1, 0))

            peer = root / "peer"
            git(root, "clone", "-q", str(remote), str(peer))
            git(peer, "config", "user.name", "RepoManager Compatibility Lab")
            git(peer, "config", "user.email", "compatibility@example.invalid")
            checkout(peer, "main")
            commit(peer, "remote ahead", content="behind\n")
            git(peer, "push", "-q", "origin", "main")
            git(local, "fetch", "-q", "upstream")

            diverged = scanner.collect_metadata(local)
            self.assertEqual((diverged["ahead"], diverged["behind"]), (1, 1))

            checkout(local, "main")
            git(local, "reset", "--hard", "-q", "upstream/main")
            behind_peer = commit(peer, "remote further ahead", content="behind two\n")
            git(peer, "push", "-q", "origin", "main")
            git(local, "fetch", "-q", "upstream")
            behind = scanner.collect_metadata(local)
            self.assertNotEqual(behind["head"], behind_peer)
            self.assertEqual((behind["ahead"], behind["behind"]), (0, 1))

    def test_branch_without_upstream_is_not_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = create_repository(Path(tmp) / "repo")
            meta = scanner.collect_metadata(repo)
            self.assertIsNone(meta["upstream"])
            self.assertEqual((meta["ahead"], meta["behind"]), (None, None))
            self.assertEqual(meta["upstream_state"], "NONE")
            self.assertFalse(meta["sync_available"])
            self.assertFalse(meta["broken"])

    def test_primary_attached_and_detached_linked_worktrees_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary = create_repository(root / "primary")
            attached = add_worktree(primary, root / "attached", branch="feature")
            detached = add_worktree(primary, root / "detached", detached=True)

            records = scanner.list_worktrees(primary)
            by_path = {Path(item["path"]).resolve(): item for item in records}
            self.assertEqual(len(records), 3)
            self.assertTrue(by_path[primary.resolve()]["current"])
            self.assertEqual(by_path[attached.resolve()]["branch"], "feature")
            self.assertIsNone(by_path[detached.resolve()]["branch"])
            self.assertIsNotNone(by_path[detached.resolve()]["head"])

    def test_inspection_is_deterministic_and_semantically_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = create_repository(Path(tmp) / "repo")
            modify_file(repo, "changed\n")
            before_head = git(repo, "rev-parse", "HEAD")
            before_status = git(repo, "status", "--porcelain")
            before_config = git(repo, "config", "--local", "--list")

            first = scanner.collect_metadata(repo)
            second = scanner.collect_metadata(repo)

            self.assertEqual(first, second)
            self.assertEqual(git(repo, "rev-parse", "HEAD"), before_head)
            self.assertEqual(git(repo, "status", "--porcelain"), before_status)
            self.assertEqual(git(repo, "config", "--local", "--list"), before_config)

    def test_unusual_repository_does_not_contaminate_following_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            detached = create_repository(root / "detached")
            detach_head(detached)
            normal = create_repository(root / "normal")

            self.assertIsNone(scanner.collect_metadata(detached)["branch"])
            normal_meta = scanner.collect_metadata(normal)
            self.assertEqual(normal_meta["branch"], "main")
            self.assertEqual(normal_meta["dirty"], 0)


if __name__ == "__main__":
    unittest.main()
