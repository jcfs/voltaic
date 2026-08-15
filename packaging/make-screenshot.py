#!/usr/bin/env python3
"""Regenerate packaging/screenshot.png from the real panel.

The README leads with a screenshot, so it goes stale every time the panel
changes. This builds the actual VoltaicPopup with fixed synthetic devices,
grabs the window with its alpha channel, and composites it over a generated
backdrop — so nothing of the machine it runs on ends up in the image, and
two runs produce the same picture.

    python3 packaging/make-screenshot.py [output.png]

Needs a running X11 session with a compositor (the panel is translucent).
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from voltaic.model import (  # noqa: E402
    CHARGE_CHARGING,
    CHARGE_DISCHARGING,
    CHARGE_DISCONNECTED,
    Battery,
    Cell,
    Device,
)
from voltaic.popup import VoltaicPopup, ensure_css  # noqa: E402

MARGIN = 40  # backdrop visible around the panel


def sample_devices() -> list[Device]:
    """A fixed cast of devices, so the picture does not depend on hardware."""
    return [
        Device(index=1, name="MX Keys S", kind="keyboard",
               path="/dev/hidraw0",
               battery=Battery(percent=75, status=CHARGE_DISCHARGING)),
        Device(index=2, name="MX Master 4", kind="mouse",
               path="/dev/hidraw0",
               battery=Battery(percent=55, status=CHARGE_DISCHARGING)),
        Device(name="AirPods Pro", kind="earbuds", path="AA:BB:CC:DD:EE:01",
               transport="airpods", cells=[
                   Cell("Left", Battery(percent=97)),
                   Cell("Right", Battery(percent=97)),
                   Cell("Case", Battery(percent=None,
                                        status=CHARGE_DISCONNECTED)),
               ]),
        Device(name="AirPods Max", kind="earbuds", path="AA:BB:CC:DD:EE:02",
               transport="airpods", cells=[
                   Cell("Left", Battery(percent=64, status=CHARGE_CHARGING)),
                   Cell("Right", Battery(percent=61, status=CHARGE_CHARGING)),
                   Cell("Case", Battery(percent=43, status=CHARGE_CHARGING)),
               ]),
    ]


def offline_devices() -> list[Device]:
    """The other documented state: Bluetooth accessories that are away."""
    return [
        Device(index=1, name="MX Keys S", kind="keyboard",
               path="/dev/hidraw0",
               battery=Battery(percent=70, status=CHARGE_DISCHARGING)),
        Device(name="AirPods Pro", kind="earbuds", path="AA:BB:CC:DD:EE:01",
               transport="airpods", online=False, cells=[
                   Cell("Left", Battery(percent=95)),
                   Cell("Right", Battery(percent=94)),
                   Cell("Case", Battery(percent=None,
                                        status=CHARGE_DISCONNECTED)),
               ]),
        Device(name="AirPods Max", kind="earbuds", path="AA:BB:CC:DD:EE:02",
               transport="airpods", online=False, cells=[
                   Cell("Left", Battery(percent=95)),
                   Cell("Right", Battery(percent=94)),
                   Cell("Case", Battery(percent=None,
                                        status=CHARGE_DISCONNECTED)),
               ]),
    ]


def draw_backdrop(ctx: cairo.Context, width: int, height: int) -> None:
    """A muted gradient with soft blobs — something for the glass to sit on."""
    gradient = cairo.LinearGradient(0, 0, width, height)
    gradient.add_color_stop_rgb(0.0, 0.16, 0.17, 0.25)
    gradient.add_color_stop_rgb(0.5, 0.28, 0.22, 0.31)
    gradient.add_color_stop_rgb(1.0, 0.35, 0.24, 0.28)
    ctx.set_source(gradient)
    ctx.paint()

    for cx, cy, radius, alpha in (
        (0.10, 0.12, 0.20, 0.10),
        (0.78, 0.10, 0.14, 0.08),
        (0.88, 0.62, 0.18, 0.09),
        (0.20, 0.88, 0.16, 0.07),
    ):
        ctx.set_source_rgba(1, 1, 1, alpha)
        ctx.arc(cx * width, cy * height, radius * min(width, height),
                0, 2 * math.pi)
        ctx.fill()


def capture(popup: VoltaicPopup, path: str) -> None:
    window = popup.get_window()
    # The grab is in device pixels, so on a HiDPI screen asking for the
    # window's logical size returns the top-left quarter of it. Scaling up
    # here also means the saved image is the crisp one.
    scale = window.get_scale_factor()
    width = window.get_width() * scale
    height = window.get_height() * scale
    margin = MARGIN * scale

    # Grab the panel's own window rather than a screen region: the alpha
    # channel comes with it, and nothing behind it is ever read.
    pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, width, height)
    if pixbuf is None:
        raise SystemExit("could not grab the panel window")

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                 width + 2 * margin, height + 2 * margin)
    ctx = cairo.Context(surface)
    draw_backdrop(ctx, surface.get_width(), surface.get_height())
    Gdk.cairo_set_source_pixbuf(ctx, pixbuf, margin, margin)
    ctx.paint()
    surface.write_to_png(path)
    print(f"wrote {path} ({surface.get_width()}x{surface.get_height()}, "
          f"scale {scale})")


def shoot_settings(output: str) -> int:
    """Capture the settings window.

    A normal toplevel and the panel disagree about scaling: the panel is an
    override-redirect ARGB window whose grab needs device pixels, while this
    one is grabbed in logical coordinates and comes back at device
    resolution. Asking for the wrong one gives either a quarter of the
    window or a window with a black margin, so the two are captured
    separately rather than sharing a helper.
    """
    from voltaic import config as config_module
    from voltaic.settings import SettingsWindow

    window = SettingsWindow(config_module.load(), sample_devices(),
                            on_apply=lambda _config: None)
    window.show_all()

    def shoot():
        gdk_window = window.get_window()
        pixbuf = Gdk.pixbuf_get_from_window(
            gdk_window, 0, 0,
            gdk_window.get_width(), gdk_window.get_height())
        if pixbuf is None:
            raise SystemExit("could not grab the settings window")
        pixbuf.savev(output, "png", [], [])
        print(f"wrote {output} ({pixbuf.get_width()}x{pixbuf.get_height()})")
        Gtk.main_quit()
        return False

    GLib.timeout_add(900, shoot)
    Gtk.main()
    return 0


def main() -> int:
    flags = {"--connect", "--settings"}
    args = [a for a in sys.argv[1:] if a not in flags]
    connect_variant = "--connect" in sys.argv[1:]

    if "--settings" in sys.argv[1:]:
        here = os.path.dirname(os.path.abspath(__file__))
        ensure_css()
        return shoot_settings(
            args[0] if args else os.path.join(here, "screenshot-settings.png"))
    here = os.path.dirname(os.path.abspath(__file__))
    default = "screenshot-connect.png" if connect_variant else "screenshot.png"
    output = args[0] if args else os.path.join(here, default)

    ensure_css()
    popup = VoltaicPopup(on_connect=lambda device: None)
    popup.hover_capable = True
    if connect_variant:
        # One accessory mid-attempt, one offering the button, so the picture
        # shows both states of the Connect control at once.
        popup.set_devices(offline_devices(),
                          connecting=["airpods:AA:BB:CC:DD:EE:02"],
                          connect_errors={
                              "airpods:AA:BB:CC:DD:EE:01":
                                  "No response — open the case or put the "
                                  "buds in"})
    else:
        popup.set_devices(sample_devices())
    popup.move(120, 120)
    popup.show_all()

    def shoot():
        try:
            capture(popup, output)
        finally:
            Gtk.main_quit()
        return False

    # Give the compositor a moment to actually paint the window.
    GLib.timeout_add(700, shoot)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
