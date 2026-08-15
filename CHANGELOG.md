# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.1] - 2026-08-15

### Added

- **A signed apt repository**, so Debian, Ubuntu and Mint users can
  `apt install voltaic` and then get new versions with `apt upgrade`, rather
  than downloading a `.deb` by hand and having to notice each release. It is
  published to <https://jcfs.github.io/voltaic> from every tagged build.
- A manual page (`man voltaic`), a Debian-format changelog, and DEP-5
  machine-readable copyright.
- AppStream metadata, so Voltaic appears in GNOME Software, KDE Discover and
  the Mint software manager with a description and screenshots instead of a
  bare package name.
- Tests for the HID++ reply matcher and the cairo tray icon, taking the
  suite to 93 tests; coverage is measured and gated in CI.

### Fixed

- The `.deb` was missing a changelog and a manual page, which any archive
  would reject or flag. The package is now lintian-clean, with CI failing on
  any error or warning.

## [1.2.0] - 2026-08-14

Installation was the biggest barrier to actually using this: every route
went through "install these GTK packages yourself, then run three make
targets". Native packages fix that properly, because a distro package is
the only format that can both declare the GTK dependencies *and* ship the
udev rules — a Flatpak or AppImage can do neither, and the udev rules are
not optional.

### Added

- **Debian package.** `sudo apt install ./voltaic_*_all.deb` is now the
  entire install on Debian, Ubuntu and Mint: dependencies come from the
  package manager, the udev rules ship inside the package, and the
  post-install step replays them against an already-plugged-in receiver, so
  there is nothing to configure and nothing to replug. Build one with
  `make deb`.
- **PKGBUILD** for Arch and Manjaro.
- **`install.sh`** for everything else — detects the distribution, prints
  exactly what it will run, and asks before touching anything. Recognises
  the Debian, Fedora, Arch and openSUSE families.
- CI builds the `.deb`, installs it on a clean runner to prove the declared
  dependencies are satisfiable and the maintainer scripts run, and attaches
  it to tagged releases.

### Changed

- The README leads with the one-command installs; building from source is
  now the last option rather than the only one.

## [1.1.0] - 2026-08-14

### Added

- Application icon, installed into the hicolor theme, so Voltaic appears in
  application launchers with its own artwork instead of a generic battery
  glyph.
- Headless unit tests covering HID++ report-descriptor parsing, the AAP
  battery frame, the voltage curve, the device model and the offline cache.
- GitHub Actions CI: tests on Python 3.9–3.13 with no GTK installed, `ruff`
  lint, package build, and validation of the desktop entry and udev rules.
- `make test` target, and `packaging/make-screenshot.py` to regenerate the
  README images from the real panel.
- Troubleshooting section in the README, and install instructions for
  Debian/Ubuntu, Fedora, Arch and openSUSE.

### Changed

- `make install` now installs into a private virtualenv created with
  `--system-site-packages` instead of `pip install --user`, which is refused
  outright on distributions that enforce PEP 668 (Ubuntu 24.04, Debian 12,
  Fedora 39 and later). The installed desktop entry uses an absolute `Exec`
  path, since launchers do not reliably have `~/.local/bin` on PATH.
- The battery gauge shows the number alone; the `%` glyph was pushing the
  digits off the centre of the ring.
- The udev rules file is named `60-voltaic.rules` rather than
  `99-voltaic.rules`. systemd applies the `uaccess` ACL from
  `73-seat-late.rules`, so a rules file that sorts after it sets the tag too
  late, the ACL is never added, and the hidraw node silently stays
  `root:root 0600`.

### Fixed

- A missing GTK stack produced a bare `ModuleNotFoundError` traceback, which
  is invisible when launched from a desktop entry — the app simply appeared
  not to start. It now names the packages to install, and falls back through
  zenity, kdialog, xmessage and notify-send so the message is seen without a
  terminal.
- Bluetooth failures were swallowed silently, making "PyGObject is missing",
  "BlueZ is not answering" and "no AirPods paired" indistinguishable from
  each other. Each is now reported.

## [1.0.0] - 2026-07-28

Initial release: Logitech battery over HID++, per-earbud AirPods battery over
AAP, hover-to-open tray panel with three status-icon backends, offline device
memory, and low-battery notifications.
