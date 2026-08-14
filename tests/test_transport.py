#!/usr/bin/env python3
"""Tests for the HID++ transport layer, against a fake hidraw node.

The stand-in is a SOCK_SEQPACKET socketpair, not a FIFO: hidraw hands back
one report per read, and a byte-stream fake would let two queued frames
arrive in a single `os.read` — which the real device never does, and which
would quietly invalidate every test about skipping the wrong frame.

That makes the trickiest part of the protocol testable with no hardware:
deciding which of the frames arriving on a shared node is *our* reply.

Run with `make test`.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voltaic import hidpp  # noqa: E402


def frame(report_id, device_index, feature_index, func_swid, *params):
    """Build a reply frame padded to the report's real length."""
    buf = bytearray(hidpp.REPORT_LENGTHS[report_id])
    buf[0] = report_id
    buf[1] = device_index
    buf[2] = feature_index
    buf[3] = func_swid
    buf[4:4 + len(params)] = bytes(params)
    return bytes(buf)


class FakeNode:
    """A message-oriented socket standing in for /dev/hidraw*."""

    def __init__(self, timeout: float = 0.5):
        self._host, self._dev = socket.socketpair(socket.AF_UNIX,
                                                  socket.SOCK_SEQPACKET)
        # Build the device around our fd rather than opening a path: the
        # point is to exercise _await_reply, not os.open.
        self.device = hidpp.HidppDevice.__new__(hidpp.HidppDevice)
        self.device.path = "/dev/hidraw-fake"
        self.device.timeout = timeout
        self.device.fd = os.dup(self._dev.fileno())

    def feed(self, data: bytes) -> None:
        """Queue one report, exactly as the device would deliver it."""
        self._host.send(data)

    def close(self) -> None:
        try:
            self.device.close()
        except OSError:
            pass
        self._host.close()
        self._dev.close()


class FifoNode:
    """A real path on disk, for the parts that do open one."""

    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "hidraw-fake")
        os.mkfifo(self.path)

    def close(self) -> None:
        os.unlink(self.path)
        os.rmdir(self.dir)


class FramingTests(unittest.TestCase):
    def test_frame_is_padded_to_the_report_length(self):
        built = hidpp.HidppDevice._frame(hidpp.REPORT_SHORT, 1, 2, 3, b"\xAA")
        self.assertEqual(len(built), hidpp.REPORT_LENGTHS[hidpp.REPORT_SHORT])
        self.assertEqual(built[:5], bytes([0x10, 1, 2, 3, 0xAA]))
        self.assertEqual(built[5:], b"\x00\x00")

    def test_long_report_is_longer(self):
        built = hidpp.HidppDevice._frame(hidpp.REPORT_LONG, 1, 2, 3, b"")
        self.assertEqual(len(built), 20)

    def test_params_do_not_overflow_the_frame(self):
        built = hidpp.HidppDevice._frame(hidpp.REPORT_LONG, 1, 2, 3, b"\x01" * 16)
        self.assertEqual(len(built), 20)


class AwaitReplyTests(unittest.TestCase):
    """Which frame on a shared node counts as the answer to our request."""

    DEVICE = 1
    FEATURE = 0x05
    # Function 2 with the software id in the low nibble, as request() builds it.
    SWID = (2 << 4) | hidpp.SOFTWARE_ID

    def setUp(self):
        self.node = FakeNode()

    def tearDown(self):
        self.node.close()

    def await_reply(self, timeout=0.4):
        return self.node.device._await_reply(
            self.DEVICE, self.FEATURE, self.SWID,
            time.monotonic() + timeout)

    def test_returns_the_matching_reply(self):
        self.node.feed(frame(hidpp.REPORT_LONG, self.DEVICE, self.FEATURE,
                             self.SWID, 0x42))
        self.assertEqual(self.await_reply()[4], 0x42)

    def test_ignores_replies_for_another_device_index(self):
        self.node.feed(frame(hidpp.REPORT_LONG, self.DEVICE + 1, self.FEATURE,
                             self.SWID, 0x99))
        self.node.feed(frame(hidpp.REPORT_LONG, self.DEVICE, self.FEATURE,
                             self.SWID, 0x42))
        self.assertEqual(self.await_reply()[4], 0x42)

    def test_ignores_another_features_reply(self):
        self.node.feed(frame(hidpp.REPORT_LONG, self.DEVICE, self.FEATURE + 1,
                             self.SWID, 0x99))
        self.node.feed(frame(hidpp.REPORT_LONG, self.DEVICE, self.FEATURE,
                             self.SWID, 0x42))
        self.assertEqual(self.await_reply()[4], 0x42)

    def test_ignores_device_notifications(self):
        # Device-initiated frames carry software id 0 — the case that makes
        # two HID++ clients on one node steal each other's replies.
        self.node.feed(frame(hidpp.REPORT_LONG, self.DEVICE, self.FEATURE,
                             (2 << 4) | 0, 0x99))
        self.node.feed(frame(hidpp.REPORT_LONG, self.DEVICE, self.FEATURE,
                             self.SWID, 0x42))
        self.assertEqual(self.await_reply()[4], 0x42)

    def test_ignores_another_clients_software_id(self):
        # Solaar's traffic on the same node: same feature, different swid.
        self.node.feed(frame(hidpp.REPORT_LONG, self.DEVICE, self.FEATURE,
                             (2 << 4) | 0x01, 0x99))
        self.node.feed(frame(hidpp.REPORT_LONG, self.DEVICE, self.FEATURE,
                             self.SWID, 0x42))
        self.assertEqual(self.await_reply()[4], 0x42)

    def test_ignores_runt_frames(self):
        self.node.feed(b"\x11\x01")
        self.node.feed(frame(hidpp.REPORT_LONG, self.DEVICE, self.FEATURE,
                             self.SWID, 0x42))
        self.assertEqual(self.await_reply()[4], 0x42)

    def test_ignores_unknown_report_ids(self):
        self.node.feed(bytes([0x99, self.DEVICE, self.FEATURE, self.SWID]
                             + [0] * 16))
        self.node.feed(frame(hidpp.REPORT_LONG, self.DEVICE, self.FEATURE,
                             self.SWID, 0x42))
        self.assertEqual(self.await_reply()[4], 0x42)

    def test_timeout_when_nothing_arrives(self):
        with self.assertRaises(hidpp.Timeout):
            self.await_reply(timeout=0.15)

    def test_timeout_when_only_noise_arrives(self):
        self.node.feed(frame(hidpp.REPORT_LONG, self.DEVICE + 1, self.FEATURE,
                             self.SWID, 0x99))
        with self.assertRaises(hidpp.Timeout):
            self.await_reply(timeout=0.15)


