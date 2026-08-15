#!/usr/bin/env python3
"""Source tests against mocked UPower and BlueZ services.

The generic sources read hardware this machine may not have, so they were
shipped on the strength of "it does not crash" — which turned out to mean
`UPowerSource` reported no devices at all for a whole release, because a
call named a symbol that does not exist and a broad `except` swallowed the
AttributeError. These tests speak the real D-Bus interfaces to a mock, so
the sources are exercised rather than merely imported.

Needs python-dbusmock and dbus-python; skipped when either is missing.

    sudo apt install python3-dbusmock python3-dbus

Run with `make test`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import dbus
    import dbusmock
except ImportError:  # pragma: no cover - depends on the machine
    dbus = None
    dbusmock = None

from voltaic.sources import BluezSource, UPowerSource  # noqa: E402

needs_dbusmock = unittest.skipIf(
    dbusmock is None, "python-dbusmock is not installed")

# UPower device types. 1 is line power, 2 the host's own battery: both are
# things the desktop already shows, and both must be filtered out.
LINE_POWER, HOST_BATTERY, MOUSE, KEYBOARD, GAMEPAD, HEADSET = 1, 2, 5, 6, 12, 17
CHARGING, DISCHARGING = 1, 2


@needs_dbusmock
class UPowerSourceTests(dbusmock.DBusTestCase if dbusmock else object):
    @classmethod
    def setUpClass(cls):
        cls.start_system_bus()
        cls.dbus_con = cls.get_dbus(system_bus=True)

    def setUp(self):
        self.mock, self.upower = self.spawn_server_template(
            "upower", {}, stdout=subprocess.DEVNULL)

    def tearDown(self):
        self.mock.terminate()
        self.mock.wait()

    def add(self, path, type_, model, percent, state=DISCHARGING,
            present=True):
        self.upower.AddObject(
            f"/org/freedesktop/UPower/devices/{path}",
            "org.freedesktop.UPower.Device",
            dbus.Dictionary({
                "Type": dbus.UInt32(type_),
                "Model": dbus.String(model),
                "Percentage": dbus.Double(percent),
                "State": dbus.UInt32(state),
                "IsPresent": dbus.Boolean(present),
            }, signature="sv"),
            dbus.Array([], signature="(ssss)"))

    def test_reads_a_mouse(self):
        self.add("mouse", MOUSE, "Test Mouse", 42.0)
        devices = UPowerSource().scan()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].name, "Test Mouse")
        self.assertEqual(devices[0].kind, "mouse")
        self.assertEqual(devices[0].battery.percent, 42)
        self.assertFalse(devices[0].battery.charging)

    def test_charging_state(self):
        self.add("pad", GAMEPAD, "Test Gamepad", 77.0, state=CHARGING)
        battery = UPowerSource().scan()[0].battery
        self.assertEqual(battery.percent, 77)
        self.assertTrue(battery.charging)

    def test_line_power_is_ignored(self):
        self.add("ac", LINE_POWER, "AC Adapter", 0.0)
        self.assertEqual(UPowerSource().scan(), [])

    def test_host_battery_is_ignored(self):
        # The desktop already shows this one.
        self.add("bat", HOST_BATTERY, "Laptop battery", 55.0)
        self.assertEqual(UPowerSource().scan(), [])

    def test_absent_device_is_ignored(self):
        self.add("gone", MOUSE, "Unplugged", 10.0, present=False)
        self.assertEqual(UPowerSource().scan(), [])

    def test_several_kinds_at_once(self):
        self.add("m", MOUSE, "Mouse", 10.0)
        self.add("k", KEYBOARD, "Keyboard", 20.0)
        self.add("h", HEADSET, "Headset", 30.0)
        self.add("ac", LINE_POWER, "AC", 0.0)
        found = {d.kind: d.battery.percent for d in UPowerSource().scan()}
        self.assertEqual(found, {"mouse": 10, "keyboard": 20, "headset": 30})

    def test_key_is_stable_and_namespaced(self):
        self.add("mouse", MOUSE, "Test Mouse", 42.0)
        self.assertEqual(
            UPowerSource().scan()[0].key,
            "upower:/org/freedesktop/UPower/devices/mouse")

    def test_no_devices_is_not_an_error(self):
        self.assertEqual(UPowerSource().scan(), [])


@needs_dbusmock
class BluezSourceTests(dbusmock.DBusTestCase if dbusmock else object):
    @classmethod
    def setUpClass(cls):
        cls.start_system_bus()
        cls.dbus_con = cls.get_dbus(system_bus=True)

    def setUp(self):
        self.mock = self.spawn_server(
            "org.bluez", "/", "org.freedesktop.DBus.ObjectManager",
            system_bus=True, stdout=subprocess.DEVNULL)
        self.bluez = dbus.Interface(
            self.get_dbus(system_bus=True).get_object("org.bluez", "/"),
            "org.freedesktop.DBus.Mock")

    def tearDown(self):
        self.mock.terminate()
        self.mock.wait()

    def set_objects(self, body: str):
        self.bluez.AddMethod(
            "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", "",
            "a{oa{sa{sv}}}", f"ret = {body}")

    def test_reads_a_connected_device_with_a_battery(self):
        self.set_objects(
            '{"/org/bluez/hci0/dev_AA": {'
            '  "org.bluez.Device1": {"Alias": "Test Headset",'
            '                        "Address": "AA:BB:CC:DD:EE:FF",'
            '                        "Connected": True},'
            '  "org.bluez.Battery1": {"Percentage": dbus.Byte(64)}}}')
        devices = BluezSource().scan()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].name, "Test Headset")
        self.assertEqual(devices[0].battery.percent, 64)
        self.assertEqual(devices[0].key, "bluez:AA:BB:CC:DD:EE:FF")

    def test_device_without_a_battery_is_skipped(self):
        self.set_objects(
            '{"/org/bluez/hci0/dev_BB": {'
            '  "org.bluez.Device1": {"Alias": "No Battery",'
            '                        "Address": "BB:BB:BB:BB:BB:BB",'
            '                        "Connected": True}}}')
        self.assertEqual(BluezSource().scan(), [])

    def test_disconnected_device_is_skipped(self):
        # Its battery figure is whatever it was when it left.
        self.set_objects(
            '{"/org/bluez/hci0/dev_CC": {'
            '  "org.bluez.Device1": {"Alias": "Away",'
            '                        "Address": "CC:CC:CC:CC:CC:CC",'
            '                        "Connected": False},'
            '  "org.bluez.Battery1": {"Percentage": dbus.Byte(30)}}}')
        self.assertEqual(BluezSource().scan(), [])

    def test_nothing_paired_is_not_an_error(self):
        self.set_objects("{}")
        self.assertEqual(BluezSource().scan(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
