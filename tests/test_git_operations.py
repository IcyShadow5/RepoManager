"""Everyday Git integration evidence uses temporary repositories only."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from repo_manager import git_operations as ops, project_actions
from tests.git_repository import (create_repository, init_repository, create_file,
                                  git, stage, add_remote, commit, add_worktree)


class EverydayGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = create_repository(self.root / "repo")
        self.service = ops.Repository(self.path)

    def test_local_staged_commit_preserves_unstaged_content_without_remote(self):
        create_file(self.path, "tracked.txt", "staged\n")
        stage(self.path)
        create_file(self.path, "tracked.txt", "unstaged\n")
        preview = self.service.state()
        result = self.service.commit(preview, "local only")
        self.assertEqual(result.outcome, ops.SUCCESS, result.output)
        self.assertEqual(git(self.path, "show", "HEAD:tracked.txt"), "staged")
        self.assertEqual((self.path / "tracked.txt").read_text(), "unstaged\n")
        self.assertEqual(git(self.path, "remote"), "")

    def test_stage_all_and_clean_commit_guidance(self):
        create_file(self.path, "new space ü.txt", "new")
        (self.path / "tracked.txt").unlink()
        result = self.service.commit(self.service.state(), "all", stage_all=True)
        self.assertEqual(result.outcome, ops.SUCCESS, result.output)
        self.assertEqual(self.service.state().changes, ())
        self.assertEqual(self.service.commit(self.service.state(), "empty").outcome, ops.FAILED)

    def test_blank_message_and_stale_index_cancel(self):
        create_file(self.path, "new.txt", "new")
        preview = self.service.state()
        self.assertEqual(self.service.commit(preview, " ", stage_all=True).outcome, ops.FAILED)
        stage(self.path, "new.txt")
        self.assertEqual(self.service.commit(preview, "stale").outcome, ops.CANCELLED)

    def test_literal_unusual_paths_stage_and_unstage_without_discard(self):
        for name in ("-leading.txt", "[literal].txt", "space ü.txt"):
            create_file(self.path, name, "content")
        state = self.service.state()
        self.assertEqual(self.service.stage(state, state.changes).outcome, ops.SUCCESS)
        state = self.service.state()
        self.assertTrue(all(change.staged for change in state.changes))
        self.assertEqual(self.service.stage(state, state.changes, unstage=True).outcome, ops.SUCCESS)
        for name in ("-leading.txt", "[literal].txt", "space ü.txt"):
            self.assertEqual((self.path / name).read_text(), "content")

    def test_unborn_stage_unstage_and_commit(self):
        path = init_repository(self.root / "unborn")
        create_file(path, "new.txt", "content")
        service = ops.Repository(path)
        state = service.state()
        self.assertIsNone(state.head)
        self.assertEqual(service.stage(state, state.changes).outcome, ops.SUCCESS)
        state = service.state()
        self.assertEqual(service.stage(state, state.changes, unstage=True).outcome, ops.SUCCESS)
        self.assertEqual((path / "new.txt").read_text(), "content")
        self.assertEqual(service.commit(service.state(), "first", stage_all=True).outcome, ops.SUCCESS)

    def test_rename_diff_and_unstage_preserve_renamed_working_file(self):
        git(self.path, "mv", "tracked.txt", "renamed.txt")
        state = self.service.state()
        self.assertEqual(state.changes[0].original, "tracked.txt")
        self.assertIn("rename", self.service.diff(state.changes[0], staged=True))
        self.assertEqual(self.service.stage(state, state.changes, unstage=True).outcome, ops.SUCCESS)
        self.assertTrue((self.path / "renamed.txt").exists())
        self.assertFalse((self.path / "tracked.txt").exists())

    def test_failed_hook_reports_retained_index(self):
        hook = self.path / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        git(self.path, "config", "core.hooksPath", str(hook.parent))
        create_file(self.path, "new.txt", "content")
        result = self.service.commit(self.service.state(), "blocked", stage_all=True)
        self.assertEqual(result.outcome, ops.PARTIAL, result.output)
        self.assertIn("Index changes remain", result.output)
        self.assertTrue(any(change.staged for change in self.service.state().changes))

    def test_authorization_revoked_starts_no_mutation(self):
        preview = self.service.state()
        service = ops.Repository(self.path, authorize=lambda: False)
        self.assertEqual(service.commit(preview, "no", stage_all=True).outcome, ops.CANCELLED)

    def remote(self):
        target = init_repository(self.root / "remote", bare=True)
        add_remote(self.path, "origin", target)
        return target

    def test_push_uses_different_upstream_destination(self):
        target = self.remote()
        approval = self.service.approve_network("push", "origin", "release", set_upstream=True)
        self.assertEqual(self.service.network(approval).outcome, ops.SUCCESS)
        create_file(self.path, "new.txt", "new")
        commit(self.path, "next")
        approval = self.service.approve_network("push", "origin")
        self.assertEqual(approval.destination, "release")
        self.assertEqual(self.service.network(approval).outcome, ops.SUCCESS)
        self.assertEqual(git(target, "rev-parse", "release"), git(self.path, "rev-parse", "HEAD"))
        with self.assertRaises(subprocess.CalledProcessError):
            git(target, "rev-parse", "refs/heads/main")

    def test_remote_and_head_change_cancel_approved_push(self):
        self.remote()
        approval = self.service.approve_network("push", "origin")
        git(self.path, "remote", "set-url", "origin", str(self.root / "other"))
        self.assertEqual(self.service.network(approval).outcome, ops.CANCELLED)

    def test_multiple_push_destinations_and_mirror_are_blocked(self):
        self.remote()
        git(self.path, "config", "--add", "remote.origin.pushurl", str(self.root / "one"))
        git(self.path, "config", "--add", "remote.origin.pushurl", str(self.root / "two"))
        with self.assertRaises(ops.GitError):
            self.service.approve_network("push", "origin")
        git(self.path, "config", "--unset-all", "remote.origin.pushurl")
        git(self.path, "config", "remote.origin.mirror", "true")
        with self.assertRaises(ops.GitError):
            self.service.approve_network("push", "origin")

    def test_fetch_changes_refs_not_checkout_and_does_not_prune(self):
        target = self.remote()
        self.assertEqual(self.service.network(self.service.approve_network("push", "origin", "main")).outcome, ops.SUCCESS)
        git(self.path, "update-ref", "refs/remotes/origin/keep", "HEAD")
        before = (self.path / "tracked.txt").read_bytes()
        self.assertEqual(self.service.network(self.service.approve_network("fetch", "origin")).outcome, ops.SUCCESS)
        self.assertEqual(before, (self.path / "tracked.txt").read_bytes())
        self.assertTrue(git(self.path, "rev-parse", "refs/remotes/origin/keep"))

    def test_read_only_diff_history_and_remotes_preserve_index_and_head(self):
        create_file(self.path, "tracked.txt", "different")
        before = self.service.state()
        self.assertIn("different", self.service.diff(before.changes[0]))
        self.assertIn("initial", self.service.history())
        self.assertIn("initial", self.service.commit_details(before.head))
        self.assertEqual(self.service.remotes(), ())
        self.assertEqual(before, self.service.state())

    def test_binary_and_large_untracked_previews(self):
        create_file(self.path, "large.txt", "x" * (ops.OUTPUT_LIMIT + 1))
        (self.path / "binary.dat").write_bytes(b"a\0b")
        state = self.service.state()
        previews = {change.path: self.service.diff(change) for change in state.changes}
        self.assertIn("truncated", previews["large.txt"])
        self.assertIn("Binary", previews["binary.dat"])

    def test_non_fast_forward_push_is_rejected_without_force(self):
        target = self.remote()
        self.assertEqual(self.service.network(self.service.approve_network("push", "origin", "main")).outcome, ops.SUCCESS)
        other = create_repository(self.root / "other")
        add_remote(other, "origin", target)
        git(other, "fetch", "origin")
        git(other, "reset", "--hard", "origin/main")
        create_file(other, "remote-only.txt", "remote")
        commit(other, "remote change")
        git(other, "push", "origin", "HEAD:main")
        remote_head = git(target, "rev-parse", "main")
        create_file(self.path, "local-only.txt", "local")
        commit(self.path, "local change")
        result = self.service.network(self.service.approve_network("push", "origin", "main"))
        self.assertEqual(result.outcome, ops.FAILED)
        self.assertEqual(git(target, "rev-parse", "main"), remote_head)

    def test_pull_fast_forwards_clean_checkout_and_blocks_dirty(self):
        target = self.remote()
        self.assertEqual(self.service.network(self.service.approve_network("push", "origin", "main", set_upstream=True)).outcome, ops.SUCCESS)
        other = create_repository(self.root / "other")
        add_remote(other, "origin", target)
        git(other, "fetch", "origin")
        git(other, "reset", "--hard", "origin/main")
        create_file(other, "remote-only.txt", "remote")
        commit(other, "remote change")
        git(other, "push", "origin", "HEAD:main")
        approval = self.service.approve_network("pull", "origin")
        create_file(self.path, "tracked.txt", "dirty")
        self.assertEqual(self.service.network(approval).outcome, ops.FAILED)
        self.assertEqual((self.path / "tracked.txt").read_text(), "dirty")
        git(self.path, "restore", "tracked.txt")
        self.assertEqual(self.service.network(approval).outcome, ops.SUCCESS)
        self.assertEqual((self.path / "remote-only.txt").read_text(), "remote")

    def test_detached_and_unborn_push_are_blocked(self):
        self.remote()
        git(self.path, "checkout", "--detach", "HEAD")
        with self.assertRaises(ops.GitError):
            self.service.approve_network("push", "origin")
        unborn = init_repository(self.root / "unborn")
        add_remote(unborn, "origin", self.root / "remote")
        with self.assertRaises(ops.GitError):
            ops.Repository(unborn).approve_network("push", "origin")

    def test_conflict_index_never_authorizes_commit_or_stage(self):
        git(self.path, "checkout", "-b", "conflict")
        create_file(self.path, "tracked.txt", "conflicting branch\n")
        commit(self.path, "branch change")
        git(self.path, "checkout", "main")
        create_file(self.path, "tracked.txt", "main branch\n")
        commit(self.path, "main change")
        with self.assertRaises(subprocess.CalledProcessError):
            git(self.path, "merge", "conflict")
        state = self.service.state()
        self.assertTrue(state.changes[0].unsupported)
        self.assertEqual(self.service.commit(state, "no").outcome, ops.FAILED)
        self.assertEqual(self.service.stage(state, state.changes).outcome, ops.FAILED)

    def test_linked_worktree_shares_physical_mutation_lock(self):
        from repo_manager.git_targets import GitMutationGuard, repository_marker_identity
        linked = add_worktree(self.path, self.root / "linked", branch="feature")
        key = repository_marker_identity(str(self.path))
        self.assertEqual(key, repository_marker_identity(str(linked)))
        guard = GitMutationGuard()
        self.assertTrue(guard.acquire(key))
        self.assertFalse(guard.acquire(repository_marker_identity(str(linked))))
        guard.release(key)

    def test_nonstandard_fetch_refspec_is_blocked(self):
        self.remote()
        git(self.path, "config", "remote.origin.fetch", "+refs/heads/*:refs/heads/*")
        with self.assertRaises(ops.GitError):
            self.service.approve_network("fetch", "origin")

    def test_timeout_after_write_is_unknown_not_retried(self):
        with mock.patch.object(ops.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 120)) as run:
            with self.assertRaises(ops.GitError) as caught:
                self.service.run("commit", "-m", "m", write=True)
        self.assertEqual(caught.exception.outcome, ops.UNKNOWN)
        run.assert_called_once()


class ParsingAndActionsTests(unittest.TestCase):
    def test_nul_status_keeps_literal_paths_and_rename_source(self):
        changes = ops.parse_status("R  renamed\0old\0?? line\nname\0")
        self.assertEqual(changes[0].paths, ("renamed", "old"))
        self.assertEqual(changes[1].path, "line\nname")
        self.assertIn("\\n", changes[1].label)
        with self.assertRaises(ops.GitError):
            ops.parse_status("R  renamed\0")

    def test_redaction(self):
        text = ops.redact("https://user:secret@example.com/repo?token=secret#secret")
        self.assertEqual(text, "https://example.com/repo")

    def test_target_never_falls_back_to_equal_legacy_record(self):
        project = {"path": "C:/repo"}
        target = project_actions.Target.capture(project)
        self.assertIs(target.resolve([project]), project)
        self.assertIsNone(target.resolve([dict(project)]))
        project["path"] = "C:/new"
        self.assertIsNone(target.resolve([project]))

    def test_capability_and_local_only_commit(self):
        project = {"project_id": "p", "path": "C:/repo"}
        self.assertTrue(project_actions.capability("commit", project).enabled)
        self.assertFalse(project_actions.capability("push", project).enabled)
        self.assertFalse(project_actions.capability("commit", project, busy=True).enabled)
        self.assertFalse(project_actions.capability("changes", {"folder_path": "C:/folder"}).enabled)


if __name__ == "__main__":
    unittest.main()
