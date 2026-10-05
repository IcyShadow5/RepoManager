"""Feedback is local or an explicit reviewable browser draft, without telemetry."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from repo_manager import feedback, store


class FeedbackTests(unittest.TestCase):
    def test_four_categories_produce_reviewable_public_issue_drafts(self):
        self.assertEqual(len(feedback.CATEGORIES), 4)
        for category in feedback.CATEGORIES:
            with self.subTest(category=category):
                report = feedback.make_report(category, "Unicode ä + &", "Steps\nExpected / actual")
                url = urlsplit(feedback.issue_url(report))
                self.assertEqual(url.scheme, "https")
                self.assertEqual(url.netloc, "github.com")
                self.assertEqual(url.path, "/IcyShadow5/RepoManager/issues/new")
                query = parse_qs(url.query)
                self.assertIn("Unicode ä + &", query["title"][0])
                self.assertIn("Steps\nExpected / actual", query["body"][0])

    def test_default_report_contains_no_runtime_paths_or_repository_state(self):
        report = feedback.make_report("bug", "", "Unexpected result")
        self.assertEqual(set(report), {"category", "title", "message", "edition", "version"})
        self.assertNotIn("runtime", report)

    def test_runtime_is_explicit_and_bounded(self):
        report = feedback.make_report("ui", "", "Layout issue", include_runtime=True, qt_version="6.11.2")
        self.assertEqual(set(report["runtime"]), {"python", "system", "qt"})
        self.assertEqual(report["runtime"]["qt"], "6.11.2")
        self.assertIn("Qt: 6.11.2", feedback.markdown(report))

    def test_invalid_empty_or_oversized_reports_are_rejected(self):
        for category, title, message in (("invalid", "", "message"), ("bug", "", " "),
                                         ("bug", "x" * 141, "message"),
                                         ("bug", "", "x" * (feedback.MAX_MESSAGE + 1))):
            with self.subTest(category=category, length=len(message)), self.assertRaises(ValueError):
                feedback.make_report(category, title, message)

    def test_saved_reports_are_distinct_and_preserve_user_text(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(store, "APP_DIR", Path(folder)):
            report = feedback.make_report("positive", "Title", "Useful ä feedback")
            first = feedback.save_report(report)
            second = feedback.save_report(report)
            self.assertNotEqual(first, second)
            self.assertEqual(json.loads(first.read_text(encoding="utf-8")), report)
            self.assertEqual(list(first.parent.glob("*.tmp")), [])

    def test_save_failure_leaves_no_partial_feedback(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(store, "APP_DIR", Path(folder)), \
                mock.patch.object(feedback.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                feedback.save_report(feedback.make_report("bug", "", "Details"))
            self.assertEqual(list((Path(folder) / "feedback").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
