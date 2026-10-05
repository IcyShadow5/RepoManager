"""R2.5B: fixture Git harness must be non-interactive, bounded, and loud."""
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import scanner
from tests import git_repository
from tests.test_compatibility_lab import (
    OBSERVATION_ATTEMPTS,
    observe_attached_head,
)


class FixtureGitHarnessTests(unittest.TestCase):
    def test_git_is_noninteractive_with_finite_timeout(self):
        with mock.patch.object(
                subprocess, "run",
                return_value=subprocess.CompletedProcess(
                    args=["git"], returncode=0,
                    stdout="sha\n", stderr="")) as run:
            self.assertEqual(
                git_repository.git(Path(r"C:\fixture"), "rev-parse", "HEAD"),
                "sha")
        self.assertEqual(run.call_count, 1)
        kwargs = run.call_args[1]
        self.assertIs(kwargs.get("stdin"), subprocess.DEVNULL)
        self.assertEqual(kwargs.get("timeout"),
                         git_repository.FIXTURE_GIT_TIMEOUT)
        self.assertEqual(git_repository.FIXTURE_GIT_TIMEOUT, 30)
        self.assertTrue(kwargs.get("check"))

    def test_git_timeout_propagates_with_command_evidence(self):
        expired = subprocess.TimeoutExpired(
            ["git", "-C", r"C:\fixture", "rev-parse", "HEAD"], 30)
        with mock.patch.object(subprocess, "run", side_effect=expired):
            with self.assertRaises(subprocess.TimeoutExpired) as ctx:
                git_repository.git(Path(r"C:\fixture"), "rev-parse", "HEAD")
        exc = ctx.exception
        self.assertIs(exc, expired)
        notes = getattr(exc, "__notes__", [])
        self.assertTrue(notes, "timeout must carry fixture diagnostics")
        self.assertIn("rev-parse", notes[0])
        self.assertIn("30", notes[0])

    def test_git_nonzero_exit_still_fails_loudly(self):
        with mock.patch.object(
                subprocess, "run",
                side_effect=subprocess.CalledProcessError(
                    128, ["git"], output="", stderr="fatal")):
            with self.assertRaises(subprocess.CalledProcessError):
                git_repository.git(Path(r"C:\fixture"), "rev-parse", "HEAD")


class BoundedObservationTests(unittest.TestCase):
    REPO = Path(r"C:\fixture")
    SHA = "abc123"

    def test_unobserved_then_observed_passes(self):
        calls = [
            ({"head": None, "broken": False}, frozenset()),
            ({"head": self.SHA, "broken": False, "branch": "main"},
             frozenset({"head", "broken", "branch"})),
        ]
        with mock.patch.object(
                scanner, "collect_metadata_observation",
                side_effect=calls) as observed:
            meta = observe_attached_head(self, self.REPO, self.SHA)
        self.assertEqual(meta["head"], self.SHA)
        self.assertEqual(observed.call_count, 2)

    def test_never_observed_fails(self):
        calls = ([({"head": None, "broken": False}, frozenset())]
                 * OBSERVATION_ATTEMPTS)
        with mock.patch.object(
                scanner, "collect_metadata_observation",
                side_effect=calls) as observed:
            with self.assertRaises(AssertionError):
                observe_attached_head(self, self.REPO, self.SHA)
        self.assertEqual(observed.call_count, OBSERVATION_ATTEMPTS)

    def test_observed_wrong_sha_fails_immediately_without_retry(self):
        with mock.patch.object(
                scanner, "collect_metadata_observation",
                return_value=({"head": "wrong", "broken": False},
                              frozenset({"head"}))) as observed:
            with self.assertRaises(AssertionError):
                observe_attached_head(self, self.REPO, self.SHA)
        self.assertEqual(observed.call_count, 1)

    def test_head_observed_branch_unobserved_retries_then_passes(self):
        calls = [
            ({"head": self.SHA, "branch": None, "broken": False},
             frozenset({"head"})),
            ({"head": self.SHA, "broken": False, "branch": "main"},
             frozenset({"head", "broken", "branch"})),
        ]
        with mock.patch.object(
                scanner, "collect_metadata_observation",
                side_effect=calls) as observed:
            meta = observe_attached_head(self, self.REPO, self.SHA)
        self.assertEqual((meta["head"], meta["branch"]),
                         (self.SHA, "main"))
        self.assertEqual(observed.call_count, 2)

    def test_observed_wrong_branch_fails_immediately_without_retry(self):
        with mock.patch.object(
                scanner, "collect_metadata_observation",
                return_value=({"head": self.SHA, "broken": False,
                               "branch": "other"},
                              frozenset({"head", "branch"}))) as observed:
            with self.assertRaises(AssertionError):
                observe_attached_head(self, self.REPO, self.SHA)
        self.assertEqual(observed.call_count, 1)

    def test_contradiction_dominates_missing_evidence(self):
        with mock.patch.object(
                scanner, "collect_metadata_observation",
                return_value=({"head": None, "broken": False,
                               "branch": "other"},
                              frozenset({"branch"}))) as observed:
            with self.assertRaises(AssertionError):
                observe_attached_head(self, self.REPO, self.SHA)
        self.assertEqual(observed.call_count, 1)

    def test_retry_bound_is_small_and_deterministic(self):
        self.assertEqual(OBSERVATION_ATTEMPTS, 3)


if __name__ == "__main__":
    unittest.main()
