"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys

from . import __version__
from .monitor import DEFAULT_INTERVAL

# Mirrors tray.BACKENDS, duplicated so `--list` and `--help` do not have to
# import GTK just to parse arguments.
BACKENDS = ("auto", "xembed", "xapp", "appindicator")

# GTK, PyGObject and pycairo come from the distribution rather than from PyPI,
# so a missing one cannot be fixed by pip and the install command differs per
# distro. Keyed by the package manager binary we can look for on PATH.
DEP_COMMANDS = (
    ("apt", "sudo apt install python3-gi python3-cairo gir1.2-xapp-1.0"),
    ("dnf", "sudo dnf install python3-gobject python3-cairo xapps"),
    ("pacman", "sudo pacman -S python-gobject python-cairo xapp"),
    ("zypper", "sudo zypper install python3-gobject python3-cairo"),
    ("apk", "sudo apk add py3-gobject3 py3-cairo"),
)


def _install_hint() -> str:
    for manager, command in DEP_COMMANDS:
        if shutil.which(manager):
            return command
    return "Install PyGObject, GTK 3 and pycairo from your distribution."


def _report_missing_deps(exc: Exception) -> None:
    """Explain a missing GTK stack somewhere the user will actually see it.

    Launched from a desktop entry there is no terminal, so a traceback on
    stderr goes nowhere and the app simply appears not to start. Fall back
    through whatever dialog tools exist before giving up on being seen.
    """
    message = (f"Voltaic needs GTK 3 and PyGObject, which are missing "
               f"({exc}).\n\n{_install_hint()}")
    print(message, file=sys.stderr)

    dialogs = (
        ["zenity", "--error", "--no-wrap", "--title=Voltaic",
         f"--text={message}"],
        ["kdialog", "--title", "Voltaic", "--error", message],
        ["xmessage", "-center", message],
        ["notify-send", "--urgency=critical", "Voltaic", message],
    )
    for argv in dialogs:
        if not shutil.which(argv[0]):
            continue
        try:
            subprocess.run(argv, check=False, timeout=120)
            return
        except (OSError, subprocess.SubprocessError):
            continue


def _claim_single_instance() -> socket.socket | None:
    """Bind an abstract socket so only one tray instance can run.

    Two instances reading the same hidraw node steal each other's replies —
    the device answers once, and whichever process reads first wins — so a
    second copy makes both of them report devices at random. The abstract
    namespace means the lock disappears automatically if we are killed.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.bind(f"\0voltaic-tray-{os.getuid()}")
    except OSError:
        sock.close()
        return None
    return sock


def _print_devices() -> int:
    from .airpods import enumerate_airpods
    from .airpods import unavailable_reason as airpods_unavailable_reason
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
    except Exception as exc:  # a broken Bluetooth stack must not hide HID++
        print(f"Bluetooth scan failed: {exc}", file=sys.stderr)
    else:
        # An empty result is normal (nothing paired), but it also happens
        # when there is no Bluetooth stack to ask — say which.
        reason = airpods_unavailable_reason()
        if reason:
            print(reason, file=sys.stderr)

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

    # Import before taking the lock: a machine without GTK should report that
    # rather than hold a lock it is about to drop anyway.
    try:
        from .app import run
    except ImportError as exc:
        _report_missing_deps(exc)
        return 1

    lock = _claim_single_instance()
    if lock is None:
        print("Voltaic is already running.", file=sys.stderr)
        return 1

    try:
        return run(interval=args.interval, notify=not args.no_notify,
                   tray_backend=args.tray)
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
