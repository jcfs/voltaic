"""The settings window.

Everything here edits the same `config.json` the command line reads, so the
file stays the source of truth and nothing is hidden behind the UI. Changes
are written on OK and applied to the running app where that is possible —
only the tray backend genuinely needs a restart, and the window says so.
"""

from __future__ import annotations

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import config as config_module  # noqa: E402
from . import sources as sources_module  # noqa: E402

# What each source reads, in the user's terms rather than the protocol's.
SOURCE_LABELS = {
    "hidpp": ("Logitech devices",
              "Keyboards and mice behind a Unifying, Bolt or Nano receiver, "
              "read directly. Does not need Solaar."),
    "airpods": ("AirPods and Beats",
                "Each earbud and the case separately, over Apple's own "
                "accessory protocol."),
    "upower": ("Everything else the system knows",
               "Gamepads, tablets, phones and generic Bluetooth accessories, "
               "via UPower. Off by default because your desktop may already "
               "show these."),
    "bluez": ("Bluetooth battery service",
              "Devices reporting the standard Bluetooth battery level, as a "
              "single figure. Off by default for the same reason."),
}

TRAY_LABELS = {
    "auto": "Automatic (recommended)",
    "xembed": "XEmbed — supports hover to open",
    "xapp": "XApp — Cinnamon, MATE, Xfce",
    "appindicator": "AppIndicator — menu only",
}


