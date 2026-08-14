"""Background device monitoring.

Runs on its own thread so a sleeping device — which can take the better part
of a second to answer a ping — never stalls the UI. Between scheduled scans
the thread parks in select() on the open hidraw nodes, so an unsolicited
battery or connection notification updates the tray immediately instead of
waiting out the poll interval.
"""

from __future__ import annotations

import errno
import os
import select
import threading
import time

from . import airpods, hidpp, state

# How often to scan when nobody is looking.
#
# The cost of a scan is not the radio traffic — 25 requests and 175 bytes is
# nothing — it is that pinging a sleeping mouse wakes it up. Measured here, a
# device idle for 20s takes ~1.9s to answer, so each scan drags both devices
# out of deep sleep.
#
# Nothing is lost by scanning rarely: opening the panel triggers its own
# refresh, so what you look at is always fresh, and the background scan only
# feeds the tray icon and the low-battery warning, neither of which needs
# minute-level accuracy on a figure that moves ~1% an hour.
DEFAULT_INTERVAL = 900.0

# Re-probing a device costs it a little power, so coalesce bursts of
# notifications rather than reacting to every single frame.
NOTIFY_DEBOUNCE = 2.0

# HID++ 1.0 notification sub-id announcing a device connect/disconnect.
NOTIFY_CONNECTION = 0x41


class Monitor(threading.Thread):
    """Polls HID++ devices and reports results through a callback.

    `on_update(devices, error)` is invoked from this thread; callers that
    touch the UI must marshal onto the main loop themselves.
    """

    def __init__(self, on_update, interval: float = DEFAULT_INTERVAL):
        super().__init__(daemon=True, name="voltaic-monitor")
        self.on_update = on_update
        self.interval = interval
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._conns: list[hidpp.HidppDevice] = []

    # -- public API -------------------------------------------------------

    def refresh_soon(self) -> None:
        """Ask for a scan as soon as the thread can get to one."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # -- connection handling ---------------------------------------------

    def _open_connections(self) -> str | None:
        """(Re)open every HID++ node. Returns an error string, or None."""
        self._close_connections()
        paths = hidpp.find_hidpp_paths()
        if not paths:
            return ("No Logitech receiver found.\n"
                    "Plug in the Bolt or Unifying receiver and try again.")
        denied = []
        for path in paths:
            try:
                self._conns.append(hidpp.HidppDevice(path))
            except PermissionError:
                denied.append(path)
            except OSError:
                continue
        if not self._conns:
            if denied:
                return ("Permission denied on " + ", ".join(denied) + ".\n"
                        "Install the udev rules, then replug the receiver.")
            return "Found a receiver but could not open it."
        return None

    def _close_connections(self) -> None:
        for conn in self._conns:
            try:
                conn.close()
            except OSError:
                pass
        self._conns = []

    # -- main loop --------------------------------------------------------

    def run(self) -> None:
        while not self._stop.is_set():
            hid_error = self._open_connections()
            devices: list[hidpp.Device] = []

            if not hid_error:
                try:
                    for conn in self._conns:
                        devices.extend(hidpp.scan_connection(conn))
                except OSError as exc:
                    # Receiver unplugged mid-scan; loop round and rediscover.
                    if exc.errno in (errno.ENODEV, errno.EIO, errno.EBADF):
                        self._close_connections()
                        continue
                    raise

            # Bluetooth accessories are independent of the hidraw side, so
            # they are scanned even when no Logitech receiver is present.
            devices.extend(self._scan_airpods())

            # Only surface the receiver problem if it left us with nothing
            # at all to show; a user with just AirPods has no receiver and
            # that is not an error.
            error = hid_error if (hid_error and not devices) else None
            self.on_update(state.reconcile(devices), error)

            if hid_error:
                # No hidraw fds to park on, so just sleep before retrying.
                self._wake.wait(min(self.interval, 30.0))
                self._wake.clear()
                continue

            self._listen(devices, deadline=time.monotonic() + self.interval)
            self._close_connections()

    @staticmethod
    def _scan_airpods() -> list[hidpp.Device]:
        try:
            return airpods.enumerate_airpods()
        except Exception:
            # Bluetooth being unavailable must never take down the poll loop.
            return []

    def _listen(self, devices: list[hidpp.Device], deadline: float) -> None:
        """Wait for the next scan, reacting to notifications in the meantime.

        Returns when the deadline passes, a refresh is requested, or a
        notification indicates the battery picture has changed.
        """
        # Feature indexes that mean "battery news", per device index.
        battery_features = {
            device.index: {
                index for feature_id, index in device.features.items()
                if feature_id in (hidpp.FEATURE_UNIFIED_BATTERY,
                                  hidpp.FEATURE_BATTERY_STATUS,
                                  hidpp.FEATURE_BATTERY_VOLTAGE)
            }
            for device in devices
        }
        fds = [conn.fd for conn in self._conns if conn.fd is not None]
        interesting_at: float | None = None

        while not self._stop.is_set():
            now = time.monotonic()
            if self._wake.is_set():
                self._wake.clear()
                return
            if now >= deadline:
                return
            # Once something interesting lands, give the burst a moment to
            # settle, then rescan.
            if interesting_at is not None and now - interesting_at >= NOTIFY_DEBOUNCE:
                return

            timeout = min(deadline, interesting_at + NOTIFY_DEBOUNCE
                          if interesting_at is not None else deadline) - now
            timeout = max(0.05, min(timeout, 1.0))
            try:
                ready = select.select(fds, [], [], timeout)[0]
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                return
            for fd in ready:
                try:
                    frame = os.read(fd, 64)
                except OSError:
                    return
                if self._is_interesting(frame, battery_features):
                    interesting_at = interesting_at or time.monotonic()

    @staticmethod
    def _is_interesting(frame: bytes, battery_features: dict[int, set[int]]) -> bool:
        """Does this unsolicited frame imply the battery display is stale?"""
        if len(frame) < 4 or frame[0] not in hidpp.REPORT_LENGTHS:
            return False
        # Replies to someone else's request carry a non-zero software id.
        if (frame[3] & 0x0F) != 0:
            return False
        if frame[2] == NOTIFY_CONNECTION:
            return True
        return frame[2] in battery_features.get(frame[1], ())
