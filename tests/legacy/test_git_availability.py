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
from repo_manager import git_availability, main

class GitOnboardingTests(unittest.TestCase):
    def _app(self):
        app = object.__new__(main.RepoManagerApp)
        app.git_notice = mock.Mock()
        app.git_notice.winfo_manager.return_value = ""
        app.git_notice_detail = mock.Mock()
        app._outer_pane = mock.Mock()
        app._status = mock.Mock()
        app._scanning = False
        app.start_scan = mock.Mock()
        return app

    def test_missing_then_recheck_resumes_scan(self):
        app = self._app()
        states = iter((git_availability.GitAvailability("not_found"),
                       git_availability.GitAvailability("available")))
        with mock.patch.object(main.git_availability, "check_git",
                               side_effect=lambda: next(states)):
            self.assertFalse(app._check_git_availability())
            app._check_git_again()
        app.git_notice.pack.assert_called_once()
        app.git_notice.pack_forget.assert_called_once()
        app.start_scan.assert_called_once()

    def test_ordinary_repository_error_does_not_show_missing_git(self):
        app = self._app()
        with mock.patch.object(main.git_availability, "check_git",
                               return_value=git_availability.GitAvailability("available")):
            self.assertTrue(app._check_git_availability())
        app.git_notice.pack.assert_not_called()
        app.start_scan.assert_not_called()

    def test_install_opens_official_page(self):
        app = self._app()
        with mock.patch.object(main.webbrowser, "open", return_value=True) as opened:
            app._open_git_install()
        opened.assert_called_once_with("https://git-scm.com/install/windows")
