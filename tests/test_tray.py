#!/usr/bin/env python3
"""Tests for tray backend selection.

Needs GTK to import the module, but constructs no real status icon: the
selection logic is exercised on a bare instance with the session type
stubbed, so these run headless and without a tray.

Skipped rather than failed where GTK is absent, so the no-GTK CI job stays
green.

Run with `make test`.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from voltaic import tray as tray_module
except (ImportError, ValueError):  # pragma: no cover - depends on the machine
    tray_module = None

needs_gtk = unittest.skipIf(tray_module is None, "GTK is not installed")


def bare_tray(wayland: bool, backend: str = "none", status=None):
    """A Tray with nothing constructed, and the session type forced."""
    obj = tray_module.Tray.__new__(tray_module.Tray)
    # Instance attribute shadows the staticmethod, so no display is needed.
    obj.is_wayland = lambda: wayland
    obj.backend = backend
    obj._status = status
    return obj


@needs_gtk
class AutoOrderTests(unittest.TestCase):
    def test_x11_prefers_xembed(self):
        # XEmbed is the only backend reporting hover and icon geometry, so
        # it stays first wherever it can actually work.
        self.assertEqual(bare_tray(wayland=False)._auto_order(),
                         ("xembed", "xapp", "appindicator"))

    def test_wayland_skips_xembed(self):
        # Gtk.StatusIcon constructs happily under Wayland and is then never
        # embedded, which shows the user no icon at all. Verified against a
        # headless Weston: is_embedded() False, get_geometry() False.
        order = bare_tray(wayland=True)._auto_order()
        self.assertNotIn("xembed", order)
        self.assertEqual(order, ("xapp", "appindicator"))


@needs_gtk
class InvisibleReasonTests(unittest.TestCase):
    def test_no_backend_on_wayland_mentions_wayland(self):
        reason = bare_tray(wayland=True, backend="none").invisible_reason()
        self.assertIsNotNone(reason)
        self.assertIn("Wayland", reason)

    def test_no_backend_on_x11_mentions_the_extension(self):
        reason = bare_tray(wayland=False, backend="none").invisible_reason()
        self.assertIsNotNone(reason)
        self.assertIn("AppIndicator", reason)

    def test_working_backend_has_no_complaint(self):
        for backend in ("xapp", "appindicator"):
            with self.subTest(backend=backend):
                self.assertIsNone(
                    bare_tray(wayland=False, backend=backend).invisible_reason())

    def test_xembed_that_never_embedded_is_reported(self):
        class NotEmbedded:
            def is_embedded(self):
                return False

        reason = bare_tray(wayland=False, backend="xembed",
                           status=NotEmbedded()).invisible_reason()
        self.assertIsNotNone(reason)
        self.assertIn("not visible", reason)

    def test_xembed_that_embedded_is_silent(self):
        class Embedded:
            def is_embedded(self):
                return True

        self.assertIsNone(bare_tray(wayland=False, backend="xembed",
                                    status=Embedded()).invisible_reason())

    def test_every_reason_tells_the_user_what_to_do(self):
        # A message that only says "broken" is worse than none.
        for wayland in (True, False):
            with self.subTest(wayland=wayland):
                reason = bare_tray(wayland=wayland,
                                   backend="none").invisible_reason()
                self.assertTrue(
                    "install" in reason.lower() or "try" in reason.lower(),
                    f"no actionable advice in: {reason}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
