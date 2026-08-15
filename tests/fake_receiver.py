"""A fake HID++ receiver, for testing the protocol without hardware.

Speaks enough of HID++ 2.0 to answer everything `_probe_index` asks: the
root feature's ping and feature lookup, the device-name feature, and the
three battery features. Requests arrive on a SOCK_SEQPACKET socket, which
preserves message boundaries the way a hidraw node does — a byte-stream
fake would let two frames arrive in one read, which the real device never
does.

Devices are described declaratively:

    receiver = FakeReceiver({
        1: FakeDevice(name="MX Keys S", kind=0x00, percent=90),
        3: FakeDevice(name="MX Master 4", kind=0x03, percent=50),
        5: FakeDevice(offline=True),
    })

Anything not listed answers "nothing paired here", exactly as a receiver
does for an empty slot.
"""

from __future__ import annotations

import os
import socket
import threading
from dataclasses import dataclass, field

from voltaic import hidpp

# Feature indexes this fake hands out. Real devices number them arbitrarily,
# which is the point of the lookup — so deliberately not 1, 2, 3.
NAME_INDEX = 0x04
UNIFIED_BATTERY_INDEX = 0x06
LEGACY_BATTERY_INDEX = 0x07
VOLTAGE_INDEX = 0x08

# HID++ 1.0 error codes, from the device's side.
ERR_UNREACHABLE = 0x08   # nothing paired at this index
ERR_OFFLINE = 0x04       # paired, but not connected


@dataclass
class FakeDevice:
    name: str = "Fake Device"
    kind: int = 0x03  # mouse
    percent: int = 50
    charge_status: int = 0x00  # discharging
    protocol: tuple[int, int] = (4, 5)
    offline: bool = False
    # Which battery feature this device advertises. Modern devices use
    # 0x1004; older ones only have 0x1000 or 0x1001.
    battery_feature: int = hidpp.FEATURE_UNIFIED_BATTERY
    has_name: bool = True
    supports_soc: bool = True
    rechargeable: bool = True
    features: dict = field(default_factory=dict)


