#!/usr/bin/env python3
"""Tray behaviour under a real Wayland session.

`tests/test_tray.py` covers backend selection with the session type stubbed
out. These run against an actual Wayland compositor, because the bug they
guard against was invisible to stubs: `Gtk.StatusIcon` constructs happily
under Wayland, reports `is_embedded() == False`, and shows nothing — so
Voltaic ran with no icon and no error on the default session of current
Ubuntu and Fedora.

Skipped unless the process is genuinely on Wayland. CI runs them under a
headless Weston:

    weston --backend=headless --socket=wl-ci &
    WAYLAND_DISPLAY=wl-ci python3 -m unittest tests.test_wayland

Run with `make test`.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import gi

    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, Gtk

    from voltaic.tray import Tray
except (ImportError, ValueError):  # pragma: no cover - depends on the machine
    Gdk = Gtk = Tray = None


def _on_wayland() -> bool:
    if Gdk is None or not Gtk.init_check([])[0]:
        return False
    display = Gdk.Display.get_default()
    return display is not None and type(display).__name__.startswith(
        "GdkWayland")


needs_wayland = unittest.skipUnless(
    _on_wayland(), "not a Wayland session (CI runs these under Weston)")


@needs_wayland
class WaylandSessionTests(unittest.TestCase):
    def test_session_is_detected(self):
        self.assertTrue(Tray.is_wayland())

    def test_xembed_is_not_offered(self):
        # No stubbing here: the live display drives the decision, which is
        # the whole point of running this under a real compositor.
        tray = Tray.__new__(Tray)
        self.assertNotIn("xembed", tray._auto_order())

    def test_status_icon_would_not_be_embedded(self):
        """The behaviour that makes XEmbed useless here.

        If this ever starts passing embedded, XEmbed works on Wayland and
        the backend order should be revisited.
        """
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            icon = Gtk.StatusIcon()
            icon.set_visible(True)
            self.assertFalse(icon.is_embedded())

    def test_a_backend_is_chosen_without_xembed(self):
        noop = lambda *a, **k: None  # noqa: E731
        tray = Tray(on_toggle=noop, on_refresh=noop, on_quit=noop)
        self.assertNotEqual(tray.backend, "xembed")

    def test_no_tray_is_explained_rather_than_silent(self):
        noop = lambda *a, **k: None  # noqa: E731
        tray = Tray(on_toggle=noop, on_refresh=noop, on_quit=noop)
        reason = tray.invisible_reason()
        if tray.backend == "none":
            self.assertIsNotNone(reason, "no backend and no explanation")
            self.assertIn("Wayland", reason)
        else:
            # A backend was found, so there is nothing to complain about.
            self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
