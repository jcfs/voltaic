#!/usr/bin/env python3
"""Tests for configuration and the device-source registry.

Both are standard library only — `sources` imports `gi` lazily, inside the
functions that need D-Bus — so these run headless with no GTK.

Run with `make test`.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voltaic import config, sources  # noqa: E402
from voltaic.model import Battery, Device  # noqa: E402


class LoadTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "config.json")

    def tearDown(self):
        self._dir.cleanup()

    def write(self, data):
        with open(self.path, "w") as handle:
            json.dump(data, handle)

    def test_missing_file_gives_defaults(self):
        self.assertEqual(config.load(self.path), config.DEFAULTS)

    def test_broken_json_falls_back_rather_than_raising(self):
        # A typo in a config file must not cost you your battery indicator.
        with open(self.path, "w") as handle:
            handle.write("{ this is not json")
        self.assertEqual(config.load(self.path), config.DEFAULTS)

    def test_non_object_json_falls_back(self):
        self.write([1, 2, 3])
        self.assertEqual(config.load(self.path), config.DEFAULTS)

    def test_partial_file_keeps_the_other_defaults(self):
        # Settings added in a later version must not vanish for someone
        # whose config file predates them.
        self.write({"interval": 60})
        loaded = config.load(self.path)
        self.assertEqual(loaded["interval"], 60)
        self.assertEqual(loaded["notify"], config.DEFAULTS["notify"])
        self.assertEqual(loaded["tray"], config.DEFAULTS["tray"])

    def test_partial_nested_dict_merges_rather_than_replaces(self):
        self.write({"sources": {"upower": True}})
        loaded = config.load(self.path)
        self.assertTrue(loaded["sources"]["upower"])
        self.assertTrue(loaded["sources"]["hidpp"], "hidpp was dropped")

    def test_round_trip(self):
        original = config.load(self.path)
        original["interval"] = 42
        config.save(original, self.path)
        self.assertEqual(config.load(self.path)["interval"], 42)

    def test_save_creates_the_directory(self):
        nested = os.path.join(self._dir.name, "a", "b", "config.json")
        config.save({"interval": 5}, nested)
        self.assertTrue(os.path.exists(nested))


class EnabledSourcesTests(unittest.TestCase):
    def test_defaults_enable_only_the_specific_sources(self):
        enabled = config.enabled_sources(config.DEFAULTS)
        self.assertIn("hidpp", enabled)
        self.assertIn("airpods", enabled)
        # The generic ones duplicate what the desktop already shows.
        self.assertNotIn("upower", enabled)
        self.assertNotIn("bluez", enabled)

    def test_enabling_a_generic_source(self):
        cfg = config._merge(config.DEFAULTS, {"sources": {"upower": True}})
        self.assertIn("upower", config.enabled_sources(cfg))

    def test_disabling_a_default_source(self):
        cfg = config._merge(config.DEFAULTS, {"sources": {"airpods": False}})
        self.assertNotIn("airpods", config.enabled_sources(cfg))

    def test_order_is_stable(self):
        cfg = config._merge(config.DEFAULTS,
                            {"sources": {"upower": True, "bluez": True}})
        self.assertEqual(config.enabled_sources(cfg),
                         ["hidpp", "airpods", "upower", "bluez"])


class OverrideTests(unittest.TestCase):
    def devices(self):
        return [
            Device(index=1, name="MX Keys S", path="/dev/hidraw3",
                   battery=Battery(percent=90)),
            Device(name="AirPods Pro", path="AA:BB", transport="airpods",
                   battery=Battery(percent=50)),
        ]

    def test_rename(self):
        cfg = config.set_device(dict(config.DEFAULTS), "hidraw3:1",
                                name="Desk keyboard")
        result = config.apply_overrides(cfg, self.devices())
        self.assertEqual(result[0].name, "Desk keyboard")

    def test_hide(self):
        cfg = config.set_device(dict(config.DEFAULTS), "airpods:AA:BB",
                                hidden=True)
        result = config.apply_overrides(cfg, self.devices())
        self.assertEqual([d.name for d in result], ["MX Keys S"])

    def test_no_overrides_changes_nothing(self):
        result = config.apply_overrides(dict(config.DEFAULTS), self.devices())
        self.assertEqual([d.name for d in result],
                         ["MX Keys S", "AirPods Pro"])

    def test_hidden_false_is_not_hidden(self):
        cfg = config.set_device(dict(config.DEFAULTS), "airpods:AA:BB",
                                hidden=False)
        self.assertEqual(len(config.apply_overrides(cfg, self.devices())), 2)

    def test_setting_none_removes_the_override(self):
        cfg = config.set_device(dict(config.DEFAULTS), "hidraw3:1",
                                name="Desk keyboard")
        cfg = config.set_device(cfg, "hidraw3:1", name=None)
        self.assertEqual(config.device_override(cfg, "hidraw3:1"), {})
        self.assertEqual(config.apply_overrides(cfg, self.devices())[0].name,
                         "MX Keys S")

    def test_set_device_does_not_mutate_the_original(self):
        original = dict(config.DEFAULTS)
        config.set_device(original, "hidraw3:1", hidden=True)
        self.assertEqual(original.get("devices"), {})

    def test_unknown_key_is_ignored(self):
        cfg = config.set_device(dict(config.DEFAULTS), "nope:9", hidden=True)
        self.assertEqual(len(config.apply_overrides(cfg, self.devices())), 2)


class RegistryTests(unittest.TestCase):
    def test_known_names_build(self):
        built = sources.build(["airpods", "upower", "bluez"])
        self.assertEqual([s.name for s in built],
                         ["airpods", "upower", "bluez"])

    def test_hidpp_is_not_a_source(self):
        # It owns the file descriptors the monitor selects on, so the
        # monitor drives it directly rather than through the registry.
        self.assertNotIn("hidpp", sources.REGISTRY)
        self.assertEqual(sources.build(["hidpp"]), [])

    def test_unknown_name_is_skipped_not_fatal(self):
        # A config written for a newer version must not stop an older one.
        self.assertEqual([s.name for s in sources.build(["airpods", "wat"])],
                         ["airpods"])

    def test_every_registered_source_has_a_matching_default(self):
        for name in sources.REGISTRY:
            self.assertIn(name, config.DEFAULTS["sources"],
                          f"{name} has no entry in config.DEFAULTS")

    def test_generic_sources_return_a_list_without_dbus(self):
        # No D-Bus in the test environment: they must come back empty
        # rather than raising, since one broken source must not take down
        # a scan.
        for name in ("upower", "bluez"):
            with self.subTest(source=name):
                self.assertIsInstance(sources.REGISTRY[name]().scan(), list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