class FakeReceiver:
    """Answers HID++ requests on a socket, on its own thread."""

    def __init__(self, devices: dict[int, FakeDevice]):
        self.devices = devices
        self.requests: list[bytes] = []
        self._host, self._dev = socket.socketpair(socket.AF_UNIX,
                                                  socket.SOCK_SEQPACKET)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

        # A HidppDevice around our end, without opening a path.
        self.connection = hidpp.HidppDevice.__new__(hidpp.HidppDevice)
        self.connection.path = "/dev/hidraw-fake"
        self.connection.timeout = 0.5
        self.connection.fd = os.dup(self._host.fileno())

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        self._stop.set()
        try:
            self.connection.close()
        except OSError:
            pass
        self._host.close()
        self._dev.close()
        self._thread.join(timeout=2)

    def __enter__(self) -> FakeReceiver:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- wire -------------------------------------------------------------

    def _serve(self) -> None:
        self._dev.settimeout(0.2)
        while not self._stop.is_set():
            try:
                frame = self._dev.recv(64)
            except socket.timeout:
                continue
            except OSError:
                return
            if not frame:
                return
            self.requests.append(frame)
            for reply in self._handle(frame):
                try:
                    self._dev.send(reply)
                except OSError:
                    return

    @staticmethod
    def _reply(device_index: int, feature_index: int, func_swid: int,
               payload: bytes = b"") -> bytes:
        buf = bytearray(hidpp.REPORT_LENGTHS[hidpp.REPORT_LONG])
        buf[0] = hidpp.REPORT_LONG
        buf[1] = device_index
        buf[2] = feature_index
        buf[3] = func_swid
        buf[4:4 + len(payload)] = payload
        return bytes(buf)

    @staticmethod
    def _error(device_index: int, feature_index: int, func_swid: int,
               code: int) -> bytes:
        buf = bytearray(hidpp.REPORT_LENGTHS[hidpp.REPORT_SHORT])
        buf[0] = hidpp.REPORT_SHORT
        buf[1] = device_index
        buf[2] = hidpp.ERROR_MARKER_HIDPP10
        buf[3] = feature_index
        buf[4] = func_swid
        buf[5] = code
        return bytes(buf)

    def _handle(self, frame: bytes) -> list[bytes]:
        if len(frame) < 4:
            return []
        device_index, feature_index, func_swid = frame[1], frame[2], frame[3]
        function = (func_swid >> 4) & 0x0F
        params = frame[4:]

        device = self.devices.get(device_index)
        if device is None:
            return [self._error(device_index, feature_index, func_swid,
                                ERR_UNREACHABLE)]
        if device.offline:
            return [self._error(device_index, feature_index, func_swid,
                                ERR_OFFLINE)]

        if feature_index == hidpp.ROOT_INDEX:
            return self._root(device, device_index, feature_index,
                              func_swid, function, params)
        if feature_index == NAME_INDEX and device.has_name:
            return self._name(device, device_index, feature_index,
                              func_swid, function, params)
        if feature_index == UNIFIED_BATTERY_INDEX:
            return self._unified(device, device_index, feature_index,
                                 func_swid, function)
        # A feature the device does not implement.
        return [self._error(device_index, feature_index, func_swid, 0x09)]

    def _root(self, device, index, feature_index, func_swid, function,
              params) -> list[bytes]:
        if function == hidpp.ROOT_FN_GET_PROTOCOL:
            magic = params[2] if len(params) > 2 else 0
            return [self._reply(index, feature_index, func_swid,
                                bytes([device.protocol[0], device.protocol[1],
                                       magic]))]
        if function == hidpp.ROOT_FN_GET_FEATURE:
            feature_id = (params[0] << 8) | params[1] if len(params) > 1 else 0
            table = {
                hidpp.FEATURE_DEVICE_NAME:
                    NAME_INDEX if device.has_name else 0,
                hidpp.FEATURE_UNIFIED_BATTERY:
                    UNIFIED_BATTERY_INDEX
                    if device.battery_feature == hidpp.FEATURE_UNIFIED_BATTERY
                    else 0,
                hidpp.FEATURE_BATTERY_STATUS:
                    LEGACY_BATTERY_INDEX
                    if device.battery_feature == hidpp.FEATURE_BATTERY_STATUS
                    else 0,
                hidpp.FEATURE_BATTERY_VOLTAGE:
                    VOLTAGE_INDEX
                    if device.battery_feature == hidpp.FEATURE_BATTERY_VOLTAGE
                    else 0,
            }
            table.update(device.features)
            return [self._reply(index, feature_index, func_swid,
                                bytes([table.get(feature_id, 0)]))]
        return [self._error(index, feature_index, func_swid, 0x09)]

    def _name(self, device, index, feature_index, func_swid, function,
              params) -> list[bytes]:
        raw = device.name.encode("utf-8")
        if function == 0x00:                      # length
            return [self._reply(index, feature_index, func_swid,
                                bytes([len(raw)]))]
        if function == 0x01:                      # a 16-byte chunk
            offset = params[0] if params else 0
            return [self._reply(index, feature_index, func_swid,
                                raw[offset:offset + 16])]
        if function == 0x02:                      # kind
            return [self._reply(index, feature_index, func_swid,
                                bytes([device.kind]))]
        return [self._error(index, feature_index, func_swid, 0x09)]

    def _unified(self, device, index, feature_index, func_swid,
                 function) -> list[bytes]:
        if function == 0x00:                      # capabilities
            flags = (0x02 if device.supports_soc else 0x00) | \
                    (0x01 if device.rechargeable else 0x00)
            return [self._reply(index, feature_index, func_swid,
                                bytes([4, flags]))]
        if function == 0x01:                      # state
            return [self._reply(index, feature_index, func_swid,
                                bytes([device.percent, 0x00,
                                       device.charge_status]))]
        return [self._error(index, feature_index, func_swid, 0x09)]
