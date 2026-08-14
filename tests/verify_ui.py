#!/usr/bin/env python3
"""Verify the tray interaction contract against the running desktop.

Run with `make verify`. Needs a session bus and a tray, so it cannot run
headless — it builds the real app, drives the same handler GTK invokes when
the pointer rests on the icon, and asserts the resulting state.

It deliberately does *not* warp the pointer. Competing with a real cursor on
a desktop somebody is using makes the results meaningless; the leave-watch
geometry is checked directly instead.
"""

from __future__ import annotations

import os
import sys

# Run from a checkout without installing.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from voltaic.app import VoltaicApp, _in_rect  # noqa: E402

checks: list[tuple[str, object, object, bool]] = []


def check(label: str, got, expect) -> None:
    checks.append((label, got, expect, got == expect))


def _buttons(widget, found=None):
    found = [] if found is None else found
    if isinstance(widget, Gtk.Button):
        found.append(widget)
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            _buttons(child, found)
    return found


def check_connect_button(app) -> None:
    """The Connect button must appear only where connecting is possible."""
    from voltaic.model import Battery, Cell, Device

    offline_bt = Device(name="AirPods", kind="earbuds", transport="airpods",
                        path="AA:BB", online=False,
                        cells=[Cell("Left", Battery(percent=90))])
    online_bt = Device(name="AirPods", kind="earbuds", transport="airpods",
                       path="CC:DD", online=True,
                       cells=[Cell("Left", Battery(percent=90))])
    # A Logitech device cannot be summoned by the host, so it gets no button
    # even when offline.
    offline_hid = Device(index=1, name="MX Keys S", kind="keyboard",
                         transport="hidpp", path="/dev/hidraw3", online=False,
                         battery=Battery(percent=70))

    clicked = []
    app.popup.on_connect = lambda device: clicked.append(device.key)

    app.popup.set_devices([offline_bt, online_bt, offline_hid])
    app.popup.show_all()
    labels = [b.get_label() for b in _buttons(app.popup)]
    check("connect button only when connectable", labels, ["Connect"])

    _buttons(app.popup)[0].emit("clicked")
    check("connect button invokes callback", clicked, [offline_bt.key])

    app.popup.set_devices([offline_bt], connecting={offline_bt.key})
    app.popup.show_all()
    button = _buttons(app.popup)[0]
    check("connecting state disables button",
          (button.get_label(), button.get_sensitive()), ("Connecting…", False))

    # Adding the button must not resize the panel.
    app.popup.set_devices([online_bt])
    app.popup.show_all()
    without = app.popup.get_preferred_size()[1].width
    app.popup.set_devices([offline_bt])
    app.popup.show_all()
    check("panel width unchanged by button",
          app.popup.get_preferred_size()[1].width, without)

    app.popup.dismiss()


def main() -> int:
    app = VoltaicApp(interval=300, notify=False)

    def phase_open() -> bool:
        print(f"backend: {app.tray.backend} | hover_capable: {app.popup.hover_capable}")
        if app.tray.backend != "xembed":
            print(f"\nBackend {app.tray.backend!r} cannot report hover; "
                  "skipping the hover checks.")
            app.quit()
            return False

        # Exactly what GTK calls when the pointer rests on the icon.
        app.tray._on_query_tooltip(None, 0, 0, False, None)
        check("hover opens panel", app.popup.get_visible(), True)
        check("hover leaves it unpinned", app._pinned, False)
        check("hover starts leave-watch", app._hover_timer is not None, True)

        # The window is placed from an idle callback; let it settle.
        GLib.timeout_add(400, phase_geometry)
        return False

    def phase_geometry() -> bool:
        icon = app.tray.geometry()
        app._icon_rect = icon
        px, py, pw, ph = app.popup.frame_rect()
        print(f"icon rect: {icon}   panel rect: {(px, py, pw, ph)}")

        check("pointer on icon counts",
              app._within_hover_zone(icon[0] + icon[2] // 2,
                                     icon[1] + icon[3] // 2), True)
        check("pointer on panel counts",
              app._within_hover_zone(px + pw // 2, py + ph // 2), True)
        check("pointer in icon/panel gap counts",
              app._within_hover_zone(icon[0], (py + ph + icon[1]) // 2), True)
        check("pointer far away does not count",
              app._within_hover_zone(px - 600, py - 600), False)

        # A click while hovering must pin the panel, not dismiss the thing
        # the pointer just summoned.
        app.toggle_popup(None, None, None)
        check("click pins open",
              (app.popup.get_visible(), app._pinned), (True, True))
        check("pin cancels leave-watch", app._hover_timer is None, True)

        app.toggle_popup(None, None, None)
        check("second click closes",
              (app.popup.get_visible(), app._pinned), (False, False))

        app.tray._on_query_tooltip(None, 0, 0, False, None)
        check("hover works after close", app.popup.get_visible(), True)
        app.popup.dismiss()

        check("_in_rect inside", _in_rect(50, 50, (0, 0, 100, 100)), True)
        check("_in_rect outside", _in_rect(500, 50, (0, 0, 100, 100)), False)
        check("_in_rect slack", _in_rect(110, 50, (0, 0, 100, 100), 20), True)

        check_connect_button(app)

        app.quit()
        return False

    GLib.timeout_add(3500, phase_open)
    Gtk.main()

    print()
    for label, got, expect, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {label:36} "
              f"got={got!r} expected={expect!r}")
    failed = [c for c in checks if not c[3]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
