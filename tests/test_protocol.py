#!/usr/bin/env python3
"""End-to-end HID++ tests against a fake receiver.

`tests/test_transport.py` covers framing and reply-matching in isolation.
These drive the whole enumeration path — ping, feature lookup, name, kind,
battery — through a receiver that answers like the real thing, which is the
part that used to be reachable only with hardware plugged in.

Run with `make test`.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
# The helper lives beside this file, which is not on the path when the
# suite is run from the project root.
sys.path.insert(0, _HERE)

from fake_receiver import (  # noqa: E402
    LEGACY_BATTERY_INDEX,
    NAME_INDEX,
    UNIFIED_BATTERY_INDEX,
    FakeDevice,
    FakeReceiver,
)

from voltaic import hidpp  # noqa: E402
from voltaic.model import CHARGE_CHARGING, CHARGE_FULL  # noqa: E402


class PingTests(unittest.TestCase):
    def test_reports_the_protocol_version(self):
        with FakeReceiver({1: FakeDevice(protocol=(4, 5))}) as receiver:
            self.assertEqual(receiver.connection.ping(1), (4, 5))

    def test_empty_slot_is_unreachable(self):
        with FakeReceiver({1: FakeDevice()}) as receiver:
            with self.assertRaises(hidpp.DeviceUnreachable):
                receiver.connection.ping(2)

    def test_paired_but_off_is_offline(self):
        # The distinction that makes greyed-out offline rows possible.
        with FakeReceiver({1: FakeDevice(offline=True)}) as receiver:
            with self.assertRaises(hidpp.DeviceOffline):
                receiver.connection.ping(1)


class FeatureLookupTests(unittest.TestCase):
    def test_resolves_a_supported_feature(self):
        with FakeReceiver({1: FakeDevice()}) as receiver:
            self.assertEqual(
                receiver.connection.feature_index(1, hidpp.FEATURE_DEVICE_NAME),
                NAME_INDEX)

    def test_absent_feature_resolves_to_zero(self):
        device = FakeDevice(battery_feature=hidpp.FEATURE_BATTERY_STATUS)
        with FakeReceiver({1: device}) as receiver:
            self.assertEqual(
                receiver.connection.feature_index(
                    1, hidpp.FEATURE_UNIFIED_BATTERY), 0)


class NameTests(unittest.TestCase):
    def test_reads_a_short_name(self):
        with FakeReceiver({1: FakeDevice(name="MX Keys S")}) as receiver:
            self.assertEqual(receiver.connection.device_name(1, NAME_INDEX),
                             "MX Keys S")

    def test_reads_a_name_longer_than_one_chunk(self):
        # Names come back 16 bytes at a time, so anything longer exercises
        # the loop that stitches the chunks together.
        long_name = "MX Master 4 for Business"
        with FakeReceiver({1: FakeDevice(name=long_name)}) as receiver:
            self.assertEqual(receiver.connection.device_name(1, NAME_INDEX),
                             long_name)

    def test_reads_the_kind(self):
        with FakeReceiver({1: FakeDevice(kind=0x00)}) as receiver:
            self.assertEqual(receiver.connection.device_kind(1, NAME_INDEX),
                             "keyboard")
        with FakeReceiver({1: FakeDevice(kind=0x03)}) as receiver:
            self.assertEqual(receiver.connection.device_kind(1, NAME_INDEX),
                             "mouse")


class BatteryTests(unittest.TestCase):
    def test_unified_battery_state_of_charge(self):
        with FakeReceiver({1: FakeDevice(percent=87)}) as receiver:
            battery = receiver.connection.unified_battery(
                1, UNIFIED_BATTERY_INDEX)
            self.assertEqual(battery.percent, 87)
            self.assertEqual(battery.source, "unified")

    def test_charging_status_is_decoded(self):
        with FakeReceiver({1: FakeDevice(percent=40,
                                         charge_status=0x01)}) as receiver:
            battery = receiver.connection.unified_battery(
                1, UNIFIED_BATTERY_INDEX)
            self.assertEqual(battery.status, CHARGE_CHARGING)
            self.assertTrue(battery.charging)

    def test_full_status(self):
        with FakeReceiver({1: FakeDevice(percent=100,
                                         charge_status=0x03)}) as receiver:
            self.assertEqual(
                receiver.connection.unified_battery(
                    1, UNIFIED_BATTERY_INDEX).status, CHARGE_FULL)

    def test_non_rechargeable_is_reported(self):
        with FakeReceiver({1: FakeDevice(rechargeable=False)}) as receiver:
            battery = receiver.connection.unified_battery(
                1, UNIFIED_BATTERY_INDEX)
            self.assertFalse(battery.rechargeable)

    def test_read_battery_picks_the_advertised_feature(self):
        with FakeReceiver({1: FakeDevice(percent=63)}) as receiver:
            features = {hidpp.FEATURE_UNIFIED_BATTERY: UNIFIED_BATTERY_INDEX}
            battery = receiver.connection.read_battery(1, features)
            self.assertEqual(battery.percent, 63)

    def test_read_battery_without_any_feature(self):
        with FakeReceiver({1: FakeDevice()}) as receiver:
            self.assertIsNone(receiver.connection.read_battery(1, {}))


class ScanTests(unittest.TestCase):
    """The whole enumeration, as the monitor performs it."""

    def test_finds_every_paired_device(self):
        devices = {
            1: FakeDevice(name="MX Keys S", kind=0x00, percent=90),
            3: FakeDevice(name="MX Master 4", kind=0x03, percent=50),
        }
        with FakeReceiver(devices) as receiver:
            found = hidpp.scan_connection(receiver.connection)
            self.assertEqual(
                {(d.index, d.name, d.kind, d.battery.percent) for d in found},
                {(1, "MX Keys S", "keyboard", 90),
                 (3, "MX Master 4", "mouse", 50)})

    def test_offline_device_is_kept_with_no_reading(self):
        devices = {1: FakeDevice(name="Awake", percent=70),
                   2: FakeDevice(offline=True)}
        with FakeReceiver(devices) as receiver:
            found = {d.index: d for d in hidpp.scan_connection(
                receiver.connection)}
            self.assertEqual(set(found), {1, 2})
            self.assertTrue(found[1].online)
            self.assertFalse(found[2].online)
            # Nothing is known about it beyond that it exists; the cache
            # fills in the name and last level.
            self.assertIsNone(found[2].battery)

    def test_empty_receiver_finds_nothing(self):
        with FakeReceiver({}) as receiver:
            self.assertEqual(hidpp.scan_connection(receiver.connection), [])

    def test_a_device_without_a_name_still_appears(self):
        with FakeReceiver({1: FakeDevice(has_name=False,
                                         percent=44)}) as receiver:
            found = hidpp.scan_connection(receiver.connection)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].name, "")
            self.assertEqual(found[0].display_name, "Device 1")
            self.assertEqual(found[0].battery.percent, 44)

    def test_features_are_recorded_for_the_notification_listener(self):
        # The monitor uses these to decide whether an unsolicited frame
        # means the battery display is stale.
        with FakeReceiver({1: FakeDevice()}) as receiver:
            device = hidpp.scan_connection(receiver.connection)[0]
            self.assertEqual(device.features[hidpp.FEATURE_UNIFIED_BATTERY],
                             UNIFIED_BATTERY_INDEX)
            self.assertEqual(device.features[hidpp.FEATURE_DEVICE_NAME],
                             NAME_INDEX)

    def test_legacy_device_advertises_only_the_old_feature(self):
        device = FakeDevice(battery_feature=hidpp.FEATURE_BATTERY_STATUS)
        with FakeReceiver({1: device}) as receiver:
            found = hidpp.scan_connection(receiver.connection)[0]
            self.assertIn(hidpp.FEATURE_BATTERY_STATUS, found.features)
            self.assertEqual(found.features[hidpp.FEATURE_BATTERY_STATUS],
                             LEGACY_BATTERY_INDEX)
            self.assertNotIn(hidpp.FEATURE_UNIFIED_BATTERY, found.features)

    def test_scan_does_not_wedge_on_a_full_receiver(self):
        # Six slots, all populated: the loop must terminate and report all.
        devices = {i: FakeDevice(name=f"Device {i}", percent=i * 10)
                   for i in range(1, 7)}
        with FakeReceiver(devices) as receiver:
            self.assertEqual(len(hidpp.scan_connection(receiver.connection)), 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
