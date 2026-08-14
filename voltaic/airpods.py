"""AirPods battery over Apple's Accessory Protocol (AAP).

BlueZ only ever exposes a single `org.bluez.Battery1` percentage for a
headset, and only with experimental features enabled — which is no good when
the thing you want is three separate numbers. Apple's own protocol reports
each earbud and the case individually, over an L2CAP channel on PSM 0x1001
that the AirPods advertise as vendor service
``74ec2172-0bad-4d01-8f77-997b2be0722a``.

We only ever read from devices that are *already* connected. Bringing up an
audio device on our own would hijack the user's sound output.
"""

from __future__ import annotations

import select
import socket
import time

from .model import (
    CHARGE_CHARGING,
    CHARGE_DISCHARGING,
    CHARGE_DISCONNECTED,
    CHARGE_FULL,
    Battery,
    Cell,
    Device,
)

# Apple's AAP vendor service, advertised by AirPods and Beats.
AAP_UUID = "74ec2172-0bad-4d01-8f77-997b2be0722a"
AAP_PSM = 0x1001

# Every AAP frame starts with this, then a one-byte opcode.
FRAME_PREFIX = bytes([0x04, 0x00, 0x04, 0x00])
OPCODE_BATTERY = 0x04

# Opening sequence. The handshake alone is not enough — the accessory only
# starts pushing state once notifications are requested. Both notification
# masks are sent because firmware revisions disagree about the last byte,
# and sending the wrong one alone yields a channel that never reports.
HANDSHAKE = bytes([0x00, 0x00, 0x04, 0x00, 0x01, 0x00, 0x02, 0x00,
                   0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
SET_FEATURES = bytes([0x04, 0x00, 0x04, 0x00, 0x4D, 0x00,
                      0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
REQUEST_NOTIFICATIONS = (
    bytes([0x04, 0x00, 0x04, 0x00, 0x0F, 0x00, 0xFF, 0xFF, 0xFE, 0xFF]),
    bytes([0x04, 0x00, 0x04, 0x00, 0x0F, 0x00, 0xFF, 0xFF, 0xFF, 0xFF]),
)

# Battery entries are five bytes: component, ?, level, status, ?
ENTRY_SIZE = 5

COMPONENT_LABELS = {
    0x01: "Buds",   # devices that report one figure for both earbuds
    0x02: "Right",
    0x04: "Left",
    0x08: "Case",
}

# Display order, so the panel always reads left-to-right the same way.
COMPONENT_ORDER = {"Left": 0, "Right": 1, "Buds": 0, "Case": 2}

STATUS = {
    0x01: CHARGE_CHARGING,
    0x02: CHARGE_DISCHARGING,
    0x03: CHARGE_FULL,
    0x04: CHARGE_DISCONNECTED,
}

# A fresh channel reports battery within a few hundred milliseconds; a
# second is already generous.
READ_TIMEOUT = 2.5
CONNECT_TIMEOUT = 3.0


class AirPodsError(Exception):
    """Could not read battery from the accessory."""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


# Why the last discovery came up empty-handed, when the cause was the stack
# rather than the absence of accessories. "No AirPods paired" and "there is no
# Bluetooth stack to ask" both yield an empty list, and a user debugging a
# missing device needs to know which one they are looking at.
_unavailable: str | None = None


def unavailable_reason() -> str | None:
    """Why Bluetooth scanning could not run, or None if it ran fine.

    Meaningful only after a discovery attempt; an empty device list with a
    reason of None means Bluetooth worked and simply found nothing.
    """
    return _unavailable


def _managed_objects():
    """(bus, objects) from BlueZ, or (None, {}) if Bluetooth is unavailable."""
    global _unavailable
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError):
        _unavailable = ("PyGObject is not installed, so Bluetooth "
                        "accessories were not scanned.")
        return None, {}

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        reply = bus.call_sync(
            "org.bluez", "/", "org.freedesktop.DBus.ObjectManager",
            "GetManagedObjects", None, None,
            Gio.DBusCallFlags.NONE, 2000, None)
    except Exception as exc:
        _unavailable = (f"BlueZ did not answer on D-Bus ({type(exc).__name__}) "
                        "— is the bluetooth service running?")
        return None, {}
    _unavailable = None
    return bus, reply.unpack()[0]


def discover() -> list[tuple[str, str, bool]]:
    """Return [(address, name, connected)] for Apple accessories using AAP.

    Uses BlueZ's object manager over D-Bus. Returns an empty list — rather
    than raising — when Bluetooth is off or unavailable, since that is a
    perfectly normal state and not an error worth surfacing.
    """
    _bus, objects = _managed_objects()
    found = []
    for _path, interfaces in objects.items():
        device = interfaces.get("org.bluez.Device1")
        if not device:
            continue
        uuids = [u.lower() for u in device.get("UUIDs", [])]
        if AAP_UUID not in uuids:
            continue
        address = device.get("Address")
        if not address:
            continue
        name = device.get("Alias") or device.get("Name") or "AirPods"
        found.append((address, name, bool(device.get("Connected", False))))
    return found


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


def parse_battery(frame: bytes) -> list[Cell]:
    """Decode an AAP battery frame into cells.

    Layout after the 6-byte header: a count, then that many 5-byte entries
    of ``component, ?, level, status, ?``.
    """
    payload = frame[6:]
    if not payload:
        return []
    count = payload[0]
    body = payload[1:]
    cells: list[Cell] = []
    for i in range(count):
        entry = body[i * ENTRY_SIZE:(i + 1) * ENTRY_SIZE]
        if len(entry) < ENTRY_SIZE:
            break
        component, level, status_code = entry[0], entry[2], entry[3]
        label = COMPONENT_LABELS.get(component)
        if label is None:
            continue
        status = STATUS.get(status_code, CHARGE_DISCHARGING)
        # A part that is away (buds out of the case, or a bud not in use)
        # reports level 0; showing that as a real 0% would be a lie.
        percent = None
        if status != CHARGE_DISCONNECTED and level != 0xFF:
            percent = min(100, level)
        cells.append(Cell(label=label,
                          battery=Battery(percent=percent, status=status,
                                          source="aap")))
    cells.sort(key=lambda c: COMPONENT_ORDER.get(c.label, 9))
    return cells


def read_cells(address: str, timeout: float = READ_TIMEOUT) -> list[Cell]:
    """Open the AAP channel, ask for notifications, and read one battery frame."""
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                         socket.BTPROTO_L2CAP)
    sock.settimeout(CONNECT_TIMEOUT)
    try:
        try:
            sock.connect((address, AAP_PSM))
        except OSError as exc:
            raise AirPodsError(f"cannot open AAP channel: {exc}") from exc

        try:
            sock.send(HANDSHAKE)
            sock.send(SET_FEATURES)
            for request in REQUEST_NOTIFICATIONS:
                sock.send(request)
        except OSError as exc:
            raise AirPodsError(f"AAP handshake failed: {exc}") from exc

        sock.setblocking(False)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AirPodsError("no battery frame received")
            if not select.select([sock], [], [], min(remaining, 0.5))[0]:
                continue
            try:
                frame = sock.recv(2048)
            except BlockingIOError:
                continue
            except OSError as exc:
                raise AirPodsError(f"AAP read failed: {exc}") from exc
            if not frame:
                raise AirPodsError("accessory closed the channel")
            # The channel carries plenty of other chatter — device info,
            # settings, firmware blobs — so pick out just the battery frame.
            if len(frame) >= 7 and frame[:4] == FRAME_PREFIX \
                    and frame[4] == OPCODE_BATTERY:
                cells = parse_battery(frame)
                if cells:
                    return cells
    finally:
        try:
            sock.close()
        except OSError:
            pass


def connect(address: str, timeout: float = 20.0) -> None:
    """Ask BlueZ to bring up the link to an accessory.

    Blocks until the link is established or BlueZ gives up, so callers must
    not run this on the UI thread. Raises AirPodsError with a message worth
    showing a human.
    """
    bus, objects = _managed_objects()
    if bus is None:
        raise AirPodsError("Bluetooth is unavailable")

    # Adapter numbering is not guaranteed, so find the object path rather
    # than assuming /org/bluez/hci0/...
    target = None
    for path, interfaces in objects.items():
        device = interfaces.get("org.bluez.Device1")
        if device and device.get("Address", "").upper() == address.upper():
            target = path
            break
    if target is None:
        raise AirPodsError("device is no longer paired")

    from gi.repository import Gio
    try:
        bus.call_sync("org.bluez", target, "org.bluez.Device1", "Connect",
                      None, None, Gio.DBusCallFlags.NONE,
                      int(timeout * 1000), None)
    except Exception as exc:
        message = _friendly_connect_error(exc)
        if message is None:
            return  # already connected: exactly what the caller wanted
        raise AirPodsError(message) from exc


def _friendly_connect_error(exc: Exception) -> str | None:
    """Turn a BlueZ D-Bus error into something worth putting on screen.

    Returns None when the "error" actually means success.
    """
    # BlueZ reports these as lowercase hyphenated tokens inside the D-Bus
    # error message ("br-connection-page-timeout"), not as prose.
    text = str(exc).lower()
    if "alreadyconnected" in text:
        return None
    if "inprogress" in text:
        return "already connecting"
    if "page-timeout" in text or "page timeout" in text or "host is down" in text:
        # Overwhelmingly the cause: the buds are in a shut case, so their
        # radio is off and nothing answers the page.
        return "no response — open the case or put the buds in"
    if "notready" in text or "not ready" in text:
        return "Bluetooth adapter is off"
    if "timeout" in text or "timed out" in text:
        return "timed out"
    if "not available" in text or "unavailable" in text:
        return "device is not available"
    return "connection failed"


def tidy_name(name: str) -> str:
    """Drop the owner prefix Apple puts on accessory names.

    Devices are usually called "Someone's AirPods Pro", which is all prefix
    and no information once it is sitting in a panel of your own devices.
    """
    for separator in ("’s ", "'s "):
        _owner, found, remainder = name.partition(separator)
        if found and remainder.strip():
            return remainder.strip()
    return name


def enumerate_airpods() -> list[Device]:
    """Every paired Apple accessory, with per-cell battery where readable.

    Accessories that are paired but not connected are still returned, marked
    offline, so they stay in the panel with their last known levels instead
    of disappearing every time they go back in the case.
    """
    devices = []
    for address, name, connected in discover():
        device = Device(name=tidy_name(name), kind="earbuds", path=address,
                        transport="airpods", online=connected)
        if connected:
            try:
                device.cells = read_cells(address)
            except AirPodsError:
                # Connected but not answering; let the cache fill it in.
                device.online = False
        devices.append(device)
    return devices
