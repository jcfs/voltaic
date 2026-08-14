"""Minimal HID++ 2.0 client that talks straight to /dev/hidraw.

Implements just enough of Logitech's HID++ protocol to enumerate the devices
paired to a Unifying/Bolt receiver (or attached directly over USB/Bluetooth)
and read their battery state.

Standard library only: no hidapi, no Solaar, no root. Access to the hidraw
node is granted by the udev rules in packaging/60-voltaic.rules.
"""

from __future__ import annotations

import errno
import os
import select
import time

from .model import (CHARGE_CHARGING, CHARGE_DISCHARGING, CHARGE_ERROR,
                    CHARGE_FULL, CHARGE_SLOW, Battery, Device)

__all__ = ["Battery", "Device", "enumerate_devices", "scan_connection",
           "find_hidpp_paths", "HidppDevice", "HidppError"]

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

REPORT_SHORT = 0x10
REPORT_LONG = 0x11
REPORT_VERY_LONG = 0x12

REPORT_LENGTHS = {REPORT_SHORT: 7, REPORT_LONG: 20, REPORT_VERY_LONG: 64}

# Every HID++ request carries a 4-bit software id so replies can be told apart
# from other clients' traffic (Solaar, the kernel). Anything 1..15 is legal;
# picking an uncommon one keeps us from being confused by Solaar's replies.
SOFTWARE_ID = 0x0D

RECEIVER_INDEX = 0xFF  # also the index a directly-connected device answers on

# A sleeping device needs a few hundred ms to wake before it answers a ping;
# measured worst case on a Bolt receiver was ~400ms, so leave real headroom.
PING_TIMEOUT = 1.5

# Feature ids we care about.
FEATURE_ROOT = 0x0000
FEATURE_FEATURE_SET = 0x0001
FEATURE_DEVICE_NAME = 0x0005
FEATURE_BATTERY_STATUS = 0x1000  # legacy, level buckets
FEATURE_BATTERY_VOLTAGE = 0x1001  # millivolts
FEATURE_UNIFIED_BATTERY = 0x1004  # modern, true state-of-charge

# Root feature is always at index 0.
ROOT_INDEX = 0x00
ROOT_FN_GET_FEATURE = 0x00
ROOT_FN_GET_PROTOCOL = 0x01

ERROR_MARKER_HIDPP10 = 0x8F
ERROR_MARKER_HIDPP20 = 0xFF

HIDPP10_ERRORS = {
    0x01: "invalid subid",
    0x02: "invalid address",
    0x03: "invalid value",
    0x04: "connection failed",
    0x05: "too many devices",
    0x06: "already exists",
    0x07: "busy",
    0x08: "unknown device",
    0x09: "resource error",
    0x0A: "request unavailable",
    0x0B: "unsupported parameter",
}

HIDPP20_ERRORS = {
    0x01: "unknown",
    0x02: "invalid argument",
    0x03: "out of range",
    0x04: "hardware error",
    0x05: "logitech internal",
    0x06: "invalid feature index",
    0x07: "invalid function id",
    0x08: "busy",
    0x09: "unsupported",
}

# The receiver distinguishes an empty pairing slot from a device that is
# paired but whose radio link is down (powered off, or switched to another
# host). That difference is what lets us keep showing a known device as
# "offline" instead of having it silently disappear from the panel.
_ABSENT_CODES = {0x08, 0x09}   # nothing paired at this index
_OFFLINE_CODES = {0x04}        # paired, but not connected right now

# Device kinds reported by feature 0x0005 function 0x02.
DEVICE_KINDS = {
    0x00: "keyboard",
    0x01: "remote control",
    0x02: "numpad",
    0x03: "mouse",
    0x04: "touchpad",
    0x05: "trackball",
    0x06: "presenter",
    0x07: "receiver",
    0x08: "headset",
    0x09: "webcam",
    0x0A: "steering wheel",
    0x0B: "joystick",
    0x0C: "gamepad",
}

_UNIFIED_CHARGE_STATUS = {
    0x00: CHARGE_DISCHARGING,
    0x01: CHARGE_CHARGING,
    0x02: CHARGE_SLOW,
    0x03: CHARGE_FULL,
    0x04: CHARGE_ERROR,
}

_LEGACY_CHARGE_STATUS = {
    0x00: CHARGE_DISCHARGING,
    0x01: CHARGE_CHARGING,
    0x02: CHARGE_CHARGING,  # "almost full"
    0x03: CHARGE_FULL,
    0x04: CHARGE_SLOW,
    0x05: CHARGE_ERROR,
    0x06: CHARGE_ERROR,
}

# When a device reports coarse buckets instead of a true percentage, these are
# the percentages we display. Flagged as `approximate` so the UI can say so.
_LEVEL_BUCKETS = {"full": 95, "good": 70, "low": 20, "critical": 5}