class ErrorFrameTests(AwaitReplyTests):
    """HID++ distinguishes 'nothing paired' from 'paired but not connected'."""

    def error_10(self, code):
        # HID++ 1.0 error: marker, then the sub-id and address it refers to.
        return frame(hidpp.REPORT_SHORT, self.DEVICE,
                     hidpp.ERROR_MARKER_HIDPP10, self.FEATURE,
                     self.SWID, code)

    def error_20(self, code):
        return frame(hidpp.REPORT_LONG, self.DEVICE,
                     hidpp.ERROR_MARKER_HIDPP20, self.FEATURE,
                     self.SWID, code)

    def test_offline_code_raises_device_offline(self):
        # 0x04 is what makes the greyed-out offline rows possible.
        self.node.feed(self.error_10(0x04))
        with self.assertRaises(hidpp.DeviceOffline):
            self.await_reply()

    def test_absent_codes_raise_device_unreachable(self):
        for code in (0x08, 0x09):
            with self.subTest(code=code):
                node = FakeNode()
                try:
                    node.feed(frame(hidpp.REPORT_SHORT, self.DEVICE,
                                    hidpp.ERROR_MARKER_HIDPP10, self.FEATURE,
                                    self.SWID, code))
                    with self.assertRaises(hidpp.DeviceUnreachable):
                        node.device._await_reply(self.DEVICE, self.FEATURE,
                                                 self.SWID,
                                                 time.monotonic() + 0.4)
                finally:
                    node.close()

    def test_unsupported_feature_raises_feature_not_supported(self):
        self.node.feed(self.error_20(0x09))
        with self.assertRaises(hidpp.FeatureNotSupported):
            self.await_reply()

    def test_other_hidpp20_error_is_a_protocol_error(self):
        self.node.feed(self.error_20(0x03))
        with self.assertRaises(hidpp.ProtocolError):
            self.await_reply()

    # The inherited happy-path tests run again here, which is fine — they
    # cost nothing and pin the same behaviour.


class DrainTests(unittest.TestCase):
    def setUp(self):
        self.node = FakeNode()

    def tearDown(self):
        self.node.close()

    def test_drain_discards_queued_reports(self):
        # Stale frames left by a previous request must not be mistaken for
        # the answer to the next one.
        self.node.feed(frame(hidpp.REPORT_LONG, 1, 5, 0x2D, 0x99))
        self.node.device._drain()
        with self.assertRaises(hidpp.Timeout):
            self.node.device._await_reply(1, 5, 0x2D, time.monotonic() + 0.15)

    def test_drain_on_empty_node_is_harmless(self):
        self.node.device._drain()
        self.node.device._drain()

    def test_drain_on_a_closed_node_does_not_raise(self):
        # The receiver can be unplugged between a scan starting and the
        # drain; a dead fd must not take the monitor thread down.
        self.node.device.close()
        self.node.device.fd = -1
        self.node.device._drain()
        self.node.device.fd = None


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.node = FifoNode()

    def tearDown(self):
        self.node.close()

    def test_context_manager_closes(self):
        with hidpp.HidppDevice(self.node.path) as device:
            self.assertIsNotNone(device.fd)
        self.assertIsNone(device.fd)

    def test_double_close_is_safe(self):
        device = hidpp.HidppDevice(self.node.path)
        device.close()
        device.close()
        self.assertIsNone(device.fd)

    def test_missing_node_raises_oserror(self):
        with self.assertRaises(OSError):
            hidpp.HidppDevice("/dev/does-not-exist-voltaic")


if __name__ == "__main__":
    unittest.main(verbosity=2)
