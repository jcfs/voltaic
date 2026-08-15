"""The translucent panel that drops out of the tray icon.

The window itself is transparent: an ARGB visual plus `app-paintable` lets us
paint the rounded glass surface (and its own drop shadow, since an
undecorated X11 window gets none from the compositor) in a draw handler,
while GTK widgets handle the text on top.
"""

from __future__ import annotations

import math
import time

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import state, theme  # noqa: E402

CSS = b"""
.voltaic-title {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.6px;
    color: rgba(245, 245, 247, 0.45);
}
.voltaic-name {
    font-size: 13px;
    font-weight: 600;
    color: #F5F5F7;
}
.voltaic-name-off {
    font-size: 13px;
    font-weight: 600;
    color: rgba(245, 245, 247, 0.45);
}
.voltaic-sub {
    font-size: 11px;
    color: rgba(245, 245, 247, 0.52);
}
.voltaic-connect {
    background-image: none;
    background-color: rgba(255, 255, 255, 0.11);
    border: 1px solid rgba(255, 255, 255, 0.20);
    border-radius: 11px;
    box-shadow: none;
    text-shadow: none;
    color: rgba(245, 245, 247, 0.92);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.4px;
    padding: 2px 12px;
    min-height: 0;
    min-width: 0;
}
.voltaic-connect:hover {
    background-color: rgba(255, 255, 255, 0.19);
    border-color: rgba(255, 255, 255, 0.30);
}
.voltaic-connect:active {
    background-color: rgba(255, 255, 255, 0.26);
}
.voltaic-connect:disabled {
    background-color: rgba(255, 255, 255, 0.05);
    border-color: rgba(255, 255, 255, 0.10);
    color: rgba(245, 245, 247, 0.38);
}
.voltaic-cell {
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.4px;
    color: rgba(245, 245, 247, 0.62);
}
.voltaic-cell-off {
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.4px;
    color: rgba(245, 245, 247, 0.30);
}
.voltaic-foot {
    font-size: 10px;
    color: rgba(245, 245, 247, 0.34);
}
.voltaic-empty {
    font-size: 12px;
    color: rgba(245, 245, 247, 0.55);
}
.voltaic-warn {
    font-size: 10px;
    color: rgba(248, 113, 113, 0.85);
}
"""


def _install_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


class BatteryRing(Gtk.DrawingArea):
    """A circular gauge with the percentage set inside it."""

    def __init__(self, percent: int | None, charging: bool,
                 size: int = theme.RING_SIZE, dimmed: bool = False):
        super().__init__()
        self.percent = percent
        self.charging = charging
        self.dimmed = dimmed
        self.set_size_request(size, size)
        self.connect("draw", self._on_draw)

    def _on_draw(self, _widget, cr: cairo.Context) -> bool:
        alloc = self.get_allocation()
        size = min(alloc.width, alloc.height)
        cx = alloc.width / 2.0
        cy = alloc.height / 2.0
        # Derived from the widget size so the same gauge works at the 46px
        # used for a whole device and the 36px used for one cell.
        thickness = max(2.6, size * 0.087)
        radius = size / 2.0 - thickness / 2.0 - 1

        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_width(thickness)

        # Track.
        cr.set_source_rgba(*theme.TRACK, theme.TRACK_ALPHA)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        color = theme.level_color(self.percent, self.charging)
        # An offline device shows its last known level, so the arc is drawn
        # faded to make clear the number is a memory, not a live reading.
        arc_alpha = 0.34 if self.dimmed else 1.0
        if self.percent:
            start = -math.pi / 2
            sweep = 2 * math.pi * max(0, min(100, self.percent)) / 100.0
            if not self.dimmed:
                # Soft glow underneath the arc gives the gauge some depth.
                cr.set_source_rgba(*color, 0.22)
                cr.set_line_width(thickness + 3)
                cr.arc(cx, cy, radius, start, start + sweep)
                cr.stroke()

            cr.set_source_rgba(*color, arc_alpha)
            cr.set_line_width(thickness)
            cr.arc(cx, cy, radius, start, start + sweep)
            cr.stroke()

        # Centred number. No "%" glyph: the ring already says this is a
        # percentage, and dropping it lets the digits sit on the centre
        # rather than being pushed off it by a trailing sign.
        label = "--" if self.percent is None else str(self.percent)
        layout = PangoCairo.create_layout(cr)
        layout.set_text(label, -1)
        desc = Pango.FontDescription()
        desc.set_size(int(size * 0.27 * Pango.SCALE))
        desc.set_weight(Pango.Weight.SEMIBOLD)
        layout.set_font_description(desc)
        text_w, text_h = layout.get_pixel_size()

        text_alpha = 0.45 if self.dimmed else 0.95
        cr.set_source_rgba(0.96, 0.96, 0.97, text_alpha)
        cr.move_to(cx - text_w / 2.0, cy - text_h / 2.0)
        PangoCairo.show_layout(cr, layout)

        if self.charging:
            # Bolt badge tucked into the bottom-right of the ring, sized
            # from the ring so it works on both the full and cell gauges.
            bx = cx + radius * 0.72
            by = cy + radius * 0.72
            badge = max(5.5, size * 0.185)
            cr.set_source_rgba(0.06, 0.07, 0.09, 0.92)
            cr.arc(bx, by, badge, 0, 2 * math.pi)
            cr.fill()
            theme.draw_bolt(cr, bx, by, badge * 1.3, theme.CHARGING)
        return False


