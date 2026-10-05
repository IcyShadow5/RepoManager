"""LEGACY / PARITY REFERENCE — NOT CURRENT UI PROOF."""
"""Light/Ice Light must be the implicit theme default; explicit Dark is kept."""
import copy
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import store, theme


def _tk_available():
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception:
        return False


TK_AVAILABLE = _tk_available()


class ThemeDefaultContractTests(unittest.TestCase):
    """First run / missing key / invalid value resolve to Light."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        app_dir = self.base / "app"
        patcher = mock.patch.multiple(
            store,
            APP_DIR=app_dir,
            REPOS_FILE=app_dir / "repos.json",
            SETTINGS_FILE=app_dir / "settings.json",
            NOTES_DIR=app_dir / "notes",
        )
        patcher.start()
        store.ensure_dirs()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def effective_name(self, settings):
        # Mirrors the production application expression (main.py).
        return settings.get("theme", "light")

    def test_first_run_without_settings_file_is_light(self):
        self.assertFalse(store.SETTINGS_FILE.exists())
        settings = store.load_settings()
        self.assertEqual(self.effective_name(settings), "light")
        self.assertIs(theme.get_palette(self.effective_name(settings)),
                      theme.PALETTES["light"])

    def test_missing_theme_key_is_light(self):
        store.SETTINGS_FILE.write_text(
            json.dumps({"roots": ["C:/x"], "depth": 4}), encoding="utf-8")
        settings = store.load_settings()
        self.assertEqual(self.effective_name(settings), "light")
        self.assertIs(theme.get_palette(self.effective_name(settings)),
                      theme.PALETTES["light"])

    def test_invalid_theme_value_sanitizes_to_light(self):
        store.SETTINGS_FILE.write_text(
            json.dumps({"theme": "ultraviolet"}), encoding="utf-8")
        settings = store.load_settings()
        self.assertEqual(settings["theme"], "light")
        self.assertEqual(self.effective_name(settings), "light")

    def test_explicit_dark_is_preserved(self):
        store.save_settings({"theme": "dark"})
        settings = store.load_settings()
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(self.effective_name(settings), "dark")
        self.assertIs(theme.get_palette("dark"), theme.PALETTES["dark"])

    def test_explicit_light_is_preserved(self):
        store.save_settings({"theme": "light"})
        settings = store.load_settings()
        self.assertEqual(settings["theme"], "light")
        self.assertEqual(self.effective_name(settings), "light")

    def test_default_settings_carry_light_theme(self):
        self.assertEqual(store.DEFAULT_SETTINGS["theme"], "light")
        self.assertEqual(copy.deepcopy(store.DEFAULT_SETTINGS)["theme"],
                         "light")

    def test_theme_module_defaults_to_ice_light(self):
        self.assertIs(theme.get_palette(), theme.PALETTES["light"])
        self.assertIs(theme.get_palette("no-such-theme"),
                      theme.PALETTES["light"])
        self.assertEqual(theme.get_palette()["bg"],
                         theme.PALETTES["light"]["bg"])
        self.assertNotEqual(theme.get_palette()["bg"],
                            theme.PALETTES["dark"]["bg"])
        params = inspect.signature(theme.apply).parameters
        self.assertEqual(params["name"].default, "light")


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class ThemeApplicationDefaultTests(unittest.TestCase):
    """Applying the first-run effective theme paints Ice Light surfaces."""

    def test_apply_first_run_theme_paints_light(self):
        import tkinter as tk
        settings = copy.deepcopy(store.DEFAULT_SETTINGS)
        name = settings.get("theme", "light")
        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.withdraw()
        pal = theme.apply(root, name)
        self.assertIs(pal, theme.PALETTES["light"])
        self.assertEqual(root.cget("background"), pal["bg"])
        self.assertEqual(pal["bg"], "#e8f2f5")


if __name__ == "__main__":
    unittest.main()
