"""Tests for icon resolution, UI copy, semantic colors, and action grouping.

Covers pure, Tk-free behaviour; these tests do not require a display and
never depend on real user data or on the presence of the shipped icon assets:
icon resource resolution, first-run empty-state copy, status/state colour-token
mapping, and the context-menu action grouping.
"""
import ctypes
import os
import tempfile
import unittest
from pathlib import Path

from repo_manager import theme
from repo_manager.main import (CONTEXT_MENU_LAYOUT, TABLE_COLUMN_DEFAULTS,
                               TABLE_COLUMN_LIMITS, clamp_column_widths,
                               empty_state_text, health_headline, package_root,
                               resolve_icon_path, working_action_label,
                               context_menu_layout,
                               set_windows_app_user_model_id,
                               WINDOWS_APP_USER_MODEL_ID)


class IconResolutionTests(unittest.TestCase):
    def test_canonical_appicon_preferred_over_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "appicon.ico").write_bytes(b"A")
            (base / "app.ico").write_bytes(b"B")
            self.assertEqual(resolve_icon_path(base), base / "appicon.ico")

    def test_falls_back_to_legacy_icon(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "app.ico").write_bytes(b"B")
            self.assertEqual(resolve_icon_path(base), base / "app.ico")

    def test_none_when_no_icon_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(resolve_icon_path(Path(tmp)))

    def test_custom_candidate_names_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "other.ico").write_bytes(b"O")
            self.assertEqual(resolve_icon_path(base, names=("other.ico",)),
                             base / "other.ico")

    def test_package_root_is_absolute_source_tree_root(self):
        root = package_root()
        self.assertTrue(root.is_absolute())
        self.assertTrue((root / "repo_manager").is_dir())

    @unittest.skipUnless(os.name == "nt", "Windows shell identity")
    def test_windows_app_user_model_id_is_explicit(self):
        self.assertTrue(set_windows_app_user_model_id())
        value = ctypes.c_void_p()
        result = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(
            ctypes.byref(value))
        self.assertEqual(result, 0)
        try:
            self.assertEqual(ctypes.wstring_at(value),
                             WINDOWS_APP_USER_MODEL_ID)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(value)


class EmptyStateTextTests(unittest.TestCase):
    def test_with_roots_lists_them(self):
        text = empty_state_text([r"C:\a", r"C:\b"])
        self.assertIn(r"C:\a", text)
        self.assertIn(r"C:\b", text)
        self.assertIn("Rescan", text)

    def test_without_roots_guides_to_settings(self):
        text = empty_state_text([])
        self.assertIn("Settings", text)
        self.assertIn("Rescan", text)

    def test_compact_and_english(self):
        text = empty_state_text([r"C:\x"])
        self.assertLess(len(text), 420)


class StatusForegroundTests(unittest.TestCase):
    def setUp(self):
        self.pal = theme.get_palette("dark")

    def test_health_pass_fail_warn(self):
        self.assertEqual(theme.status_fg(self.pal, "PASS"), self.pal["ok"])
        self.assertEqual(theme.status_fg(self.pal, "FAIL"), self.pal["danger"])
        self.assertEqual(theme.status_fg(self.pal, "WARN"),
                         self.pal["row_dirty_fg"])

    def test_unknown_stale_unavailable_use_muted_tones(self):
        mutedish = {self.pal["muted"], self.pal["row_arch_fg"]}
        for status in ("UNKNOWN", "STALE", "UNAVAILABLE"):
            self.assertIn(theme.status_fg(self.pal, status), mutedish)

    def test_provider_and_run_states(self):
        self.assertEqual(theme.status_fg(self.pal, "AVAILABLE"),
                         self.pal["ok"])
        self.assertEqual(theme.status_fg(self.pal, "NETWORK_FAILURE"),
                         self.pal["danger"])
        self.assertEqual(
            theme.status_fg(self.pal, "AUTHENTICATION_REQUIRED"),
            self.pal["danger"])
        self.assertEqual(theme.status_fg(self.pal, "RUNNING"),
                         self.pal["accent2"])

    def test_verification_states(self):
        self.assertEqual(theme.status_fg(self.pal, "VERIFIED"),
                         self.pal["ok"])
        self.assertEqual(theme.status_fg(self.pal, "FAILED"),
                         self.pal["danger"])

    def test_project_lifecycle_statuses(self):
        self.assertEqual(theme.status_fg(self.pal, "active"),
                         self.pal["activity"])
        self.assertEqual(theme.status_fg(self.pal, "ARCHIVED"),
                         self.pal["row_arch_fg"])
        self.assertEqual(theme.status_fg(self.pal, "idea"), self.pal["muted"])

    def test_unknown_status_defaults_to_muted(self):
        self.assertEqual(theme.status_fg(self.pal, "???no-such-state???"),
                         self.pal["muted"])
        self.assertEqual(theme.status_fg(self.pal, None), self.pal["muted"])