def connect_button(device, connecting: bool, on_connect) -> Gtk.Widget | None:
    """A pill button to bring up an offline Bluetooth device, if applicable."""
    if not device.can_connect or on_connect is None:
        return None
    button = Gtk.Button(label="Connecting…" if connecting else "Connect")
    button.get_style_context().add_class("voltaic-connect")
    button.set_valign(Gtk.Align.CENTER)
    button.set_sensitive(not connecting)
    button.connect("clicked", lambda _b: on_connect(device))
    return button


class CellGauge(Gtk.Box):
    """A small gauge with a caption, for one cell of a multi-part device."""

    def __init__(self, cell, offline: bool = False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        battery = cell.battery
        # Dim both when the cell itself is absent and when the whole device
        # is offline — in that case every number shown is a remembered one.
        faded = offline or not battery.present
        self.pack_start(
            BatteryRing(battery.percent, battery.charging,
                        size=theme.CELL_RING_SIZE, dimmed=faded),
            False, False, 0)
        caption = Gtk.Label(label=cell.label)
        caption.get_style_context().add_class(
            "voltaic-cell-off" if faded else "voltaic-cell")
        self.pack_start(caption, False, False, 0)


class MultiCellRow(Gtk.Box):
    """A device made of several cells — AirPods and their case."""

    def __init__(self, device, connecting=False, on_connect=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=13)

        gauges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        gauges.set_valign(Gtk.Align.CENTER)
        for cell in device.cells:
            gauges.pack_start(CellGauge(cell, offline=not device.online),
                              False, False, 0)
        self.pack_start(gauges, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_valign(Gtk.Align.CENTER)

        name = Gtk.Label(label=device.display_name, xalign=0.0)
        name.get_style_context().add_class(
            "voltaic-name" if device.online else "voltaic-name-off")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        text.pack_start(name, False, False, 0)

        sub = Gtk.Label(label=DeviceRow._subtitle(device), xalign=0.0)
        sub.get_style_context().add_class("voltaic-sub")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        text.pack_start(sub, False, False, 0)

        self.pack_start(text, True, True, 0)

        button = connect_button(device, connecting, on_connect)
        if button is not None:
            self.pack_end(button, False, False, 0)


class DeviceRow(Gtk.Box):
    """One device: gauge, name, and a status line."""

    def __init__(self, device, connecting=False, on_connect=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=13)
        battery = device.battery
        percent = battery.percent if battery else None
        charging = battery.charging if battery else False
        offline = not device.online

        self.pack_start(BatteryRing(percent, charging, dimmed=offline),
                        False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_valign(Gtk.Align.CENTER)

        name = Gtk.Label(label=device.display_name, xalign=0.0)
        name.get_style_context().add_class(
            "voltaic-name-off" if offline else "voltaic-name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        text.pack_start(name, False, False, 0)

        sub = Gtk.Label(label=self._subtitle(device), xalign=0.0)
        sub.get_style_context().add_class("voltaic-sub")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        text.pack_start(sub, False, False, 0)

        self.pack_start(text, True, True, 0)

        button = connect_button(device, connecting, on_connect)
        if button is not None:
            self.pack_end(button, False, False, 0)

    @staticmethod
    def _subtitle(device) -> str:
        battery = device.battery
        bits = []
        if device.kind:
            bits.append(device.kind.capitalize())
        if not device.online:
            bits.append(state.describe_age(device.last_seen).capitalize())
        elif device.cells:
            present = [c.battery for c in device.cells if c.battery.present]
            if not present:
                bits.append("Not in use")
            elif any(b.charging for b in present):
                bits.append("Charging")
            else:
                bits.append(present[0].status.capitalize())
        elif battery is None:
            bits.append("Battery unavailable")
        else:
            bits.append(battery.status.capitalize())
            if battery.approximate:
                bits.append("approx.")
        return "  ·  ".join(bits)


class VoltaicPopup(Gtk.Window):
    """Frameless translucent panel anchored to the tray icon."""

    def __init__(self, on_refresh=None, on_quit=None, on_connect=None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.on_refresh = on_refresh
        self.on_quit = on_quit
        self.on_connect = on_connect
        self._grabbed = False
        self._last_update: float | None = None
        # Set by the app once the tray backend is known; changes the footer
        # hint, since only some backends can open this panel on hover.
        self.hover_capable = False

        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        self.set_app_paintable(True)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_button_press)
        self.connect("key-press-event", self._on_key_press)

        pad = theme.SHADOW_MARGIN
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.content.set_margin_start(pad + 18)
        self.content.set_margin_end(pad + 18)
        self.content.set_margin_top(pad + 16)
        self.content.set_margin_bottom(pad + 14)
        self.add(self.content)

        self.set_size_request(theme.PANEL_WIDTH + pad * 2, -1)

    # -- painting ---------------------------------------------------------

    def _on_draw(self, _widget, cr: cairo.Context) -> bool:
        alloc = self.get_allocation()
        pad = theme.SHADOW_MARGIN
        x, y = pad, pad
        width = alloc.width - pad * 2
        height = alloc.height - pad * 2
        radius = theme.PANEL_RADIUS

        # Start from a fully transparent surface.
        cr.save()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.restore()

        theme.draw_shadow(cr, x, y, width, height, radius)

        gradient = cairo.LinearGradient(0, y, 0, y + height)
        gradient.add_color_stop_rgba(0, *theme.SURFACE_TOP, theme.SURFACE_ALPHA_TOP)
        gradient.add_color_stop_rgba(1, *theme.SURFACE_BOTTOM,
                                     theme.SURFACE_ALPHA_BOTTOM)
        theme.rounded_rect(cr, x, y, width, height, radius)
        cr.set_source(gradient)
        cr.fill()

        # Outer hairline plus a brighter inner top edge — the pair is what
        # makes a flat translucent rectangle read as a pane of glass.
        theme.rounded_rect(cr, x + 0.5, y + 0.5, width - 1, height - 1, radius)
        cr.set_source_rgba(*theme.BORDER, theme.BORDER_ALPHA)
        cr.set_line_width(1)
        cr.stroke()

        cr.save()
        theme.rounded_rect(cr, x + 1.5, y + 1.5, width - 3, height - 3, radius - 1)
        cr.clip()
        highlight = cairo.LinearGradient(0, y, 0, y + 40)
        highlight.add_color_stop_rgba(0, 1, 1, 1, theme.HIGHLIGHT_ALPHA)
        highlight.add_color_stop_rgba(1, 1, 1, 1, 0)
        cr.set_source(highlight)
        cr.rectangle(x, y, width, 40)
        cr.fill()
        cr.restore()
        return False

    # -- content ----------------------------------------------------------

    def set_devices(self, devices, error: str | None = None,
                    connecting=(), connect_errors=None) -> None:
        """Rebuild the panel contents.

        `connecting` holds device keys with a connection attempt in flight;
        `connect_errors` maps a key to why the last attempt failed.
        """
        connect_errors = connect_errors or {}
        for child in self.content.get_children():
            child.destroy()

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title = Gtk.Label(label="VOLTAIC", xalign=0.0)
        title.get_style_context().add_class("voltaic-title")
        header.pack_start(title, False, False, 0)
        plural = "s" if len(devices) != 1 else ""
        count = Gtk.Label(label=f"{len(devices)} device{plural}", xalign=1.0)
        count.get_style_context().add_class("voltaic-title")
        header.pack_end(count, False, False, 0)
        self.content.pack_start(header, False, False, 0)
        self.content.pack_start(self._rule(14, 12), False, False, 0)

        if error:
            message = Gtk.Label(label=error, xalign=0.0)
            message.get_style_context().add_class("voltaic-empty")
            message.set_line_wrap(True)
            message.set_max_width_chars(38)
            self.content.pack_start(message, False, False, 0)
        elif not devices:
            # Not Logitech-only: someone running this purely for AirPods
            # would otherwise be told to check a receiver they do not own.
            message = Gtk.Label(
                label="No devices found.\n"
                      "Plug in a Logitech receiver, or connect an accessory "
                      "over Bluetooth.",
                xalign=0.0)
            message.set_line_wrap(True)
            message.set_max_width_chars(38)
            message.get_style_context().add_class("voltaic-empty")
            self.content.pack_start(message, False, False, 0)
        else:
            for position, device in enumerate(devices):
                if position:
                    self.content.pack_start(self._rule(10, 10), False, False, 0)
                row_class = MultiCellRow if device.cells else DeviceRow
                row = row_class(device,
                                connecting=device.key in connecting,
                                on_connect=self.on_connect)
                self.content.pack_start(row, False, False, 0)

                failure = connect_errors.get(device.key)
                if failure:
                    note = Gtk.Label(label=failure, xalign=0.0)
                    note.get_style_context().add_class("voltaic-warn")
                    note.set_line_wrap(True)
                    note.set_max_width_chars(40)
                    note.set_margin_top(5)
                    self.content.pack_start(note, False, False, 0)

        self._last_update = time.monotonic()
        self.content.pack_start(self._rule(14, 10), False, False, 0)
        self.footer = Gtk.Label(label=self._footer_text(), xalign=0.0)
        self.footer.get_style_context().add_class("voltaic-foot")
        self.content.pack_start(self.footer, False, False, 0)
        self.content.show_all()

    def _footer_text(self) -> str:
        if self._last_update is None:
            return "Never updated"
        age = int(time.monotonic() - self._last_update)
        if age < 5:
            when = "just now"
        elif age < 60:
            when = f"{age}s ago"
        else:
            when = f"{age // 60}m ago"
        # On a tray that supports hover this panel appears on its own and
        # closes when the pointer leaves, so the useful hint is that a click
        # keeps it open; elsewhere a click is the only way in.
        hint = ("click to keep open" if self.hover_capable
                else "click the icon to refresh")
        return f"Updated {when}  ·  {hint}"

    @staticmethod
    def _rule(top: int, bottom: int) -> Gtk.Widget:
        rule = Gtk.DrawingArea()
        rule.set_size_request(-1, 1)
        rule.set_margin_top(top)
        rule.set_margin_bottom(bottom)

        def draw(widget, cr):
            width = widget.get_allocation().width
            # Fade the hairline out at both ends so it doesn't collide with
            # the panel's rounded corners.
            gradient = cairo.LinearGradient(0, 0, width, 0)
            gradient.add_color_stop_rgba(0, 1, 1, 1, 0.0)
            gradient.add_color_stop_rgba(0.5, 1, 1, 1, 0.10)
            gradient.add_color_stop_rgba(1, 1, 1, 1, 0.0)
            cr.set_source(gradient)
            cr.rectangle(0, 0, width, 1)
            cr.fill()
            return False

        rule.connect("draw", draw)
        return rule

    # -- placement and dismissal -----------------------------------------

    def show_at(self, x: int, y: int, panel_position: int | None = None,
                grab: bool = True) -> None:
        """Show the panel next to the tray icon at screen point (x, y).

        `grab` takes an input grab so a click anywhere else dismisses the
        panel. It must be off when the panel was opened by hover: a pointer
        grab would make every motion event land on us, and the caller could
        no longer tell when the pointer had left.
        """
        self.show_all()
        # get_size() reports the *current* size, which before the window is
        # mapped is still the bare size request — placing against that lands
        # the panel in the wrong spot. The preferred size is accurate as soon
        # as the children exist.
        _minimum, natural = self.get_preferred_size()
        width = max(natural.width, theme.PANEL_WIDTH + theme.SHADOW_MARGIN * 2)
        height = natural.height
        pad = theme.SHADOW_MARGIN
        gap = 2

        display = self.get_display()
        monitor = (display.get_monitor_at_point(x, y)
                   or display.get_primary_monitor())
        area = monitor.get_workarea()

        # Cinnamon reports which edge the panel is docked to; without that,
        # decide from which half of the monitor the icon sits in.
        if panel_position is not None:
            at_top = int(panel_position) == int(Gtk.PositionType.TOP)
        else:
            at_top = (y - area.y) < area.height / 2

        target_x = int(x - width / 2)
        target_y = int(y - pad + gap) if at_top else int(y - height + pad - gap)

        # Keep the panel inside the work area. The shadow gutter is allowed to
        # hang off the edge, which is what lets the body sit flush against it.
        target_x = max(area.x - pad,
                       min(target_x, area.x + area.width - width + pad))
        target_y = max(area.y - pad,
                       min(target_y, area.y + area.height - height + pad))

        self.move(target_x, target_y)
        self.present()
        # Some window managers ignore a move issued before the window is
        # mapped, so assert the position once more after it appears.
        GLib.idle_add(self._settle_position, target_x, target_y)
        if grab:
            self._grab()

    def _settle_position(self, x: int, y: int) -> bool:
        if self.get_visible() and self.get_position() != (x, y):
            self.move(x, y)
        return False

    def frame_rect(self) -> tuple[int, int, int, int]:
        """Screen rectangle of the visible panel body, excluding the shadow."""
        x, y = self.get_position()
        width, height = self.get_size()
        pad = theme.SHADOW_MARGIN
        return (x + pad, y + pad, width - pad * 2, height - pad * 2)

    def _grab(self) -> None:
        window = self.get_window()
        if window is None:
            return
        seat = self.get_display().get_default_seat()
        status = seat.grab(window, Gdk.SeatCapabilities.ALL, True,
                           None, None, None, None)
        self._grabbed = status == Gdk.GrabStatus.SUCCESS

    def _ungrab(self) -> None:
        if self._grabbed:
            self.get_display().get_default_seat().ungrab()
            self._grabbed = False

    def pin(self) -> None:
        """Keep a hover-opened panel up: take the grab so clicks dismiss it."""
        self._grab()
        self.refresh_footer()

    def dismiss(self) -> None:
        self._ungrab()
        self.hide()

    def _on_button_press(self, _widget, event) -> bool:
        # With a seat grab every click arrives here; anything landing outside
        # the panel body (including in the shadow gutter) closes it.
        alloc = self.get_allocation()
        pad = theme.SHADOW_MARGIN
        inside = (pad <= event.x <= alloc.width - pad
                  and pad <= event.y <= alloc.height - pad)
        if not inside:
            self.dismiss()
            return True
        return False

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.dismiss()
            return True
        if event.keyval in (Gdk.KEY_r, Gdk.KEY_R) and self.on_refresh:
            self.on_refresh()
            return True
        return False

    def refresh_footer(self) -> None:
        if getattr(self, "footer", None) is not None:
            self.footer.set_text(self._footer_text())


_install_css_done = False


def ensure_css() -> None:
    global _install_css_done
    if not _install_css_done:
        _install_css()
        _install_css_done = True
