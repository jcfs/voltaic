"""Status icon integration.

Three backends, tried in order:

* **xembed** — the legacy `Gtk.StatusIcon`. Deprecated, but it is the only
  one that reports the icon's screen rectangle *and* fires a signal on
  hover, which is what allows the panel to open when the pointer rests on
  the icon rather than only on click. Preferred whenever the tray accepts
  it.
* **xapp** — `XAppStatusIcon`, native to Cinnamon/MATE/Xfce. Gives click
  coordinates but has no enter/leave signal, so hover falls back to a plain
  text tooltip.
* **appindicator** — SNI. No coordinates and no left click at all, so the
  panel is opened from a menu entry.
"""

from __future__ import annotations

import os
import warnings

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

from . import icons  # noqa: E402

try:
    gi.require_version("XApp", "1.0")
    from gi.repository import XApp
    HAVE_XAPP = True
except (ValueError, ImportError):  # pragma: no cover - depends on desktop
    HAVE_XAPP = False

HAVE_INDICATOR = False
AppIndicator = None
for _namespace in ("AyatanaAppIndicator3", "AppIndicator3"):
    try:
        gi.require_version(_namespace, "0.1")
        AppIndicator = getattr(__import__("gi.repository", fromlist=[_namespace]),
                               _namespace)
        HAVE_INDICATOR = True
        break
    except (ValueError, ImportError, AttributeError):  # pragma: no cover
        continue

DEFAULT_ICON_SIZE = 22
BACKENDS = ("auto", "xembed", "xapp", "appindicator")


def _scale_factor() -> int:
    """Display scale factor, clamped to something sane."""
    display = Gdk.Display.get_default()
    if display is None:
        return 1
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    if monitor is None:
        return 1
    return max(1, min(4, monitor.get_scale_factor()))


