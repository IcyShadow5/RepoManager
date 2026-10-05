"""Fresh detection preserves approval only when launcher evidence matches."""
import sys
from dataclasses import replace
from unittest import mock

from repo_manager import launchers
from tests.test_repository_service import IsolatedSessionTests


class LauncherApprovalIdentityTests(IsolatedSessionTests):
    def setUp(self):
        super().setUp()
        self.record, self.target = self.seed()
        self.session.save_launcher(self.target, {"name": "Safe check", "executable": sys.executable,
                                  "args": ["-V"], "cwd": self.record["path"]})

    def test_fresh_candidates_compare_evidence_not_object_identity(self):
        first = self.session.inspect(self.target)[1][0]
        second = self.session.inspect(self.target)[1][0]
        self.assertIsNot(first, second)
        self.assertEqual(first, second)
        self.assertEqual(first, second.as_dict())
        for changed in (replace(second, healthy=False), replace(second, priority=99),
                        replace(second, command=("different",)), replace(second, source="different")):
            self.assertNotEqual(first, changed)

    def test_existing_execution_accepts_fresh_matching_detection(self):
        approved = self.session.inspect(self.target)[1][0]
        with mock.patch.object(launchers, "run_command") as run:
            self.session.run_launcher(self.target, approved)
            self.assertEqual(run.call_args.args[0], approved)
            self.assertIsNot(run.call_args.args[0], approved)

    def test_changed_arguments_still_require_new_approval(self):
        approved = self.session.inspect(self.target)[1][0]
        value = self.session.resolve(self.target)["custom_launchers"][0]
        self.session.save_launcher(self.target, {**value, "args": ["--help"]})
        with mock.patch.object(launchers, "run_command") as run, self.assertRaisesRegex(ValueError, "Launcher changed"):
            self.session.run_launcher(self.target, approved)
        run.assert_not_called()