class HidppError(Exception):
    """Base class for protocol failures."""


class DeviceUnreachable(HidppError):
    """Nothing is paired at this device index."""


class DeviceOffline(HidppError):
    """A device is paired here, but its wireless link is currently down."""


class FeatureNotSupported(HidppError):
    """The device does not implement the requested HID++ feature."""


class ProtocolError(HidppError):
    """The device answered with an explicit HID++ error code."""


class Timeout(HidppError):
    """No matching reply arrived before the deadline."""


# ---------------------------------------------------------------------------
# HID report descriptor parsing
# ---------------------------------------------------------------------------


def parse_report_descriptor(data: bytes) -> set[tuple[int, int]]:
    """Walk a HID report descriptor, returning {(usage_page, report_id)}.

    We only need to know whether an interface carries the Logitech vendor
    page (0xFF00) together with the HID++ report ids, so this handles just
    the two global items involved and skips everything else.
    """
    pairs: set[tuple[int, int]] = set()
    usage_page = 0
    i = 0
    n = len(data)
    while i < n:
        prefix = data[i]
        if prefix == 0xFE:  # long item: [0xFE, size, tag, data...]
            if i + 2 >= n:
                break
            i += 3 + data[i + 1]
            continue
        size_code = prefix & 0x03
        size = 4 if size_code == 3 else size_code
        tag_type = prefix & 0xFC
        payload = data[i + 1 : i + 1 + size]
        if len(payload) < size:
            break
        value = int.from_bytes(payload, "little") if size else 0
        if tag_type == 0x04:  # Global / Usage Page
            usage_page = value
        elif tag_type == 0x84:  # Global / Report ID
            pairs.add((usage_page, value))
        i += 1 + size
    return pairs


def find_hidpp_paths() -> list[str]:
    """Return /dev/hidraw* nodes whose descriptor advertises HID++.

    A HID++ interface exposes the Logitech vendor usage page (0xFF00) with
    the short (0x10) and/or long (0x11) report ids. Filtering on the
    descriptor means we never write probe packets at unrelated hardware.
    """
    paths = []
    try:
        nodes = sorted(os.listdir("/sys/class/hidraw"))
    except OSError:
        return paths
    for node in nodes:
        desc_path = f"/sys/class/hidraw/{node}/device/report_descriptor"
        try:
            with open(desc_path, "rb") as fh:
                pairs = parse_report_descriptor(fh.read())
        except OSError:
            continue
        vendor_reports = {rid for page, rid in pairs if page == 0xFF00}
        if vendor_reports & {REPORT_SHORT, REPORT_LONG}:
            paths.append(f"/dev/{node}")
    return paths


# ---------------------------------------------------------------------------
# Battery / device models
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Low-level transport
# ---------------------------------------------------------------------------


