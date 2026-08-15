# Changelog

Every notable change to Voltaic, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries say *why* as well as *what* — several of these bugs were invisible
from the machine they were written on, and the reason is usually the useful
part.

---

## [Unreleased]

### Fixed

- **The `upower` source never returned anything.** The call fetching a
  device's properties named `Gio.GLib.Variant`, which does not exist, so
  every device raised `AttributeError` — and a blanket `except Exception:
  continue` swallowed it. Enabling the source did nothing at all, silently,
  for the whole of 1.4.0 and 1.5.0.

  This is the failure the broad `except` was there to prevent and instead
  caused. The D-Bus calls now catch `GLib.Error` only, so a coding mistake
  surfaces while a stopped service still degrades quietly.

### Added

- Source tests against mocked UPower and BlueZ services, via
  python-dbusmock. `upower` and `bluez` read hardware this machine does not
  have and were shipped on the strength of "it does not crash", which is
  exactly how the above survived two releases. The mocks speak the real
  interfaces, so the sources are exercised rather than merely imported —
  including that line power, the host's own battery, absent devices and
  disconnected Bluetooth devices are all correctly ignored.
- CI installs python-dbusmock and asserts it is importable, so these tests
  run rather than skipping.

---

## [1.5.0] — 2026-08-15

**A settings window.** Configuration arrived in 1.4.0 as a JSON file, which
is a fine source of truth and a poor user interface — telling someone to
hand-edit JSON is not a settings system.

### Added

- **Settings…** in the tray icon's right-click menu, opening a window with
  three tabs:
  - *General* — how often to check, low-battery notifications and the
    threshold, and the tray icon style.
  - *Devices* — which kinds of hardware to look for, each explained in
    plain terms rather than by protocol name.
  - *My devices* — rename any device, or untick it to hide it.
- Settings apply immediately where they can. Only the tray icon style needs
  a restart, and the window says so rather than pretending otherwise.

### Notes

The window edits the same `config.json` the command line reads, so the file
stays the source of truth and nothing is hidden behind the UI.

A hidden device still appears in *My devices*, unticked — otherwise it could
never be brought back without editing the file, which is the problem this
release exists to solve.

---

## [1.4.0] — 2026-08-15

**Voltaic is configurable, and can be taught about new hardware.** Until now
it read exactly two device families, with settings that existed only as
command-line flags — and the launcher entry passes none, so in normal use
nothing could be changed at all.

### Added

- **A configuration file** at `~/.config/voltaic/config.json`, holding the
  poll interval, notifications, the low-battery threshold, the tray backend,
  which sources are enabled, and per-device settings. `voltaic --config`
  shows where it lives and what is in effect; `voltaic --write-config`
  creates it. A command-line flag still beats the file for one run.
- **Per-device settings.** Rename a device, or hide it entirely — which is
  how you get rid of an accessory you no longer own but which is still
  remembered from the last time it was seen. `voltaic --list --keys` prints
  the key to write the setting against. Changes apply on the next scan, with
  no restart.
- **Sources**, a small interface for a family of hardware, so support for new
  devices is a class with a `scan()` rather than a change to the monitor.
  Two new generic ones ship with it, both off by default:
  - `upower` — anything UPower knows the charge of: gamepads, tablets,
    phones, generic Bluetooth accessories.
  - `bluez` — Bluetooth devices exposing the standard battery service.

  They are off by default because they surface what the system already
  knows, including a laptop battery the desktop is already showing.

### Notes

HID++ is deliberately not a source: it owns the file descriptors the monitor
parks in `select()` between scans, which is what lets an unsolicited battery
notification update the panel immediately instead of waiting out the poll
interval. `upower` cannot replace it either — the kernel does not recognise
every Logitech receiver, and a Bolt receiver yields no UPower devices at all.

---

## [1.3.0] — 2026-08-15

**Voltaic now works on Wayland**, and when it cannot show a tray icon it says
so instead of running invisibly.

### Fixed

- **No tray icon at all on Wayland.** `auto` tried XEmbed first, and
  `Gtk.StatusIcon` under Wayland constructs without error, is never embedded,
  and displays nothing — so the process ran with no icon, no error, and no way
  to tell it apart from a crash. Voltaic now detects a Wayland display and
  starts at the `xapp` backend instead.

  Confirmed against a headless Weston rather than assumed:

  ```
  StatusIcon constructed: True
  is_embedded():          False
  geometry:               False
  ```

  This affected GNOME and KDE on Wayland — the default session on current
  Ubuntu and Fedora.

- **Silence when no tray exists.** Four seconds after start-up Voltaic reports
  why no icon appeared, in a notification and on standard error, naming the
  extension to install. Previously "no tray on this desktop" and "working
  normally" looked identical from the outside. Also covers XEmbed on X11 where
  no tray accepted the icon.

- **The empty panel assumed a Logitech user.** It read *"No Logitech devices
  found. Check the receiver is plugged in"* even for someone running Voltaic
  purely for AirPods, telling them to check hardware they do not own.

- A `PyGIWarning` on start-up, from importing `Gdk` and `GdkPixbuf` without a
  version guard.

### Internal

- Wayland is detected from the `GdkDisplay` type, not `XDG_SESSION_TYPE`: an
  X11 app under XWayland reports `wayland` while having a perfectly good
  display to embed into, so the environment variable would have caused the
  opposite bug.
- 8 new tests for backend selection, run headless with the session stubbed.
  The suite is now 101 tests.

---

## [1.2.2] — 2026-08-15

Installing from source was broken on Debian, Ubuntu and openSUSE. All three
bugs were found by running the install paths on those distributions for the
first time. None were visible from a Debian-family development machine, and
none were visible through the `.deb` — the one route that uses no virtualenv,
and therefore the one route that could not reveal them.

