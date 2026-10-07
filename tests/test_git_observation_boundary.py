"""Real Git must not execute repository-controlled observation helpers."""
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock

from repo_manager import git_observation, git_operations, health, scanner, windows_process
from repo_manager.repository_service import RepositorySession
from tests.git_repository import create_repository, git, add_worktree


class GitObservationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = create_repository(self.root / 'repository')
        self.marker = self.root / 'helper-executed.txt'
        self.helper = self.root / 'helper.py'
        self.helper.write_text(
            'from pathlib import Path\nimport sys\n'
            f'Path({str(self.marker)!r}).write_text("executed")\n'
            'sys.stdout.buffer.write(sys.stdin.buffer.read() if len(sys.argv) == 1 else b"\\0")\n',
            encoding='utf-8')
        self.command = shlex.quote(Path(sys.executable).as_posix()) + ' ' + shlex.quote(self.helper.as_posix())

    def filter(self, repo=None, *, process=False):
        repo = repo or self.repo
        git(repo, 'config', 'filter.sentinel.' + ('process' if process else 'clean'), self.command)
        (repo / '.gitattributes').write_text('tracked.txt filter=sentinel\n', encoding='utf-8')
        # Same length as the committed bytes forces content comparison instead
        # of Git's size-only dirty shortcut.
        (repo / 'tracked.txt').write_text('changed\n', encoding='utf-8')

    def assert_unobserved(self, repo=None):
        metadata, observed = scanner.collect_metadata_observation(repo or self.repo)
        self.assertFalse(self.marker.exists(), 'repository helper executed during scan')
        self.assertFalse(metadata['status_available'])
        self.assertIsNone(metadata['dirty'])
        self.assertNotIn('dirty', observed)
        self.assertEqual(metadata['branch'], 'main')

    def test_fsmonitor_cannot_execute_during_scan(self):
        git(self.repo, 'config', 'core.fsmonitor', self.command)
        scanner.collect_metadata_observation(self.repo)
        self.assertFalse(self.marker.exists(), 'fsmonitor helper executed during scan')

    def test_fsmonitor_cannot_execute_during_changes_status(self):
        git(self.repo, 'config', 'core.fsmonitor', self.command)
        state = git_operations.Repository(self.repo).state()
        self.assertEqual(state.branch, 'main')
        self.assertFalse(state.changes)
        self.assertFalse(self.marker.exists())

    def test_failed_isolation_never_resumes_git_and_is_unavailable(self):
        api = mock.Mock(wraps=windows_process.native_api())
        api.AssignProcessToJobObject.return_value = False
        git_observation.native_git()
        with mock.patch.object(windows_process, 'native_api', return_value=api):
            self.assert_unobserved_without_branch()
        api.ResumeThread.assert_not_called()

    def assert_unobserved_without_branch(self):
        metadata, observed = scanner.collect_metadata_observation(self.repo)
        self.assertFalse(metadata['status_available'])
        self.assertIsNone(metadata['dirty'])
        self.assertNotIn('dirty', observed)
        self.assertFalse(self.marker.exists())

    def test_clean_filter_scan_is_unobserved_without_execution(self):
        self.filter()
        self.assert_unobserved()

    def test_process_filter_scan_is_unobserved_without_execution(self):
        self.filter(process=True)
        self.assert_unobserved()

    def test_changes_status_cannot_execute_filter(self):
        self.filter()
        with self.assertRaises(git_operations.GitError):
            git_operations.Repository(self.repo).state()
        self.assertFalse(self.marker.exists())

    def test_working_tree_diff_cannot_execute_filter(self):
        (self.repo / 'tracked.txt').write_text('modified\n', encoding='utf-8')
        repository = git_operations.Repository(self.repo)
        change = repository.state().changes[0]
        self.filter()
        with self.assertRaises(git_operations.GitError):
            repository.diff(change)
        self.assertFalse(self.marker.exists())

    def test_benign_clean_and_modified_observations(self):
        clean = scanner.collect_metadata_observation(self.repo)[0]
        self.assertTrue(clean['status_available'])
        self.assertEqual(clean['dirty'], 0)
        (self.repo / 'tracked.txt').write_text('modified\n', encoding='utf-8')
        dirty = scanner.collect_metadata_observation(self.repo)[0]
        self.assertTrue(dirty['status_available'])
        self.assertEqual(dirty['dirty'], 1)
        self.assertEqual(git_operations.Repository(self.repo).state().branch, 'main')

    def test_linked_worktree_retains_metadata_and_blocks_helper(self):
        linked = self.root / 'linked'
        add_worktree(self.repo, linked, branch='linked-branch')
        normal = scanner.collect_metadata_observation(linked)[0]
        self.assertTrue(normal['status_available'])
        self.assertEqual(normal['branch'], 'linked-branch')
        self.filter(linked)
        metadata, observed = scanner.collect_metadata_observation(linked)
        self.assertFalse(self.marker.exists())
        self.assertFalse(metadata['status_available'])
        self.assertNotIn('dirty', observed)

    def test_blocked_rescan_invalidates_cached_clean_without_losing_identity(self):
        records, _ = scanner.merge_scan([], [str(self.repo)])
        identity = records[0]['project_id']
        self.assertEqual(records[0]['dirty'], 0)
        self.filter()
        records, _ = scanner.merge_scan(records, [str(self.repo)])
        self.assertFalse(self.marker.exists())
        self.assertFalse(records[0]['status_available'])
        self.assertEqual(records[0]['project_id'], identity)
        session = RepositorySession.__new__(RepositorySession)
        session.records = records
        self.assertEqual(session.counts()['clean'], 0)
        self.assertEqual(session.counts()['unobserved'], 1)
        finding = next(item for item in health.evaluate_repository(self.repo, records[0]).findings
                       if item.rule == 'working_tree')
        self.assertEqual(finding.status, health.UNKNOWN)

    def test_submodule_observation_fails_closed_without_child_helper(self):
        child = create_repository(self.root / 'child')
        git(self.repo, '-c', 'protocol.file.allow=always', 'submodule', 'add', str(child), 'module')
        git(self.repo, 'commit', '-qm', 'submodule fixture')
        args = ['git', '-C', str(self.repo), 'status', '--porcelain', '--ignore-submodules=all']
        def ignored_status():
            return git_observation.run_read_only(
                args, env=git_observation.git_environment(read_only=True),
                timeout=10, capture_output=True, text=True)
        benign = ignored_status()
        self.assertEqual((benign.returncode, benign.stdout, benign.stderr), (0, '', ''))
        self.filter(self.repo / 'module')
        # Ignoring submodules hides actual changes, so it cannot prove CLEAN.
        hidden = ignored_status()
        self.assertEqual((hidden.returncode, hidden.stdout, hidden.stderr), (0, '', ''))
        self.assertFalse(self.marker.exists())
        self.assert_unobserved()
        with self.assertRaises(git_operations.GitError):
            git_operations.Repository(self.repo).state()
        self.assertFalse(self.marker.exists())