class SettingsWindow(Gtk.Window):
    """Edit the configuration without going near a text editor."""

    def __init__(self, config: dict, devices, on_apply):
        super().__init__(title="Voltaic Settings")
        self.config = dict(config)
        self.devices = list(devices)
        self.on_apply = on_apply
        self._device_rows: list[tuple[str, Gtk.Entry, Gtk.CheckButton]] = []

        self.set_default_size(460, -1)
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(outer)

        notebook = Gtk.Notebook()
        notebook.set_border_width(10)
        notebook.append_page(self._general_page(), Gtk.Label(label="General"))
        notebook.append_page(self._sources_page(), Gtk.Label(label="Devices"))
        notebook.append_page(self._device_page(), Gtk.Label(label="My devices"))
        outer.pack_start(notebook, True, True, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_border_width(10)
        buttons.set_halign(Gtk.Align.END)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.destroy())
        buttons.pack_start(cancel, False, False, 0)

        save = Gtk.Button(label="Save")
        save.get_style_context().add_class("suggested-action")
        save.connect("clicked", self._on_save)
        buttons.pack_start(save, False, False, 0)

        outer.pack_start(buttons, False, False, 0)

    # -- pages ------------------------------------------------------------

    @staticmethod
    def _page() -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(14)
        return box

    @staticmethod
    def _row(label: str, widget: Gtk.Widget, hint: str = "") -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        text = Gtk.Label(label=label, xalign=0.0)
        line.pack_start(text, True, True, 0)
        widget.set_halign(Gtk.Align.END)
        line.pack_end(widget, False, False, 0)
        row.pack_start(line, False, False, 0)
        if hint:
            note = Gtk.Label(label=hint, xalign=0.0)
            note.set_line_wrap(True)
            note.set_max_width_chars(52)
            note.get_style_context().add_class("dim-label")
            row.pack_start(note, False, False, 0)
        return row

    def _general_page(self) -> Gtk.Widget:
        page = self._page()

        # Minutes read better than seconds for a 15-minute default, and the
        # cost of scanning is measured in device wake-ups, not in requests.
        self.interval = Gtk.SpinButton.new_with_range(1, 240, 1)
        self.interval.set_value(float(self.config.get("interval", 900)) / 60.0)
        page.pack_start(self._row(
            "Check every (minutes)", self.interval,
            "Opening the panel always refreshes it, so a longer interval only "
            "makes the tray icon and the low-battery warning less current. "
            "Each check briefly wakes a sleeping device."), False, False, 0)

        self.notify = Gtk.Switch()
        self.notify.set_active(bool(self.config.get("notify", True)))
        page.pack_start(self._row("Low-battery notifications", self.notify),
                        False, False, 0)

        self.low_percent = Gtk.SpinButton.new_with_range(1, 100, 1)
        self.low_percent.set_value(int(self.config.get("low_percent", 20)))
        page.pack_start(self._row("Warn below (%)", self.low_percent),
                        False, False, 0)

        self.tray = Gtk.ComboBoxText()
        for value, label in TRAY_LABELS.items():
            self.tray.append(value, label)
        self.tray.set_active_id(str(self.config.get("tray", "auto")))
        page.pack_start(self._row(
            "Tray icon style", self.tray,
            "Takes effect when Voltaic restarts. Only XEmbed can open the "
            "panel on hover, and it does not exist under Wayland."),
            False, False, 0)
        return page

    def _sources_page(self) -> Gtk.Widget:
        page = self._page()
        intro = Gtk.Label(
            label="Which kinds of device Voltaic looks for.", xalign=0.0)
        intro.get_style_context().add_class("dim-label")
        page.pack_start(intro, False, False, 0)

        enabled = self.config.get("sources", {})
        self.source_toggles: dict[str, Gtk.CheckButton] = {}
        for name in config_module.DEFAULTS["sources"]:
            label, hint = SOURCE_LABELS.get(name, (name, ""))
            check = Gtk.CheckButton()
            check.set_active(bool(enabled.get(
                name, config_module.DEFAULTS["sources"][name])))
            self.source_toggles[name] = check
            page.pack_start(self._row(label, check, hint), False, False, 0)
        return page

    def _device_page(self) -> Gtk.Widget:
        page = self._page()
        if not self.devices:
            page.pack_start(Gtk.Label(
                label="No devices found yet.", xalign=0.0), False, False, 0)
            return page

        intro = Gtk.Label(
            label="Rename a device, or hide one you no longer use.",
            xalign=0.0)
        intro.get_style_context().add_class("dim-label")
        page.pack_start(intro, False, False, 0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        grid.attach(Gtk.Label(label="Device", xalign=0.0), 0, 0, 1, 1)
        grid.attach(Gtk.Label(label="Show", xalign=0.5), 1, 0, 1, 1)

        for row, device in enumerate(self.devices, start=1):
            override = config_module.device_override(self.config, device.key)
            entry = Gtk.Entry()
            entry.set_width_chars(26)
            entry.set_text(override.get("name", "") or device.display_name)
            entry.set_placeholder_text(device.display_name)
            entry.set_tooltip_text(device.key)

            visible = Gtk.CheckButton()
            visible.set_active(not override.get("hidden", False))
            visible.set_halign(Gtk.Align.CENTER)

            grid.attach(entry, 0, row, 1, 1)
            grid.attach(visible, 1, row, 1, 1)
            self._device_rows.append((device.key, entry, visible))

        page.pack_start(grid, False, False, 0)
        return page

    # -- saving -----------------------------------------------------------

    def collect(self) -> dict:
        """The configuration as the window currently describes it."""
        config = dict(self.config)
        config["interval"] = float(self.interval.get_value()) * 60.0
        config["notify"] = bool(self.notify.get_active())
        config["low_percent"] = int(self.low_percent.get_value())
        config["tray"] = self.tray.get_active_id() or "auto"
        config["sources"] = {name: bool(check.get_active())
                             for name, check in self.source_toggles.items()}

        for key, entry, visible in self._device_rows:
            # An empty box, or the device's own name, means "no override" —
            # storing the name verbatim would freeze it if the device ever
            # reported a different one.
            typed = entry.get_text().strip()
            original = entry.get_placeholder_text()
            name = typed if typed and typed != original else None
            hidden = True if not visible.get_active() else None
            config = config_module.set_device(config, key,
                                              name=name, hidden=hidden)
        return config

    def _on_save(self, _button) -> None:
        config = self.collect()
        try:
            config_module.save(config)
        except OSError as exc:
            dialog = Gtk.MessageDialog(
                transient_for=self, modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CLOSE,
                text="Could not save settings")
            dialog.format_secondary_text(
                f"{config_module.CONFIG_PATH}: {exc}")
            dialog.run()
            dialog.destroy()
            return
        self.on_apply(config)
        self.destroy()


def available_source_names() -> list[str]:
    """Sources this build knows about — the registry, plus HID++."""
    return ["hidpp"] + [n for n in sources_module.REGISTRY]