### Fixed

- **Debian and Ubuntu source installs.** `python3 -m venv` needs
  `python3-venv`, which both distributions split out of the standard library
  and do not install by default. Installs died with `ensurepip is not
  available`. `.deb` users were never affected.
- **openSUSE source installs.** The GTK typelib lives in `typelib-1_0-Gtk-3_0`,
  not `gtk3`. The install reported success and `voltaic --version` worked,
  while the tray could never start — `Namespace Gtk not available for version
  3.0`.
- **`install.sh` could not tell "installed" from "usable".** It trusted the
  package manager's exit code, which is exactly how the openSUSE bug passed
  silently. It now imports Gtk and cairo afterwards and fails loudly, pointing
  at the issue tracker.
- `install.sh` runs as root, where `sudo` may not exist, and no longer aborts
  on a machine with no running udev.
- `make uninstall` explains how to remove the udev rules, which it cannot
  remove itself without root.

### Added

- CI runs `install.sh` end to end on Debian, Ubuntu, Fedora, Arch and openSUSE
  containers, and builds the PKGBUILD on Arch — on every push. Both were
  advertised in the README while never having been executed anywhere.

---

## [1.2.1] — 2026-08-15

### Added

- **A signed apt repository** at <https://jcfs.github.io/voltaic>, so Debian,
  Ubuntu and Mint users can `apt install voltaic` and get new versions with
  `apt upgrade`, rather than downloading a `.deb` and having to notice each
  release. Rebuilt and re-signed on every tagged release.
- A manual page (`man voltaic`), a Debian-format changelog, and DEP-5
  machine-readable copyright.
- AppStream metadata, so Voltaic appears in GNOME Software, KDE Discover and
  the Mint software manager with a description and screenshots instead of a
  bare package name.
- Tests for the HID++ reply matcher and the cairo tray icon; coverage is
  measured and gated in CI.

### Fixed

- The `.deb` was missing a changelog and a manual page, which any archive
  would reject or flag. The package is lintian-clean, and CI fails on any
  error or warning.

---

## [1.2.0] — 2026-08-14

Installing meant "install these GTK packages yourself, then run three make
targets". Native packages fix that properly: a distro package is the only
format that can both declare the GTK dependencies *and* ship the udev rules.
A Flatpak or AppImage can do neither, and the udev rules are not optional.

### Added

- **Debian package.** `sudo apt install ./voltaic_*_all.deb` is the entire
  install: dependencies come from the package manager, the udev rules ship
  inside the package, and the post-install step replays them against an
  already-connected receiver — so there is nothing to configure and nothing to
  replug. Build one with `make deb`.
- **PKGBUILD** for Arch and Manjaro.
- **`install.sh`** for everything else — detects the distribution, prints
  exactly what it will run, and asks before touching anything.
- CI builds the `.deb`, installs it on a clean runner to prove the declared
  dependencies are satisfiable, and attaches it to tagged releases.

---

## [1.1.0] — 2026-08-14

### Added

- Application icon, installed into the hicolor theme, so Voltaic appears in
  launchers with its own artwork instead of a generic battery glyph.
- Headless unit tests covering HID++ report-descriptor parsing, the AAP
  battery frame, the voltage curve, the device model and the offline cache.
- GitHub Actions CI: tests on Python 3.9–3.13 with no GTK installed, `ruff`
  lint, package build, and validation of the desktop entry and udev rules.
- `make test`, and `packaging/make-screenshot.py` to regenerate the README
  images from the real panel.
- Troubleshooting section, and install instructions for Debian/Ubuntu, Fedora,
  Arch and openSUSE.

### Changed

- `make install` uses a private virtualenv created with
  `--system-site-packages` instead of `pip install --user`, which is refused
  outright on distributions enforcing
  [PEP 668](https://peps.python.org/pep-0668/) — Ubuntu 24.04, Debian 12 and
  Fedora 39 among them. The installed desktop entry uses an absolute `Exec`
  path, since launchers do not reliably have `~/.local/bin` on `PATH`.
- The battery gauge shows the number alone; the `%` glyph pushed the digits
  off the centre of the ring.
- The udev rules file is `60-voltaic.rules`, not `99-`. systemd applies the
  `uaccess` ACL from `73-seat-late.rules`, so a rules file sorting after it
  sets the tag too late, the ACL is never added, and the hidraw node silently
  stays `root:root 0600`.

### Fixed

- A missing GTK stack produced a bare `ModuleNotFoundError`, invisible when
  launched from a desktop entry — the app simply appeared not to start. It now
  names the packages to install and falls back through zenity, kdialog,
  xmessage and notify-send so the message is seen without a terminal.
- Bluetooth failures were swallowed, making "PyGObject missing", "BlueZ not
  answering" and "no AirPods paired" indistinguishable. Each is now reported.

---

## [1.0.0] — 2026-07-28

Initial release: Logitech battery over HID++, per-earbud AirPods battery over
AAP, hover-to-open tray panel with three status-icon backends, offline device
memory, and low-battery notifications.

[1.5.0]: https://github.com/jcfs/voltaic/releases/tag/v1.5.0
[1.4.0]: https://github.com/jcfs/voltaic/releases/tag/v1.4.0
[1.3.0]: https://github.com/jcfs/voltaic/releases/tag/v1.3.0
[1.2.2]: https://github.com/jcfs/voltaic/releases/tag/v1.2.2
[1.2.1]: https://github.com/jcfs/voltaic/releases/tag/v1.2.1
[1.2.0]: https://github.com/jcfs/voltaic/releases/tag/v1.2.0
[1.1.0]: https://github.com/jcfs/voltaic/releases/tag/v1.1.0
[1.0.0]: https://github.com/jcfs/voltaic/releases/tag/v1.0.0
