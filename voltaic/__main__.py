"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import socket
import sys

from .monitor import DEFAULT_INTERVAL

__version__ = "1.0.0"

# Mirrors tray.BACKENDS, duplicated so `--list` and `--help` do not have to
# import GTK just to parse arguments.
BACKENDS = ("auto", "xembed", "xapp", "appindicator")


def _claim_single_instance() -> socket.socket | None:
    """Bind an abstract socket so only one tray instance can run.

    Two instances reading the same hidraw node steal each other's replies —
    the device answers once, and whichever process reads first wins — so a
    second copy makes both of them report devices at random. The abstract
    namespace means the lock disappears automatically if we are killed.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.bind("\0voltaic-tray-%d" % os.getuid())
    except OSError:
        sock.close()
        return None
    return sock


def _print_devices() -> int:
    from .airpods import enumerate_airpods
    from .hidpp import enumerate_devices, find_hidpp_paths
    from .state import describe_age, reconcile

    devices = []
    paths = find_hidpp_paths()
    if paths:
        try:
            devices.extend(enumerate_devices(paths))
        except PermissionError:
            print(f"Permission denied opening {', '.join(paths)}.\n"
                  "Install the udev rules and replug the receiver.",
                  file=sys.stderr)
            return 1
    try:
        devices.extend(enumerate_airpods())
    except Exception:
        pass

    if not devices:
        print("No devices found. Is the receiver plugged in, or a "
              "Bluetooth accessory connected?", file=sys.stderr)
        return 1

    devices = reconcile(devices)
    for device in devices:
        battery = device.battery
        if not device.online:
            level = device.lowest_percent
            known = (f"{level}% when last seen" if level is not None
                     else "unknown")
            status = f"{known}, {describe_age(device.last_seen)}"
        elif device.cells:
            status = ", ".join(
                f"{cell.label} {cell.battery.percent}%"
                if cell.battery.present else f"{cell.label} —"
                for cell in device.cells)
        elif battery is None or battery.percent is None:
            status = "unknown"
        else:
            status = f"{battery.percent}%"
            if battery.approximate:
                status += " (approx)"
            status += f", {battery.status}"
        kind = f" [{device.kind}]" if device.kind else ""
        print(f"{device.display_name}{kind} — {status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="voltaic",
        description="Logitech device battery levels in the system tray.")
    parser.add_argument("--list", action="store_true",
                        help="print battery levels and exit")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        metavar="SECONDS",
                        help=f"seconds between scans (default: {DEFAULT_INTERVAL:.0f})")
    parser.add_argument("--no-notify", action="store_true",
                        help="do not post low-battery notifications")
    parser.add_argument("--tray", default="auto", choices=BACKENDS,
                        help="status icon backend; only 'xembed' supports "
                             "opening the panel on hover (default: auto)")
    parser.add_argument("--version", action="version",
                        version=f"voltaic {__version__}")
    args = parser.parse_args(argv)

    if args.list:
        return _print_devices()

    lock = _claim_single_instance()
    if lock is None:
        print("Voltaic is already running.", file=sys.stderr)
        return 1

    from .app import run
    try:
        return run(interval=args.interval, notify=not args.no_notify,
                   tray_backend=args.tray)
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
