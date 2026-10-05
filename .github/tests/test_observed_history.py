"""
Replay the attribution strings actually observed in the account's history
through both policy layers.

This is the anti-regression check: every historical violation must be caught,
and the scan is also reported as a coverage figure (how far back the policy
reaches), never as a repair. Existing history is left untouched by design.
"""

from __future__ import annotations

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

import attribution_policy as P  # noqa: E402
from test_attribution_policy import RE2, MUST_ALLOW  # noqa: E402

# Verbatim attribution shapes harvested from the live history scan
# (see docs/EVIDENCE.md). Both marker orderings occur in the wild.
OBSERVED = [
    "\U0001F916 Generated with Codebuff",
    "Generated with Codebuff \U0001F916",
    "Co-Authored-By: Codebuff <noreply@codebuff.com>",
    "co-authored-by: Codebuff <noreply@codebuff.com>",
    "Co-authored-by: Codebuff <noreply@codebuff.com>",
]

LEGITIMATE = [
    "Initial commit: Freebuff Multiplayer web game",
    "feat: add native save integrity for Free Buff clients",
    "chore: add .gitattributes to normalize text files to LF",
    "Freebuff-Multiplayer: bump save schema to v2",
    "docs: explain why Codebuff trailers are rejected",
    "ci: run the Playwright step in bash instead of PowerShell",
]


class TestObservedHistory(unittest.TestCase):
    def test_observed_violations_are_caught(self):
        for msg in OBSERVED:
            with self.subTest(msg=msg):
                self.assertFalse(P.check_message(msg).ok, msg)
                self.assertTrue(RE2.search(msg), f"RE2 missed: {msg}")

    def test_observed_violations_inside_a_realistic_commit(self):
        """The shapes as they actually appear: body + trailer block."""
        for trailer in ("\U0001F916 Generated with Codebuff",
                        "Co-Authored-By: Codebuff <noreply@codebuff.com>"):
            with self.subTest(trailer=trailer):
                msg = (
                    "feat: implement the run storage codecs\n\n"
                    "Add RunStatus and ExecutionMode storage encoding.\n\n"
                    f"{trailer}\n"
                )
                self.assertFalse(P.check_message(msg).ok, msg)
                self.assertTrue(RE2.search(msg), f"RE2 missed: {trailer}")

    def test_legitimate_history_lines_pass(self):
        for msg in LEGITIMATE:
            with self.subTest(msg=msg):
                self.assertTrue(P.check_message(msg).ok, msg)
                self.assertIsNone(RE2.search(msg), f"RE2 false positive: {msg}")

    def test_no_false_positive_across_must_allow_with_observed_prefix(self):
        """A subject naming the product plus an observed trailer = reject."""
        msg = "fix Freebuff integration\n\nCo-Authored-By: Codebuff <noreply@codebuff.com>\n"
        self.assertFalse(P.check_message(msg).ok)


class TestPolicyScope(unittest.TestCase):
    def test_only_the_four_named_agents_are_blocked(self):
        """Other tools and humans must remain committable."""
        for msg in [
            "feat: integrate Copilot suggestions",
            "chore: bump actions/checkout",
            "feat: add Claude Code session export",
            "Co-Authored-By: Jane Doe <jane@example.com>",
            "Co-authored-by: Alex <alex@example.org>",
        ]:
            with self.subTest(msg=msg):
                self.assertTrue(P.check_message(msg).ok, msg)

    def test_other_robot_markers_not_blocked(self):
        """The policy targets U+1F916 specifically, not emoji in general."""
        for msg in [
            "docs: add \U0001F680 release notes template",
            "feat: add \U0001F9EA test fixtures",
        ]:
            with self.subTest(msg=msg):
                self.assertTrue(P.check_message(msg).ok, msg)
                self.assertIsNone(RE2.search(msg))


if __name__ == "__main__":
    unittest.main(verbosity=2)
