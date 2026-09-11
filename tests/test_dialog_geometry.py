"""B2.4 — multi-monitor dialog geometry and fallback behavior.

Covers the parent-monitor work-area resolution and the pure
``dialog_position`` placement used by Help/Settings (and every other
``_prepare_dialog`` surface). Expected coordinates are asserted
independently of the implementation formulas.

Win32 monitor lookup itself is only exercised indirectly: pure tests feed
synthetic work areas, real-Tk tests patch the native boundary to prove the
Tk-fallback path, and one smoke test exercises the real Win32 call when
running on Windows with a display.
"""
import unittest
import tkinter as tk
from unittest import mock

from repo_manager import main as main_module
from tests.test_gui_scaling import _build_real_app


def _tk_available():
    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except (tk.TclError, OSError, AttributeError):
        return False


TK_AVAILABLE = _tk_available()


class DialogPositionGeometryTests(unittest.TestCase):
    """Pure, deterministic placement against synthetic work areas."""

    def test_primary_monitor_centers_over_parent(self):
        # (1) Primary monitor at origin, parent well inside the work area:
        # visually identical to the historical primary-screen placement.
        self.assertEqual(
            main_module.dialog_position((280, 100, 1640, 880),
                                        (0, 0, 1920, 1080), 760, 560),
            (580, 210))

    def test_negative_x_monitor_keeps_negative_origin(self):
        # (2) Monitor left of primary. Parent rect from the confirmed defect
        # reproduction: the historical code clamped this to (0, 330).
        self.assertEqual(
            main_module.dialog_position((-1800, 200, -400, 1020),
                                        (-1920, 0, 0, 1080), 760, 560),
            (-1480, 330))

    def test_negative_y_monitor_top_align_uses_work_top(self):
        # (3) Monitor above primary: Settings top alignment must pin to
        # work_top, never to desktop 0.
        self.assertEqual(
            main_module.dialog_position((160, -880, 1760, -100),
                                        (0, -1080, 1920, 0), 760, 700,
                                        top_align=True),
            (580, -1080))

    def test_positive_nonzero_origin_monitor(self):
        # (4) Monitor right of primary with its own taskbar reservation.
        self.assertEqual(
            main_module.dialog_position((2000, 120, 3400, 900),
                                        (1920, 0, 4480, 1390), 760, 560),
            (2320, 230))

    def test_parent_near_right_bottom_edge_clamps_into_work_area(self):
        # (5) Tiny parent pinned at the right/bottom edge: the dialog cannot
        # center over it without leaving the work area, so it clamps to the
        # right/bottom, keeping the 16px edge padding.
        self.assertEqual(
            main_module.dialog_position((1840, 960, 1900, 1060),
                                        (0, 0, 1920, 1080), 760, 560),
            (1160, 520))

    def test_parent_near_left_top_edge_clamps_into_work_area(self):
        # (6) Tiny parent pinned at the left/top edge: centering would push
        # the dialog past the left/top work-area bounds, so it clamps to the
        # padded work-area origin.
        self.assertEqual(
            main_module.dialog_position((10, 10, 300, 200),
                                        (0, 0, 1920, 1080), 760, 560),
            (26, 26))

    def test_oversized_dialog_is_deterministically_anchored(self):
        # (7) Dialog larger than the usable space: deterministic anchored
        # placement instead of invalid arithmetic, for both orientations of
        # the overflow and on a negative-origin monitor.
        self.assertEqual(
            main_module.dialog_position((100, 100, 600, 400),
                                        (0, 0, 800, 600), 1600, 1000),
            (0, 0))
        self.assertEqual(
            main_module.dialog_position((-1800, 200, -400, 1020),
                                        (-1920, 0, 0, 1080), 2400, 900),
            (-1920, 180))
        # Overflow in exactly one axis must not corrupt the other axis.
        x, y = main_module.dialog_position((100, 100, 1100, 400),
                                           (0, 0, 1920, 1080), 2400, 560)
        self.assertEqual(x, 0)
        self.assertEqual(y, 100 + max(16, (300 - 560) // 2))

    def test_settings_top_align_pins_to_work_area_top(self):
        # (8) Settings top alignment inside the primary work area is y=0,
        # and y=work_top on any other monitor — never desktop 0.
        self.assertEqual(
            main_module.dialog_position((160, 200, 1760, 980),
                                        (0, 0, 1920, 1080), 760, 700,
                                        top_align=True),
            (580, 0))
        self.assertEqual(
            main_module.dialog_position((160, -880, 1760, -100),
                                        (0, -1080, 1920, 0), 760, 624,
                                        top_align=True),
            (580, -1080))

    def test_degenerate_geometry_falls_back_to_safe_origin(self):
        # Defensive: unmapped/degenerate parent rectangles still yield a
        # deterministic, in-bounds placement instead of invalid arithmetic.
        self.assertEqual(
            main_module.dialog_position((0, 0, 0, 0), (0, 0, 1920, 1080),
                                        760, 560),
            (16, 16))
        self.assertEqual(
            main_module.dialog_position((10, 10, 300, 200), (0, 0, 0, 0),
                                        760, 560),
            (0, 0))


class SettingsDialogHeightTests(unittest.TestCase):
    """(9) Work-area height calculation for the constrained Settings dialog.

    Expected values are the historical B2.1C behavior; available height
    derives from work_bottom - work_top, not from abs(bottom).
    """

    def test_constrained_heights_match_historical_values(self):
        self.assertEqual(main_module.settings_dialog_height((0, 0, 1920, 1080)),
                         700)
        self.assertEqual(main_module.settings_dialog_height((0, 0, 1920, 720)),
                         624)
        self.assertEqual(main_module.settings_dialog_height((0, 0, 1920, 640)),
                         544)

    def test_negative_origin_monitor_uses_span_not_absolute_bottom(self):
        # A monitor whose bottom edge is negative still has a positive
        # usable height; the absolute bottom value must not be used.
        self.assertEqual(main_module.settings_dialog_height((-1920, 0, 0, 1080)),
                         700)
        self.assertEqual(
            main_module.settings_dialog_height((0, -1080, 1920, 0)),
            min(700, max(1, 1080 - 96)))

    def test_degenerate_work_area_yields_minimal_dialog(self):
        self.assertEqual(main_module.settings_dialog_height((0, 0, 1920, 0)),
                         1)


class WorkAreaResolutionTests(unittest.TestCase):
    """(10)/(11) Monitor lookup failure falls back safely, in order."""

    class _FakeWidget:
        def __init__(self, hwnd_error=None, hwnd_value=0,
                     screen=(None, None)):
            self._hwnd_error = hwnd_error
            self._hwnd_value = hwnd_value
            self._screen = screen

        def winfo_id(self):
            if self._hwnd_error is not None:
                raise self._hwnd_error
            return self._hwnd_value

        def winfo_screenwidth(self):
            if self._screen[0] is None:
                raise AttributeError("no display")
            return self._screen[0]

        def winfo_screenheight(self):
            if self._screen[1] is None:
                raise AttributeError("no display")
            return self._screen[1]

    def test_native_failure_falls_back_to_tk_screen_dimensions(self):
        # (10) Win32 lookup fails -> Tk screen dimensions (origin 0,0).
        widget = self._FakeWidget(screen=(2560, 1440))
        with mock.patch.object(main_module, "_windows_work_area",
                               return_value=None):
            self.assertEqual(main_module._resolve_work_area(widget),
                             (0, 0, 2560, 1440))

    def test_invalid_tk_dimensions_fall_back_to_safe_default(self):
        # (11) Tk dimensions invalid/unavailable -> conservative default.
        for broken in (self._FakeWidget(),
                       self._FakeWidget(screen=(0, 1440)),
                       self._FakeWidget(screen=(2560, -5))):
            with mock.patch.object(main_module, "_windows_work_area",
                                   return_value=None):
                self.assertEqual(main_module._resolve_work_area(broken),
                                 main_module.DEFAULT_WORK_AREA)

    def test_winfo_id_failure_still_falls_back_to_tk_dimensions(self):
        widget = self._FakeWidget(hwnd_error=RuntimeError("no window"),
                                  screen=(1280, 1024))
        with mock.patch.object(main_module, "_windows_work_area",
                               return_value=None) as native:
            self.assertEqual(main_module._resolve_work_area(widget),
                             (0, 0, 1280, 1024))
        # The unresolvable handle (0) is still safely rejected by the
        # native boundary before falling back to Tk dimensions.
        native.assert_called_once_with(0)

    def test_monitor_from_window_receives_defaulttonearest_flag_2(self):
        # Regression (B2.4 review blocker): the Win32 contract for
        # MonitorFromWindow's MONITOR_DEFAULTTONEAREST is 2; the value 1
        # means MONITOR_DEFAULTTOPRIMARY and would silently anchor dialogs
        # to the primary display. Only the DLL boundary is faked; the
        # production lookup path must pass flag 2 verbatim.
        self.assertEqual(main_module.MONITOR_DEFAULTTONEAREST, 2)
        self.assertIsInstance(main_module.MONITOR_DEFAULTTONEAREST, int)

        fake_os = mock.MagicMock()
        fake_os.name = "nt"
        fake_hmonitor = mock.MagicMock(name="HMONITOR")
        monitor_from_window = mock.MagicMock(return_value=fake_hmonitor)

        def fake_get_monitor_info(monitor, info_ref):
            info_ref._obj.rcWork = main_module.wintypes.RECT(
                0, 0, 1920, 1040)
            return 1

        user32 = mock.MagicMock()
        user32.MonitorFromWindow = monitor_from_window
        user32.GetMonitorInfoW = mock.MagicMock(
            side_effect=fake_get_monitor_info)

        widget = self._FakeWidget(hwnd_value=12345)
        with mock.patch.object(main_module, "os", fake_os), \
                mock.patch("ctypes.WinDLL", return_value=user32):
            self.assertEqual(main_module._resolve_work_area(widget),
                             (0, 0, 1920, 1040))
        monitor_from_window.assert_called_once_with(
            12345, main_module.MONITOR_DEFAULTTONEAREST)
        # The flag actually reaching the Win32 boundary is 2, never 1.
        self.assertEqual(monitor_from_window.call_args.args, (12345, 2))


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
class DialogGeometryGuiTests(unittest.TestCase):
    """Real-Tk evidence: dialogs open, fallback works, geometry survives."""

    def _open_help(self, app):
        app.open_help()
        app.update()
        return app._help_dialog

    def _open_settings(self, app):
        app.open_settings()
        app.update()
        dialog = next(child for child in app.winfo_children()
                      if isinstance(child, tk.Toplevel)
                      and child.title() == "Settings")
        return dialog

    @staticmethod
    def _sentinel_work_area():
        # A synthetic monitor with a positive non-zero origin (never 0,0)
        # and its own usable bounds, applied through real Tk geometry.
        return (100, 100, 1720, 980)

    def test_native_failure_fallback_still_opens_help_and_settings(self):
        # Fallback path (Win32 lookup fails): both dialogs must open with
        # usable geometry derived from Tk's screen dimensions.
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        with mock.patch.object(main_module, "_windows_work_area",
                               return_value=None):
            help_dialog = self._open_help(app)
            self.assertTrue(help_dialog.winfo_exists())
            width, height, x, y = self._parsed_geometry(help_dialog)
            self.assertEqual((width, height), (760, 560))
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + width,
                                 app.winfo_screenwidth())
            self.assertLessEqual(y + height,
                                 app.winfo_screenheight())
            self.assertTrue(help_dialog.bind("<Escape>"))
            help_dialog.destroy()
            app.update()

            settings_dialog = self._open_settings(app)
            self.assertTrue(settings_dialog.winfo_exists())
            s_width, s_height, s_x, s_y = self._parsed_geometry(
                settings_dialog)
            expected_height = main_module.settings_dialog_height(
                (0, 0, app.winfo_screenwidth(), app.winfo_screenheight()))
            self.assertEqual(s_height, expected_height)
            self.assertGreaterEqual(s_x, 0)
            self.assertGreaterEqual(s_y, 0)
            settings_dialog.destroy()
            app.update()
            self.assertFalse(settings_dialog.winfo_exists())

    def test_settings_constrained_height_uses_resolved_work_area(self):
        # B2.1C constrained-height behavior must survive the work-area
        # rewire, including a work area whose top edge is not 0.
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        for work_area in ((0, 0, 1920, 640), (0, 100, 1920, 740),
                          (0, 0, 1920, 1080)):
            with self.subTest(work_area=work_area):
                with mock.patch.object(
                        main_module, "_resolve_work_area",
                        return_value=work_area):
                    dialog = self._open_settings(app)
                self.assertEqual(
                    dialog.winfo_height(),
                    main_module.settings_dialog_height(work_area))
                dialog.destroy()
                app.update()

    def test_help_and_settings_geometry_survives_tk_application(self):
        # Synthetic multi-monitor: the parent sits on the visible desktop
        # while the mocked parent monitor reports a positive non-zero
        # origin; Tk must apply the requested geometry verbatim.
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        work_area = self._sentinel_work_area()
        app.geometry("1200x700+1500+150")
        app.update_idletasks()
        app.update()
        with mock.patch.object(main_module, "_resolve_work_area",
                               return_value=work_area):
            for opener in (self._open_help, self._open_settings):
                dialog = opener(app)
                width, height, x, y = self._parsed_geometry(dialog)
                self.assertEqual((width, height),
                                 self._expected_dialog_size(dialog))
                # Settings is only top-aligned when the work area forced a
                # constrained height (B2.1C); Help is never top-aligned.
                top_align = (dialog.title() == "Settings"
                             and height < 700)
                expected_x, expected_y = main_module.dialog_position(
                    (app.winfo_rootx(), app.winfo_rooty(),
                     app.winfo_rootx() + app.winfo_width(),
                     app.winfo_rooty() + app.winfo_height()),
                    work_area, width, height,
                    top_align=top_align)
                self.assertEqual((x, y), (expected_x, expected_y))
                self.assertGreater(x, 0)
                self.assertGreater(y, 0)
                self.assertTrue(dialog.bind("<Escape>"))
                self.assertTrue(dialog.protocol("WM_DELETE_WINDOW"))
                # Close deterministically through the registered WM_DELETE
                # WINDOW handler (synthetic <Escape> key events depend on OS
                # window activation, which is not guaranteed for windows
                # created after the first Tk root in a test process).
                dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))
                app.update()
                self.assertFalse(dialog.winfo_exists())
                app.update()

    def test_negative_origin_geometry_survives_tk_application(self):
        # Tk treats "-1480" in a geometry spec as right-edge anchoring, but
        # the explicit "+-1480" form (which _prepare_dialog's f-string
        # naturally produces for negative x) places the left edge at the
        # negative desktop coordinate. The dialog must physically land on
        # the negative-origin monitor, not be pulled back toward the
        # primary screen. Decorations add a small positive frame offset,
        # so the live position is asserted within a tolerance band.
        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.withdraw()
        for requested in ((-1480, 330), (100, -1064), (-1480, -1064)):
            with self.subTest(requested=requested):
                dlg = tk.Toplevel(root)
                self.addCleanup(dlg.destroy)
                x, y = requested
                # Same format expression used by _prepare_dialog.
                dlg.geometry(f"760x560+{x}+{y}")
                dlg.update_idletasks()
                dlg.update()
                self.assertEqual(dlg.geometry(), f"760x560+{x}+{y}")
                self.assertGreaterEqual(dlg.winfo_rootx(), x)
                self.assertLessEqual(dlg.winfo_rootx(), x + 40)
                self.assertGreaterEqual(dlg.winfo_rooty(), y)
                self.assertLessEqual(dlg.winfo_rooty(), y + 40)

    def test_escape_and_close_protocol_remain_intact(self):
        app = _build_real_app(1)
        self.addCleanup(app.destroy)
        help_dialog = self._open_help(app)
        # The Escape binding and the WM_DELETE_WINDOW handler must both be
        # registered and must both close the dialog.
        self.assertTrue(help_dialog.bind("<Escape>"))
        help_dialog.tk.call(help_dialog.protocol("WM_DELETE_WINDOW"))
        app.update()
        self.assertFalse(help_dialog.winfo_exists())

        settings_dialog = self._open_settings(app)
        self.assertTrue(settings_dialog.bind("<Escape>"))
        close_command = settings_dialog.protocol("WM_DELETE_WINDOW")
        self.assertTrue(close_command)
        settings_dialog.tk.call(close_command)
        app.update()
        self.assertFalse(settings_dialog.winfo_exists())

    @staticmethod
    def _parsed_geometry(dialog):
        parts = dialog.geometry().split("+")
        width, height = (int(part) for part in parts[0].split("x"))
        return width, height, int(parts[1]), int(parts[2])

    @staticmethod
    def _expected_dialog_size(dialog):
        if dialog.title() == "Settings":
            return (760, 700)
        return (760, 560)


@unittest.skipUnless(TK_AVAILABLE, "Tk not available")
@unittest.skipUnless(__import__("os").name == "nt",
                     "Win32 monitor lookup requires Windows")
class WindowsMonitorBoundarySmokeTests(unittest.TestCase):
    """Real Win32 lookup evidence on Windows with a live window."""

    def test_windows_work_area_returns_taskbar_excluded_bounds(self):
        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.geometry("400x300+60+60")
        root.update_idletasks()
        root.update()
        work = main_module._windows_work_area(int(root.winfo_id()))
        self.assertIsNotNone(work)
        left, top, right, bottom = work
        # rcWork must sit inside the physical monitor bounds and exclude
        # reserved bars, so its height cannot exceed Tk's reported screen.
        self.assertLessEqual(bottom - top,
                             root.winfo_screenheight())
        self.assertGreater(right - left, 0)
        self.assertGreater(bottom - top, 0)

    def test_windows_work_area_rejects_invalid_handles(self):
        self.assertIsNone(main_module._windows_work_area(0))
        self.assertIsNone(main_module._windows_work_area(0xDEADBEEF))


if __name__ == "__main__":
    unittest.main()
