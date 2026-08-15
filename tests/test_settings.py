#!/usr/bin/env python3
"""Tests for the settings window.

The window is built but never shown. It still needs a display to construct,
so these skip without one (CI runs them under xvfb). What matters is that
the widgets round-trip to the same config the command line reads.

Skipped where GTK is absent, so the no-GTK CI job stays green.

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
    from gi.repository import Gtk

    from voltaic import settings as settings_module
except (ImportError, ValueError):  # pragma: no cover - depends on the machine
    settings_module = None
    Gtk = None

from voltaic import config as config_module  # noqa: E402
from voltaic.model import Battery, Device  # noqa: E402


def _has_display() -> bool:
    """Can a window actually be constructed here?

    Importing GTK only needs the typelib; building a Gtk.Window needs a
    display. Checking the import alone made these tests pass on a desktop
    and error on a headless runner, which is the wrong way round for a
    suite that claims to run anywhere.
    """
    if Gtk is None:
        return False
    return Gtk.init_check([])[0]


needs_gtk = unittest.skipUnless(
    _has_display(), "needs GTK and a display (try xvfb-run)")


def sample_devices():
    return [
        Device(index=1, name="MX Keys S", kind="keyboard",
               path="/dev/hidraw3", battery=Battery(percent=90)),
        Device(name="AirPods Pro", kind="earbuds", path="A0:A3",
               transport="airpods", online=False),
    ]


def window(config=None):
    return settings_module.SettingsWindow(
        config or dict(config_module.DEFAULTS), sample_devices(),
        on_apply=lambda _c: None)


@needs_gtk
class GeneralTests(unittest.TestCase):
    def test_defaults_are_shown(self):
        win = window()
        # Stored in seconds, shown in minutes.
        self.assertEqual(win.interval.get_value(), 15)
        self.assertEqual(int(win.low_percent.get_value()), 20)
        self.assertTrue(win.notify.get_active())
        self.assertEqual(win.tray.get_active_id(), "auto")

    def test_interval_round_trips_through_minutes(self):
        win = window()
        win.interval.set_value(5)
        self.assertEqual(win.collect()["interval"], 300.0)

    def test_existing_config_populates_the_widgets(self):
        cfg = config_module._merge(config_module.DEFAULTS,
                                   {"interval": 1800, "low_percent": 33,
                                    "notify": False, "tray": "xapp"})
        win = window(cfg)
        self.assertEqual(win.interval.get_value(), 30)
        self.assertEqual(int(win.low_percent.get_value()), 33)
        self.assertFalse(win.notify.get_active())
        self.assertEqual(win.tray.get_active_id(), "xapp")

    def test_notify_and_threshold_collect(self):
        win = window()
        win.notify.set_active(False)
        win.low_percent.set_value(15)
        collected = win.collect()
        self.assertFalse(collected["notify"])
        self.assertEqual(collected["low_percent"], 15)


@needs_gtk
class SourceTests(unittest.TestCase):
    def test_every_source_has_a_toggle(self):
        win = window()
        self.assertEqual(set(win.source_toggles),
                         set(config_module.DEFAULTS["sources"]))

    def test_every_source_has_a_human_label(self):
        # A checkbox reading "bluez" tells a user nothing.
        for name in config_module.DEFAULTS["sources"]:
            self.assertIn(name, settings_module.SOURCE_LABELS,
                          f"{name} has no label")

    def test_toggling_a_source_collects(self):
        win = window()
        win.source_toggles["upower"].set_active(True)
        self.assertTrue(win.collect()["sources"]["upower"])

    def test_generic_sources_start_off(self):
        win = window()
        self.assertFalse(win.source_toggles["upower"].get_active())
        self.assertTrue(win.source_toggles["hidpp"].get_active())


@needs_gtk
class DeviceTests(unittest.TestCase):
    def test_a_row_per_device(self):
        self.assertEqual(len(window()._device_rows), 2)

    def test_rename_becomes_an_override(self):
        win = window()
        win._device_rows[0][1].set_text("Desk keyboard")
        self.assertEqual(win.collect()["devices"]["hidraw3:1"],
                         {"name": "Desk keyboard"})

    def test_unchanged_name_stores_nothing(self):
        # Storing the current name verbatim would freeze it if the device
        # ever reported a different one.
        win = window()
        self.assertEqual(win.collect()["devices"], {})

    def test_clearing_the_name_removes_the_override(self):
        cfg = config_module.set_device(dict(config_module.DEFAULTS),
                                       "hidraw3:1", name="Old name")
        win = window(cfg)
        win._device_rows[0][1].set_text("")
        self.assertNotIn("name", win.collect()["devices"].get("hidraw3:1", {}))

    def test_unchecking_show_hides(self):
        win = window()
        win._device_rows[1][2].set_active(False)
        self.assertTrue(win.collect()["devices"]["airpods:A0:A3"]["hidden"])

    def test_hidden_device_still_appears_in_the_window(self):
        # Otherwise it could never be un-hidden from the UI.
        cfg = config_module.set_device(dict(config_module.DEFAULTS),
                                       "airpods:A0:A3", hidden=True)
        win = window(cfg)
        self.assertEqual(len(win._device_rows), 2)
        self.assertFalse(win._device_rows[1][2].get_active())

    def test_re_showing_removes_the_override(self):
        cfg = config_module.set_device(dict(config_module.DEFAULTS),
                                       "airpods:A0:A3", hidden=True)
        win = window(cfg)
        win._device_rows[1][2].set_active(True)
        self.assertEqual(win.collect()["devices"], {})

    def test_collected_config_drives_the_panel(self):
        win = window()
        win._device_rows[0][1].set_text("Desk keyboard")
        win._device_rows[1][2].set_active(False)
        shown = config_module.apply_overrides(win.collect(), sample_devices())
        self.assertEqual([d.display_name for d in shown], ["Desk keyboard"])

    def test_no_devices_does_not_crash(self):
        win = settings_module.SettingsWindow(
            dict(config_module.DEFAULTS), [], on_apply=lambda _c: None)
        self.assertEqual(win._device_rows, [])
        self.assertEqual(win.collect()["devices"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