class HidppDevice:
    """An open hidraw node that speaks HID++.

    One node fronts every device paired to a receiver; `device_index`
    selects which one a request is addressed to.
    """

    def __init__(self, path: str, timeout: float = 1.0):
        self.path = path
        self.timeout = timeout
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None

    def __enter__(self) -> "HidppDevice":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- framing ----------------------------------------------------------

    @staticmethod
    def _frame(report_id: int, device_index: int, feature_index: int,
               func_swid: int, params: bytes) -> bytes:
        buf = bytearray(REPORT_LENGTHS[report_id])
        buf[0] = report_id
        buf[1] = device_index
        buf[2] = feature_index
        buf[3] = func_swid
        buf[4 : 4 + len(params)] = params
        return bytes(buf)

    def _drain(self) -> None:
        """Discard queued reports so stale replies can't be mistaken for ours."""
        while True:
            try:
                if not select.select([self.fd], [], [], 0)[0]:
                    return
                os.read(self.fd, 64)
            except (OSError, ValueError):
                return

    def _await_reply(self, device_index: int, feature_index: int,
                     func_swid: int, deadline: float) -> bytes:
        """Read until the reply matching our request shows up.

        The node is shared: notifications from the device and replies
        destined for other clients arrive on the same fd, so everything that
        doesn't match the request triple is dropped.
        """
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Timeout(f"no reply for feature 0x{feature_index:02x}")
            try:
                ready = select.select([self.fd], [], [], remaining)[0]
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                raise
            if not ready:
                raise Timeout(f"no reply for feature 0x{feature_index:02x}")
            try:
                data = os.read(self.fd, 64)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    continue
                raise HidppError(f"read failed: {exc}") from exc

            if len(data) < 4 or data[0] not in REPORT_LENGTHS:
                continue
            if data[1] != device_index:
                continue

            # Error frames name the request they refer to, so match on that.
            if data[2] == ERROR_MARKER_HIDPP10 and len(data) >= 6:
                if data[4] == func_swid or data[3] == feature_index:
                    code = data[5]
                    if code in _ABSENT_CODES:
                        raise DeviceUnreachable(HIDPP10_ERRORS.get(code, "absent"))
                    if code in _OFFLINE_CODES:
                        raise DeviceOffline(HIDPP10_ERRORS.get(code, "offline"))
                    raise ProtocolError(HIDPP10_ERRORS.get(code, f"0x{code:02x}"))
                continue
            if data[2] == ERROR_MARKER_HIDPP20 and len(data) >= 6:
                if data[3] == feature_index and data[4] == func_swid:
                    code = data[5]
                    if code == 0x09:  # unsupported
                        raise FeatureNotSupported(f"feature 0x{feature_index:02x}")
                    raise ProtocolError(HIDPP20_ERRORS.get(code, f"0x{code:02x}"))
                continue

            # Device-initiated notifications carry software id 0; skip them.
            if (data[3] & 0x0F) == 0:
                continue
            if data[2] == feature_index and data[3] == func_swid:
                return data

    def request(self, device_index: int, feature_index: int, function: int,
                params: bytes = b"", timeout: float | None = None) -> bytes:
        """Send one HID++ request and return the matching reply frame."""
        func_swid = ((function & 0x0F) << 4) | SOFTWARE_ID
        report_id = REPORT_SHORT if len(params) <= 3 else REPORT_LONG
        frame = self._frame(report_id, device_index, feature_index, func_swid, params)
        self._drain()
        try:
            os.write(self.fd, frame)
        except OSError as exc:
            raise HidppError(f"write to {self.path} failed: {exc}") from exc
        deadline = time.monotonic() + (timeout or self.timeout)
        return self._await_reply(device_index, feature_index, func_swid, deadline)

    # -- root feature -----------------------------------------------------

    def ping(self, device_index: int, timeout: float = PING_TIMEOUT) -> tuple[int, int]:
        """Probe a device index; returns its (major, minor) protocol version.

        Raises DeviceUnreachable when nothing is paired/awake at that index.

        An idle device takes a few hundred milliseconds to wake and answer,
        so the timeout is generous. Empty slots are cheap regardless: the
        receiver rejects them immediately with an error rather than going
        quiet, so only paired-but-absent devices ever burn the full wait.
        """
        magic = 0x5A
        reply = self.request(device_index, ROOT_INDEX, ROOT_FN_GET_PROTOCOL,
                             bytes([0x00, 0x00, magic]), timeout=timeout)
        if len(reply) >= 7 and reply[6] != magic:
            raise ProtocolError("ping echo mismatch")
        return (reply[4], reply[5])

    def feature_index(self, device_index: int, feature_id: int) -> int:
        """Resolve a feature id to this device's feature index (0 = absent)."""
        reply = self.request(
            device_index, ROOT_INDEX, ROOT_FN_GET_FEATURE,
            bytes([(feature_id >> 8) & 0xFF, feature_id & 0xFF, 0x00]),
        )
        return reply[4]

    # -- device identity --------------------------------------------------

    def device_name(self, device_index: int, name_index: int) -> str:
        reply = self.request(device_index, name_index, 0x00)
        length = reply[4]
        chunks = []
        offset = 0
        while offset < length and offset < 64:
            part = self.request(device_index, name_index, 0x01, bytes([offset]))
            chunks.append(part[4:])
            offset += len(part) - 4
        raw = b"".join(chunks)[:length]
        return raw.split(b"\x00")[0].decode("utf-8", "replace").strip()

    def device_kind(self, device_index: int, name_index: int) -> str:
        reply = self.request(device_index, name_index, 0x02)
        return DEVICE_KINDS.get(reply[4], "")

    # -- battery ----------------------------------------------------------

    def unified_battery(self, device_index: int, index: int) -> Battery:
        """Feature 0x1004 — true state of charge on modern devices."""
        caps = self.request(device_index, index, 0x00)
        supported_levels, flags = caps[4], caps[5]
        has_soc = bool(flags & 0x02)
        rechargeable = bool(flags & 0x01)

        status = self.request(device_index, index, 0x01)
        state_of_charge, level_bits, charge_status = status[4], status[5], status[6]

        battery = Battery(status=_UNIFIED_CHARGE_STATUS.get(charge_status,
                                                            CHARGE_DISCHARGING),
                          rechargeable=rechargeable,
                          source="unified")
        if has_soc and state_of_charge:
            battery.percent = min(100, state_of_charge)
        else:
            # Fall back to the coarse level bitfield.
            for bit, key in ((0x08, "full"), (0x04, "good"),
                             (0x02, "low"), (0x01, "critical")):
                if level_bits & bit:
                    battery.percent = _LEVEL_BUCKETS[key]
                    battery.approximate = True
                    break
            _ = supported_levels
        return battery

    def legacy_battery(self, device_index: int, index: int) -> Battery:
        """Feature 0x1000 — older devices, percentage plus a status byte."""
        reply = self.request(device_index, index, 0x00)
        level, _next_level, status = reply[4], reply[5], reply[6]
        battery = Battery(status=_LEGACY_CHARGE_STATUS.get(status, CHARGE_DISCHARGING),
                          source="legacy")
        if level:
            battery.percent = min(100, level)
        return battery

    def voltage_battery(self, device_index: int, index: int) -> Battery:
        """Feature 0x1001 — millivolts only; percentage is approximated."""
        reply = self.request(device_index, index, 0x00)
        mv = (reply[4] << 8) | reply[5]
        charging = bool(reply[6] & 0x80)
        battery = Battery(
            voltage_mv=mv,
            status=CHARGE_CHARGING if charging else CHARGE_DISCHARGING,
            percent=_voltage_to_percent(mv),
            approximate=True,
            source="voltage",
        )
        return battery

    def read_battery(self, device_index: int, features: dict[int, int]) -> Battery | None:
        """Try each battery feature the device advertises, best first."""
        attempts = (
            (FEATURE_UNIFIED_BATTERY, self.unified_battery),
            (FEATURE_BATTERY_STATUS, self.legacy_battery),
            (FEATURE_BATTERY_VOLTAGE, self.voltage_battery),
        )
        for feature_id, reader in attempts:
            index = features.get(feature_id)
            if not index:
                continue
            try:
                return reader(device_index, index)
            except (HidppError, IndexError):
                continue
        return None


