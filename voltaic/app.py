"""Application wiring: monitor thread -> tray icon -> popup panel."""

from __future__ import annotations

import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import airpods, hidpp, sources  # noqa: E402
from . import config as config_module
from .monitor import DEFAULT_INTERVAL, Monitor  # noqa: E402
from .popup import VoltaicPopup, ensure_css  # noqa: E402
from .tray import Tray  # noqa: E402

# Warn once per device when it crosses this level on the way down.
LOW_BATTERY_PERCENT = 20

# How often to check whether the pointer has wandered off a hover-opened
# panel, and how long it may sit outside before the panel closes. The grace
# period is what lets the pointer cross the gap between icon and panel.
HOVER_POLL_MS = 120
HOVER_GRACE_SECONDS = 0.45

# Slack around the icon and panel rectangles, so the dead space between them
# still counts as "still hovering".
HOVER_SLACK = 26


class VoltaicApp:
    def __init__(self, interval: float = DEFAULT_INTERVAL, notify: bool = True,
                 tray_backend: str = "auto", config: dict | None = None):
        ensure_css()
        # Settings come from the config file; the caller passes whatever the
        # command line overrode, which always wins for this run.
        self.config = config if config is not None else config_module.load()
        self.low_percent = int(self.config.get("low_percent",
                                               LOW_BATTERY_PERCENT))
        self.devices: list[hidpp.Device] = []
        self.all_devices: list[hidpp.Device] = []
        self.error: str | None = None
        self._settings_window = None
        self.notify_enabled = notify
        self._warned: set[str] = set()

        # A panel opened by hover closes itself when the pointer leaves; one
        # opened by click stays put until dismissed.
        self._pinned = False
        self._hover_timer: int | None = None
        self._left_at: float | None = None
        self._icon_rect: tuple[int, int, int, int] | None = None

        # Device keys with a connection attempt in flight, and why the last
        # attempt failed. Both are only ever touched on the main loop.
        self._connecting: set[str] = set()
        self._connect_errors: dict[str, str] = {}

        self.popup = VoltaicPopup(on_refresh=self.refresh,
                                  on_connect=self.connect_device)
        self.tray = Tray(on_toggle=self.toggle_popup,
                         on_hover=self.hover_popup,
                         on_refresh=self.refresh,
                         on_quit=self.quit,
                         on_settings=self.open_settings,
                         preferred=tray_backend)
        # Only the XEmbed backend reports hover and icon geometry.
        self.popup.hover_capable = self.tray.backend == "xembed"
        self.tray.set_state(None, False, "Voltaic — looking for devices…")

        self.monitor = Monitor(
            self._on_update_threaded, interval=interval,
            sources=sources.build(config_module.enabled_sources(self.config)))
        self.monitor.start()

        # Keep the popup's "updated Ns ago" line honest while it is open.
        GLib.timeout_add_seconds(10, self._tick_footer)

        # Give the tray a few seconds to embed the icon, then say something
        # if it never appeared. Silence here reads as "the app is broken".
        GLib.timeout_add_seconds(4, self._check_tray_visible)

    def _check_tray_visible(self) -> bool:
        reason = self.tray.invisible_reason()
        if reason:
            print(f"voltaic: {reason}", file=sys.stderr)
            _notify("Voltaic has no tray icon", reason)
        return False  # once is enough

    # -- monitor callbacks ------------------------------------------------

    def _on_update_threaded(self, devices, error) -> None:
        # Called on the monitor thread; hop to the main loop before touching
        # any GTK object.
        GLib.idle_add(self._on_update, devices, error)

    def _on_update(self, devices, error) -> bool:
        # Everything found, before hiding — the settings window lists these,
        # so that a hidden device can still be brought back.
        self.all_devices = list(devices)
        # Hidden devices are dropped and renames applied here, so changing
        # the config affects the next scan without a restart.
        devices = config_module.apply_overrides(self.config, devices)
        self.devices = devices
        self.error = error
        self.tray.set_state(*self._summary())
        self._render_popup(devices, error)
        if self.notify_enabled:
            self._check_low_battery(devices)
        return False

    def _summary(self) -> tuple[int | None, bool, str]:
        """Icon level, charging flag, and tooltip text for the tray."""
        if self.error:
            return None, False, f"Voltaic — {self.error.splitlines()[0]}"
        # Only devices that are actually connected may drive the icon — a
        # cached level from an offline device is not a live reading.
        readable = [d for d in self.devices
                    if d.online and d.lowest_percent is not None]
        lines = []
        for device in self.devices:
            if not device.online:
                level = device.lowest_percent
                known = (f"{level}% when last seen" if level is not None
                         else "offline")
                lines.append(f"{device.display_name} — {known}")
            elif device.cells:
                parts = ", ".join(
                    f"{cell.label} {cell.battery.percent}%"
                    if cell.battery.present else f"{cell.label} —"
                    for cell in device.cells)
                lines.append(f"{device.display_name} — {parts}")
            elif device.battery is None or device.battery.percent is None:
                lines.append(f"{device.display_name} — unknown")
            else:
                battery = device.battery
                suffix = f" ({battery.status})" if battery.charging else ""
                lines.append(f"{device.display_name} — {battery.percent}%{suffix}")

        if not readable:
            return None, False, "\n".join(lines) or "Voltaic — no battery data"

        # The icon shows whatever will run out first, across every device.
        worst = min(readable, key=lambda d: d.lowest_percent)
        return worst.lowest_percent, worst.any_charging, "\n".join(lines)

    def _check_low_battery(self, devices) -> None:
        for device in devices:
            key = device.key
            level = device.lowest_percent
            if not device.online or level is None or device.any_charging:
                self._warned.discard(key)
                continue
            if level > self.low_percent:
                self._warned.discard(key)
            elif key not in self._warned:
                self._warned.add(key)
                _notify(f"{device.display_name} battery low",
                        f"{level}% remaining")

    def open_settings(self) -> None:
        """Show the settings window, or raise the one already open."""
        if self._settings_window is not None:
            self._settings_window.present()
            return

        from .settings import SettingsWindow
        window = SettingsWindow(self.config, self.all_devices,
                                on_apply=self.apply_config)
        self._settings_window = window

        def forget(_window):
            self._settings_window = None
        window.connect("destroy", forget)
        window.show_all()

    def apply_config(self, config: dict) -> None:
        """Adopt saved settings without a restart, where that is possible.

        The tray backend is the exception: it is chosen once, when the icon
        is created, and the settings window says as much.
        """
        self.config = config
        self.low_percent = int(config.get("low_percent", LOW_BATTERY_PERCENT))
        self.notify_enabled = bool(config.get("notify", True))
        self.monitor.interval = float(config.get("interval", DEFAULT_INTERVAL))
        self.monitor.sources = sources.build(
            config_module.enabled_sources(config))
        # Re-render immediately so a rename or a newly hidden device is
        # visible now rather than at the next scan.
        self._on_update(self.all_devices, self.error)
        self.refresh()

    def _tick_footer(self) -> bool:
        if self.popup.get_visible():
            self.popup.refresh_footer()
        return True

    # -- actions ----------------------------------------------------------

    def _open(self, x, y, panel_position, pinned: bool) -> None:
        if x is None or y is None:
            # AppIndicator gives no coordinates; fall back to the pointer.
            seat = self.popup.get_display().get_default_seat()
            _screen, x, y = seat.get_pointer().get_position()
            panel_position = None
        self._icon_rect = self.tray.geometry()
        self._pinned = pinned
        self._render_popup(self.devices, self.error)
        self.popup.show_at(int(x), int(y), panel_position, grab=pinned)
        self.refresh()

    def toggle_popup(self, x, y, panel_position) -> None:
        """Left click on the icon.

        Three cases: closed opens it, already open by hover pins it in place
        (clicking must not dismiss the panel the pointer just summoned), and
        already pinned closes it.
        """
        self._stop_hover_watch()
        if self.popup.get_visible():
            if self._pinned:
                self.popup.dismiss()
                self._pinned = False
            else:
                self._pinned = True
                self.popup.pin()
            return
        self._open(x, y, panel_position, pinned=True)

    def hover_popup(self, x, y, panel_position) -> None:
        """Pointer rested on the icon: open unpinned and watch for it leaving."""
        if self.popup.get_visible():
            return
        self._open(x, y, panel_position, pinned=False)
        self._start_hover_watch()

    # -- hover tracking ---------------------------------------------------

    def _start_hover_watch(self) -> None:
        self._left_at = None
        if self._hover_timer is None:
            self._hover_timer = GLib.timeout_add(HOVER_POLL_MS, self._watch_pointer)

    def _stop_hover_watch(self) -> None:
        if self._hover_timer is not None:
            GLib.source_remove(self._hover_timer)
            self._hover_timer = None
        self._left_at = None

    def _watch_pointer(self) -> bool:
        # There is no leave event to hook: the tray icon lives in the panel's
        # process, so the only way to know the pointer has gone is to look.
        if self._pinned or not self.popup.get_visible():
            self._hover_timer = None
            return False

        seat = self.popup.get_display().get_default_seat()
        _screen, px, py = seat.get_pointer().get_position()

        if self._within_hover_zone(px, py):
            self._left_at = None
            return True

        now = time.monotonic()
        if self._left_at is None:
            self._left_at = now
            return True
        if now - self._left_at >= HOVER_GRACE_SECONDS:
            self.popup.dismiss()
            self._hover_timer = None
            return False
        return True

    def _within_hover_zone(self, px: int, py: int) -> bool:
        """Is the pointer on the icon, on the panel, or in the gap between?"""
        if _in_rect(px, py, self.popup.frame_rect(), HOVER_SLACK):
            return True
        if self._icon_rect and _in_rect(px, py, self._icon_rect, HOVER_SLACK):
            return True
        return False

    def _render_popup(self, devices, error) -> None:
        self.popup.set_devices(devices, error,
                               connecting=set(self._connecting),
                               connect_errors=dict(self._connect_errors))

    # -- connecting -------------------------------------------------------

    def connect_device(self, device) -> None:
        """Bring up a Bluetooth device's link, without blocking the UI.

        BlueZ takes seconds to page a device and can sit out a full timeout
        when it is switched off, so the call goes to a worker thread and the
        result comes back through the main loop.
        """
        key = device.key
        if key in self._connecting:
            return
        self._connecting.add(key)
        self._connect_errors.pop(key, None)
        self._render_popup(self.devices, self.error)

        def worker():
            error = None
            try:
                airpods.connect(device.path)
            except Exception as exc:
                error = str(exc) or "connection failed"
            GLib.idle_add(self._connect_finished, key, error)

        threading.Thread(target=worker, daemon=True,
                         name="voltaic-connect").start()

    def _connect_finished(self, key: str, error: str | None) -> bool:
        self._connecting.discard(key)
        if error:
            self._connect_errors[key] = error
            self._render_popup(self.devices, self.error)
        else:
            # Give BlueZ a moment to settle the profiles before we go asking
            # the accessory for its battery.
            GLib.timeout_add(1200, self._after_connect)
        return False

    def _after_connect(self) -> bool:
        self.refresh()
        return False

    def refresh(self) -> None:
        self.monitor.refresh_soon()

    def quit(self) -> None:
        self.monitor.stop()
        Gtk.main_quit()


def _in_rect(px: int, py: int, rect: tuple[int, int, int, int],
             slack: int = 0) -> bool:
    x, y, width, height = rect
    return (x - slack <= px <= x + width + slack
            and y - slack <= py <= y + height + slack)


def _notify(summary: str, body: str) -> None:
    """Post a desktop notification, silently doing nothing if unavailable."""
    try:
        gi.require_version("Notify", "0.7")
        from gi.repository import Notify
        if not Notify.is_initted():
            Notify.init("Voltaic")
        Notify.Notification.new(summary, body, "battery-low").show()
    except Exception:
        pass


def run(interval: float = DEFAULT_INTERVAL, notify: bool = True,
        tray_backend: str = "auto", config: dict | None = None) -> int:
    app = VoltaicApp(interval=interval, notify=notify, config=config,
                     tray_backend=tray_backend)
    try:
        Gtk.main()
    except KeyboardInterrupt:
        app.quit()
    return 0
