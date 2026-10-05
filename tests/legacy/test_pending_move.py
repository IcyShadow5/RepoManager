"""LEGACY / PARITY REFERENCE — NOT CURRENT UI PROOF."""
import ctypes
import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from repo_manager import scanner, store, main as main_module
from repo_manager.relocation import detach_pending_for_keep_both, move_outcome, perform_confirmed_move
from repo_manager.projects import repository_path_key
from tests.test_pending_move import (NOW, TS, _isolate_store, _valid_pending, _old_record, _pristine_target, _live_meta, _rehydrate)

class KeepBothResolutionTests(unittest.TestCase):
    OLD = r"C:\work\OldRepo"
    NEW = r"C:\work\NewRepo"

    def _suggestion(self):
        return {"kind": "move", "category": "strong",
                "old_path": self.OLD, "new_path": self.NEW,
                "name": "OldRepo", "evidence": [],
                "identity": {"remotes": [], "root_commits": ["abc"]},
                "new_project_id": "cp-id"}

    def _stub(self, projects, suggestion=None, persist=None):
        settings = {"move_suppressions": []}
        return SimpleNamespace(
            projects=projects, settings=settings,
            _move_suggestions=[suggestion or self._suggestion()],
            _persist_projects=persist or (lambda: None),
            _status=mock.Mock(), _render_problems_body=mock.Mock(),
            _populate_coalesced=mock.Mock())

    def test_keep_both_strips_pending_and_suppresses(self):
        old = _old_record(self.OLD, pending_move=_valid_pending(
            new_id="cp-id", new_path=self.NEW))
        target = _pristine_target(self.NEW, "cp-id")
        projects = [old, target]
        suggestion = self._suggestion()
        stub = self._stub(projects, suggestion)
        main_module.RepoManagerApp._keep_both_move(stub, suggestion)
        self.assertNotIn("pending_move", projects[0])
        self.assertIn({"old": self.OLD.lower(), "new": self.NEW.lower()},
                      stub.settings["move_suppressions"])
        self.assertEqual(stub._move_suggestions, [])
        # Suppression wins: rehydration cannot regenerate the suggestion.
        suggestions, conflicts = _rehydrate(
            projects, [self.NEW], [_live_meta(["abc"])],
            [frozenset({"fingerprint", "remotes"})],
            stub.settings["move_suppressions"])
        self.assertEqual((suggestions, conflicts), ([], []))

    def test_keep_both_registry_failure_restores_pending(self):
        old = _old_record(self.OLD, pending_move=_valid_pending(
            new_id="cp-id", new_path=self.NEW))
        projects = [old, _pristine_target(self.NEW, "cp-id")]

        def failing_persist():
            raise OSError("disk full")

        stub = self._stub(projects, persist=failing_persist)
        with mock.patch("tkinter.messagebox.showerror"), \
                mock.patch.object(main_module.store, "save_settings",
                                  ) as saved:
            main_module.RepoManagerApp._keep_both_move(stub,
                                                      self._suggestion())
        # R2.6C-FIX-01: suppression is persisted first, so a later
        # registry failure leaves safe redundancy, not lost protection.
        self.assertEqual(projects[0]["pending_move"]["new_project_id"],
                         "cp-id")
        self.assertIn({"old": self.OLD.lower(), "new": self.NEW.lower()},
                      stub.settings["move_suppressions"])
        saved.assert_called_once()
        self.assertEqual(len(stub._move_suggestions), 1)

    def test_detach_helper_only_touches_old_side(self):
        other = _old_record(r"C:\work\Other",
                            pending_move=_valid_pending(
                                new_id="x", new_path=r"C:\work\X"))
        old = _old_record(self.OLD, pending_move=_valid_pending(
            new_id="cp-id", new_path=self.NEW))
        projects = [other, old]
        detached = main_module.detach_pending_for_keep_both(
            projects, self._suggestion())
        self.assertEqual(len(detached), 1)
        self.assertNotIn("pending_move", old)
        self.assertIn("pending_move", other)