# A coarse Li-ion discharge curve, used only when a device reports raw
# millivolts and nothing better. Percentages here are indicative.
_VOLTAGE_CURVE = [
    (4186, 100), (4067, 90), (3989, 80), (3922, 70), (3859, 60),
    (3811, 50), (3778, 40), (3751, 30), (3717, 20), (3671, 10),
    (3593, 5), (3400, 0),
]


def _voltage_to_percent(mv: int) -> int | None:
    if not mv:
        return None
    if mv >= _VOLTAGE_CURVE[0][0]:
        return 100
    for (hi_mv, hi_pct), (lo_mv, lo_pct) in zip(_VOLTAGE_CURVE, _VOLTAGE_CURVE[1:]):
        if lo_mv <= mv <= hi_mv:
            span = hi_mv - lo_mv
            ratio = (mv - lo_mv) / span if span else 0
            return int(lo_pct + ratio * (hi_pct - lo_pct))
    return 0


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

# Receivers address paired devices as 1..6; a directly attached device (USB
# cable or Bluetooth) answers on 0xFF instead.
PAIRED_INDEXES = range(1, 7)


def _probe_index(conn: HidppDevice, index: int, path: str) -> Device | None:
    try:
        protocol = conn.ping(index)
    except DeviceOffline:
        # Paired but not connected. Report it so the UI can show the device
        # greyed out with its last known charge rather than dropping it.
        return Device(index=index, path=path, online=False)
    except (DeviceUnreachable, Timeout, ProtocolError):
        return None
    except HidppError:
        return None

    device = Device(index=index, protocol=protocol, path=path)

    wanted = (FEATURE_DEVICE_NAME, FEATURE_UNIFIED_BATTERY,
              FEATURE_BATTERY_STATUS, FEATURE_BATTERY_VOLTAGE)
    for feature_id in wanted:
        try:
            found = conn.feature_index(index, feature_id)
        except HidppError:
            continue
        if found:
            device.features[feature_id] = found

    name_index = device.features.get(FEATURE_DEVICE_NAME)
    if name_index:
        try:
            device.name = conn.device_name(index, name_index)
        except HidppError:
            pass
        try:
            device.kind = conn.device_kind(index, name_index)
        except HidppError:
            pass

    device.battery = conn.read_battery(index, device.features)
    return device


def scan_connection(conn: HidppDevice) -> list[Device]:
    """Probe every device index on an already-open connection."""
    # A directly-connected device answers on the receiver index; if one does,
    # this node is that device rather than a receiver, so stop there.
    direct = _probe_index(conn, RECEIVER_INDEX, conn.path)
    if direct is not None and direct.protocol[0] >= 2:
        return [direct]
    found = []
    for index in PAIRED_INDEXES:
        device = _probe_index(conn, index, conn.path)
        if device is not None:
            found.append(device)
    return found


def enumerate_devices(paths: list[str] | None = None) -> list[Device]:
    """Discover every reachable HID++ device and read its battery.

    Unreadable nodes are skipped rather than raising, so a partially
    permissioned system still reports whatever it can see.
    """
    devices: list[Device] = []
    for path in paths if paths is not None else find_hidpp_paths():
        try:
            conn = HidppDevice(path)
        except OSError:
            continue
        with conn:
            devices.extend(scan_connection(conn))
    return devices
