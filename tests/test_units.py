#!/usr/bin/env python3
"""Headless unit tests for the parsing and model layers.

Everything here runs without a display, a receiver, or GTK — the protocol
code is standard library only, which is what makes it testable in CI. The
tray and panel are covered by tests/verify_ui.py, which needs a real desktop.

Run with `make test`, or `python3 -m unittest discover -s tests`.
"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voltaic import airpods, hidpp, state  # noqa: E402
from voltaic.model import (  # noqa: E402
    CHARGE_CHARGING,
    CHARGE_DISCHARGING,
    CHARGE_DISCONNECTED,
    Battery,
    Cell,
    Device,
)


class ReportDescriptorTests(unittest.TestCase):
    """Finding the HID++ interface means reading report descriptors."""

    # Usage Page (Vendor 0xFF00), Report ID 0x10, Report ID 0x11 — the
    # signature Voltaic looks for when picking a hidraw node.
    HIDPP = bytes([0x06, 0x00, 0xFF, 0x85, 0x10, 0x85, 0x11])

    def test_finds_vendor_page_and_report_ids(self):
        self.assertEqual(hidpp.parse_report_descriptor(self.HIDPP),
                         {(0xFF00, 0x10), (0xFF00, 0x11)})

    def test_report_ids_track_the_current_usage_page(self):
        # A keyboard interface (generic desktop page) must not be mistaken
        # for a HID++ one just because it also uses report id 0x10.
        data = bytes([0x05, 0x01, 0x85, 0x10]) + self.HIDPP
        pairs = hidpp.parse_report_descriptor(data)
        self.assertIn((0x01, 0x10), pairs)
        self.assertIn((0xFF00, 0x10), pairs)

    def test_long_items_are_skipped(self):
        # 0xFE introduces a long item: [0xFE, size, tag, data...].
        data = bytes([0xFE, 0x02, 0x00, 0xAA, 0xBB]) + self.HIDPP
        self.assertEqual(hidpp.parse_report_descriptor(data),
                         {(0xFF00, 0x10), (0xFF00, 0x11)})

    def test_truncated_descriptor_does_not_raise(self):
        # Real descriptors get cut short by short reads; bail, do not crash.
        self.assertIsInstance(hidpp.parse_report_descriptor(bytes([0x06, 0x00])),
                              set)
        self.assertEqual(hidpp.parse_report_descriptor(b""), set())


class VoltageTests(unittest.TestCase):
    """Older devices report millivolts, not a state of charge."""

    def test_zero_is_unknown_not_empty(self):
        self.assertIsNone(hidpp._voltage_to_percent(0))

    def test_above_curve_is_full(self):
        top_mv = hidpp._VOLTAGE_CURVE[0][0]
        self.assertEqual(hidpp._voltage_to_percent(top_mv + 50), 100)
        self.assertEqual(hidpp._voltage_to_percent(top_mv), 100)

    def test_below_curve_is_empty(self):
        bottom_mv = hidpp._VOLTAGE_CURVE[-1][0]
        self.assertEqual(hidpp._voltage_to_percent(bottom_mv - 100), 0)

    def test_curve_points_map_to_their_own_percent(self):
        for mv, percent in hidpp._VOLTAGE_CURVE:
            self.assertEqual(hidpp._voltage_to_percent(mv), percent,
                             f"{mv}mv should read {percent}%")

    def test_interpolates_between_points(self):
        (hi_mv, hi_pct), (lo_mv, lo_pct) = hidpp._VOLTAGE_CURVE[1:3]
        midpoint = hidpp._voltage_to_percent((hi_mv + lo_mv) // 2)
        self.assertGreater(midpoint, lo_pct)
        self.assertLess(midpoint, hi_pct)

    def test_percent_never_leaves_0_100(self):
        for mv in range(3000, 4400, 7):
            percent = hidpp._voltage_to_percent(mv)
            self.assertTrue(0 <= percent <= 100, f"{mv}mv gave {percent}")


class AapFrameTests(unittest.TestCase):
    """The AAP battery frame documented in the README."""

    # 6-byte header, count, then 5-byte entries: component, ?, level, status, ?
    FRAME = bytes([0x04, 0x00, 0x04, 0x00, 0x04, 0x00, 0x03,
                   0x02, 0x01, 0x63, 0x02, 0x01,   # right, 99%, discharging
                   0x04, 0x01, 0x63, 0x02, 0x01,   # left,  99%, discharging
                   0x08, 0x01, 0x00, 0x04, 0x01])  # case, absent

    def test_decodes_the_documented_frame(self):
        cells = airpods.parse_battery(self.FRAME)
        self.assertEqual([c.label for c in cells], ["Left", "Right", "Case"])
        self.assertEqual(cells[0].battery.percent, 99)
        self.assertEqual(cells[1].battery.percent, 99)

    def test_disconnected_part_has_no_percent(self):
        # A case that is not in play reports level 0; showing 0% would lie.
        case = airpods.parse_battery(self.FRAME)[2]
        self.assertIsNone(case.battery.percent)
        self.assertEqual(case.battery.status, CHARGE_DISCONNECTED)
        self.assertFalse(case.battery.present)

    def test_cells_are_ordered_left_right_case(self):
        # The frame lists right before left; the panel must not.
        self.assertEqual([c.label for c in airpods.parse_battery(self.FRAME)],
                         ["Left", "Right", "Case"])

    def test_unknown_component_is_ignored(self):
        frame = bytes([0x04, 0x00, 0x04, 0x00, 0x04, 0x00, 0x01,
                       0x40, 0x01, 0x50, 0x02, 0x01])
        self.assertEqual(airpods.parse_battery(frame), [])

    def test_0xff_level_is_unknown(self):
        frame = bytes([0x04, 0x00, 0x04, 0x00, 0x04, 0x00, 0x01,
                       0x02, 0x01, 0xFF, 0x02, 0x01])
        self.assertIsNone(airpods.parse_battery(frame)[0].battery.percent)

    def test_charging_status_is_decoded(self):
        frame = bytes([0x04, 0x00, 0x04, 0x00, 0x04, 0x00, 0x01,
                       0x02, 0x01, 0x2A, 0x01, 0x01])
        battery = airpods.parse_battery(frame)[0].battery
        self.assertEqual(battery.percent, 42)
        self.assertTrue(battery.charging)

    def test_truncated_entry_stops_cleanly(self):
        # Count claims three entries but only one and a half are present.
        frame = self.FRAME[:15]
        self.assertEqual(len(airpods.parse_battery(frame)), 1)

    def test_empty_payload(self):
        self.assertEqual(airpods.parse_battery(b"\x04\x00\x04\x00\x04\x00"), [])
        self.assertEqual(airpods.parse_battery(b""), [])

    def test_level_is_clamped_to_100(self):
        frame = bytes([0x04, 0x00, 0x04, 0x00, 0x04, 0x00, 0x01,
                       0x02, 0x01, 0xC8, 0x02, 0x01])
        self.assertEqual(airpods.parse_battery(frame)[0].battery.percent, 100)


class TidyNameTests(unittest.TestCase):
    def test_strips_typographic_apostrophe(self):
        self.assertEqual(airpods.tidy_name("Alex’s AirPods Pro"), "AirPods Pro")

    def test_strips_plain_apostrophe(self):
        self.assertEqual(airpods.tidy_name("Alex's AirPods Pro"), "AirPods Pro")

    def test_leaves_unowned_names_alone(self):
        self.assertEqual(airpods.tidy_name("AirPods Max"), "AirPods Max")

    def test_keeps_name_that_is_only_a_prefix(self):
        # Nothing after the separator means there is nothing to keep.
        self.assertEqual(airpods.tidy_name("Alex's "), "Alex's ")


class DeviceModelTests(unittest.TestCase):
    def test_display_name_falls_back_to_index(self):
        self.assertEqual(Device(index=3).display_name, "Device 3")
        self.assertEqual(Device(index=3, name="MX Master 4").display_name,
                         "MX Master 4")

    def test_hidpp_key_is_node_and_index(self):
        device = Device(index=2, path="/dev/hidraw4", transport="hidpp")
        self.assertEqual(device.key, "hidraw4:2")

    def test_airpods_key_is_address(self):
        device = Device(path="AA:BB:CC:DD:EE:FF", transport="airpods")
        self.assertEqual(device.key, "airpods:AA:BB:CC:DD:EE:FF")

    def test_lowest_percent_ignores_absent_case(self):
        # The whole point: buds in your ears must not peg the icon at 0%.
        device = Device(transport="airpods", cells=[
            Cell("Left", Battery(percent=80)),
            Cell("Right", Battery(percent=75)),
            Cell("Case", Battery(percent=None, status=CHARGE_DISCONNECTED)),
        ])
        self.assertEqual(device.lowest_percent, 75)

    def test_lowest_percent_is_none_when_nothing_reports(self):
        self.assertIsNone(Device().lowest_percent)
        self.assertIsNone(Device(battery=Battery(percent=None)).lowest_percent)

    def test_single_battery_device(self):
        device = Device(battery=Battery(percent=40))
        self.assertEqual(device.lowest_percent, 40)
        self.assertEqual(len(device.batteries), 1)

    def test_any_charging_across_cells(self):
        device = Device(cells=[
            Cell("Left", Battery(percent=80, status=CHARGE_DISCHARGING)),
            Cell("Right", Battery(percent=75, status=CHARGE_CHARGING)),
        ])
        self.assertTrue(device.any_charging)

    def test_can_connect_only_for_offline_bluetooth(self):
        # A Logitech device behind a receiver cannot be summoned by the host,
        # so offering a Connect button there would be a lie.
        self.assertTrue(Device(transport="airpods", online=False).can_connect)
        self.assertFalse(Device(transport="airpods", online=True).can_connect)
        self.assertFalse(Device(transport="hidpp", online=False).can_connect)


class DescribeAgeTests(unittest.TestCase):
    def test_never_seen(self):
        self.assertEqual(state.describe_age(None), "offline")

    def test_recent(self):
        self.assertIn("just now", state.describe_age(time.time() - 5))

    def test_minutes_hours_days(self):
        self.assertIn("m ago", state.describe_age(time.time() - 600))
        self.assertIn("h ago", state.describe_age(time.time() - 7200))
        self.assertIn("d ago", state.describe_age(time.time() - 3 * 86400))

    def test_future_timestamp_does_not_go_negative(self):
        # A clock change must not produce "seen -4m ago".
        self.assertNotIn("-", state.describe_age(time.time() + 600))


class ReconcileTests(unittest.TestCase):
    """Offline devices are filled back in from the cache."""

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self._saved = state.CACHE_PATH
        state.CACHE_PATH = os.path.join(self._dir.name, "devices.json")

    def tearDown(self):
        state.CACHE_PATH = self._saved
        self._dir.cleanup()

    def test_online_device_is_remembered_and_restored(self):
        online = Device(index=1, name="MX Keys S", kind="keyboard",
                        path="/dev/hidraw4", battery=Battery(percent=55))
        state.reconcile([online])

        # Same slot, but the device has since been switched off: no name,
        # no battery, which without the cache looks like a bug.
        offline = Device(index=1, path="/dev/hidraw4", online=False)
        state.reconcile([offline])
        self.assertEqual(offline.name, "MX Keys S")
        self.assertEqual(offline.kind, "keyboard")
        self.assertEqual(offline.battery.percent, 55)
        self.assertEqual(offline.battery.source, "cached")
        self.assertIsNotNone(offline.last_seen)

    def test_cells_survive_going_offline(self):
        online = Device(name="AirPods Pro", path="AA:BB", transport="airpods",
                        cells=[Cell("Left", Battery(percent=90)),
                               Cell("Right", Battery(percent=88))])
        state.reconcile([online])

        offline = Device(path="AA:BB", transport="airpods", online=False)
        state.reconcile([offline])
        self.assertEqual([c.battery.percent for c in offline.cells], [90, 88])

    def test_unknown_offline_device_is_left_alone(self):
        offline = Device(index=6, path="/dev/hidraw4", online=False)
        state.reconcile([offline])
        self.assertEqual(offline.name, "")
        self.assertIsNone(offline.battery)

    def test_live_reading_is_not_overwritten_by_cache(self):
        first = Device(index=1, name="MX Master 4", path="/dev/hidraw4",
                       battery=Battery(percent=50))
        state.reconcile([first])
        second = Device(index=1, name="MX Master 4", path="/dev/hidraw4",
                        battery=Battery(percent=45))
        state.reconcile([second])
        self.assertEqual(second.battery.percent, 45)

    def test_missing_cache_file_is_not_an_error(self):
        self.assertEqual(state.load(), {})


class InstallHintTests(unittest.TestCase):
    """The missing-dependency message has to name the right package manager."""

    def setUp(self):
        from voltaic import __main__ as entry
        self.entry = entry
        self._which = entry.shutil.which

    def tearDown(self):
        self.entry.shutil.which = self._which

    def test_picks_the_available_manager(self):
        self.entry.shutil.which = lambda name: "/usr/bin/dnf" if name == "dnf" else None
        self.assertIn("dnf", self.entry._install_hint())

    def test_falls_back_when_nothing_matches(self):
        self.entry.shutil.which = lambda name: None
        self.assertIn("PyGObject", self.entry._install_hint())

    def test_every_command_mentions_pygobject(self):
        for manager, command in self.entry.DEP_COMMANDS:
            self.assertRegex(command, r"gi|gobject", f"{manager}: {command}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
