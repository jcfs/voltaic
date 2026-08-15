"""Device sources — the families of hardware Voltaic knows how to read.

Adding support for new hardware means adding a `Source` here and a default
in `config.DEFAULTS["sources"]`; nothing else needs to change. The monitor
scans whatever is enabled and merges the results.

HID++ is deliberately not one of these. It owns long-lived file descriptors
that the monitor parks in `select()` between scans, so that it can react to
an unsolicited battery notification instead of waiting out the poll
interval. Flattening it into a scan-only interface would throw that away,
which is the one thing that makes Voltaic feel live rather than polled.

The two built-in sources need no configuration and are on by default. The
generic ones are off, because they surface whatever the system already
knows about — including a laptop battery or a phone the desktop is already
showing — and duplicating that is worse than showing nothing.
"""

from __future__ import annotations

from .model import CHARGE_CHARGING, CHARGE_DISCHARGING, CHARGE_FULL, Battery, Device


class Source:
    """One family of devices.

    Subclasses implement `scan`. Returning an empty list is normal — no
    devices of this kind are attached — and must not be confused with
    failure, which should raise.
    """

    name = "unnamed"

    def scan(self) -> list[Device]:  # pragma: no cover - interface
        raise NotImplementedError


class AirPodsSource(Source):
    """Apple accessories over Apple's AAP, per earbud and case."""

    name = "airpods"

    def scan(self) -> list[Device]:
        from . import airpods
        return airpods.enumerate_airpods()


# ---------------------------------------------------------------------------
# Generic system sources
# ---------------------------------------------------------------------------

# UPower device types worth showing. Line power is excluded — a mains
# adapter has no charge — and so is the host's own battery, which every
# desktop already puts in its own panel.
_UPOWER_KINDS = {
    3: "ups",
    5: "mouse",
    6: "keyboard",
    7: "pda",
    8: "phone",
    9: "media player",
    10: "tablet",
    12: "gamepad",
    13: "pen",
    14: "touchpad",
    17: "headset",
    18: "speakers",
    19: "headphones",
    21: "audio",
    22: "remote",
    26: "wearable",
    28: "bluetooth",
}

# UPower battery states.
_UPOWER_STATES = {
    1: CHARGE_CHARGING,
    2: CHARGE_DISCHARGING,
    3: CHARGE_FULL,   # empty
    4: CHARGE_FULL,
}


def _system_bus():
    """The system bus, or None where D-Bus is unavailable."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError):
        return None, None
    try:
        return Gio.bus_get_sync(Gio.BusType.SYSTEM, None), Gio
    except Exception:
        return None, None


class UPowerSource(Source):
    """Anything UPower knows the charge of — gamepads, tablets, phones.

    This is the catch-all: UPower already aggregates the kernel's power
    supplies and BlueZ, so enabling it covers hardware Voltaic has no
    specific support for. It cannot replace the HID++ source, because the
    kernel does not recognise every Logitech receiver — a Bolt receiver
    yields no UPower devices at all, which is the reason this project
    speaks HID++ directly.
    """

    name = "upower"

    def scan(self) -> list[Device]:
        bus, Gio = _system_bus()
        if bus is None:
            return []
        try:
            reply = bus.call_sync(
                "org.freedesktop.UPower", "/org/freedesktop/UPower",
                "org.freedesktop.UPower", "EnumerateDevices", None, None,
                Gio.DBusCallFlags.NONE, 3000, None)
        except Exception:
            return []

        devices = []
        for path in reply.unpack()[0]:
            try:
                props = bus.call_sync(
                    "org.freedesktop.UPower", path,
                    "org.freedesktop.DBus.Properties", "GetAll",
                    Gio.GLib.Variant("(s)", ("org.freedesktop.UPower.Device",)),
                    None, Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
            except Exception:
                continue

            kind = _UPOWER_KINDS.get(props.get("Type"))
            if kind is None:
                continue
            if not props.get("IsPresent", True):
                continue
            percent = props.get("Percentage")
            if percent is None:
                continue

            name = (props.get("Model") or props.get("NativePath")
                    or kind.title())
            devices.append(Device(
                name=str(name).strip(),
                kind=kind,
                path=path,
                transport="upower",
                online=True,
                battery=Battery(
                    percent=int(percent),
                    status=_UPOWER_STATES.get(props.get("State"),
                                              CHARGE_DISCHARGING),
                    source="upower"),
            ))
        return devices


class BluezSource(Source):
    """Bluetooth accessories exposing the standard battery service.

    BlueZ reports a single blended percentage per device, which is why
    AirPods get their own source — but for a headset or a controller that
    one figure is all there is, and it is better than nothing.
    """

    name = "bluez"

    def scan(self) -> list[Device]:
        bus, Gio = _system_bus()
        if bus is None:
            return []
        try:
            reply = bus.call_sync(
                "org.bluez", "/", "org.freedesktop.DBus.ObjectManager",
                "GetManagedObjects", None, None,
                Gio.DBusCallFlags.NONE, 3000, None)
        except Exception:
            return []

        devices = []
        for path, interfaces in reply.unpack()[0].items():
            device = interfaces.get("org.bluez.Device1")
            battery = interfaces.get("org.bluez.Battery1")
            if not device or not battery:
                continue
            if not device.get("Connected", False):
                continue
            percent = battery.get("Percentage")
            if percent is None:
                continue
            devices.append(Device(
                name=str(device.get("Alias") or device.get("Name")
                         or "Bluetooth device"),
                kind="bluetooth",
                path=str(device.get("Address") or path),
                transport="bluez",
                online=True,
                battery=Battery(percent=int(percent),
                                status=CHARGE_DISCHARGING, source="bluez"),
            ))
        return devices


# Everything except HID++, which the monitor drives directly.
REGISTRY: dict[str, type[Source]] = {
    "airpods": AirPodsSource,
    "upower": UPowerSource,
    "bluez": BluezSource,
}


def build(enabled: list[str]) -> list[Source]:
    """Instantiate the enabled sources, ignoring names we do not know.

    An unknown name in a config file is skipped rather than fatal — a
    config written for a newer version should not stop an older one from
    starting.
    """
    return [REGISTRY[name]() for name in enabled if name in REGISTRY]