class SemanticStateTests(unittest.TestCase):
    def test_severity_activity_and_unknown_are_distinct(self):
        self.assertEqual(theme.semantic_role("FAIL"), "Error")
        self.assertEqual(theme.semantic_role("PASS"), "Success")
        self.assertEqual(theme.semantic_role("WARN"), "Warning")
        self.assertEqual(theme.semantic_role("ACTIVE"), "Activity")
        self.assertEqual(theme.semantic_role("RUNNING"), "Activity")
        self.assertEqual(theme.semantic_role("UNKNOWN"), "Neutral")
        self.assertEqual(theme.semantic_role("UNAVAILABLE"), "Neutral")
        self.assertEqual(theme.semantic_role("TARGET_RECHECKED"), "Neutral")

    def test_semantic_style_names_are_stable(self):
        self.assertEqual(theme.semantic_style("FAIL"),
                         "Semantic.Error.TLabel")
        self.assertEqual(theme.semantic_style("PASS"),
                         "Semantic.Success.TLabel")

    def test_ice_light_uses_cool_nonwhite_application_surfaces(self):
        pal = theme.get_palette("light")
        self.assertNotEqual(pal["bg"].lower(), "#ffffff")
        self.assertNotEqual(pal["panel"].lower(), "#ffffff")
        self.assertNotEqual(pal["bg"], pal["panel"])
        self.assertNotEqual(pal["warning_bg"], pal["error_bg"])
        self.assertEqual(pal["selection_fg"], pal["text"])
        self.assertNotEqual(pal["selection_fg"].lower(), "#ffffff")


class ColumnWidthConstraintTests(unittest.TestCase):
    def test_defaults_are_complete_and_within_bounds(self):
        self.assertEqual(clamp_column_widths(), TABLE_COLUMN_DEFAULTS)
        for name, value in TABLE_COLUMN_DEFAULTS.items():
            low, high = TABLE_COLUMN_LIMITS[name]
            self.assertGreaterEqual(value, low)
            self.assertLessEqual(value, high)

    def test_persisted_widths_are_clamped_and_malformed_values_reset(self):
        widths = clamp_column_widths({
            "name": 1,
            "path": 99999,
            "status": "broken",
            "dirty": True,
            "future_column": 123,
        })
        self.assertEqual(widths["name"], TABLE_COLUMN_LIMITS["name"][0])
        self.assertEqual(widths["path"], TABLE_COLUMN_LIMITS["path"][1])
        self.assertEqual(widths["status"], TABLE_COLUMN_DEFAULTS["status"])
        self.assertEqual(widths["dirty"], TABLE_COLUMN_DEFAULTS["dirty"])
        self.assertNotIn("future_column", widths)


class HealthHeadlineTests(unittest.TestCase):
    def test_headlines_explain_without_hiding_evidence_status(self):
        self.assertIn("Healthy", health_headline("PASS"))
        self.assertIn("Needs attention", health_headline("WARN"))
        self.assertIn("Problems found", health_headline("FAIL"))
        self.assertIn("Unknown", health_headline("UNKNOWN"))


class WorkingActionPresentationTests(unittest.TestCase):
    def test_action_label_follows_lifecycle_status(self):
        for status in ("idea", "paused", "archived"):
            with self.subTest(status=status):
                self.assertEqual(working_action_label(status), "Work on this")
        self.assertEqual(working_action_label("active"),
                         "Stop working on this")

    def test_context_menu_action_is_status_aware(self):
        non_active = context_menu_layout("paused")
        active = context_menu_layout("active")
        self.assertEqual(non_active[0][1], "Work on this (pin + Active)")
        self.assertEqual(active[0][1], "Stop working on this")
        self.assertIn(("command", "Pin / Unpin", "_toggle_pinned"),
                      active)


class ContextMenuGroupingTests(unittest.TestCase):
    @staticmethod
    def _groups():
        groups, current = [], []
        for item in CONTEXT_MENU_LAYOUT:
            if item == "-sep-":
                groups.append(current)
                current = []
            else:
                current.append(item[1])
        if current:
            groups.append(current)
        return groups

    def test_all_intended_actions_present(self):
        all_labels = "\n".join(
            label for group in self._groups() for label in group)
        for label in ("Work on this (pin + Active)",
                      "Open in Explorer", "Open Terminal", "Open Agent",
                      "Set status", "Pin / Unpin",
                      "Remove from RepoManager\u2026", "Open on GitHub",
                      "Copy path", "Copy GitHub URL",
                      "Commit & Push\u2026", "Pull"):
            self.assertIn(label, all_labels)

    def test_mutating_git_never_mixed_with_read_only(self):
        read_only = {"Open on GitHub", "Copy path", "Copy GitHub URL"}
        mutating = {"Commit & Push…", "Pull"}
        for group in self._groups():
            content = set(group)
            self.assertFalse(content & read_only and content & mutating,
                             f"group mixes read-only and mutating actions: "
                             f"{sorted(content)}")

    def test_launcher_execution_grouped_together(self):
        launch = {"Open in Explorer", "Open in VS Code", "Open Terminal",
                  "Open Agent"}
        self.assertTrue(
            any(set(group).issuperset(launch) for group in self._groups()))

    def test_project_metadata_actions_are_grouped_together(self):
        metadata = {"Set status", "Pin / Unpin",
                    "Remove from RepoManager\u2026"}
        self.assertTrue(
            any(set(group).issuperset(metadata) for group in self._groups()))


if __name__ == "__main__":
    unittest.main()