class KeepBothDurabilityTests(unittest.TestCase):
    """R2.6C-FIX-01: Keep Both must never durably lose both protections.

    Required order: suppression persisted FIRST, pending detached only
    after. There must never be a durable state with neither the exact
    move suppression nor the pending_move provenance.
    """
    OLD = r"C:\work\OldRepo"
    NEW = r"C:\work\NewRepo"
    PAIR = {"old": OLD.lower(), "new": NEW.lower()}

    def _suggestion(self):
        return {"kind": "move", "category": "strong",
                "old_path": self.OLD, "new_path": self.NEW,
                "name": "OldRepo", "evidence": [],
                "identity": {"remotes": [], "root_commits": ["abc"]},
                "new_project_id": "cp-id"}

    def _projects(self):
        return [_old_record(self.OLD, pending_move=_valid_pending(
                    new_id="cp-id", new_path=self.NEW)),
                _pristine_target(self.NEW, "cp-id")]

    def _stub(self, projects, suggestion, persist):
        return SimpleNamespace(
            projects=projects, settings={"move_suppressions": []},
            _move_suggestions=[suggestion],
            _persist_projects=persist,
            _status=mock.Mock(), _render_problems_body=mock.Mock(),
            _populate_coalesced=mock.Mock())

    def _durable_registry(self):
        projects, _report = store.read_registry()
        return projects

    def test_A_settings_failure_keeps_pending_and_claims_nothing(self):
        with tempfile.TemporaryDirectory() as base:
            _isolate_store(self, base)
            projects = self._projects()
            store.save_projects(projects)
            store.save_settings({"move_suppressions": []})
            persist = mock.Mock()
            stub = self._stub(projects, self._suggestion(), persist)
            with mock.patch("tkinter.messagebox.showerror") as error, \
                    mock.patch.object(
                        main_module.store, "save_settings",
                        side_effect=OSError("settings locked")):
                main_module.RepoManagerApp._keep_both_move(
                    stub, stub._move_suggestions[0])
            # Registry cleanup must never run when suppression failed.
            persist.assert_not_called()
            # Pending provenance retained in memory and durably.
            self.assertEqual(projects[0]["pending_move"]["new_project_id"],
                             "cp-id")
            durable = self._durable_registry()
            durable_old = next(p for p in durable
                               if p.get("project_id") == "old-stable-id")
            self.assertEqual(durable_old["pending_move"]["new_project_id"],
                             "cp-id")
            # No suppression anywhere; suggestion actionable; no success.
            self.assertEqual(stub.settings["move_suppressions"], [])
            self.assertEqual(store.load_settings().get(
                "move_suppressions"), [])
            self.assertEqual(len(stub._move_suggestions), 1)
            error.assert_called_once()
            stub._status.set.assert_not_called()

    def test_B_registry_failure_after_suppression_keeps_redundant_safety(self):
        with tempfile.TemporaryDirectory() as base:
            _isolate_store(self, base)
            projects = self._projects()
            store.save_projects(projects)
            store.save_settings({"move_suppressions": []})

            def failing_persist():
                raise OSError("disk full")

            stub = self._stub(projects, self._suggestion(),
                              failing_persist)
            with mock.patch("tkinter.messagebox.showerror") as error:
                main_module.RepoManagerApp._keep_both_move(
                    stub, stub._move_suggestions[0])
            # Suppression is durable; pending restored in memory and
            # still durable (registry save never committed).
            self.assertIn(self.PAIR, stub.settings["move_suppressions"])
            self.assertIn(self.PAIR, store.load_settings().get(
                "move_suppressions"))
            self.assertEqual(projects[0]["pending_move"]["new_project_id"],
                             "cp-id")
            durable = self._durable_registry()
            durable_old = next(p for p in durable
                               if p.get("project_id") == "old-stable-id")
            self.assertEqual(durable_old["pending_move"]["new_project_id"],
                             "cp-id")
            self.assertEqual(len(projects), 2)
            # No clean success: suggestion retained for retry, error shown.
            self.assertEqual(len(stub._move_suggestions), 1)
            error.assert_called_once()
            stub._status.set.assert_not_called()

    def test_C_success_suppresses_and_cleans_pending(self):
        with tempfile.TemporaryDirectory() as base:
            _isolate_store(self, base)
            projects = self._projects()
            store.save_projects(projects)
            store.save_settings({"move_suppressions": []})
            stub = self._stub(projects, self._suggestion(),
                              lambda: store.save_projects(projects))
            with mock.patch("tkinter.messagebox.showerror") as error:
                main_module.RepoManagerApp._keep_both_move(
                    stub, stub._move_suggestions[0])
            self.assertNotIn("pending_move", projects[0])
            self.assertIn(self.PAIR, stub.settings["move_suppressions"])
            self.assertIn(self.PAIR, store.load_settings().get(
                "move_suppressions"))
            durable = self._durable_registry()
            durable_old = next(p for p in durable
                               if p.get("project_id") == "old-stable-id")
            self.assertNotIn("pending_move", durable_old)
            self.assertEqual(len(projects), 2)
            self.assertEqual(stub._move_suggestions, [])
            error.assert_not_called()
            stub._status.set.assert_called_once_with(
                "kept both repositories")

    def test_D_redundant_state_resolves_to_suppression_on_next_scan(self):
        projects = self._projects()
        suggestions, conflicts = _rehydrate(
            projects, [self.NEW], [_live_meta(["abc"])],
            [frozenset({"fingerprint", "remotes"})], [self.PAIR])
        self.assertEqual((suggestions, conflicts), ([], []))
        self.assertEqual(len(projects), 2)
        self.assertNotIn("pending_move", projects[0])