class Tray:
    """Wraps whichever status-icon backend the desktop provides.

    Callbacks:
      on_toggle(x, y, panel_position)  left click — opens the panel pinned
      on_hover(x, y, panel_position)   pointer rested on the icon (may be None)
      on_refresh()                     menu entry
      on_quit()                        menu entry
    """

    def __init__(self, on_toggle, on_refresh, on_quit, on_hover=None,
                 preferred: str = "auto"):
        self.on_toggle = on_toggle
        self.on_hover = on_hover
        self.on_refresh = on_refresh
        self.on_quit = on_quit
        self._icon_path: str | None = None
        self._size_hint = DEFAULT_ICON_SIZE
        self.backend = "none"
        self._xapp = None
        self._status = None
        self._indicator = None

        self.menu = self._build_menu()
        order = (("xembed", "xapp", "appindicator") if preferred == "auto"
                 else (preferred,))
        for name in order:
            if self._try_backend(name):
                self.backend = name
                break

    # -- backend setup ----------------------------------------------------

    def _try_backend(self, name: str) -> bool:
        if name == "xembed":
            return self._setup_xembed()
        if name == "xapp" and HAVE_XAPP:
            return self._setup_xapp()
        if name == "appindicator" and HAVE_INDICATOR:
            return self._setup_indicator()
        return False

    def _setup_xembed(self) -> bool:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            status = Gtk.StatusIcon()
            status.set_title("Voltaic")
            status.set_name("voltaic")
            # We answer query-tooltip ourselves (returning False, so no
            # tooltip is drawn) purely to learn that the pointer is hovering.
            status.set_has_tooltip(True)
            status.set_visible(True)
        status.connect("query-tooltip", self._on_query_tooltip)
        status.connect("activate", self._on_activate)
        status.connect("popup-menu", self._on_popup_menu)
        status.connect("size-changed", self._on_size_changed)
        self._status = status
        return True

    def _setup_xapp(self) -> bool:
        self._xapp = XApp.StatusIcon()
        self._xapp.set_name("voltaic")
        self._xapp.set_secondary_menu(self.menu)
        self._xapp.connect("button-press-event", self._on_xapp_press)
        return True

    def _setup_indicator(self) -> bool:
        self._indicator = AppIndicator.Indicator.new(
            "voltaic", "battery", AppIndicator.IndicatorCategory.HARDWARE)
        self._indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        # SNI has no notion of a plain left click, so the panel gets a menu
        # whose first entry opens it.
        show = Gtk.MenuItem(label="Show devices")
        show.connect("activate", lambda _i: self.on_toggle(None, None, None))
        show.show()
        self.menu.insert(show, 0)
        self._indicator.set_menu(self.menu)
        return True

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        refresh = Gtk.MenuItem(label="Refresh now")
        refresh.connect("activate", lambda _i: self.on_refresh())
        menu.append(refresh)

        autostart = Gtk.CheckMenuItem(label="Start at login")
        autostart.set_active(is_autostart_enabled())
        autostart.connect("toggled", lambda item: set_autostart(item.get_active()))
        menu.append(autostart)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit Voltaic")
        quit_item.connect("activate", lambda _i: self.on_quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

    # -- geometry ---------------------------------------------------------

    def geometry(self) -> tuple[int, int, int, int] | None:
        """Screen rectangle of the icon in logical pixels, if knowable."""
        if self._status is None:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ok, _screen, rect, _orientation = self._status.get_geometry()
        if not ok or rect.width <= 0:
            return None
        return (rect.x, rect.y, rect.width, rect.height)

    def _anchor(self) -> tuple[int, int, object]:
        """Where the panel should point, plus which edge the tray is on."""
        rect = self.geometry()
        if rect is None:
            display = Gdk.Display.get_default()
            _s, px, py = display.get_default_seat().get_pointer().get_position()
            return px, py, None
        x, y, width, height = rect
        centre_x = x + width // 2
        centre_y = y + height // 2
        # Decide the docked edge from where the icon sits on its monitor.
        display = Gdk.Display.get_default()
        monitor = (display.get_monitor_at_point(centre_x, centre_y)
                   or display.get_primary_monitor())
        area = monitor.get_workarea()
        at_top = (centre_y - area.y) < area.height / 2
        position = Gtk.PositionType.TOP if at_top else Gtk.PositionType.BOTTOM
        return centre_x, centre_y, position

    # -- signal handlers --------------------------------------------------

    def _on_query_tooltip(self, _icon, _x, _y, _keyboard, _tooltip) -> bool:
        if self.on_hover is not None:
            self.on_hover(*self._anchor())
        # False => GTK draws no tooltip; the panel is the tooltip.
        return False

    def _on_activate(self, _icon) -> None:
        self.on_toggle(*self._anchor())

    def _on_popup_menu(self, _icon, button, time) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.menu.popup(None, None, Gtk.StatusIcon.position_menu,
                            self._status, button, time)

    def _on_size_changed(self, _icon, size) -> bool:
        self._size_hint = int(size) or DEFAULT_ICON_SIZE
        self._icon_path = None  # force a re-render at the new size
        return False

    def _on_xapp_press(self, _icon, x, y, button, _time, panel_position) -> bool:
        if button == 1:
            self.on_toggle(x, y, panel_position)
        return False

    # -- appearance -------------------------------------------------------

    @property
    def icon_size(self) -> int:
        """Physical pixel size to render at.

        Both backends report a *logical* size, so on a HiDPI panel the PNG
        has to be multiplied by the monitor scale factor or the icon shows
        up at half the size of its neighbours.
        """
        size = self._size_hint
        if self._xapp is not None:
            reported = self._xapp.get_icon_size()
            if reported:
                size = int(reported)
        return size * _scale_factor()

    def set_state(self, percent: int | None, charging: bool, tooltip: str) -> None:
        path = icons.render_icon(percent, charging, self.icon_size)
        changed = path != self._icon_path
        self._icon_path = path

        if self._status is not None:
            if changed:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    # Hand over the full-resolution render. The tray scales
                    # the pixbuf into its slot itself, so downsampling here
                    # first would only throw away detail on a HiDPI panel.
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
                    self._status.set_from_pixbuf(pixbuf)
        elif self._xapp is not None:
            if changed:
                self._xapp.set_icon_name(path)
            self._xapp.set_tooltip_text(tooltip)
        elif self._indicator is not None:
            if changed:
                self._indicator.set_icon_full(path, "Battery")
            self._indicator.set_title(tooltip)


# ---------------------------------------------------------------------------
# Autostart
# ---------------------------------------------------------------------------

AUTOSTART_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "autostart", "voltaic.desktop",
)

AUTOSTART_ENTRY = """[Desktop Entry]
Type=Application
Name=Voltaic
Comment=Logitech device battery levels in the system tray
Exec={exec}
Icon=battery
Terminal=false
Categories=Utility;
X-GNOME-Autostart-enabled=true
"""


def _exec_command() -> str:
    import shutil
    import sys
    installed = shutil.which("voltaic")
    if installed:
        return installed
    # Running from a checkout: re-exec the same interpreter and package.
    return f"{sys.executable} -m voltaic"


def is_autostart_enabled() -> bool:
    return os.path.exists(AUTOSTART_PATH)


def set_autostart(enabled: bool) -> None:
    if not enabled:
        try:
            os.remove(AUTOSTART_PATH)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(AUTOSTART_PATH), exist_ok=True)
    with open(AUTOSTART_PATH, "w") as handle:
        handle.write(AUTOSTART_ENTRY.format(exec=_exec_command()))
