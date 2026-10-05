"""Ambient Git paths/config cannot change the authorized real repository."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from repo_manager import git_operations as ops, scanner
from repo_manager.git_environment import git_environment
from tests.git_repository import create_repository, create_file, commit, add_remote, init_repository, add_worktree


class GitEnvironmentTests(unittest.TestCase):
    def test_copy_removes_case_insensitive_redirection_and_config_injection(self):
        source = {"PATH": "keep", "GIT_SSH_COMMAND": "configured transport",
                  "git_dir": "other repo", "GIT_CONFIG_COUNT": "1",
                  "GIT_CONFIG_KEY_0": "core.worktree", "GIT_CONFIG_VALUE_0": "other",
                  "GIT_TERMINAL_PROMPT": "1", "GIT_OPTIONAL_LOCKS": "1", "LC_ALL": "other"}
        before = source.copy()
        clean = git_environment(read_only=True, inherited=source)
        self.assertEqual(source, before)
        self.assertEqual(clean, {"PATH": "keep", "GIT_SSH_COMMAND": "configured transport",
                                "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
        self.assertNotIn("GIT_OPTIONAL_LOCKS", git_environment(inherited=source))


class PoisonedRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a = create_repository(self.root / "authorized A")
        self.b = create_repository(self.root / "protected B")
        create_file(self.b, "tracked.txt", "B committed sentinel\n")
        commit(self.b, "B distinct commit")
        create_file(self.a, "tracked.txt", "A changed content\n")
        create_file(self.b, "tracked.txt", "B unstaged sentinel\n")
        self.base = os.environ.copy()
        self.base.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
        self.repo = ops.Repository(self.a)
        self.poison = {
            "GIT_DIR": str(self.b / ".git"), "GIT_WORK_TREE": str(self.b),
            "GIT_INDEX_FILE": str(self.b / ".git/index"), "GIT_COMMON_DIR": str(self.b / ".git"),
            "GIT_OBJECT_DIRECTORY": str(self.b / ".git/objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(self.b / ".git/objects"),
            "GIT_NAMESPACE": "poisoned", "GIT_CEILING_DIRECTORIES": str(self.root),
            "GIT_CONFIG": str(self.b / ".git/config"),
            "GIT_CONFIG_SYSTEM": str(self.b / ".git/config"),
            "GIT_CONFIG_GLOBAL": str(self.b / ".git/config"),
            "GIT_CONFIG_PARAMETERS": "'core.worktree=other'",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.worktree",
            "GIT_CONFIG_VALUE_0": str(self.b),
        }

    def raw(self, path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args], env=self.base,
            timeout=30, creationflags=0x08000000 if os.name == "nt" else 0).decode().strip()

    def snapshot_b(self):
        return {p.relative_to(self.b).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.b.rglob('*') if p.is_file()}

    def test_unsanitized_git_really_can_redirect_to_b(self):
        env = {**self.base, "GIT_DIR": str(self.b / ".git"), "GIT_WORK_TREE": str(self.b)}
        actual = subprocess.check_output(["git", "-C", str(self.a), "rev-parse", "HEAD"],
            env=env, timeout=30, creationflags=0x08000000 if os.name == "nt" else 0).decode().strip()
        self.assertEqual(actual, self.raw(self.b, "rev-parse", "HEAD"))
        self.assertNotEqual(actual, self.raw(self.a, "rev-parse", "HEAD"))

    def test_each_redirection_and_combination_cannot_change_scan_attribution(self):
        before = self.snapshot_b()
        expected = self.raw(self.a, "rev-parse", "HEAD")
        for poison in [{key: value} for key, value in self.poison.items()] + [self.poison]:
            with self.subTest(keys=sorted(poison)), mock.patch.dict(os.environ, poison):
                observed = scanner.collect_metadata(self.a)
                self.assertEqual(observed["head"], expected)
                self.assertEqual(self.repo.state().head, expected)
                self.assertIn("A changed content", self.repo.diff(self.repo.state().changes[0]))
        self.assertEqual(self.snapshot_b(), before)

    def test_stage_and_unstage_only_change_a_index_and_preserve_content_heads(self):
        before_b = self.snapshot_b()
        a_head = self.raw(self.a, "rev-parse", "HEAD")
        a_index = self.raw(self.a, "ls-files", "--stage")
        content = (self.a / "tracked.txt").read_bytes()
        with mock.patch.dict(os.environ, self.poison):
            state = self.repo.state()
            self.assertEqual(self.repo.stage(state, state.changes).outcome, ops.SUCCESS)
            self.assertTrue(self.repo.state().changes[0].staged)
            state = self.repo.state()
            self.assertEqual(self.repo.stage(state, state.changes, unstage=True).outcome, ops.SUCCESS)
        self.assertEqual(self.raw(self.a, "ls-files", "--stage"), a_index)
        self.assertEqual(self.raw(self.a, "rev-parse", "HEAD"), a_head)
        self.assertEqual((self.a / "tracked.txt").read_bytes(), content)
        self.assertEqual(self.snapshot_b(), before_b)

    def test_commit_is_only_in_a_and_preserves_unstaged_content(self):
        self.raw(self.a, "add", "tracked.txt")
        create_file(self.a, "tracked.txt", "A newer unstaged content\n")
        before_b = self.snapshot_b()
        previous = self.raw(self.a, "rev-parse", "HEAD")
        with mock.patch.dict(os.environ, self.poison):
            result = self.repo.commit(self.repo.state(), "authorized A only")
        self.assertEqual(result.outcome, ops.SUCCESS, result.output)
        self.assertNotEqual(self.raw(self.a, "rev-parse", "HEAD"), previous)
        self.assertEqual(self.raw(self.a, "show", "HEAD:tracked.txt"), "A changed content")
        self.assertEqual((self.a / "tracked.txt").read_text(), "A newer unstaged content\n")
        self.assertEqual(self.snapshot_b(), before_b)

    def test_linked_worktree_still_resolves_its_own_index_and_branch(self):
        linked = add_worktree(self.a, self.root / "linked A", branch="linked")
        create_file(linked, "new.txt", "linked content")
        before_b = self.snapshot_b()
        main_index = self.raw(self.a, "ls-files", "--stage")
        with mock.patch.dict(os.environ, self.poison):
            repo = ops.Repository(linked)
            state = repo.state()
            self.assertEqual(state.branch, "linked")
            self.assertEqual(repo.stage(state, state.changes).outcome, ops.SUCCESS)
            self.assertEqual(scanner.collect_metadata(linked)["branch"], "linked")
        self.assertEqual(self.raw(self.a, "ls-files", "--stage"), main_index)
        self.assertEqual(self.snapshot_b(), before_b)

    def test_worktree_mutation_uses_a_common_repository(self):
        before_b = self.snapshot_b()
        target = self.root / "created linked A"
        with mock.patch.dict(os.environ, self.poison):
            result = scanner.create_worktree(str(self.a), str(target), "HEAD")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.raw(target, "rev-parse", "HEAD"), self.raw(self.a, "rev-parse", "HEAD"))
        self.assertEqual(self.snapshot_b(), before_b)

    def test_fetch_pull_push_use_only_approved_a_and_remote_not_injected_config(self):
        # Both repositories have the same remote name but different local destinations.
        remote_a = init_repository(self.root / "remote A", bare=True)
        remote_b = init_repository(self.root / "remote B", bare=True)
        add_remote(self.a, "origin", remote_a)
        add_remote(self.b, "origin", remote_b)
        self.raw(self.a, "restore", "tracked.txt")
        self.raw(self.a, "push", "--set-upstream", "origin", "main")
        next_commit = self.raw(self.a, "commit-tree", self.raw(self.a, "rev-parse", "HEAD^{tree}"),
                               "-p", self.raw(self.a, "rev-parse", "HEAD"), "-m", "remote advance")
        self.raw(self.a, "push", "origin", next_commit + ':refs/heads/main')
        before_b = self.snapshot_b()
        b_remote_before = {p.relative_to(remote_b).as_posix(): p.read_bytes()
                           for p in remote_b.rglob('*') if p.is_file()}
        poison = {**self.poison, "GIT_CONFIG_COUNT": "1",
                  "GIT_CONFIG_KEY_0": "remote.origin.url", "GIT_CONFIG_VALUE_0": str(remote_b)}
        for operation in ("fetch", "pull", "push"):
            if operation == 'push':
                create_file(self.a, 'tracked.txt', 'A new pushed content\n')
                self.raw(self.a, 'add', 'tracked.txt')
                self.raw(self.a, 'commit', '-qm', 'A outgoing commit')
            with self.subTest(operation=operation), mock.patch.dict(os.environ, poison):
                approval = self.repo.approve_network(operation, "origin")
                self.assertEqual(approval.remote.fetch_urls, (str(remote_a.resolve()),))
                result = self.repo.network(approval)
                self.assertEqual(result.outcome, ops.SUCCESS, result.output)
            if operation == 'fetch':
                self.assertEqual(self.raw(self.a, 'rev-parse', 'refs/remotes/origin/main'), next_commit)
            elif operation == 'pull':
                self.assertEqual(self.raw(self.a, 'rev-parse', 'HEAD'), next_commit)
        self.assertEqual(self.raw(remote_a, "rev-parse", "refs/heads/main"), self.raw(self.a, "rev-parse", "HEAD"))
        self.assertEqual(self.snapshot_b(), before_b)
        self.assertEqual({p.relative_to(remote_b).as_posix(): p.read_bytes()
                          for p in remote_b.rglob('*') if p.is_file()}, b_remote_before)
