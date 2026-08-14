#!/usr/bin/env python3
"""Tests for the colour palette and the cairo-rendered tray icon.

These need pycairo but no display and no GTK — cairo draws to an in-memory
surface quite happily on a headless machine. They are skipped rather than
failed where pycairo is absent, so the no-GTK CI job (which exists to prove
the protocol layers stay dependency-free) still passes.

Run with `make test`.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import cairo
except ImportError:  # pragma: no cover - depends on the machine
    cairo = None

from voltaic import theme  # noqa: E402

if cairo is not None:
    from voltaic import icons
else:  # pragma: no cover
    icons = None

needs_cairo = unittest.skipIf(cairo is None, "pycairo is not installed")


class LevelColorTests(unittest.TestCase):
    """Which colour a reading gets. No cairo needed — it is pure arithmetic."""

    def test_full_is_good(self):
        self.assertEqual(theme.level_color(100), theme.GOOD)
        self.assertEqual(theme.level_color(50), theme.GOOD)

    def test_middling_is_medium(self):
        self.assertEqual(theme.level_color(49), theme.MEDIUM)
        self.assertEqual(theme.level_color(21), theme.MEDIUM)

    def test_low_is_low(self):
        self.assertEqual(theme.level_color(20), theme.LOW)
        self.assertEqual(theme.level_color(0), theme.LOW)

    def test_unknown_is_grey(self):
        self.assertEqual(theme.level_color(None), theme.UNKNOWN)

    def test_charging_wins_over_level(self):
        # A charging device is blue whatever its charge, including empty.
        for percent in (None, 0, 20, 50, 100):
            with self.subTest(percent=percent):
                self.assertEqual(theme.level_color(percent, charging=True),
                                 theme.CHARGING)

    def test_thresholds_match_the_documented_constants(self):
        self.assertEqual(theme.level_color(theme.LOW_BELOW - 1), theme.LOW)
        self.assertEqual(theme.level_color(theme.LOW_BELOW), theme.MEDIUM)
        self.assertEqual(theme.level_color(theme.MEDIUM_BELOW - 1),
                         theme.MEDIUM)
        self.assertEqual(theme.level_color(theme.MEDIUM_BELOW), theme.GOOD)

    def test_every_colour_is_in_range(self):
        for name in ("GOOD", "MEDIUM", "LOW", "CHARGING", "UNKNOWN"):
            for channel in getattr(theme, name):
                self.assertTrue(0.0 <= channel <= 1.0, f"{name}: {channel}")


@needs_cairo
class CairoHelperTests(unittest.TestCase):
    def surface(self, size=64):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        return surface, cairo.Context(surface)

    def test_rounded_rect_paints_something(self):
        surface, cr = self.surface()
        theme.rounded_rect(cr, 4, 4, 56, 56, 8)
        cr.set_source_rgba(1, 1, 1, 1)
        cr.fill()
        surface.flush()
        self.assertTrue(any(surface.get_data()), "nothing was drawn")

    def test_rounded_rect_clamps_an_oversized_radius(self):
        # A radius larger than the box would otherwise produce a broken path.
        surface, cr = self.surface()
        theme.rounded_rect(cr, 0, 0, 20, 10, 999)
        cr.set_source_rgba(1, 1, 1, 1)
        cr.fill()
        surface.flush()
        self.assertTrue(any(surface.get_data()))

    def test_draw_bolt_paints_something(self):
        surface, cr = self.surface()
        theme.draw_bolt(cr, 32, 32, 20, theme.CHARGING)
        surface.flush()
        self.assertTrue(any(surface.get_data()))


@needs_cairo
class RenderIconTests(unittest.TestCase):
    """The tray icon is drawn rather than themed, and cached per level."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._saved = icons.CACHE_DIR
        icons.CACHE_DIR = self._dir.name

    def tearDown(self):
        icons.CACHE_DIR = self._saved
        self._dir.cleanup()

    def test_renders_a_png_of_the_requested_size(self):
        path = icons.render_icon(75, size=22)
        self.assertTrue(os.path.exists(path))
        surface = cairo.ImageSurface.create_from_png(path)
        self.assertEqual((surface.get_width(), surface.get_height()), (22, 22))

    def test_icon_is_not_blank(self):
        path = icons.render_icon(75, size=22)
        surface = cairo.ImageSurface.create_from_png(path)
        surface.flush()
        self.assertTrue(any(surface.get_data()), "icon came out empty")

    def test_second_call_reuses_the_cached_file(self):
        first = icons.render_icon(75, size=22)
        stamp = os.stat(first).st_mtime_ns
        second = icons.render_icon(75, size=22)
        self.assertEqual(first, second)
        self.assertEqual(os.stat(second).st_mtime_ns, stamp,
                         "cached icon was rewritten")

    def test_distinct_levels_get_distinct_files(self):
        self.assertNotEqual(icons.render_icon(75, size=22),
                            icons.render_icon(20, size=22))

    def test_charging_gets_its_own_file(self):
        self.assertNotEqual(icons.render_icon(75, size=22, charging=False),
                            icons.render_icon(75, size=22, charging=True))

    def test_unknown_level_renders(self):
        path = icons.render_icon(None, size=22)
        self.assertTrue(os.path.exists(path))

    def test_size_has_a_floor(self):
        # HiDPI maths can ask for a silly size; a 0px surface would throw.
        path = icons.render_icon(50, size=1)
        surface = cairo.ImageSurface.create_from_png(path)
        self.assertGreaterEqual(surface.get_width(), 16)

    def test_extreme_levels_render(self):
        for percent in (0, 1, 99, 100):
            with self.subTest(percent=percent):
                self.assertTrue(os.path.exists(
                    icons.render_icon(percent, size=22)))

    def test_no_temporary_files_are_left_behind(self):
        icons.render_icon(75, size=22)
        leftovers = [n for n in os.listdir(self._dir.name)
                     if not n.endswith(".png") or n.startswith("tmp")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
